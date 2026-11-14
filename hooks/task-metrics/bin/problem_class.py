#!/usr/bin/env python3
"""问题类扫描：按已登记的问题类扫描会话转录，输出「有没有、多贵、怎么验」。

与 daily_case.py 的分工：
  daily_case  一次深挖一个任务（单次、事后报告）
  problem_class 按类扫全量（长期、可重复、带阈值），命中才出 finding

每条 finding 只写四样东西：命中的证据、成本口径、建议干预、验证指标。
事后无法判定的问题类（如「资料与业务要求不一致」）标记为 gate-only：
不编造检出率，只说明必须先设门、门记录才是证据。

用法：
  problem_class.py                      # 最近 30 天，只打印
  problem_class.py --days 90 --write    # 写 data/findings/<今天>.jsonl
  problem_class.py --class cli-help-churn --json
"""
import argparse
import datetime
import glob
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402

DATA = hs.DATA_DIR
FINDINGS = os.path.join(DATA, "findings")
PROJECTS = os.path.expanduser(os.environ.get("KIT_PROJECTS_DIR") or "~/.qoder/projects")

BASH_TOOLS = {"Bash", "bash", "Shell", "shell", "run_in_terminal", "run_terminal_command"}
CLI_ROOTS = {"a1", "dws", "mw", "alipay", "ucp-cli", "testctl", "aliyun", "gh",
             "kubectl", "sunfire", "clawcli", "workctl", "dsh"}
HELP_RE = re.compile(r"(?:^|\s)(--help|-h|help)(?=\s|$|2>&1|[|;])")
SEG_SPLIT = re.compile(r"\s*(?:2>&1|\||;|&&|\n)\s*")
WINDOW = 15 * 60          # 同一根命令的 help 视为一次探索窗口的最大间隔
IDLE_GAP = 180            # 计主动耗时时，超过这个间隔算空档

# 问题类登记表：每一类都必须给出「检出方式 / 干预落点 / 验证指标」，
# 缺任何一项就不许登记，否则又是一篇没法验收的报告。
CLASSES = [
    {
        "id": "cli-help-churn",
        "name": "命令行靠 --help 逐层试",
        "kind": "detectable",
        "threshold": "help≥8 次，或逐层链≥3，或重复目标≥3",
        "fix": "把高频命令固化成 Skill 命令卡，替代每次现翻 --help",
        "solution": "",
        "metric": "help 调用次数 / 最长逐层链 / 重复目标数 / 探索窗口时长",
        "verify": "干预后同类任务重跑本检测器，四项指标应同向下降",
    },
    {
        "id": "spec-business-drift",
        "name": "资料/协议与业务要求不一致导致返工",
        "kind": "gate-only",
        "threshold": "无法事后判定；只统计纠正信号，不下结论",
        "fix": "改代码前强制对齐门：负责人、结论、验收标准落盘",
        "solution": "alignment-gate",
        "metric": "门覆盖率 / 门后返工比例",
        "verify": "对比有门记录与无门记录任务的返工时长；无记录不算已对齐",
    },
    {
        "id": "first-pass-no-evidence",
        "name": "首轮只靠代码推断、没取运行证据就下结论",
        "kind": "detectable",
        "threshold": "首段无证据类工具调用，且随后出现纠偏与 ≥1.5 倍返工",
        "fix": "首段先取一条可复核证据，再下结论",
        "solution": "",
        "metric": "首段证据调用数 / 返工倍数",
        "verify": "同类任务首段证据调用是否 ≥1，返工倍数是否 <1.5",
    },
]


def ts_epoch(v):
    return hs.ts_epoch(v)


