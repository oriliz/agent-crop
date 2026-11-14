#!/usr/bin/env python3
"""效率资产登记与命中回写：每条资产有来源、载体、触发条件、度量口径和状态。

资产不是「写过的文档」，而是「该被反复用到的东西」：记忆、skill、规则、命令卡。
登记后从转录自动回写命中（Skill 调用 / 读记忆文件 / 探针提醒 tag），回答两个问题：
  哪些资产真的在被用；哪些登记了但 0 命中，该改触发条件还是该下线。

用法：
  assets.py seed                         写入内置起始资产（已存在的不覆盖）
  assets.py add --id <id> --source memory|skill|rule|doc|tool --carrier <载体>
                --path <路径> --trigger <什么时候用> --metric <省什么>
                [--status 未验证|试用中|已验证|已停用] [--reminder-tag <tag>]
  assets.py list
  assets.py hit <id> [--sid <sid>] [--note 说明]
  assets.py auto [--days 7] [--session <sid>]   从转录和提醒事件回写命中
  assets.py report [--days 30]
数据：data/assets.jsonl（一条资产一行，命中去重键 <日期>:<会话或tag>）。
"""
import argparse
import glob
import json
import os
import sys
import time
from datetime import datetime

DATA = os.path.expanduser(os.environ.get("KIT_DATA_DIR") or os.path.join(
    os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "task-metrics", "data"))
ASSETS = os.path.join(DATA, "assets.jsonl")
IDE_HOME = os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder")
MEMORY = os.path.join(IDE_HOME, "memory")
SKILLS = os.path.join(IDE_HOME, "skills")

SEED = [
    {"id": "a1-kbase-fetch", "source": "memory", "carrier": "qoder-memory",
     "path": f"{MEMORY}/yuque-read-a1-kbase-fetch.md",
     "trigger": "遇到语雀/钉钉文档链接", "metric": "a1 kbase 直读正文，省登录与浏览器",
     "status": "已验证"},
    {"id": "sls-aliyun-cli", "source": "memory", "carrier": "qoder-memory",
     "path": f"{MEMORY}/sls-query-aliyun-cli.md",
     "trigger": "查 traceId / 时间窗日志", "metric": "一条命令取日志，不试错 sf/toolkit",
     "status": "已验证"},
    {"id": "no-local-server-boot", "source": "memory", "carrier": "qoder-memory",
     "path": f"{MEMORY}/no-local-server-boot.md",
     "trigger": "服务端项目要验证", "metric": "不白跑本机启动；验证口径=构建+测试绿",
     "status": "已验证"},
    {"id": "workspace-root-aicoding", "source": "memory", "carrier": "qoder-memory",
     "path": f"{MEMORY}/workspace-root-aicoding.md",
     "trigger": "新建项目/放产物", "metric": "目录不散落，避免跨盘搬移",
     "status": "已验证"},
    {"id": "verifiable-evidence", "source": "memory", "carrier": "qoder-memory",
     "path": f"{MEMORY}/feedback-verifiable-evidence.md",
     "trigger": "写测试证据/回归报告", "metric": "证据一次可复核，减少返工补证",
     "status": "已验证"},
    {"id": "mcp-chrome", "source": "memory", "carrier": "qoder-memory",
     "path": f"{MEMORY}/mcp-chrome.md",
     "trigger": "浏览器自动化", "metric": "用 MCP 实例，不动日常 Chrome",
     "status": "已验证"},
    {"id": "fast-loop", "source": "skill", "carrier": "qoder-skill",
     "path": f"{SKILLS}/fast-loop/SKILL.md",
     "trigger": "非平凡任务开工", "metric": "先决策表+拆并行+部署先跑，压任务块时长",
     "status": "试用中"},
    {"id": "req-clarify", "source": "skill", "carrier": "qoder-skill",
     "path": f"{SKILLS}/req-clarify/SKILL.md",
     "trigger": "需求/方案评审", "metric": "澄清清单落盘，减少开工后反复",
     "status": "试用中"},
    {"id": "html-report-loop", "source": "skill", "carrier": "qoder-skill",
     "path": f"{SKILLS}/html-report-loop/SKILL.md",
     "trigger": "报告类交付", "metric": "单文件 HTML + 批注迭代，省来回改",
     "status": "试用中"},
]


