#!/usr/bin/env python3
"""工作项收尾巡检：找出该关没关的需求。

扫描个人工作箱（或指定项目空间）里所有未终态工作项，联查关联研发任务（CR/DEF）、
父项关系与最后更新时间，分成四类：

  closable  已具备关闭条件（待测试/测试中/待验收 且关联研发任务全部完成）
  blocked   想关但关不掉（同上状态，但仍有研发任务没完成 —— 平台会拒绝）
  stale     僵尸项（待处理/已选择/开发中，超过 N 天无更新）
  orphan    孤岛项（没有任何父项/关联，未并入大需求）

用法：
  python3 closeout_check.py                  # 个人工作箱，默认 14 天僵尸阈值
  python3 closeout_check.py --days 7
  python3 closeout_check.py --project 2165091
  python3 closeout_check.py --json           # 机器可读，供定时任务二次加工
  python3 closeout_check.py --advance        # 研发任务已完成的推进到「待验收」，不关单

--advance 的边界：只推进到「待验收」（研发/测试结束）。终态「已发布」需要人确认，
脚本永远不自己走这一步，只把候选列出来。
"""

import argparse
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

TERMINAL = {
    "已发布", "已取消", "已完成", "已关闭", "已解决",
    "Closed", "Done", "Resolved", "Fixed", "Won'tfix", "Won't Fix", "Invalid", "Duplicate", "ByDesign",
}
NEAR_END = {"待测试", "测试中", "待验收"}
# CANCEL = CR 已关闭（a1 app cr close 后工作项状态就是它），不再阻塞需求流转
DEV_DONE = {"DONE", "SUCCESS", "MERGED", "CANCEL", "已发布", "已完成"}


def run_a1(args):
    cmd = ["a1"] + args
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if proc.returncode != 0:
        raise RuntimeError(f"{' '.join(cmd)} -> rc={proc.returncode}: {proc.stderr.strip()[:300]}")
    out = proc.stdout.strip()
    if not out:
        return None
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        raise RuntimeError(f"{' '.join(cmd)} -> 非 JSON 输出: {out[:200]}")


def as_items(data):
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for k in ("result", "data", "items"):
            v = data.get(k)
            if isinstance(v, list):
                return v
    return []


def owner_of(item):
    a = item.get("assignedTo")
    if isinstance(a, dict):
        return a.get("displayName") or a.get("nickName") or ""
    return a or ""


def parse_time(value):
    if not value:
        return None
    text = str(value)
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, fmt).replace(tzinfo=timezone(timedelta(hours=8)))
        except ValueError:
            continue
    return None


def collect_relations(wid):
    try:
        data = run_a1(["project", "workitem", "relation", "list", wid, "-f", "json"]) or {}
    except RuntimeError as exc:
        return {"dev": [], "workitem": [], "error": str(exc)}
    dev = []
    for d in data.get("dev") or []:
        if not isinstance(d, dict):
            continue
        dev.append({
            "id": d.get("Identifier") or d.get("identifier"),
            "type": d.get("Type") or d.get("type"),
            "subject": d.get("Subject") or d.get("subject"),
            "status": d.get("Status") or d.get("status"),
        })
    parents = []
    for w in data.get("workitem") or []:
        if not isinstance(w, dict):
            continue
        parents.append({
            "relation": w.get("Type") or w.get("type"),
            "id": w.get("Identifier") or w.get("identifier"),
            "subject": w.get("Subject") or w.get("subject"),
        })
    return {"dev": dev, "workitem": parents}


def is_dev_done(status):
    return str(status or "").upper() in DEV_DONE


# 状态链只用于候选排序，真实可用状态以平台报错里的 available 为准
CHAIN = ["待处理", "已选择", "开发中", "排期中", "待测试", "测试中", "待验收", "已发布", "已完成", "Closed"]
FIELD_MAP = {"评审日期": "288", "计划开始日期": "79", "计划开始": "79",
             "计划截止日期": "80", "计划截止": "80"}
ADVANCE_CAP = "待验收"
# 只自动推进已经进过开发态的单：停在待处理/已选择却已有完成研发任务的属于流程异常，交人工
ADVANCE_FROM = {"开发中", "排期中", "待测试", "测试中"}


