#!/usr/bin/env python3
"""开工对齐门：照资料/协议实现的任务，在改代码前留下可复核的对齐记录。

为什么需要门而不是事后分析：
  「资料与业务要求不一致」这件事，会话里只留下返工结果，没有留下
  「改代码前跟谁对齐过、结论是什么」。事后任何检测都只能猜。
  所以这里不猜，改为在开工点强制问三行，并把回答记进 data/gates/。

事件：
  user-prompt-submit  命中「按资料/协议实现」→ 注入三行确认清单
  post-tool-use       首次改文件时仍无对齐记录 → 提醒一次

记录（data/gates/<date>.jsonl）：
  {"ev":"gate","sid":...,"prompt":...}      门已触发
  {"ev":"ack","sid":...,"prompt":...}       用户回答了负责人/结论/验收
  {"ev":"edit-before-ack","sid":...,"tool":...} 无记录先改代码

fail-open：任何异常静默退出 0，不影响宿主。
"""
import datetime
import json
import os
import re
import sys

DATA = os.path.expanduser(os.environ.get("KIT_DATA_DIR") or os.path.join(
    os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "task-metrics", "data"))
GATES = os.path.join(DATA, "gates")

# 「按外部资料实现」的触发：既有资料类名词，又有落地动作
SPEC = re.compile(r"(需求|协议|PRD|prd|文档|资料|方案|设计稿|接口定义|schema|规范)")
BUILD = re.compile(r"(实现|开发|改造|落地|按(这个|照|文档|协议|方案)|照着|据此|开始写|动手)")
# 讨论/评审类话语里有资料名词和动词，但不是要开工；2026-11-05 真实误触发后加
DISCUSS = re.compile(r"(我在想|是不是|你觉得|怎么看|为什么|有没有可能|是否(可以|需要)|讨论|梳理|对比|复盘|评估|调研)")
EDIT_TOOLS = re.compile(r"(Write|Edit|SearchReplace|ReplaceInFile|create_file|write_file|"
                        r"write_to_file|search_replace|replace_in_file)", re.I)
ACK = re.compile(r"(负责人|对齐|确认|结论|验收|拍板|owner)", re.I)
DURATION = re.compile(r"(\d+(?:\.\d+)?)\s*(小时|个小时|h|天|d|周|w)", re.I)
UNIT_HOURS = {"小时": 1, "个小时": 1, "h": 1, "天": 24, "d": 24, "周": 168, "w": 168}


def parse_estimate(text):
    m = DURATION.search(text or "")
    if not m:
        return None
    return round(float(m.group(1)) * UNIT_HOURS[m.group(2).lower()], 1)

GATE_TEXT = (
    "[开工对齐门] 这是按资料/协议实现的任务。动第一行代码前，先用四行回答并落盘：\n"
    "① 负责人：谁提的要求、谁有权拍板\n"
    "② 已对齐：什么时候、结论是什么（没有对齐就直说没有，不要默认资料即需求）\n"
    "③ 验收：完成后看哪个现象判断做对了\n"
    "④ 预计：第一版多久能给（小时/天，用于事后比对实际跨度）\n"
    "资料内部如有自相矛盾，先列出来问，不要自行选一个实现。"
)
WARN_TEXT = "[开工对齐门] 本任务还没有对齐记录（①②③），先补齐再继续改文件。"


def now():
    return datetime.datetime.now()


def read_event():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read() or ""
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def pick(d, *keys):
    for k in keys:
        v = d.get(k)
        if isinstance(v, str) and v.strip():
            return v
    return ""


def sid_of(d):
    sid = pick(d, "session_id", "sessionId", "conversation_id")
    if sid:
        return sid
    # PostToolUse 可能没有 session_id：沿用最近 4 小时内触发过门的会话
    try:
        for rec in reversed(recent_records(4)):
            if rec.get("sid"):
                return rec["sid"]
    except Exception:
        pass
    return "unknown"


def gate_file(day=None):
    day = day or now().date().isoformat()
    return os.path.join(GATES, f"{day}.jsonl")


def recent_records(hours=24):
    out = []
    today = now()
    for delta in range(2):
        day = (today - datetime.timedelta(days=delta)).date().isoformat()
        p = gate_file(day)
        if not os.path.isfile(p):
            continue
        with open(p, errors="ignore") as f:
            for line in f:
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    cutoff = today.timestamp() - hours * 3600
    return [r for r in out if r.get("t", 0) >= cutoff]


def append(rec):
    os.makedirs(GATES, exist_ok=True)
    rec.setdefault("t", now().timestamp())
    rec.setdefault("ts", now().isoformat(timespec="seconds"))
    with open(gate_file(), "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")


def sid_has(sid, ev):
    return any(r.get("sid") == sid and r.get("ev") == ev for r in recent_records(48))


def prompt_of(d):
    p = pick(d, "prompt", "user_prompt", "text")
    if p:
        return p
    c = d.get("content") or d.get("message")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return " ".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def on_prompt(d):
    prompt = prompt_of(d)
    sid = sid_of(d)
    if not prompt or sid == "unknown":
        return ""
    if ACK.search(prompt) and sid_has(sid, "gate"):
        est = parse_estimate(prompt)
        rec = {"ev": "ack", "sid": sid, "prompt": prompt[:160]}
        if est:
            rec["estimate_hours"] = est
        append(rec)
        return ""
    if DISCUSS.search(prompt):
        return ""
    if not (SPEC.search(prompt) and BUILD.search(prompt)):
        return ""
    if sid_has(sid, "gate"):
        return ""
    append({"ev": "gate", "sid": sid, "prompt": prompt[:160]})
    return GATE_TEXT


def on_tool(d):
    tool = pick(d, "tool_name", "toolName", "tool") or ""
    if not EDIT_TOOLS.search(tool):
        return ""
    sid = sid_of(d)
    if not sid_has(sid, "gate") or sid_has(sid, "edit-before-ack"):
        return ""
    if sid_has(sid, "ack"):
        return ""
    append({"ev": "edit-before-ack", "sid": sid, "tool": tool})
    return WARN_TEXT


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    d = read_event()
    out = on_prompt(d) if event == "user-prompt-submit" else (
        on_tool(d) if event == "post-tool-use" else "")
    if out:
        print(out)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