def load():
    out = {}
    if os.path.isfile(ASSETS):
        for line in open(ASSETS, encoding="utf-8", errors="ignore"):
            try:
                r = json.loads(line)
                out[r["id"]] = r
            except Exception:
                continue
    return out


def save(rows):
    os.makedirs(DATA, exist_ok=True)
    tmp = ASSETS + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        for r in sorted(rows.values(), key=lambda x: (x.get("source", ""), x["id"])):
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    os.replace(tmp, ASSETS)


def hit(rows, aid, key, source="manual", note=""):
    r = rows.get(aid)
    if not r:
        return False
    hits = r.setdefault("hits", [])
    if key in hits:
        return False
    hits.append(key)
    r["last_hit"] = {"key": key, "at": datetime.now().isoformat(timespec="seconds"),
                     "via": source, "note": note}
    return True


def cmd_seed(rows):
    added = 0
    for a in SEED:
        if a["id"] in rows:
            continue
        if not os.path.isfile(a["path"]):
            continue
        a = dict(a, added=datetime.now().date().isoformat(), hits=[])
        rows[a["id"]] = a
        added += 1
    save(rows)
    print(f"assets seed：新增 {added} 条（已存在或路径不存在跳过）")


def cmd_add(rows, a):
    if not a.id or not a.path:
        print("用法：assets.py add --id <id> --source ... --carrier ... --path ... --trigger ... --metric ...")
        return
    old = rows.get(a.id) or {}
    rows[a.id] = {
        "id": a.id, "source": a.source, "carrier": a.carrier, "path": os.path.expanduser(a.path),
        "trigger": a.trigger, "metric": a.metric, "status": a.status,
        "added": old.get("added") or datetime.now().date().isoformat(),
        "hits": old.get("hits") or [],
    }
    if a.reminder_tag:
        rows[a.id]["reminder_tag"] = a.reminder_tag
    save(rows)
    print(f"已登记：{a.id}")


def cmd_list(rows):
    for r in rows.values():
        print(f"{r['id']:<24}{r.get('status', ''):<6}{len(r.get('hits') or []):<4}"
              f"{r.get('source', ''):<7}{r.get('trigger', '')}")
        print(f"{'':<24}{r.get('path', '')}")


def transcript_files(days, session=None):
    if session:
        return glob.glob(os.path.join(IDE_HOME, "projects", "*", session + ".jsonl"))
    cutoff = time.time() - days * 86400
    return [p for p in glob.glob(os.path.join(IDE_HOME, "projects", "*", "*.jsonl"))
            if os.path.getmtime(p) >= cutoff]