def run_update(wid, status, cfs=None):
    cmd = ["a1", "project", "workitem", "update", str(wid)]
    if status:
        cmd += ["--status", status]
    for key, value in (cfs or {}).items():
        cmd += ["--cfs", f"{key}={value}"]
    proc = subprocess.run(cmd, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    return proc.returncode, (proc.stdout or "") + (proc.stderr or "")


def _pick_forward(available, current, cap_idx):
    idx_now = CHAIN.index(current) if current in CHAIN else -1
    scored = [(CHAIN.index(a) if a in CHAIN else -1, a) for a in available]
    # 严格向前且不越过「待验收」：越界一步都不走，终态交给人工确认
    candidates = [s for s in scored if idx_now < s[0] <= cap_idx]
    return max(candidates)[1] if candidates else None


def advance_to_dev_end(wid, current_status, today):
    """推进到「待验收」为止，返回 (结果, 轨迹)。只沿平台允许的流转走。

    结果：done / blocked_by_dev / error。永不推进到终态，关单必须人工确认。
    """
    cap_idx = CHAIN.index(ADVANCE_CAP)
    trail = []
    current = current_status
    for _ in range(8):
        rc, msg = run_update(wid, ADVANCE_CAP)
        if rc == 0:
            return "done", trail + [f"->{ADVANCE_CAP}"]
        if "研发任务未完成" in msg:
            return "blocked_by_dev", trail + ["关联研发任务未完成，平台拒绝"]
        if "填写下列属性" in msg:
            missing = [n for n in FIELD_MAP if n in msg]
            if not missing:
                return "error", trail + [msg.strip()[:100]]
            rc2, msg2 = run_update(wid, ADVANCE_CAP, {FIELD_MAP[n]: today for n in missing})
            if rc2 == 0:
                return "done", trail + [f"补字段({','.join(missing)}) ->{ADVANCE_CAP}"]
            return "error", trail + [f"补字段后仍失败: {msg2.strip()[:80]}"]
        if "available" in msg:
            raw = msg.split("available:", 1)[1].strip().rstrip("。.")
            available = [x.strip() for x in raw.replace("，", ",").split(",") if x.strip()]
            nxt = _pick_forward(available, current, cap_idx)
            if not nxt:
                return "error", trail + [f"没有不越过「{ADVANCE_CAP}」的流转: {raw[:60]}"]
            rcx, msgx = run_update(wid, nxt)
            if rcx != 0 and any(k in msgx for k in FIELD_MAP):
                miss = [n for n in FIELD_MAP if n in msgx]
                rcx, msgx = run_update(wid, nxt, {FIELD_MAP[n]: today for n in miss})
            if rcx != 0:
                return "error", trail + [f"->{nxt} 失败: {msgx.strip()[:80]}"]
            trail.append(f"->{nxt}")
            current = nxt
            continue
        return "error", trail + [msg.strip()[:100]]
    return "error", trail + [f"超过 8 步仍未到「{ADVANCE_CAP}」"]


def read_status(wid):
    try:
        data = run_a1(["project", "workitem", "get", str(wid), "-f", "json"]) or {}
    except RuntimeError:
        return None
    for f in data.get("fields") or []:
        if isinstance(f, dict) and f.get("identifier") == "status":
            v = f.get("value")
            if isinstance(v, dict):
                return v.get("name") or v.get("displayName")
            return v
    return None


def classify(item, rel, stale_days, now):
    status = item.get("status") or ""
    dev = rel["dev"]
    pending_dev = [d for d in dev if not is_dev_done(d["status"])]
    modified = parse_time(item.get("gmtModified"))
    age_days = (now - modified).days if modified else None

    if status in NEAR_END:
        if pending_dev:
            return "blocked", f"研发任务未完成：{', '.join(str(d['id']) for d in pending_dev)}"
        return "closable", f"{len(dev)} 个研发任务全部完成，可推进到已发布"
    if status in TERMINAL:
        return "done", ""
    done_dev = [d for d in dev if is_dev_done(d["status"])]
    if done_dev and not pending_dev:
        return "dev_done", f"研发任务已完成（{len(done_dev)} 个），状态仍停在「{status}」"
    if age_days is not None and age_days >= stale_days:
        return "stale", f"{age_days} 天未更新"
    if not rel["workitem"] and not dev:
        return "orphan", "无父项、无关联研发任务"
    return "active", ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--project", help="限定项目空间 ID；缺省=个人工作箱")
    ap.add_argument("--days", type=int, default=14, help="僵尸项阈值（天），默认 14")
    ap.add_argument("--page-size", type=int, default=300)
    ap.add_argument("--owner", help="只看这些负责人（逗号分隔）；缺省不过滤")
    ap.add_argument("--workers", type=int, default=6, help="relation 并发数，默认 6")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--advance", action="store_true",
                    help=f"把研发任务已完成的工作项推进到「{ADVANCE_CAP}」（会写平台，执行后回读）；不关单")
    ap.add_argument("--no-relations", action="store_true", help="跳过 relation 查询（快但分类粗）")
    args = ap.parse_args()

    list_args = ["project", "workitem", "list", "--page-size", str(args.page_size), "-f", "json"]
    if args.project:
        list_args += ["--project", args.project]
    try:
        items = as_items(run_a1(list_args))
    except RuntimeError as exc:
        print(f"ERROR 拉取工作项失败: {exc}", file=sys.stderr)
        return 2

    truncated = len(items) >= args.page_size
    owners = {o.strip() for o in args.owner.split(",")} if args.owner else None
    now = datetime.now(timezone(timedelta(hours=8)))
    pending = [
        it for it in items
        if (it.get("status") or "") not in TERMINAL
        and (owners is None or owner_of(it) in owners)
    ]

    def fetch(item):
        wid = str(item.get("identifier") or item.get("id"))
        if args.no_relations:
            return {"dev": [], "workitem": []}
        return collect_relations(wid)

    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as pool:
        rels = list(pool.map(fetch, pending))

    rows = []
    for item, rel in zip(pending, rels):
        wid = item.get("identifier") or item.get("id")
        status = item.get("status") or ""
        bucket, reason = classify(item, rel, args.days, now)
        rows.append({
            "id": str(wid),
            "subject": item.get("subject") or "",
            "status": status,
            "owner": owner_of(item),
            "modified": item.get("gmtModified") or "",
            "space": item.get("spaceIdentifier") or "",
            "bucket": bucket,
            "reason": reason,
            "dev": rel["dev"],
            "parents": rel["workitem"],
        })

    if args.json:
        print(json.dumps({"scanned": len(items), "truncated": truncated, "rows": rows},
                         ensure_ascii=False, indent=2))
        return 0

    order = ["closable", "blocked", "stale", "orphan", "active"]
    title = {"closable": "可关闭", "blocked": "想关但被拦", "stale": "僵尸项",
             "orphan": "孤岛项（未挂大需求）", "active": "正常推进中"}
    scope = f"负责人 {'/'.join(sorted(owners))}" if owners else "全部负责人"
    print(f"扫描 {len(items)} 条{'（已达分页上限，可能截断）' if truncated else ''}，"
          f"未终态 {len(rows)} 条｜{scope}｜僵尸阈值 {args.days} 天")
    for bucket in order:
        group = [r for r in rows if r["bucket"] == bucket]
        if not group:
            continue
        print(f"\n## {title[bucket]}（{len(group)}）")
        print("| ID | 状态 | 标题 | 负责人 | 最后更新 | 说明 |")
        print("|---|---|---|---|---|---|")
        for r in sorted(group, key=lambda x: str(x["modified"])):
            print(f"| {r['id']} | {r['status']} | {r['subject'][:40]} | {r['owner']} "
                  f"| {str(r['modified'])[:10]} | {r['reason']} |")

    if args.advance:
        today = now.date().isoformat()
        advance_rows = [r for r in rows if r["bucket"] in ("dev_done", "closable")]
        auto_rows = [r for r in advance_rows if r["status"] in ADVANCE_FROM or r["status"] == ADVANCE_CAP]
        manual_rows = [r for r in advance_rows if r["status"] not in ADVANCE_FROM and r["status"] != ADVANCE_CAP]
        print(f"\n## 推进到「{ADVANCE_CAP}」（研发任务已完成 {len(advance_rows)} 条，"
              f"可自动 {len(auto_rows)} / 需人工 {len(manual_rows)}）")
        if not advance_rows:
            print("没有需要推进的工作项。")
        for r in manual_rows:
            print(f"- {r['id']} 需人工：「{r['status']}」但研发任务已完成，先补评审/立项流程再推进"
                  f"（{r['subject'][:30]}）")
        pending_close = []
        for r in auto_rows:
            if r["status"] == ADVANCE_CAP:
                print(f"- {r['id']} 已在「{ADVANCE_CAP}」，待人工确认关单")
                pending_close.append(r)
                continue
            result, trail = advance_to_dev_end(r["id"], r["status"], today)
            if result == "done":
                back = read_status(r["id"]) or "?"
                print(f"- {r['id']} 已推进：{r['status']}{' '.join(trail)}｜回读={back}")
                if back == ADVANCE_CAP:
                    pending_close.append(r)
            elif result == "blocked_by_dev":
                print(f"- {r['id']} 被拦：{r['status']}{' '.join(trail)}")
            else:
                print(f"- {r['id']} 失败：{r['status']}{' '.join(trail)}")

        if pending_close:
            print(f"\n## 待你确认关单（{len(pending_close)} 条，脚本不会自己关）")
            for r in pending_close:
                print(f"| {r['id']} | {r['status']} | {r['subject'][:40]} | 关联研发 "
                      f"{', '.join(str(d['id']) for d in r['dev']) or '无'} |")
    return 0


if __name__ == "__main__":
    sys.exit(main())
