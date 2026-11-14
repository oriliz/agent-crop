#!/usr/bin/env python3
"""任务事实：每个任务块一条确定性记录，供效果评估按类型/难度分桶，不靠自述。

用法：
  taskfacts.py backfill [--days 30] [--session <sid>]
  taskfacts.py --report [days]              汇总（默认 7 天）
  taskfacts.py --compare [days]             最近 N 天 vs 前 N 天
  taskfacts.py --session <sid>              临时查看某会话的块事实（不落盘）

字段口径（全部可由事件流 + 转录算出）：
  task_type   部署发布 / 调试 / 改名重构 / 文档设计 / 调研 / 代码改动 / 其他（按优先级取第一个命中）
  difficulty  small（执行 ≤15 分钟且 ≤3 文件且 ≤10 次编辑）/ large（执行 ≥60 分钟或 ≥10 文件或 ≥40 次编辑）/ medium
  markers     lsp / search / subagent / background / skill / deploy / browser 是否用过
  metrics     回合数、工具调用、失败数、改到的文件数、编辑数、验证次数；
              minutes 墙钟、work_minutes agent 执行、wait_minutes 块内等你（两者相加≈墙钟）、
              away_minutes 块结束后你离开到下次开工
数据：data/taskfacts/<date>.jsonl，一行一个块，id=<sid>:<start_ms>，重算按 id 覆盖。
"""
import argparse
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import daily_case as dc  # noqa: E402
import task_retro  # noqa: E402

DATA = dc.DATA
FACTS = os.path.join(DATA, "taskfacts")
IDE_HOME = os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder")

DEPLOY_RE = re.compile(r"(部署|发布|上线|预发|apre|hotpatch|热发|回归|deploy)")
DEBUG_RE = re.compile(r"(报错|失败|不对|排查|定位|为什么|bug|异常|trace|日志|根因|修复)")
RENAME_RE = re.compile(r"(改名|重命名|rename|重构|抽(出来|出|取)|下沉|搬(到|过去))")
DOC_RE = re.compile(r"(文档|设计|方案|评审|纪要|报告|readme|总结|prd|复盘)")
RESEARCH_RE = re.compile(r"(调研|对比|评估|盘点|看看|查一下|梳理|分析)")
CODE_EXT = (".md", ".txt", ".html", ".svg", ".png", ".json", ".yaml", ".yml")
VERIFY_RE = re.compile(r"\b(pytest|npm (run )?test|mvn .*\b(test|verify)\b|gradle.*\btest\b|go test|make (test|check))\b")


def transcript_path(sid):
    root = os.path.join(IDE_HOME, "projects")
    try:
        for slug in os.listdir(root):
            p = os.path.join(root, slug, sid + ".jsonl")
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    return ""


def type_of(prompts, metrics, markers, files):
    text = " ".join(prompts).lower()
    code_edits = [f for f in files if not f.lower().endswith(CODE_EXT)]
    if markers.get("deploy") or (DEPLOY_RE.search(text) and not metrics["edits"]):
        return "部署发布"
    if metrics["failures"] >= 2 or (DEBUG_RE.search(text) and (metrics["edits"] or metrics["reads"])):
        return "调试"
    if RENAME_RE.search(text):
        return "改名重构"
    if code_edits:
        return "代码改动"
    if DOC_RE.search(text) or (metrics["edits"] and files):
        return "文档设计"
    if RESEARCH_RE.search(text):
        return "调研"
    return "其他"


def difficulty_of(minutes, files, edits):
    if minutes >= 60 or files >= 10 or edits >= 40:
        return "large"
    if minutes <= 15 and files <= 3 and edits <= 10:
        return "small"
    return "medium"