def cmd_auto(rows, days, session=None):
    by_skill = {r["id"]: r for r in rows.values()
                if r.get("source") == "skill" and r.get("path")}
    path_rows = [r for r in rows.values() if r.get("path")]
    by_tag = {r.get("reminder_tag"): r for r in rows.values() if r.get("reminder_tag")}

    def match_path(fp):
        fp = os.path.abspath(os.path.expanduser(fp))
        for r in path_rows:
            p = os.path.abspath(os.path.expanduser(r["path"]))
            if fp == p or fp.endswith(p):
                return r
            # skill 的 SKILL.md 不能按 basename 匹配，否则任意 skill 文件都会串台
            if r.get("source") != "skill" and os.path.basename(p) == os.path.basename(fp):
                return r
        return None
    hits = 0
    for p in transcript_files(days, session):
        sid = os.path.basename(p)[:-6]
        day = datetime.fromtimestamp(os.path.getmtime(p)).date().isoformat()
        key = f"{day}:{sid}"
        try:
            with open(p, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if '"tool_use"' not in line:
                        continue
                    try:
                        o = json.loads(line)
                    except Exception:
                        continue
                    c = (o.get("message") or {}).get("content")
                    if not isinstance(c, list):
                        continue
                    for x in c:
                        if not isinstance(x, dict) or x.get("type") != "tool_use":
                            continue
                        name = (x.get("name") or "").lower()
                        inp = x.get("input") or {}
                        if name == "skill":
                            sname = (inp.get("skill") or inp.get("name") or "").split(":")[-1]
                            r = by_skill.get(sname)
                            if r and hit(rows, r["id"], key, "skill"):
                                hits += 1
                        elif name in ("read", "read_file", "readfile"):
                            fp = str(inp.get("file_path") or inp.get("path") or "")
                            r = match_path(fp) if fp else None
                            if r and hit(rows, r["id"], key, "read"):
                                hits += 1
        except Exception:
            continue
    # 探针提醒 tag：提醒命中说明对应做法被点到了（还没发生行为，只算触达）
    evdir = os.path.join(DATA, "events")
    for fn in glob.glob(os.path.join(evdir, "*.jsonl")):
        if session:
            continue
        day = os.path.basename(fn)[:-6]
        for line in open(fn, encoding="utf-8", errors="ignore"):
            if '"reminder"' not in line:
                continue
            try:
                e = json.loads(line)
            except Exception:
                continue
            if e.get("kind") != "reminder":
                continue
            if time.time() - e.get("t", 0) > days * 86400:
                continue
            r = by_tag.get(e.get("tag"))
            if r and hit(rows, r["id"], f"{day}:reminder:{e.get('tag')}", "reminder"):
                hits += 1
    save(rows)
    print(f"assets auto：新增命中 {hits} 次（扫描 {len(transcript_files(days, session))} 个会话）")


def cmd_hit(rows, aid, sid, note):
    key = f"{datetime.now().date().isoformat()}:{sid or 'manual'}"
    print("已记录" if hit(rows, aid, key, "manual", note) else "已存在或资产不存在")
    save(rows)


def cmd_report(rows, days):
    cutoff = (datetime.now().timestamp() - days * 86400)
    total = len(rows)
    status = {}
    recent, zero = [], []
    for r in rows.values():
        status[r.get("status", "未验证")] = status.get(r.get("status", "未验证"), 0) + 1
        n = sum(1 for h in (r.get("hits") or [])
                if h.split(":")[0] >= datetime.fromtimestamp(cutoff).date().isoformat())
        if n:
            recent.append((n, r))
        elif r.get("status") != "已停用":
            zero.append(r)
    print(f"资产 {total} 条（" + " / ".join(f"{k} {v}" for k, v in status.items()) + f"），近 {days} 天有命中 {len(recent)} 条")
    for n, r in sorted(recent, key=lambda x: -x[0])[:10]:
        print(f"  {r['id']:<24}{n:>3} 次  {r.get('last_hit', {}).get('via', '')}")
    if zero:
        print("  0 命中：")
        for r in zero[:10]:
            print(f"  · {r['id']}（{r.get('source')}）触发条件：{r.get('trigger')}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="list")
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--session")
    ap.add_argument("--id")
    ap.add_argument("--source", default="memory")
    ap.add_argument("--carrier", default="")
    ap.add_argument("--path", default="")
    ap.add_argument("--trigger", default="")
    ap.add_argument("--metric", default="")
    ap.add_argument("--status", default="试用中")
    ap.add_argument("--reminder-tag", dest="reminder_tag", default="")
    ap.add_argument("--sid", default="")
    ap.add_argument("--note", default="")
    ap.add_argument("rest", nargs="*")
    a = ap.parse_args()
    rows = load()
    if a.mode == "seed":
        cmd_seed(rows)
    elif a.mode == "add":
        cmd_add(rows, a)
    elif a.mode == "hit":
        cmd_hit(rows, a.id or (a.rest[0] if a.rest else ""), a.sid, a.note)
    elif a.mode == "auto":
        cmd_auto(rows, a.days, a.session)
    elif a.mode == "report":
        cmd_report(rows, a.days)
    else:
        cmd_list(rows)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"出错：{type(e).__name__}: {e}")
        sys.exit(1)
