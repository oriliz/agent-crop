#!/usr/bin/env python3
"""每日一案：当天最长任务 → 定位耗时最多的一段 → 证据 / 一条对策 / 验证口径 / 采纳建议。

用法：
  daily_case.py                          # 今天，自动挑活跃时长最长的任务块
  daily_case.py --list                   # 只看候选
  daily_case.py --date 2026-11-04 --task <session:turn>
  daily_case.py --record adopt --case <报告 json 路径> [--note 备注]

产出：data/reports/<日期>/<session>-<turn>.html + 同名 .json（基线，供后续验证对比）。
数据只在本机；报告不写对话正文，只写工具名、目标、次数与时长。
"""
import argparse
import html
import json
import os
import re
import subprocess
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402

DATA = hs.DATA_DIR
REPORTS = os.path.join(DATA, "reports")
DECISIONS = os.path.join(DATA, "decisions")
SOLUTIONS = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))),
    "solutions")
TOOL_CAP = 300.0
IDLE_GAP = 180.0
EPISODE_GAP = 1800.0
HUMAN_GATE = ("askuserquestion", "ask_user", "askuserinput", "exitplanmode")
CORRECTION_RE = re.compile(r"不对|说白了|缺乏证据|没有证据|重新|没做好|返工|看不懂|不是这样|怎么又|你又|质疑")
EVIDENCE_RE = re.compile(
    r"(?i)\bsls\b|get-logs|get_logs|trace|日志|复现|repro|\.log\b|mcp__[a-z0-9_]*_(log|trace)")
PROBLEM_CN = {"repeat_nav": "重复导航", "tool_error": "工具失败与重试", "reopen": "反复权衡",
              "serial_wait": "串行等待", "long_call": "单次长调用"}


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def mmss(sec):
    sec = int(round(sec))
    return f"{sec // 60}′{sec % 60:02d}″" if sec >= 60 else f"{sec}″"


def hhmm(epoch):
    return datetime.fromtimestamp(epoch).strftime("%H:%M:%S")


def norm(s):
    return re.sub(r"\s+", " ", (s or "")).strip()