def facts_of_block(block, steps, away_ms=0):
    lo, hi = block["start"] / 1000.0 - 1, block["end"] / 1000.0 + 1
    tools = [s for s in steps if s.get("kind") == "tool" and lo <= (s.get("start") or 0) <= hi]
    edits = [s for s in tools if s["family"] == "edit" and s["target"]]
    files = sorted({s["target"] for s in edits})
    reads = {s["target"] for s in tools if s["family"] in ("read", "search") and s["target"]}
    minutes = round((block["end"] - block["start"]) / 60000.0, 1)
    work_minutes = round(sum(block.get("turns") or []) / 60000.0, 1)
    wait_minutes = round(sum(block.get("waits") or []) / 60000.0, 1)
    markers = {
        "lsp": any((s["name"] or "").lower().startswith("lsp") or re.search(r"\blsp[-_]", s["cmd"] or "", re.I)
                   for s in tools),
        "search": any(s["family"] == "search" or "search" in (s["name"] or "").lower() for s in tools),
        "subagent": any(bool(s.get("agent")) for s in tools),
        "background": any(bool(s.get("bg")) or (s["name"] or "").lower() == "bash" and "run_in_background" in (s["cmd"] or "")
                          for s in tools),
        "skill": any((s["name"] or "").lower() == "skill" or bool(s.get("skill")) for s in tools),
        "deploy": any(s["family"] == "deploy" or "deploy_watch" in (s["cmd"] or "") for s in tools),
        "browser": any(s["family"] == "browser" for s in tools),
    }
    verifies = sum(1 for s in tools
                   if s["family"] in ("build", "deploy") or VERIFY_RE.search(s["cmd"] or ""))
    metrics = {
        "turns": block.get("stops", 0),
        "tool_calls": len(tools),
        "failures": sum(1 for s in tools if s.get("is_error")),
        "files": len(files),
        "edits": len(edits),
        "reads": len(reads),
        "verifies": verifies,
        "minutes": minutes,
        "work_minutes": work_minutes,
        "wait_minutes": wait_minutes,
        "away_minutes": round(max(0, away_ms) / 60000.0, 1),
    }
    prompts = [p for p in (block.get("snippets") or []) if p]
    return {
        "id": f"{block['sid']}:{block['start']}",
        "date": datetime.fromtimestamp(block["start"] / 1000).strftime("%Y-%m-%d"),
        "sid": block["sid"],
        "start": block["start"],
        "end": block["end"],
        "task_type": type_of(prompts, metrics, markers, files),
        "difficulty": difficulty_of(work_minutes, len(files), len(edits)),
        "markers": markers,
        "metrics": metrics,
        "files": files[:20],
    }


def load_done():
    done = {}
    if os.path.isdir(FACTS):
        for fn in os.listdir(FACTS):
            if fn.endswith(".jsonl"):
                for line in open(os.path.join(FACTS, fn), encoding="utf-8", errors="ignore"):
                    try:
                        r = json.loads(line)
                        done[r.get("id")] = r
                    except Exception:
                        continue
    return done