def parse_transcript(path):
    """返回 (tools, users)。tools=[(ts, name, cmd)]，users=[(ts, text)]。"""
    tools, users = [], []
    with open(path, errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = ts_epoch(d.get("timestamp") or d.get("created_at"))
            msg = d.get("message") or {}
            role = msg.get("role")
            c = msg.get("content")
            if isinstance(c, str):
                if role == "user":
                    users.append((t, c))
                continue
            if not isinstance(c, list):
                continue
            for b in c:
                if not isinstance(b, dict):
                    continue
                if b.get("type") == "tool_use" and b.get("name") in BASH_TOOLS:
                    cmd = (b.get("input") or {}).get("command") or ""
                    if cmd:
                        tools.append((t, b.get("name"), cmd))
                elif b.get("type") == "text" and role == "user":
                    users.append((t, b.get("text") or ""))
    return tools, users


def help_target(cmd):
    first = SEG_SPLIT.split(cmd.strip(), 1)[0]
    if not HELP_RE.search(first):
        return None
    sub = re.sub(r"\s*(--help|-h)\s*$", "", first).strip()
    if sub.endswith(" help"):
        sub = sub[:-5].strip()
    root = sub.split()[0] if sub.split() else ""
    return (root, sub) if root in CLI_ROOTS else None


def detect_cli_help_churn(tools):
    hits = []
    for t, _, cmd in tools:
        g = help_target(cmd)
        if g and t:
            hits.append((t, g[0], g[1], cmd))
    if not hits:
        return None
    hits.sort()
    by_root = {}
    for h in hits:
        by_root.setdefault(h[1], []).append(h)

    max_chain, chains = 0, []
    repeats, explore = [], 0.0
    for root, hs_ in by_root.items():
        # 探索窗口：同根 help 间隔 ≤15 分钟
        windows, cur = [], [hs_[0]]
        for a, b in zip(hs_, hs_[1:]):
            if b[0] - a[0] <= WINDOW:
                cur.append(b)
            else:
                windows.append(cur)
                cur = [b]
        windows.append(cur)
        for w in windows:
            uni = sorted({x[2] for x in w}, key=lambda s: (len(s.split()), s))
            seq = []
            for s in uni:
                if seq and s.split()[:len(seq[-1].split())] == seq[-1].split():
                    seq.append(s)
                else:
                    seq = [s] if not seq or len(s.split()) == 1 else [seq[-1], s]
            if len(seq) > max_chain:
                max_chain = len(seq)
                chains = seq
            if len(w) >= 2:
                explore += sum(min(b[0] - a[0], IDLE_GAP) for a, b in zip(w, w[1:]))
        # 重复目标：同一目标出现在两个不同窗口
        seen = {}
        for wi, w in enumerate(windows):
            for x in w:
                seen.setdefault(x[2], set()).add(wi)
        for s, ws in seen.items():
            if len(ws) >= 2:
                occ = [x for x in hs_ if x[2] == s]
                repeats.append({"target": s, "count": len(occ),
                                "gap_hours": round((occ[-1][0] - occ[0][0]) / 3600, 1)})

    n = len(hits)
    if n < 8 and max_chain < 3 and len(repeats) < 3:
        return None
    return {
        "help_calls": n,
        "by_root": {r: len(v) for r, v in sorted(by_root.items(), key=lambda kv: -len(kv[1]))},
        "max_chain": max_chain,
        "chain": chains,
        "repeated_targets": len(repeats),
        "repeats": sorted(repeats, key=lambda x: -x["count"])[:3],
        "explore_seconds": int(explore),
        "first": hits[0][0],
        "last": hits[-1][0],
    }


SPEC_SIGNAL = re.compile(
    r"(不是这个意思|不是这么|不符合|偏离|理解错|业务(上|要求|逻辑|口径)|"
    r"需求(是|要)|协议|对齐|自相矛盾|矛盾|负责人)")


def detect_spec_business_drift(users):
    """事后只能看到纠偏，看不到纠偏的原因；这里只产出信号，不判因。"""
    human = [(t, x) for t, x in users if x and not x.startswith("<")]
    hits = [(t, x) for t, x in human if SPEC_SIGNAL.search(x)]
    if len(hits) < 2:
        return None
    return {"corrections": len(hits), "first": hits[0][0], "last": hits[-1][0],
            "samples": [x[:100] for _, x in hits[:3]]}


def detect_first_pass_no_evidence(tools, users):
    """首段（第一次用户消息到第二次之间）没有任何证据类工具调用。"""
    if not tools or not users:
        return None
    first_user = min(t for t, _ in users if t)
    starts = sorted(t for t, x in users if t and x and not x.startswith("<"))
    if len(starts) < 2:
        return None
    seg_end = starts[1]
    first_tools = [c for t, _, c in tools if first_user <= t <= seg_end]
    if not first_tools:
        return None
    evidence = re.compile(r"\b(curl|wget)\b|trace|TraceID|sls|log|日志|grep .*\.log|"
                          r"mysql|select .* from|dms|dubbo|arthas")
    if any(evidence.search(c) for c in first_tools):
        return None
    return {"first_segment_calls": len(first_tools),
            "first_segment_end": seg_end - first_user}


def scan(days, project_filter="", since=None, until=None):
    root = PROJECTS
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    files = []
    for p in glob.glob(os.path.join(root, "*", "*.jsonl")):
        try:
            if os.path.getsize(p) < 60000 or os.path.getmtime(p) < cutoff:
                continue
        except OSError:
            continue
        if project_filter and project_filter not in p:
            continue
        files.append(p)

    results = {c["id"]: [] for c in CLASSES}
    scanned = 0
    for p in files:
        try:
            tools, users = parse_transcript(p)
        except Exception:
            continue
        stamps = [t for t, _, _ in tools] + [t for t, _ in users]
        t0 = min(stamps) if stamps else os.path.getmtime(p)
        if since and t0 < since:
            continue
        if until and t0 > until:
            continue
        scanned += 1
        sid = os.path.basename(p)[:-6]
        proj = os.path.basename(os.path.dirname(p))
        for c in CLASSES:
            cid = c["id"]
            if cid == "cli-help-churn":
                r = detect_cli_help_churn(tools)
            elif cid == "spec-business-drift":
                r = detect_spec_business_drift(users)
            elif cid == "first-pass-no-evidence":
                r = detect_first_pass_no_evidence(tools, users)
            else:
                r = None
            if r:
                r.update({"session": sid, "project": proj, "file": p})
                results[cid].append(r)
    return scanned, results


def mmss(sec):
    sec = int(max(0, sec))
    return f"{sec // 60}′{sec % 60:02d}″"


def render(scanned, results, days, only=""):
    print(f"问题类扫描 · 最近 {days} 天 · {scanned} 个会话\n")
    for c in CLASSES:
        if only and c["id"] != only:
            continue
        rows = results.get(c["id"], [])
        tag = "可检出" if c["kind"] == "detectable" else "事后不可判定，必须先设门"
        print(f"[{c['id']}] {c['name']} —— {tag}")
        if c["kind"] == "gate-only":
            g = gate_stats(days)
            print(f"  信号：{len(rows)} 个会话出现 ≥2 句业务/协议纠偏")
            print("  为什么不下结论：纠偏原因发生在会话之外（会议/私聊），"
                  "会话里只有返工结果，没有'改代码前是否对齐'的记录")
            if g["fired"]:
                print(f"  门记录：触发 {g['fired']}、确认 {g['acked']}（覆盖率 {g['coverage']}）、"
                      f"无记录先改 {g['early_edit']}")
            else:
                print("  门记录：无（未启用 alignment-gate，或启用后还没有命中）")
            print(f"  干预：{c['fix']}")
            if c.get("solution"):
                print(f"  方案：{c['solution']}（python3 solutions/cli.py enable {c['solution']}）")
            print(f"  验证：{c['verify']}\n")
            continue
        if not rows:
            print(f"  未命中（阈值：{c['threshold']}）\n")
            continue
        rows.sort(key=lambda r: -r.get("help_calls", r.get("first_segment_calls", 0)))
        print(f"  命中 {len(rows)} 个会话（{len(rows) * 100 // max(1, scanned)}%）")
        if c["id"] == "cli-help-churn":
            total = sum(r["help_calls"] for r in rows)
            rep = sum(r["repeated_targets"] for r in rows)
            print(f"  合计 help 调用 {total} 次；有重复目标的 {sum(1 for r in rows if r['repeated_targets'])} 个会话")
            top = rows[0]
            print(f"  最重：{top['project']} / {top['session']}")
            print(f"        {top['help_calls']} 次 help，{top['repeated_targets']} 个重复目标，"
                  f"最长逐层链 {top['max_chain']}（{' → '.join(top['chain'])}）")
            for rp in top["repeats"]:
                print(f"        重复 {rp['count']} 次、跨 {rp['gap_hours']} 小时：{rp['target']}")
            print(f"        探索窗口内主动耗时合计 {mmss(top['explore_seconds'])}（含窗口内真实工作，属上限）")
        else:
            top = rows[0]
            print(f"  最重：{top['project']} / {top['session']}，"
                  f"首段 {top['first_segment_calls']} 个动作、{mmss(top['first_segment_end'])}，无证据类调用")
        print(f"  干预：{c['fix']}")
        if c.get("solution"):
            print(f"  方案：{c['solution']}（python3 solutions/cli.py enable {c['solution']}）")
        print(f"  验证：{c['verify']}\n")


def write_findings(results, day):
    os.makedirs(FINDINGS, exist_ok=True)
    path = os.path.join(FINDINGS, f"{day}.jsonl")
    n = 0
    with open(path, "a", encoding="utf-8") as f:
        for c in CLASSES:
            ok = [r for r in results[c["id"]] if c["kind"] == "detectable"]
            if not ok:
                continue
            ok.sort(key=lambda r: -r.get("help_calls", r.get("first_segment_calls", 0)))
            top = ok[0]
            rec = {
                "id": f"F-{day}-{c['id']}",
                "date": day,
                "status": "observed",
                "class": c["id"],
                "symptom": c["name"],
                "hits": len(ok),
                "evidence": {
                    "session_file": top["file"],
                    **{k: v for k, v in top.items()
                       if k not in ("file", "project", "session", "samples")},
                },
                "causes": [],
                "interventions": [c["fix"]],
                "verification": c["verify"],
                "metric_definitions": {
                    "help_calls": "首段命令为已登记 CLI 根、且带 --help/-h/help 的调用次数",
                    "max_chain": "同一探索窗口内，按子命令前缀逐层展开的最长序列",
                    "repeated_targets": "同一 help 目标出现在 ≥2 个探索窗口的个数（说明知识没留下）",
                    "explore_seconds": "探索窗口内相邻 help 调用的间隔合计（≤180 秒部分，含真实工作，属上限）",
                },
                "source_note": "problem_class.py 自动扫描，阈值见 threshold",
                "threshold": c["threshold"],
            }
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            n += 1
    return path, n


def session_span_hours(sid):
    """会话首末事件跨度，含空档；用来和门里记的预计工期比。"""
    hits = glob.glob(os.path.join(PROJECTS, "*", f"{sid}.jsonl"))
    if not hits:
        return None
    stamps = []
    with open(hits[0], errors="ignore") as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            t = ts_epoch(d.get("timestamp") or d.get("created_at"))
            if t:
                stamps.append(t)
    if not stamps:
        return None
    return round((max(stamps) - min(stamps)) / 3600, 1)


def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if not n:
        return None
    return xs[n // 2] if n % 2 else round((xs[n // 2 - 1] + xs[n // 2]) / 2, 1)


def gate_stats(days):
    """读对齐门记录：触发多少、确认多少、多少任务没记录就先改代码。"""
    gates_dir = os.path.join(DATA, "gates")
    cutoff = datetime.datetime.now().timestamp() - days * 86400
    recs = []
    for p in glob.glob(os.path.join(gates_dir, "*.jsonl")):
        with open(p, errors="ignore") as f:
            for line in f:
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("t", 0) >= cutoff:
                    recs.append(r)
    by_sid = {}
    for r in recs:
        by_sid.setdefault(r.get("sid", "?"), set()).add(r.get("ev"))
    fired = sum(1 for v in by_sid.values() if "gate" in v)
    acked = sum(1 for v in by_sid.values() if "ack" in v)
    early = sum(1 for v in by_sid.values() if "edit-before-ack" in v)
    pairs = []
    for r in recs:
        if r.get("ev") == "ack" and r.get("estimate_hours"):
            actual = session_span_hours(r.get("sid"))
            if actual is not None:
                pairs.append({"sid": r.get("sid"), "estimate": r["estimate_hours"], "actual": actual})
    return {"fired": fired, "acked": acked, "early_edit": early,
            "coverage": round(acked / fired, 2) if fired else None,
            "sessions": len(by_sid), "pairs": pairs}


def render_gates(days, spec_signals=0):
    g = gate_stats(days)
    print(f"对齐门记录 · 最近 {days} 天\n")
    if not g["fired"]:
        print("  暂无门记录（方案未启用，或启用后还没遇到资料/协议类任务）。")
        print(f"  当前基线：{spec_signals} 个会话出现 ≥2 句业务/协议纠偏，但没有一条对齐记录。")
        print("  启用：python3 solutions/cli.py enable alignment-gate")
        return
    print(f"  触发 {g['fired']} 个任务；已确认 {g['acked']} 个（覆盖率 {g['coverage']}）；"
          f"无记录先改代码 {g['early_edit']} 个")
    pairs = g["pairs"]
    if pairs:
        est = median([p["estimate"] for p in pairs])
        act = median([p["actual"] for p in pairs])
        over = sum(1 for p in pairs if p["actual"] > p["estimate"] * 1.5)
        print(f"  预计 vs 实际：{len(pairs)} 个任务有工期记录；中位预计 {est} 小时，"
              f"中位实际跨度 {act} 小时，超预计 1.5 倍的有 {over} 个")
        print("  口径：实际跨度=会话首末事件差，含空档，属上限；跨会话任务需人工合并")
    else:
        print("  预计 vs 实际：还没有带工期的确认记录（④ 预计：X 小时/天）")
    print("  判读：覆盖率低说明门只是被看到、没被回答；先改话术或触发条件，别急着算效果")
    print("  效果口径：确认过的任务与未确认任务，比较首次纠偏前的返工时长，样本 ≥3 再下结论")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=30)
    ap.add_argument("--since", default="", help="起始日期 YYYY-MM-DD（按会话首条事件）")
    ap.add_argument("--until", default="", help="截止日期 YYYY-MM-DD，含当天")
    ap.add_argument("--project", default="")
    ap.add_argument("--class", dest="only", default="")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--gates", action="store_true", help="只看对齐门记录")
    a = ap.parse_args()

    since = datetime.datetime.fromisoformat(a.since).timestamp() if a.since else None
    until = (datetime.datetime.fromisoformat(a.until) + datetime.timedelta(days=1)).timestamp() \
        if a.until else None
    scanned, results = scan(a.days, a.project, since, until)
    if a.gates:
        render_gates(a.days, len(results.get("spec-business-drift", [])))
        return
    if a.only:
        results = {k: v for k, v in results.items() if k == a.only}
    if a.json:
        print(json.dumps({"scanned": scanned, "results": results,
                          "gates": gate_stats(a.days)}, ensure_ascii=False, indent=2))
        return
    render(scanned, results, a.days, a.only)
    if a.write:
        day = datetime.date.today().isoformat()
        path, n = write_findings(results, day)
        print(f"已写入 {n} 条 finding → {path}")


if __name__ == "__main__":
    main()