def target_of(name, inp):
    if not isinstance(inp, dict):
        return ""
    for k in ("file_path", "filePath", "path", "file"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return os.path.basename(v.strip())
    for k in ("pattern", "query", "url"):
        v = inp.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()[:60]
    cmd = inp.get("command")
    if isinstance(cmd, str) and cmd.strip():
        return norm(cmd)[:70]
    return ""


def is_nav(step):
    n = (step["name"] or "").lower()
    return (step["family"] in ("read", "search") or "search" in n
            or n.startswith("lsp") or "grep" in n or "glob" in n)


def collect_steps(path, start_e, end_e):
    """转录里按时间窗重建：模型段（含思考量）+ 每次工具调用（耗时/成败/目标）。

    同时保留原文片段（思考节选、上一句用户要求），供报告解释「为什么慢」。
    """
    steps, pending, model, prev = [], {}, None, start_e
    last_human = ""
    for line in open(path, errors="replace"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        msg = o.get("message") or {}
        role = msg.get("role") or o.get("type")
        content = msg.get("content")
        human = hs.is_human_text(o)
        if human and not hs.label_is_injection(human):
            last_human = norm(human)[:220]
        ts = hs.ts_epoch(o.get("timestamp"))
        if not ts or ts < start_e - 5 or ts > end_e + 5:
            continue
        if role == "assistant" and isinstance(content, list):
            if model is None or ts != model["ts"]:
                dur = max(0.0, ts - prev) if prev else 0.0
                model = {"kind": "model", "start": prev or ts, "dur": min(dur, 600.0),
                         "thinking": 0, "reopen": 0, "ts": ts, "last_human": last_human,
                         "thinking_text": "", "reply": ""}
                steps.append(model)
                prev = ts
            for x in content:
                if not isinstance(x, dict):
                    continue
                t = x.get("type")
                if t == "thinking":
                    th = x.get("thinking", "")
                    model["thinking"] += len(th)
                    model["reopen"] += len(hs.REOPEN_RE.findall(th))
                    if len(model["thinking_text"]) < 12000:
                        model["thinking_text"] += th
                elif t == "text":
                    if len(model["reply"]) < 800:
                        model["reply"] += x.get("text", "")
                elif t == "tool_use":
                    pending[x.get("id")] = (x.get("name") or "", x.get("input") or {}, ts)
        elif isinstance(content, list):
            for x in content:
                if not isinstance(x, dict) or x.get("type") != "tool_result":
                    continue
                use = pending.pop(x.get("tool_use_id"), None)
                if not use:
                    continue
                name, inp, use_ts = use
                cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                dur = max(0.0, ts - use_ts) if ts and use_ts else 0.0
                fam = hs.classify_tool(name, cmd)
                if (name or "").lower() in HUMAN_GATE:
                    fam = "等你回答"
                steps.append({
                    "kind": "tool", "start": use_ts, "dur": min(dur, TOOL_CAP),
                    "name": name, "target": target_of(name, inp), "cmd": norm(cmd)[:120],
                    "family": fam, "is_error": bool(x.get("is_error")),
                    "error": norm(str(x.get("content")))[:160] if x.get("is_error") else "",
                    "bg": bool(isinstance(inp, dict) and inp.get("run_in_background")),
                    "skill": (inp.get("skill") or "") if isinstance(inp, dict) else "",
                    "agent": (inp.get("subagent_type") or inp.get("description") or "")
                    if (name or "").lower() == "agent" else "",
                    "last_human": last_human,
                })
                prev = max(prev, ts) if ts else prev
        elif ts:
            prev = max(prev, ts) if ts > prev else prev
    return steps


def compaction_events(path):
    """上下文压缩点：isCompactSummary 或续接标记。用来判断慢是不是压缩后的重来。"""
    out = []
    for line in open(path, errors="replace"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        ts = hs.ts_epoch(o.get("timestamp"))
        if not ts:
            continue
        hit = bool(o.get("isCompactSummary"))
        if not hit:
            txt = ""
            c = (o.get("message") or {}).get("content")
            if isinstance(c, list):
                txt = " ".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
            elif isinstance(o.get("content"), str):
                txt = o["content"]
            hit = hs.COMPACT_MARK in txt
        if hit:
            out.append(ts)
    res = []
    for t in sorted(out):
        if not res or t - res[-1] > 5:
            res.append(t)
    return res


def human_messages(path):
    """真实用户消息（排除工具结果、系统注入、Skill 注入）。"""
    out = []
    for line in open(path, errors="replace"):
        try:
            o = json.loads(line)
        except Exception:
            continue
        text = hs.is_human_text(o)
        if not text or hs.label_is_injection(text):
            continue
        ts = hs.ts_epoch(o.get("timestamp"))
        if ts:
            out.append((ts, norm(text)))
    return out


def group_episodes(recs):
    """同一个问题（会话）内，按两次开工的起点间隔 >30 分钟切成连续工作段。"""
    eps = []
    for r in sorted(recs, key=lambda x: x.get("start", "")):
        t0 = hs.ts_epoch(r.get("start", ""))
        if not t0:
            continue
        if eps and t0 - eps[-1]["last_start"] <= EPISODE_GAP:
            eps[-1]["turns"].append(r)
            eps[-1]["last_start"] = t0
        else:
            eps.append({"turns": [r], "last_start": t0})
    for ep in eps:
        first, last = ep["turns"][0], ep["turns"][-1]
        ep["start"] = hs.ts_epoch(first.get("start", ""))
        ep["end"] = hs.ts_epoch(last.get("end", ""))
        ep["active"] = sum(t.get("active_seconds", 0) for t in ep["turns"])
        ep["wall"] = max(0.0, ep["end"] - ep["start"])
        ep["idle"] = max(0.0, ep["wall"] - ep["active"])
        ep["label"] = next((t.get("label") for t in ep["turns"] if t.get("label")),
                           first.get("label", ""))
        ep["turn"] = first.get("turn")
    return eps


def problem_view(path, recs, comps, cur_rec):
    """以会话为一个问题：还原连续工作段，判断首段是否取证、返工倍数是否成立。"""
    eps = group_episodes(recs)
    if len(eps) < 2:
        return None
    humans = human_messages(path)
    for ep in eps:
        ep["corrections"] = [(ts, txt) for ts, txt in humans
                             if ep["start"] - 60 <= ts <= ep["end"] + 60
                             and CORRECTION_RE.search(txt)]
    cur_idx = 0
    for i, ep in enumerate(eps):
        if any(t.get("id") == cur_rec.get("id") for t in ep["turns"]):
            cur_idx = i
    first = eps[0]
    first_steps = collect_steps(path, first["start"], first["end"])
    ev = [s for s in first_steps if s["kind"] == "tool"
          and EVIDENCE_RE.search(" ".join([s.get("name", ""), s.get("cmd", ""), s.get("target", "")]))]
    rework, correction = None, ""
    for ep in eps[1:]:
        if ep["corrections"]:
            rework, correction = ep, ep["corrections"][0][1]
            break
    ratio = (rework["active"] / first["active"]) if rework and first["active"] >= 180 else 0
    rootcause = bool(rework and not ev and ratio >= 1.5)
    pv = {
        "episodes": eps, "total_active": sum(e["active"] for e in eps),
        "wall": max(0.0, eps[-1]["end"] - eps[0]["start"]),
        "compactions": len(comps), "current_idx": cur_idx,
        "rootcause": rootcause, "evidence_calls": len(ev),
        "first": first, "rework": rework, "ratio": ratio,
        "correction": correction[:100], "why": "", "baseline": "", "comp_ts": comps,
    }
    if rootcause:
        rw_steps = collect_steps(path, rework["start"], rework["end"])
        model = [s for s in rw_steps if s["kind"] == "model"]
        offered = bool(re.search(r"我(先)?提供|需不需要我提供|我可以提供", correction, re.I)
                       and re.search(r"(?i)trace|日志", correction))
        pv["why"] = (
            f"首次处理 {mmss(first['active'])} 只做了代码检索（{len(first_steps)} 个动作），"
            f"没有任何运行时证据，直接给出代码层面的根因；你在 {hhmm(rework['start'])} 指出"
            f"“{norm(correction)[:60]}”。返工 {mmss(rework['active'])}（{ratio:.1f}×）里，"
            f"模型思考合计约 {mmss(sum(s['dur'] for s in model))}，分散在 {len(model)} 次里，没有一次特别长，"
            f"主要花在重新盘观测面、加埋点和验证环境上"
            + ("；你当时已经提出可以提供 Trace，但走的是自建埋点" if offered else "")
            + "。根因在入口没有取证，不在这一轮反复思考。")
        pv["baseline"] = (f"首轮运行时证据 {len(ev)} 次；返工段 {mmss(rework['active'])} / "
                          f"首次 {mmss(first['active'])} = {ratio:.1f}×")
    return pv


SUMMARY_MIN_ACTIVE = 600.0
SUMMARIES = os.path.join(DATA, "summaries")


def turn_actions(sub):
    c = {"read": 0, "edit": 0, "run": 0, "wait": 0}
    for s in sub:
        if s["kind"] != "tool":
            continue
        f = s["family"]
        if f in ("read", "search"):
            c["read"] += 1
        elif f == "edit":
            c["edit"] += 1
        elif f == "等你回答":
            c["wait"] += 1
        else:
            c["run"] += 1
    return c


def segment_summary_md(path, ep, idx, total):
    """Stop 时给当前连续工作段做一份事实摘要：谁提了什么、这轮干了什么、卡在哪。"""
    humans = [(ts, t) for ts, t in human_messages(path) if ep["start"] - 60 <= ts <= ep["end"] + 60]
    comps = [c for c in compaction_events(path) if ep["start"] - 60 <= c <= ep["end"] + 60]
    steps = collect_steps(path, ep["start"], ep["end"])
    heads = [t for t in ep["turns"]]
    corr = [(ts, t) for ts, t in humans if CORRECTION_RE.search(t)]
    tool_txt = 0
    fails = []
    files, cmds = [], []
    for s in steps:
        if s["kind"] != "tool":
            continue
        tool_txt += 1
        if s["is_error"]:
            fails.append(s)
        if s["family"] == "edit" and s["target"]:
            files.append(s["target"])
        if s["cmd"] and s["family"] not in ("read", "search"):
            cmds.append(s["cmd"])

    def uniq(xs, n):
        out = []
        for x in xs:
            if x and x not in out:
                out.append(x)
        return out[:n]

    start_txt = datetime.fromtimestamp(ep["start"]).strftime("%m-%d %H:%M")
    end_txt = datetime.fromtimestamp(ep["end"]).strftime("%H:%M")
    lines = [
        f"# 本段总结 · 第 {idx + 1}/{total} 段 · {start_txt}–{end_txt}",
        "",
        f"- 实际投入 {mmss(ep['active'])}，从开始到结束 {mmss(ep['wall'])}（含空档），工具调用 {tool_txt} 次，失败 {len(fails)} 次，压缩 {len(comps)} 次",
        f"- 提问 {len(heads)} 次" + (f"，其中纠正 {len(corr)} 次（首次 {hhmm(corr[0][0])}）" if corr else "，无纠正"),
    ]
    if corr:
        lead = corr[0][0] <= ep["start"] + 90
        lines.append("- 按规则：" + ("纠正出现在本段开头，针对的是前一段的工作"
                                     if lead else "本段中途出现纠正，纠正前那部分判为没做好"))
    lines += ["", "## 每次问答", ""]
    for i, t in enumerate(heads):
        t0 = hs.ts_epoch(t.get("start", ""))
        t1 = hs.ts_epoch(t.get("end", ""))
        req = next((txt for ts, txt in reversed(humans) if ts <= t0 + 5), "")
        sub = [s for s in steps if t0 - 5 <= s["start"] <= t1 + 5]
        c = turn_actions(sub)
        reply = next((norm(s["reply"]) for s in reversed(sub) if s["kind"] == "model" and s["reply"]), "")
        slow = max((s for s in sub if s["kind"] == "tool" and s["family"] != "等你回答"),
                   key=lambda s: s["dur"], default=None)
        lines.append(f"### {i + 1}. {hhmm(t0)} · {mmss(t.get('active_seconds', 0))}")
        if req:
            lines.append(f"你说：{req[:80]}")
        lines.append(f"动作：读 {c['read']} · 改 {c['edit']} · 跑 {c['run']} · 等 {c['wait']} · 失败 "
                     f"{sum(1 for s in sub if s['kind'] == 'tool' and s['is_error'])}")
        if slow and slow["dur"] >= 60:
            lines.append(f"最慢一步：{slow['name']} · {slow['target'] or slow['cmd'][:40]} · {mmss(slow['dur'])}")
        if reply:
            lines.append(f"它说：{reply[:120]}")
        lines.append("")
    lines += ["## 本段动作", ""]
    lines.append("- 改动：" + ("、".join(uniq(files, 6)) if files else "无写文件操作"))
    cmds = uniq(cmds, 8)
    if cmds:
        lines.append("- 主要命令：" + "；".join("`" + c[:70] + "`" for c in cmds))
    if fails:
        lines.append("- 失败：" + "；".join(
            f"{f['name']} {f['target'] or f['cmd'][:40]} → {f['error'][:60]}" for f in fails[:4]))
    if comps:
        lines.append("- 压缩：" + "、".join(hhmm(c) for c in comps))
    lines += ["", f"原文：{path}", ""]
    return "\n".join(lines)


def summary_mode(sid, path):
    if not path:
        return 1
    recs = hs.scan_file(path, sid or os.path.basename(path).rsplit(".", 1)[0])
    eps = group_episodes(recs)
    if not eps:
        return 0
    ep = eps[-1]
    if ep["active"] < SUMMARY_MIN_ACTIVE:
        return 0
    md = segment_summary_md(path, ep, len(eps) - 1, len(eps))
    day = datetime.fromtimestamp(ep["start"]).strftime("%Y-%m-%d")
    outdir = os.path.join(SUMMARIES, day)
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, (sid or "session") + ".md")
    with open(out, "w", encoding="utf-8") as f:
        f.write(md)
    print(f"[本段总结] {mmss(ep['active'])} · 第 {len(eps)} 段 · {out}")
    return 0


def reopen_snippets(text, limit=3, width=70):
    return [(text[max(0, m.start() - width):m.end() + width], m.group(0))
            for m in list(hs.REOPEN_RE.finditer(text or ""))[:limit]]


def mark_quote(text, match):
    e, m = esc(norm(text)), esc(match)
    return e.replace(m, f"<mark>{m}</mark>", 1) if m in e else e


def _idx(steps, target):
    for i, s in enumerate(steps):
        if s is target:
            return i
    return -1


def _tool_list(tools, n=4):
    if not tools:
        return '<p class="qp">（这段之后没有工具调用）</p>'
    lis = "".join(
        f"<li>{esc(t['name'])}"
        f"{' · ' + esc(t['target'] or t['cmd'][:40]) if (t['target'] or t['cmd']) else ''}"
        f" <span class='d'>{mmss(t['dur'])}</span></li>" for t in tools[:n])
    return f"<ul>{lis}</ul>"


def wait_note(s, steps, comps):
    """停顿前发生了什么：失败、压缩，还是什么都没有。只给观察，不下结论。"""
    i = _idx(steps, s)
    prev = next((x for x in reversed(steps[:i]) if x["kind"] == "tool"), None)
    if prev and prev["is_error"]:
        return f"停之前的最后一步是失败命令（{action_of(prev)}），先确认它是不是触发原因。"
    c = [t for t in comps if t <= s["start"]]
    if c and s["start"] - max(c) <= 180:
        return f"压缩发生在停之前 {(s['start'] - max(c)) / 60:.0f} 分钟，可能是在重建上下文。"
    if prev:
        return (f"停之前的最后一步是「{action_of(prev)}」，之后没有新信息进来；"
                f"是犹豫、想起旧事还是前后矛盾，要看这段原文才能判断。")
    return "停之前没有工具调用，属于纯模型阶段。"


def excerpt_html(problem, steps, comps, w0, w1):
    cards = []

    def card(meta, human, quotes, actions, note=""):
        h = f'<div class="q"><div class="qh"><b>{esc(meta[0])}</b><span>{esc(meta[1])}</span></div>'
        if human:
            h += f'<div class="ql">这段之前的用户要求</div><p class="qp">{esc(human)}</p>'
        for label, block in quotes:
            h += f'<div class="ql">{esc(label)}</div><blockquote>{block}</blockquote>'
        if note:
            h += f'<p class="qp">{esc(note)}</p>'
        if actions is not None:
            h += '<div class="ql">随后动作</div>' + _tool_list(actions)
        return h + "</div>"

    if problem:
        k = problem["key"]
        if k == "reopen":
            for s in sorted(problem["steps"], key=lambda x: -x["reopen"])[:2]:
                i = _idx(steps, s)
                nxt = [x for x in steps[i + 1:] if x["kind"] == "tool"][:4]
                quotes = [("思考原文（节选，反复处高亮）", mark_quote(frag, m))
                          for frag, m in reopen_snippets(s["thinking_text"])]
                if not quotes and s["thinking_text"]:
                    quotes = [("思考原文（节选）", esc(norm(s["thinking_text"])[:220]))]
                cards.append(card(
                    (hhmm(s["start"]), f"模型段 {mmss(s['dur'])} · 思考 {s['thinking']} 字 · 反复 {s['reopen']} 处"),
                    s["last_human"], quotes, nxt, wait_note(s, steps, comps)))
        elif k == "tool_error":
            for s in problem["fails"][:2]:
                quotes = ([("执行的命令", esc(s["cmd"]))] if s["cmd"] else []) + \
                         [("返回的错误", esc(s["error"]))]
                cards.append(card((hhmm(s["start"]), f"{s['name']} 失败 · {mmss(s['dur'])}"),
                                  s["last_human"], quotes, None))
        elif k == "repeat_nav":
            (name, target), arr = problem["groups"][0]
            cards.append(card(
                (hhmm(arr[0]["start"]), f"{name} · {target} 被翻 {len(arr)} 次"),
                arr[0]["last_human"],
                [("同一目标的调用时间", esc("、".join(hhmm(x["start"]) for x in arr)))], None))
        else:
            ss = [x for x in (problem.get("steps") or [problem.get("step")]) if x][:2]
            for s in ss:
                cards.append(card((hhmm(s["start"]), f"{s['family']} · {mmss(s['dur'])}"),
                                  s["last_human"],
                                  [("执行的命令", esc(s["cmd"]))] if s["cmd"] else [], None))

    inside = [t for t in comps if w0 <= t <= w1]
    before = [t for t in comps if t < w0]
    after = [t for t in comps if t > w1]
    if inside:
        for t in inside[:2]:
            nav = [x for x in steps if x["kind"] == "tool" and is_nav(x)
                   and 0 <= x["start"] - t <= 300]
            cards.append(card((hhmm(t), "上下文压缩"), "", [], nav,
                              f"压缩后 5 分钟内导航 {len(nav)} 次"))
    else:
        near = ""
        if before:
            near = f"最近一次在任务开始前 {(w0 - max(before)) / 60:.0f} 分钟"
        elif after:
            near = f"最近一次在任务结束后 {(min(after) - w1) / 60:.0f} 分钟"
        cards.append(card(("—", "上下文压缩（旁证）"), "", [], None,
                          "本任务窗口内没有压缩事件" + (f"；{near}。" if near else "。")))
    return "".join(cards)


def merge_len(intervals):
    """并行工具调用会重叠，按区间并集计时，避免把同一段墙钟时间算两遍。"""
    if not intervals:
        return 0.0
    iv = sorted((max(0.0, s), max(0.0, e)) for s, e in intervals)
    total, cs, ce = 0.0, iv[0][0], iv[0][1]
    for s, e in iv[1:]:
        if s > ce:
            total += ce - cs
            cs, ce = s, e
        else:
            ce = max(ce, e)
    return total + (ce - cs)


def ivs(steps):
    return [(s["start"], s["start"] + s["dur"]) for s in steps]


def aggregate(steps, rec):
    w0 = hs.ts_epoch(rec.get("start", "").replace(" ", "T"))
    w1 = hs.ts_epoch(rec.get("end", "").replace(" ", "T"))
    groups = {}
    for s in steps:
        key = "模型思考" if s["kind"] == "model" else s["family"]
        s0, s1 = max(s["start"], w0), min(s["start"] + s["dur"], w1)
        if s1 > s0:
            groups.setdefault(key, []).append((s0, s1))
    agg = {k: merge_len(v) for k, v in groups.items()}
    used = merge_len([(max(s["start"], w0), min(s["start"] + s["dur"], w1)) for s in steps])
    total = sum(agg.values())
    if total > used > 0:  # 边说边跑工具时模型段与工具段会重叠，按并集等比压回
        agg = {k: v * used / total for k, v in agg.items()}
    agg["空档"] = max(0.0, rec.get("active_seconds", 0) - used)
    return dict(sorted(agg.items(), key=lambda kv: -kv[1]))


def find_problems(steps, rec):
    tools = [s for s in steps if s["kind"] == "tool"]
    models = [s for s in steps if s["kind"] == "model"]
    active = max(1, rec.get("active_seconds", 1))
    out = []

    groups = {}
    for s in tools:
        if is_nav(s):
            groups.setdefault((s["name"], s["target"] or s["cmd"][:60]), []).append(s)
    repeat = sorted(((k, v) for k, v in groups.items() if len(v) >= 3),
                    key=lambda kv: -merge_len(ivs(kv[1][1:])))[:5]
    if repeat:
        score = merge_len([iv for _, v in repeat for iv in ivs(v[1:])])
        if score >= 30:
            out.append({"key": "repeat_nav", "score": score, "groups": repeat})

    fails = [s for s in tools if s["is_error"]]
    retries = []
    for i, s in enumerate(tools):
        if not s["is_error"]:
            continue
        for nxt in tools[i + 1:i + 4]:
            if nxt["name"] == s["name"] and nxt["start"] - s["start"] <= 120:
                retries.append(nxt)
                break
    if fails:
        score = merge_len(ivs(fails) + ivs(retries))
        if score >= 20:
            out.append({"key": "tool_error", "score": score, "fails": fails, "retries": retries})

    reopen = [s for s in models if s["reopen"] > 0]
    total_reopen = sum(s["reopen"] for s in models)
    if total_reopen >= 3:
        score = merge_len(ivs(reopen))
        if score >= 60:
            out.append({"key": "reopen", "score": score, "steps": reopen, "marks": total_reopen,
                        "chars": sum(s["thinking"] for s in models)})

    waits = [s for s in tools if s["dur"] >= 120 and s["family"] in
             ("build", "deploy", "network", "install", "test")]
    sleeps = []
    for s in tools:
        for m in hs.SLEEP_CMD_RE.findall(s["cmd"] or ""):
            try:
                if float(m) >= 20:
                    sleeps.append(s)
            except ValueError:
                pass
    wait_steps = {id(s) for s in waits + sleeps}
    if wait_steps:
        score = merge_len(ivs([s for s in tools if id(s) in wait_steps]))
        if score >= 60:
            out.append({"key": "serial_wait", "score": score,
                        "steps": [s for s in tools if id(s) in wait_steps]})

    peak = max((s for s in tools if s["family"] != "等你回答"), key=lambda s: s["dur"], default=None)
    if peak and peak["dur"] >= 180 and id(peak) not in wait_steps:
        out.append({"key": "long_call", "score": peak["dur"], "step": peak})

    for p in out:
        p["share"] = p["score"] / active
    return sorted(out, key=lambda p: -p["score"])


PLAYBOOK = {
    "repeat_nav": {
        "title": "同一处反复翻文件，定位靠试错",
        "change": "对同一文件/同一 pattern 的导航，第 2 次起改走语义定位（lsp references/definition 或一次 SearchWorkspace），"
                  "并把该仓库的「先怎么找」写成一行写进 AGENTS.md；重复翻同一处的调用视为失败信号。",
        "metric": "同一目标的导航次数（≥2 次算重复）",
        "target": "单任务重复导航 ≤2 次",
        "solution": "lsp-autoconfig",
    },
    "tool_error": {
        "title": "命令/参数靠猜，失败重试吃掉时间",
        "change": "把这次报错的命令写法固化成可复用片段（对应 skill 或项目 AGENTS.md）：先 `--help` 或 `--dry-run` 校验参数，"
                  "同一命令失败 2 次必须换查文档，不再第三次试错。",
        "metric": "单任务工具失败次数与紧随其后的重试耗时",
        "target": "失败率减半，重试耗时 ≤ 首轮失败的 1/3",
        "solution": "fast-loop",
    },
    "reopen": {
        "title": "反复权衡同一件事，决策没有落盘",
        "change": "开工先写三行决策表（做什么/不做什么/怎么验证）并落盘 decisions；"
                  "同一问题追问超过 2 次，先回读已拍板项，翻案必须写理由，禁止在脑子里重开辩论。",
        "metric": "每千字思考里的反复标记数 + 重新决策耗时",
        "target": "反复标记密度下降 50%，决策段耗时占比 ≤30%",
        "solution": "tempo-rules",
    },
    "serial_wait": {
        "title": "串行等构建/部署，人机一起干等",
        "change": "长命令一律后台跑，等待期做下一件独立事；部署/构建排到任务最前面，别等设计定稿才排队。",
        "metric": "单任务 ≥2 分钟的前台等待累计时长",
        "target": "前台等待 ≤ 任务活跃时长的 15%",
        "solution": "deploy-watch",
    },
    "long_call": {
        "title": "一次调用吃掉整段任务",
        "change": "把这次长调用拆成可并行的子步骤，或改成后台执行 + 完成通知；单次调用超过 2 分钟必须有超时与降级路径。",
        "metric": "单次工具调用时长与占比",
        "target": "单次 ≤2 分钟，或后台化且不阻塞下一步",
        "solution": "deploy-watch",
    },
    "evidence_first": {
        "title": "调试类问题首轮先取证，再下结论",
        "change": "首次处理时，先按 UTP 排查方法拿到一份可复核的运行时证据（日志 / Trace / 复现），再给根因；"
                  "代码行号只能作为假设线索，不能单独作为结论。",
        "metric": "首轮运行时证据调用数 + 返工段与首次处理段的倍数",
        "target": "同类问题连续 3 个首轮有运行时证据，返工段 ≤ 首次处理段 1.5 倍",
        "solution": "fast-loop",
    },
}


def load_solution(sid):
    p = os.path.join(SOLUTIONS, sid, "solution.json")
    try:
        return json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}


def evidence_rows(problem, steps):
    """把问题还原成 4-6 条可核对的证据（时间点 + 次数 + 耗时）。"""
    rows = []
    k = problem["key"]
    if k == "repeat_nav":
        for (name, target), arr in problem["groups"]:
            extra = merge_len(ivs(arr[1:]))
            rows.append([hhmm(arr[0]["start"]), f"{name} · {target}",
                         f"{len(arr)} 次（重复 {len(arr) - 1} 次）", mmss(extra)])
    elif k == "tool_error":
        for s in problem["fails"][:3]:
            rows.append([hhmm(s["start"]), f"{s['name']} · {s['target'] or s['cmd'][:40]}",
                         "失败：" + (s["error"].split("\n")[0][:60] or "is_error"), mmss(s["dur"])])
        if problem["retries"]:
            rows.append([hhmm(problem["retries"][0]["start"]), "失败后紧跟重试",
                         f"{len(problem['retries'])} 次", mmss(sum(x["dur"] for x in problem["retries"]))])
    elif k == "reopen":
        density = problem["marks"] / max(1, problem["chars"]) * 1000
        rows.append(["—", "全任务思考量",
                     f"{problem['chars']} 字 / {problem['marks']} 处反复标记（{density:.1f}/千字）",
                     mmss(problem["score"])])
        for s in sorted(problem["steps"], key=lambda x: -x["reopen"])[:3]:
            rows.append([hhmm(s["start"]), "模型思考中出现反复标记",
                         f"{s['reopen']} 处 / {s['thinking']} 字", mmss(s["dur"])])
    elif k == "serial_wait":
        for s in sorted(problem["steps"], key=lambda x: -x["dur"])[:4]:
            rows.append([hhmm(s["start"]), f"{s['family']} · {s['target'] or s['cmd'][:40]}",
                         "前台等待", mmss(s["dur"])])
    elif k == "long_call":
        s = problem["step"]
        rows.append([hhmm(s["start"]), f"{s['name']} · {s['target'] or s['cmd'][:40]}",
                     "单次调用", mmss(s["dur"])])
    return rows


def top_steps(steps, n=8):
    tools = [s for s in steps if s["kind"] == "tool" and s["family"] != "等你回答"]
    return sorted(tools, key=lambda s: -s["dur"])[:n]


ACTION_RULES = [
    (r"git (checkout|switch)", "切分支"),
    (r"git (status|branch|log|diff|fetch|pull|remote)", "查仓库状态"),
    (r"git (add|commit|push)", "提交/推送"),
    (r"tsc\b|typecheck", "类型检查"),
    (r"(pnpm|npm|yarn).*\b(test|vitest|jest)\b|pytest|go test|mvn .*\btest\b", "跑测试"),
    (r"(pnpm|npm|yarn).*\bbuild\b|go build|mvn .*(compile|package)|make\b", "编译"),
    (r"dsh-testmgr|deploy|hotpatch|kubectl|a1 (env|pipeline)", "部署/起测试环境"),
    (r"\b(curl|wget)\b", "调接口"),
    (r"\b(pnpm|npm|yarn) (i|install|add)\b|pip3? install", "装依赖"),
    (r"^\s*(cd .*&&\s*)?(ls|cat|sed|head|tail|wc|jq)\b", "看文件/目录"),
    (r"\b(rg|grep|ag|ack|find)\b", "搜代码"),
    (r"\brm\b", "删文件"),
]


def action_of(s):
    c = s.get("cmd") or ""
    for pat, label in ACTION_RULES:
        if re.search(pat, c, re.I):
            return label
    return {"read": "看文件", "search": "搜代码", "edit": "改文件", "git": "git 操作",
            "build": "编译", "test": "跑测试", "deploy": "部署/起环境", "install": "装依赖",
            "network": "网络请求", "browser": "开浏览器", "other-shell": "跑命令",
            "等你回答": "等你回答"}.get(s.get("family"), "其他工具")


def timing_note(s):
    """给耗时一个参照：不是所有耗时都值得追，先分出“正常”和“异常”。"""
    if s.get("is_error"):
        return "失败，需要重试"
    d, fam = s["dur"], s.get("family")
    if fam == "git" and d >= 10:
        return "git 操作通常几秒，这里偏慢"
    if re.search(r"\bgit\b", s.get("cmd") or "") and d >= 10:
        return "git 操作通常几秒，这里 1 分钟以上，偏慢"
    if fam in ("read", "search") and d >= 10:
        return "只是查看/搜索，却用了这么久，值得看"
    if fam in ("build", "test", "deploy", "install") and d >= 60:
        return "编译/测试/部署类，通常几十秒到几分钟"
    if fam not in ("build", "test", "deploy", "install") and d >= 120:
        return "超过 2 分钟，看是否该改成后台跑"
    return ""


def actions_table(steps, n=8):
    rows = sorted([s for s in steps if s.get("family") != "等你回答"], key=lambda s: -s["dur"])[:n]
    out = []
    for s in rows:
        if s["kind"] == "model":
            what = f"模型思考（{s['thinking']} 字）"
            note = f"出现 {s['reopen']} 处停顿词" if s["reopen"] else ""
        else:
            what = action_of(s)
            tgt = s["target"] or s["cmd"][:44]
            if tgt:
                what += " · " + tgt
            note = timing_note(s)
        out.append(f"<tr><td>{hhmm(s['start'])}</td><td>{esc(what)}</td>"
                   f"<td>{mmss(s['dur'])}</td><td>{esc(note)}</td></tr>")
    model = [s for s in steps if s["kind"] == "model"]
    tools = [s for s in steps if s["kind"] == "tool" and s.get("family") != "等你回答"]
    longest = max(tools, key=lambda s: s["dur"], default=None)
    head = (f"模型思考合计 {mmss(sum(s['dur'] for s in model))}，分 {len(model)} 段"
            f"（单段最长 {mmss(max((s['dur'] for s in model), default=0))}）；"
            f"工具 {len(tools)} 次，单步最长 {mmss(longest['dur']) if longest else '0″'}"
            f"{'（' + action_of(longest) + '）' if longest else ''}。")
    return (f"<h3>耗时最多的 {len(rows)} 个动作</h3>"
            f'<p class="hint">{esc(head)}</p>'
            f'<p class="hint">按耗时排序；说明列只给参照，不是结论。</p>'
            f"<table><thead><tr><th>时间</th><th>在做什么</th><th>耗时</th><th>说明</th></tr></thead>"
            f'<tbody>{"".join(out)}</tbody></table>')


def hours(sec):
    return f"{sec / 3600:.1f} 小时" if sec >= 3600 else mmss(sec)


def problem_html(pv, comps):
    """问题全貌：同一会话 → 连续工作段 → 本次细看的那段。"""
    if not pv:
        return ""
    eps = pv["episodes"]
    total = sum(e["active"] for e in eps) or 1
    cur_idx = pv["current_idx"]
    cur = eps[cur_idx]
    segs, legs = [], []
    for i, e in enumerate(eps, 1):
        if i == 1:
            c = "#adb5bd"
        elif e is pv.get("rework"):
            c = "#e8590c"
        else:
            c = "#2f6feb"
        segs.append(f'<span class="seg" style="width:{e["active"] / total * 100:.2f}%;'
                    f'background:{c}" title="第{i}段 {mmss(e["active"])}"></span>')
        legs.append(f'<span class="lg"><i style="background:{c}"></i>第{i}段 {mmss(e["active"])}</span>')
    rows = []
    for i, e in enumerate(eps, 1):
        if len(e["turns"]) == 1 and e["idle"] >= 1800:
            when = f"{hhmm(e['start'])} 起（之后空档 {hours(e['idle'])}）"
        else:
            when = f"{hhmm(e['start'])}–{hhmm(e['end'])}"
        tags = []
        if i == 1:
            tags.append("首次处理")
        if e is pv.get("rework"):
            tags.append("你的纠正 → 返工")
        if i == cur_idx + 1:
            tags.append("本次细看")
        rows.append(f"<tr><td>{i}</td><td>{esc(when)}</td><td>{esc(e['label'][:64])}</td>"
                    f"<td>{mmss(e['active'])}</td><td>{esc(' · '.join(tags))}</td></tr>")
    start_txt = datetime.fromtimestamp(eps[0]["start"]).strftime("%m-%d %H:%M")
    share = cur["active"] / total * 100
    line = (f"问题从 {start_txt} 开始，共 {len(eps)} 段连续工作；实际投入 {mmss(total)}，"
            f"从开始到结束 {hours(pv['wall'])}（含停顿 {hours(max(0, pv['wall'] - total))}），压缩 {pv['compactions']} 次。"
            f"本次细看第 {cur_idx + 1} 段（{mmss(cur['active'])}，占 {share:.0f}%）。")
    if comps:
        comp_note = (f"本问题发生 {len(comps)} 次压缩（"
                     f"{'、'.join(hhmm(t) for t in comps)}），都落在连续工作段内部，是插曲不是起因。")
    else:
        comp_note = "本问题没有发生上下文压缩。"
    if pv.get("rootcause"):
        box = (f'<div class="box" style="margin-top:12px"><p><b>根因在入口，不在这一轮。</b>'
               f'{esc(pv["why"])}</p><p class="hint">{esc(comp_note)}</p></div>')
    else:
        box = (f'<div class="box" style="margin-top:12px"><p>首次处理段没有发现“未取证就下结论”的证据，'
               f'本次仍按单段问题分析。</p><p class="hint">{esc(comp_note)}</p></div>')
    return f"""<h2>一、问题全貌（同一会话）</h2>
<p class="hint">同一个会话按一件事处理；中间停顿超过 30 分钟，切成一段连续工作。</p>
<p>{esc(line)}</p>
<div class="bar-wrap"><div class="ribbon">{"".join(segs)}</div><div>{"".join(legs)}</div></div>
<table style="margin-top:12px"><thead><tr><th>段</th><th>时间</th><th>发生了什么</th><th>实际投入</th><th>标记</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table>
{box}
"""


def build_html(rec, title, problem, agg, steps, play, sol, report_json, excerpts="", pv=None):
    active = rec.get("active_seconds", 0)
    main_play, main_sol = play, sol
    if pv and pv.get("rootcause"):
        main_play = PLAYBOOK["evidence_first"]
        main_sol = load_solution(main_play["solution"])
        title_txt = f"首段没取证，第二天返工 {mmss(pv['rework']['active'])}"
        share = pv["rework"]["active"] / max(1, pv["total_active"]) * 100
        verdict = (f"这个问题实际投入 {mmss(pv['total_active'])}，其中 {share:.0f}% 花在纠正后的返工段；"
                   f"先看问题全貌，再看返工段里耗时最多的是哪一步、在干什么。")
    else:
        saving = min(problem["score"], active * 0.5) if problem else 0
        title_txt = play["title"] if problem else "今天没有被单个问题吃掉的大块时间"
        verdict = (f"耗时最多的是「{title_txt}」，本任务可回收约 {saving / 60:.0f} 分钟（上界）。"
                   if problem else "今天的最长任务没有明显单点浪费，建议保持观察。")
    total = sum(v for v in agg.values()) or 1
    agg_bar = "".join(
        f'<span class="seg" style="width:{v / total * 100:.2f}%;background:{color(k)}" '
        f'title="{esc(k)} {mmss(v)}"></span>' for k, v in agg.items())
    agg_legend = "".join(
        f'<span class="lg"><i style="background:{color(k)}"></i>{esc(k)} {mmss(v)}</span>'
        for k, v in agg.items())
    rows = evidence_rows(problem, steps) if (problem and not (pv and pv.get("rootcause"))) else []
    ev = "".join(f"<tr><td>{esc(r[0])}</td><td>{esc(r[1])}</td><td>{esc(r[2])}</td><td>{esc(r[3])}</td></tr>"
                 for r in rows) or '<tr><td colspan="4">无</td></tr>'
    peak = top_steps(steps)
    peak_max = max((s["dur"] for s in peak), default=1)
    bars = "".join(
        f'<div class="bar"><span class="t">{esc(hhmm(s["start"]))}</span>'
        f'<span class="n">{esc((s["name"] + " · " + (s["target"] or s["cmd"][:36]))[:52])}</span>'
        f'<span class="track"><i style="width:{max(2, s["dur"] / peak_max * 100):.1f}%;'
        f'background:{color(s["family"])}"></i></span>'
        f'<span class="d">{mmss(s["dur"])}</span></div>' for s in peak)
    if pv and pv.get("rootcause"):
        action_block = actions_table(steps)
        evidence_block = ""
    else:
        action_block = f'<div class="bar-wrap" style="margin-top:12px">{bars}</div>'
        evidence_block = (f"<h3>量化证据</h3>"
                          f"<table><thead><tr><th>时间</th><th>对象</th><th>发生了什么</th><th>耗时</th></tr></thead>"
                          f"<tbody>{ev}</tbody></table>")
    status = main_sol.get("status", "未验证")
    sol_line = (f"<code>{esc(main_play['solution'])}</code>（{esc(status)}）："
                f"{esc(main_sol.get('name', ''))}" if problem else "无")
    state = report_json.get("decision", "待拍板")
    quotes_block = (f'<h3>关键片段（会话原文）</h3>'
                    f'<p class="hint">每张卡：这段之前的用户要求 → 当时说了什么（反复处高亮）→ 随后做了什么。</p>'
                    f'{excerpts}' if excerpts else
                    '<h3>关键片段（会话原文）</h3><p class="hint">本次用 --no-quotes 生成，未写入原文片段。</p>')
    top_hint = ("先看时间花在哪，再看耗时最多的一步在干什么。片段摘自本机会话转录，只存在本机报告里。"
                if excerpts else "先看时间花在哪，再看耗时最多的一步在干什么。")
    footer_note = "报告含本机会话原文，勿直接外发。" if excerpts else ""
    problems = problem_html(pv, pv.get("comp_ts", [])) if pv else ""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>今日一案 · {esc(rec.get('start', '')[:10])}</title>
<style>
:root{{--fg:#1b1f24;--mut:#6b7280;--line:#e5e7eb;--bg:#fbfbfc;--acc:#2f6feb}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);
font:15px/1.65 -apple-system,BlinkMacSystemFont,"PingFang SC","Helvetica Neue",sans-serif}}
main{{max-width:920px;margin:0 auto;padding:36px 24px 64px}}
h1{{font-size:26px;margin:0 0 6px}}h2{{font-size:18px;margin:36px 0 10px}}
.sub{{color:var(--mut);font-size:13px}}.lead{{font-size:17px;line-height:1.6;margin:14px 0 0}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px;margin:20px 0 0}}
.card{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:12px 14px}}
.card b{{display:block;font-size:22px;font-variant-numeric:tabular-nums}}
.card span{{color:var(--mut);font-size:12px}}
.bar-wrap{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px;margin-top:14px}}
.ribbon{{display:flex;height:26px;border-radius:6px;overflow:hidden;background:#eef0f3}}
.seg{{display:block;height:100%}}.lg{{display:inline-flex;align-items:center;gap:6px;
margin:10px 14px 0 0;font-size:12px;color:var(--mut)}}.lg i{{width:9px;height:9px;border-radius:2px}}
.bar{{display:grid;grid-template-columns:62px 1fr 160px 52px;gap:10px;align-items:center;
font-size:12px;padding:3px 0}}.bar .t{{color:var(--mut);font-variant-numeric:tabular-nums}}
.bar .n{{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}}
.track{{background:#eef0f3;height:9px;border-radius:5px;overflow:hidden}}
.track i{{display:block;height:100%}}.bar .d{{text-align:right;color:var(--mut);font-variant-numeric:tabular-nums}}
table{{width:100%;border-collapse:collapse;background:#fff;border:1px solid var(--line);border-radius:10px}}
th,td{{text-align:left;padding:8px 10px;border-bottom:1px solid var(--line);font-size:13px}}
th{{color:var(--mut);font-weight:500;background:#fafbfc;white-space:nowrap}}tr:last-child td{{border-bottom:0}}
td:nth-child(4){{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.box{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:16px 18px}}
.box p{{margin:0 0 10px}}.box p:last-child{{margin:0}}
.k{{color:var(--mut);font-size:12px;margin-right:6px}}
.tag{{display:inline-block;padding:1px 8px;border-radius:999px;background:#fff4e5;color:#9a5b00;font-size:12px}}
.opts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:10px;margin-top:12px}}
.opt{{border:1px solid var(--line);border-radius:10px;padding:12px;background:#fff}}
.opt.rec{{border-color:var(--acc);box-shadow:0 0 0 1px var(--acc) inset}}
.opt b{{display:block;margin-bottom:4px}}.opt small{{color:var(--mut)}}
code{{background:#f3f4f6;padding:1px 5px;border-radius:4px;font-size:12px}}
h3{{font-size:15px;margin:26px 0 6px}}.hint{{color:var(--mut);font-size:12px;margin:0 0 8px}}
.q{{background:#fff;border:1px solid var(--line);border-radius:10px;padding:14px 16px;margin-top:10px}}
.qh{{display:flex;gap:10px;align-items:baseline}}.qh span{{color:var(--mut);font-size:12px}}
.ql{{color:var(--mut);font-size:12px;margin-top:10px}}
.qp{{margin:3px 0 0;font-size:13px}}blockquote{{margin:5px 0 0;padding:8px 12px;background:#fafbfc;
border-left:3px solid #d0d7de;font-size:13px;color:#24292f}}mark{{background:#fff3bf;color:inherit}}
.q ul{{margin:4px 0 0;padding-left:20px;font-size:13px}}.q .d{{color:var(--mut);font-size:12px}}
footer{{margin-top:40px;color:var(--mut);font-size:12px;border-top:1px solid var(--line);padding-top:12px}}
</style></head><body><main>
<div class="sub">今日一案 · {esc(rec.get('start', '')[:10])} · 任务：{esc((rec.get('label') or '')[:56])}</div>
<h1>{esc(main_play['title'] if problem else '今天没有单点浪费')}</h1>
<p class="lead">{esc(verdict)}</p>
<div class="cards">
<div class="card"><b>{mmss(active)}</b><span>真正在干的时间（本段）</span></div>
<div class="card"><b>{mmss(agg.get('模型思考', rec.get('model_seconds', 0)))}</b><span>模型思考+回复</span></div>
<div class="card"><b>{mmss(sum(v for k, v in agg.items() if k not in ('模型思考', '空档', '等你回答')))}</b><span>工具调用</span></div>
<div class="card"><b>{rec.get('tool_calls', 0)}</b><span>工具调用次数</span></div>
<div class="card"><b>{sum(1 for s in steps if s['kind'] == 'tool' and s['is_error'])}</b><span>失败调用</span></div>
</div>

{problems}

<h2>二、本段事实与证据</h2>
<p class="hint">{top_hint}</p>
<div class="bar-wrap"><div class="ribbon">{agg_bar}</div><div>{agg_legend}</div></div>
{action_block}

{quotes_block}

{evidence_block}

<h2>三、解决对策</h2>
<div class="box">
<p><span class="k">只改一处</span>{esc(main_play['change']) if problem else '无需改动。'}</p>
<p><span class="k">为什么是这里</span>{esc(pv['why']) if pv and pv.get('rootcause') else esc(PROBLEM_CN.get(problem['key'], problem['key']) + f"占任务活跃时长 {problem['share'] * 100:.0f}%" if problem else '未发现占比突出的单点问题')}</p>
<p><span class="k">关联方案</span>{sol_line}</p>
</div>

<h2>四、测试验证</h2>
<div class="box">
<p><span class="k">指标</span>{esc(main_play['metric']) if problem else '—'}</p>
<p><span class="k">本问题基线</span>{esc(pv['baseline']) if pv and pv.get('rootcause') else (esc(mmss(problem['score'])) + '（' + esc(PROBLEM_CN.get(problem['key'], '—')) + '可归因耗时）' if problem else '—')}</p>
<p><span class="k">判定线</span>{esc(main_play['target']) if problem else '—'}</p>
<p><span class="k">验证方式</span>改完后，对后续 3 个同类长任务重跑本报告：<code>python3 daily_case.py --date &lt;日期&gt; --task &lt;session:turn&gt;</code>，看该指标是否连续达标。</p>
<p><span class="k">当前状态</span><span class="tag">未验证 · 本报告只给口径</span></p>
</div>

<h2>五、采纳建议</h2>
<div class="box">
<p><b>建议：采纳。</b>改动只有一处，回滚成本低；若 3 个任务后指标未动，按「不采纳」处理并记录理由。</p>
<div class="opts">
<div class="opt rec"><b>采纳</b><small>我按上面改，并跟踪 3 个任务回填</small></div>
<div class="opt"><b>先观察</b><small>只记录基线，不改行为</small></div>
<div class="opt"><b>不采纳</b><small>说明理由，之后不再提</small></div>
</div>
<p style="margin-top:12px"><span class="k">当前决策</span>{esc(state)}　<span class="k">回一句</span>「采纳 / 先观察 / 不采纳」即可，我负责落盘。</p>
</div>
<footer>任务：{esc(rec.get('id', ''))} · {esc(rec.get('start', ''))} → {esc(rec.get('end', ''))}<br>
数据源：~/.qoder/task-metrics/data（本机）· 报告：{esc(report_json.get('path', ''))} · 生成于 {datetime.now().strftime('%Y-%m-%d %H:%M')}<br>{esc(footer_note)}</footer>
</main></body></html>"""


PALETTE = {"模型思考": "#5468ff", "read": "#2f9e44", "search": "#0ca678", "edit": "#e8590c",
           "other-shell": "#f08c00", "build": "#c92a2a", "deploy": "#a61e4d", "git": "#7048e8",
           "test": "#1098ad", "network": "#5c7cfa", "install": "#d6336c", "browser": "#495057",
           "空档": "#ced4da", "等你回答": "#adb5bd", "other": "#868e96"}


def color(k):
    return PALETTE.get(k, "#868e96")


def pick(day, task_id=""):
    recs = hs.load_day(day)
    if task_id:
        hit = [r for r in recs if r.get("id") == task_id]
        if not hit and ":" in task_id:
            hit = [r for r in recs if r.get("session") == task_id.split(":", 1)[0]]
        recs = hit
    return sorted([r for r in recs if r.get("active_seconds", 0) >= 180],
                  key=lambda r: -r.get("active_seconds", 0))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--task", default="", help="session:turn 或 session")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--record", choices=("adopt", "observe", "reject"), default="")
    ap.add_argument("--case", default="", help="--record 时对应报告 json")
    ap.add_argument("--note", default="")
    ap.add_argument("--no-quotes", action="store_true", help="不把会话原文片段写进报告")
    ap.add_argument("--summary", action="store_true", help="Stop hook：给当前连续工作段写事实摘要")
    ap.add_argument("--session", default="", help="--summary 时的会话 id")
    ap.add_argument("--file", default="", help="--summary 时的转录路径")
    args = ap.parse_args()

    if args.record:
        return record_decision(args)

    if args.summary:
        data = hs.read_stdin()
        sid = args.session or hs.pick(data, "session_id", "sessionId") or ""
        path = (args.file or hs.pick(data, "transcript_path", "transcriptPath")
                or (hs.find_transcript(sid) if sid else ""))
        return summary_mode(sid, path)

    cands = pick(args.date, args.task)
    if args.list:
        for r in cands[:12]:
            print(f"{r['active_seconds']:>5}s  {r['id']:<48} {r['start']}  {r['label'][:36]}")
        return 0
    if not cands:
        print(f"{args.date} 没有 ≥3 分钟的任务块")
        return 1

    rec = cands[0]
    path = hs.find_transcript(rec.get("session", ""))
    if not path:
        print("找不到会话转录：" + rec.get("session", ""))
        return 1
    scanned = hs.scan_file(path, rec.get("session", ""))
    fresh = [r for r in scanned if r.get("active_seconds", 0) >= 180]
    if fresh:
        t0 = hs.ts_epoch(rec.get("start", "").replace(" ", "T"))
        rec = min(fresh, key=lambda r: abs(hs.ts_epoch(r.get("start", "")) - t0))
    session_recs = scanned or [rec]
    start_e = hs.ts_epoch(rec.get("start", "").replace(" ", "T"))
    end_e = hs.ts_epoch(rec.get("end", "").replace(" ", "T"))
    steps = collect_steps(path, start_e, end_e)
    agg = aggregate(steps, rec)
    problems = find_problems(steps, rec)
    problem = problems[0] if problems else None
    play = PLAYBOOK.get(problem["key"], {}) if problem else {}
    sol = load_solution(play.get("solution", "")) if problem else {}
    comps = compaction_events(path)
    pv = problem_view(path, session_recs, comps, rec)

    outdir = os.path.join(REPORTS, args.date)
    os.makedirs(outdir, exist_ok=True)
    base = os.path.join(outdir, rec["id"].replace(":", "-"))
    pv_json = {}
    if pv:
        pv_json = {k: v for k, v in pv.items() if k not in ("episodes", "first", "rework", "comp_ts")}
        pv_json["episodes"] = [{"index": i + 1, "start": e["start"], "active": e["active"],
                                "label": e["label"]} for i, e in enumerate(pv["episodes"])]
        pv_json["first"] = {"active": pv["first"]["active"], "start": pv["first"]["start"]}
        pv_json["rework"] = ({"active": pv["rework"]["active"], "start": pv["rework"]["start"]}
                             if pv.get("rework") else None)
    data = {
        "date": args.date, "task": rec, "aggregate": agg,
        "problems": [{k: v for k, v in p.items() if k not in ("steps", "fails", "retries", "groups", "step")}
                     for p in problems],
        "picked": problem["key"] if problem else "",
        "playbook": play, "solution_status": sol.get("status", ""),
        "problem_view": pv_json,
        "decision": "待拍板", "path": base + ".html",
    }
    with open(base + ".json", "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    excerpts = "" if args.no_quotes else excerpt_html(problem, steps, comps, start_e, end_e)
    html_out = build_html(rec, rec.get("label") or rec["id"], problem, agg, steps, play, sol,
                          data, excerpts, pv)
    with open(base + ".html", "w", encoding="utf-8") as f:
        f.write(html_out)

    if pv and pv.get("rootcause"):
        print(f"今日一案：{PLAYBOOK['evidence_first']['title']}")
        print(f"问题：{len(pv['episodes'])} 段连续工作，实际投入 {mmss(pv['total_active'])}，"
              f"从开始到结束 {hours(pv['wall'])}")
        print(f"证据：首段未取证；返工段 {mmss(pv['rework']['active'])}，是首段的 {pv['ratio']:.1f} 倍")
    else:
        print(f"今日一案：{play.get('title', '无明显单点问题')}")
    print(f"任务：{rec['id']} 活跃 {mmss(rec['active_seconds'])} / 工具 {mmss(rec.get('tool_seconds', 0))}")
    if problem and not (pv and pv.get("rootcause")):
        print(f"本段证据：{problem['key']} 可归因 {mmss(problem['score'])}，占 {problem['share'] * 100:.0f}%")
    if pv and pv.get("rootcause"):
        print(f"建议：{PLAYBOOK['evidence_first']['change']}")
    elif problem:
        print(f"建议：{play['change']}")
    print(f"报告：{base}.html")
    return 0


def record_decision(args):
    if not args.case or not os.path.isfile(args.case):
        print("需要 --case <报告 json>")
        return 2
    d = json.load(open(args.case, encoding="utf-8"))
    d["decision"] = {"adopt": "采纳", "observe": "先观察", "reject": "不采纳"}[args.record]
    json.dump(d, open(args.case, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    os.makedirs(DECISIONS, exist_ok=True)
    day = d.get("date") or datetime.now().strftime("%Y-%m-%d")
    p = os.path.join(DECISIONS, day + ".md")
    if not os.path.exists(p):
        open(p, "w", encoding="utf-8").write("|时间|决策|理由|状态|\n|--|--|--|--|\n")
    title = d.get("playbook", {}).get("title", "每日一案")
    note = (args.note or "").replace("|", "/")
    with open(p, "a", encoding="utf-8") as f:
        f.write(f"|{datetime.now().strftime('%H:%M')}|每日一案 · {title}|{note}|{d['decision']}|\n")
    print(f"已记录：{d['decision']} -> {p}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