def save_facts(records):
    by_date = {}
    for r in records:
        path = os.path.join(FACTS, r["date"] + ".jsonl")
        by_date.setdefault(path, []).append(r)
    for path, recs in by_date.items():
        existing = {}
        if os.path.isfile(path):
            for line in open(path, encoding="utf-8", errors="ignore"):
                try:
                    r = json.loads(line)
                    existing[r.get("id")] = r
                except Exception:
                    continue
        for r in recs:
            existing[r["id"]] = r
        os.makedirs(FACTS, exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            for r in sorted(existing.values(), key=lambda x: x.get("start", 0)):
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        os.replace(tmp, path)


def build(days=30, session=None, quiet=False):
    now = int(datetime.now().timestamp() * 1000)
    cutoff = now - days * 86400 * 1000
    by_sid = {}
    for t in task_retro.all_tasks():
        if t.get("stops", 0) <= 0 or t["end"] > now:
            continue
        if t["start"] < cutoff:
            continue
        if session and t["sid"] != session:
            continue
        by_sid.setdefault(t["sid"], []).append(t)
    done = load_done()
    redline_away = {}
    for e in task_retro.load():
        if e.get("ev") == "redline":
            k = (e.get("sid"), e.get("block_start"))
            redline_away[k] = max(redline_away.get(k, 0), (e.get("away_minutes") or 0) * 60000)
    fresh = []
    for sid, blocks in by_sid.items():
        blocks.sort(key=lambda x: x["start"])
        for i, b in enumerate(blocks):
            nxt = blocks[i + 1]["start"] if i + 1 < len(blocks) else None
            gap = max(0, nxt - b["end"]) if nxt else 0
            b["_away_ms"] = max(gap, redline_away.get((sid, b["start"]), 0))
        path = transcript_path(sid)
        steps = []
        if path:
            lo = min(b["start"] for b in blocks) / 1000.0 - 5
            hi = max(b["end"] for b in blocks) / 1000.0 + 5
            try:
                steps = dc.collect_steps(path, lo, hi)
            except Exception:
                steps = []
        for b in blocks:
            rec = facts_of_block(b, steps, b.pop("_away_ms", 0))
            rec["transcript"] = bool(path)
            old = done.get(rec["id"])
            if old and all(old.get(k) == rec[k] for k in ("end", "task_type", "difficulty", "markers", "metrics")):
                continue
            fresh.append(rec)
    if fresh:
        save_facts(fresh)
    if not quiet:
        print(f"taskfacts：新增/更新 {len(fresh)} 条（扫描 {sum(len(v) for v in by_sid.values())} 个块，"
              f"会话 {len(by_sid)} 个）")


def load(days=7):
    now = datetime.now().timestamp()
    out = []
    if not os.path.isdir(FACTS):
        return out
    for fn in sorted(os.listdir(FACTS)):
        if not fn.endswith(".jsonl"):
            continue
        for line in open(os.path.join(FACTS, fn), encoding="utf-8", errors="ignore"):
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("end", 0) / 1000 >= now - days * 86400:
                out.append(r)
    return out


def summarize(recs):
    n = len(recs)
    mins = sorted(r["metrics"].get("minutes", 0) for r in recs)
    median = mins[len(mins) // 2] if mins else 0
    works = sorted(r["metrics"].get("work_minutes", 0) for r in recs)
    median_work = works[len(works) // 2] if works else 0
    wall = sum(r["metrics"].get("minutes", 0) for r in recs)
    waited = sum(r["metrics"].get("wait_minutes", 0) for r in recs)
    aways = sorted(r["metrics"].get("away_minutes", 0) for r in recs)
    away_median = aways[len(aways) // 2] if aways else 0
    away_blocks = sum(1 for x in aways if x >= 10)
    types, diffs = {}, {}
    flags = {k: 0 for k in ("lsp", "search", "subagent", "background", "skill", "deploy")}
    for r in recs:
        types[r["task_type"]] = types.get(r["task_type"], 0) + 1
        diffs[r["difficulty"]] = diffs.get(r["difficulty"], 0) + 1
        for k in flags:
            if (r.get("markers") or {}).get(k):
                flags[k] += 1
    tools = sum(r["metrics"].get("tool_calls", 0) for r in recs)
    fails = sum(r["metrics"].get("failures", 0) for r in recs)
    ver = sum(1 for r in recs if r["metrics"].get("verifies"))
    return {"blocks": n, "median_minutes": round(median, 1),
            "median_work_minutes": round(median_work, 1),
            "wait_share": round(waited / wall * 100, 1) if wall else 0,
            "away_median": round(away_median, 1), "away_blocks": away_blocks,
            "types": types, "difficulty": diffs,
            "flags": flags, "tool_calls": tools,
            "failure_rate": round(fails / tools * 100, 1) if tools else 0,
            "verify_rate": round(ver / n * 100, 1) if n else 0}


def show(s, days):
    print(f"任务事实（{days} 天）：{s['blocks']} 块，中位墙钟 {s['median_minutes']} 分钟，"
          f"中位执行 {s['median_work_minutes']} 分钟；块内等你占墙钟 {s['wait_share']}%，"
          f"块后离开中位 {s['away_median']} 分钟（≥10 分钟的 {s['away_blocks']} 块）")
    if not s["blocks"]:
        return
    print("  类型  " + " / ".join(f"{k} {v}" for k, v in sorted(s["types"].items(), key=lambda x: -x[1])))
    print("  难度  " + " / ".join(f"{k} {v}" for k, v in sorted(s["difficulty"].items())))
    used = " / ".join(f"{k} {v}" for k, v in s["flags"].items() if v)
    print("  方法  " + (used or "无"))
    print(f"  工具失败率 {s['failure_rate']}% / 有验证的块 {s['verify_rate']}%")


def compare(days):
    recent = load(days)
    older = load(days * 2)
    older = [r for r in older if r not in recent]
    a, b = summarize(older), summarize(recent)
    print(f"前 {days} 天  vs  近 {days} 天")
    for key, label in (("blocks", "块数"), ("median_minutes", "中位墙钟"),
                       ("median_work_minutes", "中位执行"), ("wait_share", "等你占比"),
                       ("away_median", "离开中位")):
        print(f"  {label:<8}{a[key]:<8}{b[key]:<8}{b[key] - a[key]:+.1f}")
    for k in ("lsp", "search", "subagent", "background"):
        va = a["flags"][k] / a["blocks"] * 100 if a["blocks"] else 0
        vb = b["flags"][k] / b["blocks"] * 100 if b["blocks"] else 0
        print(f"  {k:<8}{va:>5.0f}%  {vb:>5.0f}%  {vb - va:+.0f}pp")
    print(f"  失败率   {a['failure_rate']:>5.1f}%  {b['failure_rate']:>5.1f}%  {b['failure_rate'] - a['failure_rate']:+.1f}pp")
    print(f"  验证率   {a['verify_rate']:>5.1f}%  {b['verify_rate']:>5.1f}%  {b['verify_rate'] - a['verify_rate']:+.1f}pp")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="")
    ap.add_argument("--days", type=int)
    ap.add_argument("--session")
    ap.add_argument("--report", nargs="?", type=int, const=7)
    ap.add_argument("--compare", nargs="?", type=int, const=7)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    days = a.days or 7

    if a.mode == "backfill" or a.session:
        build(days=a.days or 30, session=a.session)
    if a.compare is not None or a.mode == "compare":
        compare(a.compare or days)
        return
    if a.report is not None or a.mode == "report":
        days = a.report or days
        recs = load(days)
        if a.json:
            print(json.dumps({"summary": summarize(recs), "records": recs}, ensure_ascii=False))
        else:
            show(summarize(recs), days)
        return
    if a.session:
        recs = [r for r in load(90) if r["sid"] == a.session]
        for r in recs:
            print(json.dumps(r, ensure_ascii=False))
        return
    if not a.mode:
        show(summarize(load(a.days)), a.days)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"出错：{type(e).__name__}: {e}")
        sys.exit(1)
