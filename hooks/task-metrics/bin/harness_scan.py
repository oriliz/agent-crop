#!/usr/bin/env python3
"""Harness 观测脚本（通用，无项目专名）。

模式（由 ~/.qoder/settings.json 的 hooks 调用，也可手工运行）:
  scan    解析会话记录，按人类消息切任务块，落盘客观指标       (Stop hook)
  inject  上一任务指标越线时，注入一行提示                     (UserPromptSubmit hook)
  post-tool 工具级提醒 + 轮内时长 + 方案分发                    (PostToolUse hook)
  daily   当日黄线以上任务与耗时归因                           (手工/每日复盘)
  backfill 回扫最近 N 天会话补指标                             (手工)
  status  一体看板：钩子健康 / 今日任务 / 规则 / 调整 / 趋势     (手工)
  report  查看最近若干任务块指标                               (手工)
  rules   规则台账：list / add / status                        (手工)

约定：fail-open，任何异常静默退出 0；不改写输入；只写本地。
数据：~/.qoder/task-metrics/data/metrics/<date>.jsonl、rules.jsonl、harness_events.jsonl
配置：~/.qoder/task-metrics/data/harness_config.json（不存在时按默认生成）
"""
import argparse
import glob
import json
import os
import re
import socket
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone, timedelta

IDE_HOME = os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder")
DATA_DIR = os.path.expanduser(
    os.environ.get("KIT_DATA_DIR") or os.path.join(IDE_HOME, "task-metrics", "data")
)
METRICS_DIR = os.path.join(DATA_DIR, "metrics")
PEERS_DIR = os.path.join(DATA_DIR, "peers")
RULES = os.path.join(DATA_DIR, "rules.jsonl")
CONFIG = os.path.join(DATA_DIR, "harness_config.json")
INJECTED = os.path.join(METRICS_DIR, "injected.jsonl")
EVENTS = os.path.join(DATA_DIR, "harness_events.jsonl")   # 旧版滚动文件（只读兼容）
EVENTS_DIR = os.path.join(DATA_DIR, "events")             # 按天归档，反查要跨周
ROUTES_DIR = os.path.join(DATA_DIR, "routes")
SETTINGS = os.path.join(IDE_HOME, "settings.json")
TURN_STATE = os.path.join(DATA_DIR, ".turn_state.json")
NOTIFY_STATE = os.path.join(DATA_DIR, ".notify_state.json")
SOLUTIONS_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.realpath(__file__))))),
    "solutions")
try:
    sys.path.insert(0, SOLUTIONS_DIR)
    import kit_solutions as sol
except Exception:
    sol = None


def solution_on(solution_id):
    return bool(sol and sol.is_on(solution_id))


def solution_output(event, data):
    """方案的 hook 走 solutions/dispatch.py 统一分发；没有启用的方案时返回空。"""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(SOLUTIONS_DIR, "dispatch.py"), event],
            input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True, timeout=6)
        return r.stdout.strip()
    except Exception:
        return ""

DEFAULT_CONFIG = {
    "thinking_chars": 15000,
    "max_thinking_block": 6000,
    "blocked_waits": 2,
    "reopen_marks": 30,
    "plan_items": 40,
    "reply_chars": 3000,
    "inject_cooldown_hours": 12,
    "yellow_line_minutes": 10,
    "red_line_minutes": 30,
    "slow_tool_seconds": 120,
    "idle_gap_seconds": 900,
    "tool_cap_seconds": 3600,
    "backfill_interval_hours": 24,
    "backfill_days": 14,
}

BASH_TOOLS = ("Bash", "bash", "Shell", "shell", "run_in_terminal", "run_terminal_command")
DEPLOY_CMD_RE = re.compile(
    r"(?:^|[;&|]\s*)"
    r"(?:a1\s+(?:env|pipeline|build)\b|kubectl\b|helm\b"
    r"|(?:mvn|mvnw|gradle|gradlew)\b[^;&|]*\b(?:package|install|deploy|build|verify)\b"
    r"|(?:npm|yarn|pnpm)\s+(?:run\s+)?build\b|go\s+build\b"
    r"|docker\s+(?:push|build)\b|make\b"
    r"|[^;&|\n]*\b(?:hotpatch|hotdeploy)\b)", re.I)

# 只读 Bash 判定用的命令集合：命中即视为「导航」而不是「干活」
READONLY_CMDS = {
    "cat", "head", "tail", "sed", "awk", "grep", "egrep", "fgrep", "rg", "ag",
    "ls", "find", "wc", "echo", "printf", "sort", "uniq", "cut", "tr", "jq",
    "diff", "comm", "file", "stat", "basename", "dirname", "realpath", "tree",
    "du", "df", "which", "type", "date", "pwd", "true", "nl", "column", "uname",
}
WRITE_TOKENS = {"rm", "mv", "cp", "mkdir", "rmdir", "touch", "tee", "chmod",
                "chown", "ln", "truncate", "dd", "install", "patch", "unlink"}
GIT_READONLY_SUB = {"status", "log", "show", "diff", "blame", "branch",
                    "rev-parse", "remote", "describe", "ls-files", "config"}
SEG_SPLIT_RE = re.compile(r"\|\||&&|[|;\n]|(?<![0-9&])&(?!&)")
HARMLESS_REDIR_RE = re.compile(r"\d?>>?\s*/dev/null|&>\s*/dev/null|2>&1")
CMD_SUB_RE = re.compile(r"\$\([^()]*\)|`[^`]*`")

FAMILY_RULES = [
    ("test", r"\b(mvn|mvnw|gradle|gradlew)\b.*\b(test|verify)\b|\bpytest\b|\b(jest|vitest|mocha)\b|npm (run )?test|\bgo test\b"),
    ("build", r"\b(mvn|mvnw|gradle|gradlew)\b.*\b(compile|package|install|build|clean)\b|npm (run )?build|\byarn build|\btsc\b|\bmake\b|\bgo build\b|\bcargo build\b"),
    ("deploy", r"\bdeploy\b|\bhotpatch\b|\bhotdeploy\b|\ba1 (env|pipeline|build)\b|\bkubectl\b|\bdocker (push|build)\b|\bhelm\b"),
    ("git", r"^\s*git\b"),
    ("install", r"npm (i|install)\b|\byarn (add|install)\b|\bpip3? install\b|\bbrew install\b"),
    ("network", r"\b(curl|wget|rsync|scp|ssh)\b"),
    ("search", r"\b(rg|grep|ag|ack)\b|\bfind\b"),
    ("read", r"^\s*(cat|head|tail|less|wc|jq)\b"),
]


def classify_tool(name, cmd):
    n = (name or "").lower()
    if n in ("read", "read_file", "readfile") or n.endswith("read_document"):
        return "read"
    if n in ("grep", "glob", "search", "codebase_search") or n.endswith("search_documents"):
        return "search"
    if n in ("edit", "write", "edit_file", "write_file", "multiedit", "apply_patch"):
        return "edit"
    if "browser" in n or "playwright" in n or "chrome" in n:
        return "browser"
    c = cmd or ""
    for fam, pat in FAMILY_RULES:
        if re.search(pat, c, re.I):
            return fam
    if n in ("bash", "shell", "run_terminal_command"):
        return "other-shell"
    return "other"

REOPEN_RE = re.compile(
    r"(?i)\b(wait|hold on|hmm+|actually|reconsider|on second thought)\b|等一下|等等|重新考虑|话说回来",
)
PLAN_ITEM_RE = re.compile(r"(?m)^\s*\d+[\.、)]\s+")
SLEEP_RE = re.compile(r"\bsleep\s+(\d+(?:\.\d+)?)")
# 提醒路径用严格版：只认命令位（行首 / ; & | 之后），避免脚本里的 "sleep 25" 字样误报
SLEEP_CMD_RE = re.compile(r"(?:^|[;&|]\s*)sleep\s+(\d+(?:\.\d+)?)")
INTERRUPT_MARK = "[Request interrupted by user"
COMPACT_MARK = "This session is being continued from a previous conversation"
NOTIFY_MARK = "[SYSTEM NOTIFICATION"
IGNORE_PREFIXES = (INTERRUPT_MARK, COMPACT_MARK, NOTIFY_MARK)


def label_is_injection(text):
    t = (text or "").lstrip()
    return (t.startswith(("<", "Caveat:", "Note:", "Base directory for this skill"))
            or t.startswith(IGNORE_PREFIXES))


def clean_label(text):
    return " ".join((text or "").split())[:40]


def load_config():
    cfg = dict(DEFAULT_CONFIG)
    try:
        with open(CONFIG, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except Exception:
        os.makedirs(DATA_DIR, exist_ok=True)
        try:
            with open(CONFIG, "w", encoding="utf-8") as f:
                json.dump(DEFAULT_CONFIG, f, ensure_ascii=False, indent=2)
        except Exception:
            pass
    for key, env in (("yellow_line_minutes", "KIT_YELLOW_MIN"),
                     ("red_line_minutes", "KIT_RED_MIN")):
        try:
            cfg[key] = int(os.environ[env])
        except Exception:
            pass
    return cfg


def log_event(kind, **kv):
    """每个模式记一行运行事实：按天归档，供 status 判断钩子是否真在跑、供反查跨周对比。"""
    try:
        rec = {"t": int(time.time()), "kind": kind}
        rec.update(kv)
        os.makedirs(EVENTS_DIR, exist_ok=True)
        path = os.path.join(EVENTS_DIR, datetime.now().strftime("%Y-%m-%d") + ".jsonl")
        with open(path, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass


def load_events(since_epoch=0.0):
    out = []
    paths = ([EVENTS] if os.path.exists(EVENTS) else []) + \
            sorted(glob.glob(os.path.join(EVENTS_DIR, "*.jsonl")))
    for path in paths:
        try:
            with open(path, encoding="utf-8") as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if (r.get("t") or 0) >= since_epoch:
                        out.append(r)
        except Exception:
            continue
    return out


def machine_name():
    try:
        for line in open(os.path.expanduser("~/.agent-sync/config.env"), encoding="utf-8"):
            if line.startswith("MACHINE="):
                return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return socket.gethostname().split(".")[0]


def read_stdin():
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read() or ""
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def pick(data, *keys):
    for k in keys:
        v = data.get(k)
        if isinstance(v, str) and v.strip():
            return v.strip()
    return ""


def find_transcript(sid, explicit=None):
    if explicit and os.path.isfile(explicit):
        return explicit
    if sid and sid != "unknown":
        hits = glob.glob(os.path.expanduser(f"~/.qoder/projects/*/{sid}.jsonl"))
        hits += glob.glob(os.path.expanduser(f"~/.codex/sessions/*/*/*/rollout-*{sid}*.jsonl"))
        if hits:
            return max(hits, key=os.path.getmtime)
    return ""


def ts_epoch(s):
    # Qoder 转录里时间戳混用 ISO 字符串与 epoch 毫秒整数（如 active-leaf 等事件），两种都要认
    if isinstance(s, (int, float)):
        return float(s) / 1000.0 if s > 1e12 else float(s)
    if not s:
        return 0.0
    for fmt in ("%Y-%m-%dT%H:%M:%S.%fZ", "%Y-%m-%dT%H:%M:%SZ"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc).timestamp()
        except Exception:
            continue
    try:
        v = float(s)
        return v / 1000.0 if v > 1e12 else v
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00")).timestamp()
    except Exception:
        return 0.0


def is_human_text(o):
    msg = o.get("message") or {}
    if (msg.get("role") or o.get("type")) != "user":
        return ""
    if o.get("isCompactSummary"):
        return ""
    c = msg.get("content")
    if isinstance(c, str):
        return c
    if not isinstance(c, list):
        return ""
    if any(isinstance(x, dict) and x.get("type") == "tool_result" for x in c):
        return ""
    text = "".join(x.get("text", "") for x in c if isinstance(x, dict) and x.get("type") == "text")
    return text


# ---- Codex rollout（~/.codex/sessions/**/rollout-*.jsonl）→ 与 Qoder 转录同构 ----
# Codex 每行是 {timestamp, type, payload}，工具调用/结果是 response_item 里的独立行；
# 这里只做形状转换，切段/计时/归因仍复用下面同一套逻辑。thinking 在 Codex 里是加密的，
# 只有 summary 明文（通常为空），所以 Codex 块的 thinking_chars 天然偏低，不能当「思考少」读。
CODEX_TOOL_ALIAS = {"exec": "bash", "shell": "bash", "local_shell": "bash",
                    "exec_command": "bash", "apply_patch": "apply_patch",
                    "read_file": "read", "view_image": "read", "web_search": "search"}
CODEX_SKIP_PREFIXES = ("# AGENTS.md instructions", "# Files mentioned by the user:",
                       "# Context from your IDE", "<environment_context>",
                       "<user_instructions>", "Another language model started")


def is_codex_path(path):
    return f"{os.sep}.codex{os.sep}sessions{os.sep}" in (path or "")


def _codex_events(o):
    """一条 Codex rollout 行 → 0..n 条 Qoder 形状的记录。"""
    if not isinstance(o, dict) or o.get("type") != "response_item":
        return []
    p = o.get("payload") or {}
    ts, pt = o.get("timestamp"), p.get("type")
    if pt == "message":
        role = p.get("role")
        kind = {"user": "input_text", "assistant": "output_text"}.get(role)
        if not kind:
            return []
        txt = "".join(x.get("text", "") for x in (p.get("content") or [])
                      if isinstance(x, dict) and x.get("type") == kind)
        if not txt.strip() or txt.lstrip().startswith(CODEX_SKIP_PREFIXES):
            return []
        return [{"timestamp": ts, "message": {"role": role,
                                              "content": [{"type": "text", "text": txt}]}}]
    if pt == "reasoning":
        th = "".join(x.get("text", "") for x in (p.get("summary") or []) if isinstance(x, dict))
        return [{"timestamp": ts, "message": {"role": "assistant",
                                              "content": [{"type": "thinking", "thinking": th}]}}]
    if pt in ("custom_tool_call", "function_call", "local_shell_call"):
        name = p.get("name") or ("bash" if pt == "local_shell_call" else "")
        raw = p.get("input") if p.get("input") is not None else p.get("arguments")
        if pt == "local_shell_call":
            raw = (p.get("action") or {}).get("command") or raw
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                inp = parsed if isinstance(parsed, dict) else {"command": raw}
            except Exception:
                inp = {"command": raw}
        else:
            inp = raw if isinstance(raw, dict) else {}
        return [{"timestamp": ts, "message": {"role": "assistant", "content": [{
            "type": "tool_use", "id": p.get("call_id") or p.get("id"),
            "name": CODEX_TOOL_ALIAS.get(name, name), "input": inp}]}}]
    if pt in ("custom_tool_call_output", "function_call_output", "local_shell_call_output"):
        return [{"timestamp": ts, "message": {"role": "user", "content": [{
            "type": "tool_result", "tool_use_id": p.get("call_id") or p.get("id")}]}}]
    return []


def codex_normalized(path):
    """把 Codex rollout 落成 Qoder 形状的临时转录；调用方负责删除。"""
    fd, tmp = tempfile.mkstemp(prefix="codex-scan-", suffix=".jsonl")
    with os.fdopen(fd, "w", encoding="utf-8") as fh, open(path, errors="replace") as src:
        for line in src:
            try:
                o = json.loads(line)
            except Exception:
                continue
            for r in _codex_events(o):
                fh.write(json.dumps(r, ensure_ascii=False) + "\n")
    return tmp


def codex_session_files():
    return glob.glob(os.path.expanduser("~/.codex/sessions/*/*/*/rollout-*.jsonl"))


def codex_recent_transcript(max_age=180):
    best, best_m = "", 0.0
    now = time.time()
    for f in codex_session_files():
        try:
            m = os.path.getmtime(f)
        except OSError:
            continue
        if now - m <= max_age and m > best_m:
            best, best_m = f, m
    return best


def codex_sid(path):
    base = os.path.basename(path).rsplit(".", 1)[0]
    m = re.search(r"([0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$", base)
    return m.group(1) if m else base


def new_segment(epoch, label):
    return {"start": epoch, "end": epoch, "label": label, "thinking": 0,
            "thinking_blocks": 0, "max_block": 0, "reply": 0, "tools": 0,
            "blocked": 0, "reopen": 0, "plan_items": 0, "tool_seconds": 0,
            "active_seconds": 0.0, "idle_seconds": 0, "slow_tools": 0,
            "families": {}, "pending": {}}


def scan_file(path, sid):
    tmp = codex_normalized(path) if is_codex_path(path) else ""
    if tmp:
        path = tmp
    cfg = load_config()
    slow_limit = cfg["slow_tool_seconds"]
    idle_gap = cfg["idle_gap_seconds"]
    tool_cap = cfg["tool_cap_seconds"]
    segments = []
    cur = None
    last_epoch = 0.0
    last_was_tool_use = False
    with open(path, errors="replace") as f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            epoch = ts_epoch(o.get("timestamp") or "")
            msg = o.get("message") or {}
            content = msg.get("content")
            is_result = (msg.get("role") == "user" and isinstance(content, list)
                         and any(isinstance(x, dict) and x.get("type") == "tool_result" for x in content))
            text = is_human_text(o)

            if is_result:
                if cur:
                    cur["end"] = max(cur["end"], epoch)
                    if last_epoch and 0 < epoch - last_epoch <= idle_gap:
                        cur["active_seconds"] += epoch - last_epoch
                    for x in content:
                        if not isinstance(x, dict) or x.get("type") != "tool_result":
                            continue
                        tid = x.get("tool_use_id")
                        use = cur["pending"].pop(tid, None) if tid else None
                        if not use:
                            continue
                        fam, use_epoch = use[2], use[3]
                        if use_epoch <= 0 or epoch <= 0:
                            continue
                        dur = max(0.0, epoch - use_epoch)
                        real = min(dur, tool_cap)
                        cur["tool_seconds"] += real
                        cur["families"][fam] = cur["families"].get(fam, 0.0) + real
                        if dur > tool_cap:
                            cur["idle_seconds"] += dur - real
                        if dur >= slow_limit:
                            cur["slow_tools"] += 1
                last_epoch, last_was_tool_use = epoch, False
                continue

            if text:
                if not label_is_injection(text):
                    if cur and cur["tools"] == 0 and cur["thinking"] == 0 and cur["reply"] == 0:
                        if not label_is_injection(text):
                            cur["label"] = clean_label(text)
                    else:
                        if cur:
                            segments.append(cur)
                        cur = new_segment(epoch, "" if label_is_injection(text) else clean_label(text))
                elif cur and last_epoch and epoch > last_epoch + idle_gap:
                    # 压缩/系统注入不切段，但隔了这么久说明会话是中断后恢复的，空档必须记账
                    cur["idle_seconds"] += epoch - last_epoch
                last_epoch, last_was_tool_use = epoch, False
                continue

            if not cur:
                continue
            if last_epoch and epoch > last_epoch + idle_gap:
                cur["idle_seconds"] += epoch - last_epoch
            if (msg.get("role") or o.get("type")) != "assistant":
                last_epoch, last_was_tool_use = epoch, False
                continue
            cur["end"] = max(cur["end"], epoch)
            if last_epoch and 0 < epoch - last_epoch <= idle_gap:
                cur["active_seconds"] += epoch - last_epoch
            last_epoch, last_was_tool_use = epoch, False
            if not isinstance(content, list):
                continue
            for x in content:
                if not isinstance(x, dict):
                    continue
                t = x.get("type")
                if t == "thinking":
                    th = x.get("thinking", "")
                    n = len(th)
                    cur["thinking"] += n
                    cur["thinking_blocks"] += 1
                    cur["max_block"] = max(cur["max_block"], n)
                    cur["reopen"] += len(REOPEN_RE.findall(th))
                    cur["plan_items"] += len(PLAN_ITEM_RE.findall(th))
                elif t == "text":
                    cur["reply"] += len(x.get("text", ""))
                elif t == "tool_use":
                    last_was_tool_use = True
                    cur["tools"] += 1
                    name = x.get("name") or ""
                    inp = x.get("input") or {}
                    cmd = inp.get("command", "") if isinstance(inp, dict) else ""
                    fam = classify_tool(name, cmd)
                    cur["pending"][x.get("id")] = (name, cmd, fam, epoch)
                    if name in ("Bash", "bash", "Shell", "shell"):
                        for m in SLEEP_RE.findall(cmd or ""):
                            try:
                                if float(m) >= 20:
                                    cur["blocked"] += 1
                            except ValueError:
                                pass
    if cur:
        segments.append(cur)

    out = []
    for i, s in enumerate(segments, 1):
        wall = int(max(0, s["end"] - s["start"]))
        active = int(min(wall, max(0.0, s["active_seconds"])))
        idle = wall - active
        tool_s = int(min(max(0.0, s["tool_seconds"]), active))
        flags = [k for k in ("thinking_chars", "max_thinking_block", "blocked_waits",
                             "reopen_marks", "plan_items", "reply_chars")
                 if s[{"thinking_chars": "thinking", "max_thinking_block": "max_block",
                       "blocked_waits": "blocked", "reopen_marks": "reopen",
                       "plan_items": "plan_items", "reply_chars": "reply"}[k]] >= cfg[k]]
        if active >= cfg["red_line_minutes"] * 60:
            line = "red"
        elif active >= cfg["yellow_line_minutes"] * 60:
            line = "yellow"
        else:
            line = ""
        out.append({
            "id": f"{sid}:{i}",
            "session": sid,
            "host": machine_name(),
            "turn": i,
            "start": datetime.fromtimestamp(s["start"]).strftime("%Y-%m-%dT%H:%M:%S") if s["start"] else "",
            "end": datetime.fromtimestamp(s["end"]).strftime("%Y-%m-%dT%H:%M:%S") if s["end"] else "",
            "label": s["label"],
            "wall_seconds": wall,
            "active_seconds": active,
            "tool_seconds": tool_s,
            "model_seconds": max(0, active - tool_s),
            "idle_seconds": idle,
            "slow_tools": s["slow_tools"],
            "families": {k: int(v) for k, v in sorted(s["families"].items(), key=lambda kv: -kv[1]) if v >= 1},
            "line": line,
            "thinking_chars": s["thinking"],
            "thinking_blocks": s["thinking_blocks"],
            "max_thinking_block": s["max_block"],
            "reply_chars": s["reply"],
            "tool_calls": s["tools"],
            "blocked_waits": s["blocked"],
            "reopen_marks": s["reopen"],
            "plan_items": s["plan_items"],
            "flags": flags,
        })
    if tmp:
        try:
            os.remove(tmp)
        except Exception:
            pass
    return out


def append_metrics(records):
    """按 (id,start) 合并写入：同一任务块以末条事件更晚、指标更大的版本为准。"""
    if not records:
        return 0
    os.makedirs(METRICS_DIR, exist_ok=True)
    rescan_sessions = {r["session"] for r in records}
    by_day = {}
    for p in glob.glob(os.path.join(METRICS_DIR, "*.jsonl")):
        if p.endswith("injected.jsonl"):
            continue
        try:
            for line in open(p, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if r.get("session") in rescan_sessions:
                    continue
                day = (r.get("start") or "")[:10] or os.path.basename(p)[:10]
                by_day.setdefault(day, {})[(r.get("id"), r.get("start"))] = r
        except Exception:
            continue
    n = 0
    for r in records:
        day = r["start"][:10]
        rows = by_day.setdefault(day, {})
        key = (r["id"], r["start"])
        old = rows.get(key)
        if old is None:
            rows[key] = r
            n += 1
        elif (r["end"] or "") >= (old.get("end") or ""):
            rows[key] = r
            n += 1
    for day, rows in by_day.items():
        tmp = os.path.join(METRICS_DIR, day + ".jsonl.tmp")
        with open(tmp, "w", encoding="utf-8") as f:
            for key in sorted(rows):
                f.write(json.dumps(rows[key], ensure_ascii=False) + "\n")
        os.replace(tmp, os.path.join(METRICS_DIR, day + ".jsonl"))
    return n


def latest_metrics(sid):
    best = None
    for p in glob.glob(os.path.join(METRICS_DIR, "*.jsonl")):
        try:
            for line in open(p, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                if sid and r.get("session") != sid:
                    continue
                if best is None or (r.get("end") or "") > (best.get("end") or ""):
                    best = r
        except Exception:
            continue
    return best


def inject(sid, cfg):
    if not solution_on("inline-reminders"):
        return ""
    first = reset_counter(sid) if sid and sid != "unknown" else False
    # 每会话首条消息给一句可执行动作，不给原则
    hint = ("[Harness] 本轮若要找东西：一条 rg \"符号\" -A 6 拿全部命中，"
            "或一次 SearchWorkspace 顶掉 5 次逐文件 grep。") if first else ""
    r = latest_metrics(sid)
    if not r or not r.get("flags"):
        log_event("inject", session=sid, sent=0, reason="no-flag", hint=bool(hint))
        return hint
    try:
        end = datetime.strptime(r["end"], "%Y-%m-%dT%H:%M:%S").timestamp()
    except Exception:
        log_event("inject", session=sid, sent=0, reason="bad-time")
        return hint
    if time.time() - end > cfg["inject_cooldown_hours"] * 3600:
        log_event("inject", session=sid, sent=0, reason="cooldown")
        return hint
    key = r["id"] + "@" + r["start"]
    try:
        done = {json.loads(l).get("key") for l in open(INJECTED, encoding="utf-8")}
    except Exception:
        done = set()
    if key in done:
        log_event("inject", session=sid, sent=0, reason="dup")
        return hint
    os.makedirs(METRICS_DIR, exist_ok=True)
    with open(INJECTED, "a", encoding="utf-8") as f:
        f.write(json.dumps({"key": key, "t": int(time.time() * 1000)}) + "\n")
    log_event("inject", session=sid, sent=1, target=r["id"])
    recap = (
        f"[Harness 回看] 上一任务思考 {r['thinking_chars']} 字（最大单段 {r['max_thinking_block']}）、"
        f"回复 {r['reply_chars']} 字、阻塞等待 {r['blocked_waits']} 次、慢调用 {r.get('slow_tools', 0)} 次、"
        f"重开标记 {r['reopen_marks']} 次、编号计划项 {r['plan_items']} 个。\n"
        "本轮开工三件套：① 已定决定不重开，计划写进任务清单；"
        "② 改代码前先用 lsp-*/jdtls（references/definition）或 SearchWorkspace 定位再读；"
        "③ ≥60s 的命令（构建/部署/日志）一律后台，部署先起、排队期间写代码。"
    )
    return "\n".join(x for x in (hint, recap) if x)


def load_day(day, host_filter=""):
    sources = [(os.path.join(METRICS_DIR, day + ".jsonl"), machine_name())]
    for d in sorted(glob.glob(os.path.join(PEERS_DIR, "*"))):
        if os.path.isdir(d):
            sources.append((os.path.join(d, day + ".jsonl"), os.path.basename(d)))
    recs = []
    for p, host in sources:
        if host_filter and host != host_filter:
            continue
        try:
            for line in open(p, encoding="utf-8"):
                try:
                    r = json.loads(line)
                except Exception:
                    continue
                r["host"] = r.get("host") or host
                recs.append(r)
        except Exception:
            continue
    # 跨机镜像可能含对端复制过来的同一会话，按 session:turn 去重，本机记录优先
    me = machine_name()
    seen = {}
    for r in recs:
        k = r.get("id") or (r.get("session", ""), r.get("turn"))
        old = seen.get(k)
        if old is None or (old.get("host") != me and r.get("host") == me):
            seen[k] = r
    return list(seen.values())


def fmt_min(seconds):
    return f"{seconds / 60:.1f}′"


def act(r):
    return r.get("active_seconds", r.get("wall_seconds") or 0)


SUSPECT_SECONDS = 8 * 3600


def is_suspect(r):
    """跨天 + 零空闲 + 单块 ≥8h：会话中断后恢复但没记到空档的待核数据。"""
    s, e = r.get("start", ""), r.get("end", "")
    return bool(s and e and s[:10] != e[:10]
                and (r.get("idle_seconds") or 0) == 0 and act(r) >= SUSPECT_SECONDS)


def daily(day, cfg, show_all=False, as_json=False, host_filter="", since=""):
    all_recs = load_day(day)
    if since:
        # 复盘窗口可跨日（如「昨日 17:00 → 今日 17:00」）：把前一天也并进来再按 start 过滤
        prev = (datetime.strptime(day, "%Y-%m-%d") - timedelta(days=1)).strftime("%Y-%m-%d")
        all_recs = [r for r in load_day(prev) + all_recs if (r.get("start") or "") >= since]
    hosts = {}
    for r in all_recs:
        hosts[r["host"]] = hosts.get(r["host"], 0) + 1
    recs = all_recs if not host_filter else [r for r in all_recs if r["host"] == host_filter]
    if not show_all:
        recs = [r for r in recs if r.get("line") in ("red", "yellow")]
    recs.sort(key=lambda r: -act(r))
    fam = {}
    for r in recs:
        for k, v in (r.get("families") or {}).items():
            fam[k] = fam.get(k, 0) + v
    total = sum(act(r) for r in recs)
    if as_json:
        print(json.dumps({"date": day, "tasks": recs, "families": fam,
                          "total_seconds": total, "hosts": hosts}, ensure_ascii=False, indent=2))
        return
    print(f"{day} 黄线以上 {len(recs)} 个任务，共 {fmt_min(total)}"
          f"（黄线 {cfg['yellow_line_minutes']}′ / 红线 {cfg['red_line_minutes']}′）")
    if len(hosts) > 1:
        print("来源 " + " / ".join(f"{h} {n}" for h, n in sorted(hosts.items())) + " 个任务块")
    for r in recs:
        mark = "红" if r.get("line") == "red" else "黄"
        tag = f"[{r['host']}] " if len(hosts) > 1 else ""
        print(f"{mark} {tag}{r['start'][11:16]}-{r['end'][11:16]} {fmt_min(act(r))} "
              f"模型 {fmt_min(r['model_seconds'])} 工具 {fmt_min(r['tool_seconds'])} "
              f"空档 {fmt_min(r.get('idle_seconds', 0))} 慢调用 {r['slow_tools']} "
              f"| {(r.get('label') or '')[:32]}")
    if fam:
        parts = " / ".join(f"{k} {fmt_min(v)}" for k, v in sorted(fam.items(), key=lambda kv: -kv[1]))
        print("工具族合计：" + parts)
    for d in sorted(glob.glob(os.path.join(PEERS_DIR, "*"))):
        if not os.path.isdir(d):
            continue
        name = os.path.basename(d)
        if hosts.get(name):
            continue
        line = peer_tempo_line(name, day, cfg["yellow_line_minutes"])
        if line:
            print(line + "（仅时长；详细归因需在对端跑一次 backfill）")


def fmt_ts(epoch):
    try:
        return datetime.fromtimestamp(epoch).strftime("%m-%d %H:%M")
    except Exception:
        return "-"


PLUGIN_HOOK_GLOB = os.path.join(
    "plugins", "cache", "*", "ai-coding-efficiency-kit", "*", "hooks")


def _hook_sources():
    """钩子来源：宿主 settings.json（自助安装），或插件清单（原生插件安装，不动 settings.json）。"""
    out = [(SETTINGS, None)]
    manifest = ("codex-hooks.json" if "codex" in os.path.basename(IDE_HOME).lower()
                else "qoder-hooks.json")
    for p in glob.glob(os.path.join(IDE_HOME, PLUGIN_HOOK_GLOB, manifest)):
        out.append((p, os.path.dirname(os.path.dirname(p))))
    codex_hooks = os.path.expanduser("~/.codex/hooks.json")
    if os.path.isfile(codex_hooks):
        out.append((codex_hooks, None))
    return out


def hook_registry():
    out = []
    for src, plugin_root in _hook_sources():
        try:
            s = json.load(open(src, encoding="utf-8"))
        except Exception:
            continue
        for ev, entries in (s.get("hooks") or {}).items():
            for e in entries or []:
                for h in (e.get("hooks") or []):
                    cmd = h.get("command") or ""
                    m = re.search(r"([^\s'\"]*harness_scan\.py)", cmd)
                    if not m:
                        continue
                    mode = "scan"
                    for cand in ("post-tool", "inject", "status", "daily", "scan"):
                        if re.search(r"harness_scan\.py'?\s+" + re.escape(cand) + r"\b", cmd):
                            mode = cand
                            break
                    if plugin_root:
                        ok = os.path.isfile(os.path.join(
                            plugin_root, "hooks", "task-metrics", "bin", "harness_scan.py"))
                    else:
                        ok = os.path.isfile(os.path.expanduser(m.group(1)))
                    out.append((ev, mode, ok))
    return out


def day_stats(day):
    recs = load_day(day)
    if not recs:
        return None
    n = len(recs)
    hot = [r for r in recs if r.get("line") in ("red", "yellow")]
    return {
        "tasks": n, "hot": len(hot),
        "red": sum(1 for r in hot if r.get("line") == "red"),
        "total": sum(act(r) for r in hot),
        "model": sum(r.get("model_seconds") or 0 for r in hot),
        "tool": sum(r.get("tool_seconds") or 0 for r in hot),
        "avg_thinking": sum(r.get("thinking_chars", 0) for r in recs) / n,
        "avg_reopen": sum(r.get("reopen_marks", 0) for r in recs) / n,
        "avg_plan": sum(r.get("plan_items", 0) for r in recs) / n,
        "blocked": sum(r.get("blocked_waits", 0) for r in recs),
    }


def peer_defaults():
    """对端信息：环境变量优先，其次多机协同配置；都没有则视为未启用对端。"""
    peer = os.environ.get("KIT_PEER_NAME", "")
    ssh = os.environ.get("KIT_PEER_SSH", "")
    cfg_path = os.environ.get("AGENTSYNC_CONFIG") or os.path.expanduser(
        "~/.agent-sync/config.env")
    try:
        for line in open(cfg_path, encoding="utf-8"):
            if line.startswith("PEER_NAME=") and not peer:
                peer = line.split("=", 1)[1].strip()
            elif line.startswith("PEER_SSH=") and not ssh:
                ssh = line.split("=", 1)[1].strip()
    except Exception:
        pass
    return peer, ssh


def ssh_ok(target, timeout=6):
    try:
        return subprocess.run(["ssh", "-o", f"ConnectTimeout={timeout}", "-o", "BatchMode=yes",
                               target, "true"], capture_output=True).returncode == 0
    except Exception:
        return False


def peer_fallback_path(peer):
    return os.path.join(PEERS_DIR, peer, ".fallback_ssh")


def refresh_fallback(peer, ssh_target):
    """链路通的时候把对端内网地址存下来，断了才有得用。"""
    try:
        out = subprocess.run(["ssh", "-o", "ConnectTimeout=6", "-o", "BatchMode=yes", ssh_target,
                              "ipconfig getifaddr en0 || ipconfig getifaddr en1 || true"],
                             capture_output=True, text=True).stdout

        ip = (out or "").strip().splitlines()[0] if out.strip() else ""
        if not ip.count(".") == 3:
            return
        skip_prefix = os.environ.get("KIT_PEER_TB_PREFIX", "")
        if skip_prefix and ip.startswith(skip_prefix):
            return
        user = ssh_target.split("@", 1)[0] if "@" in ssh_target else ""
        target = f"{user}@{ip}" if user else ip
        path = peer_fallback_path(peer)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        prev = open(path, encoding="utf-8").read().strip() if os.path.exists(path) else ""
        if prev != target:
            with open(path, "w", encoding="utf-8") as f:
                f.write(target)
            print(f"对端内网地址已更新 {prev or '-'} -> {target}")
    except Exception:
        pass


def resolve_target(peer, ssh_target):
    if ssh_ok(ssh_target):
        return ssh_target
    fb = peer_fallback_path(peer)
    if os.path.exists(fb):
        alt = open(fb, encoding="utf-8").read().strip()
        if alt and ssh_ok(alt):
            print(f"主链路 {ssh_target} 不可达，改用内网地址 {alt}")
            return alt
    return ssh_target


def pull(peer, ssh_target, deploy=False, rescan=False, days=8):
    dest = os.path.join(PEERS_DIR, peer)
    os.makedirs(dest, exist_ok=True)
    ssh_target = resolve_target(peer, ssh_target)
    if not ssh_ok(ssh_target):
        print(f"pull 失败：{ssh_target} 不可达（主链路与内网备用地址都连不上）")
        log_event("pull", peer=peer, rc=1, reason="unreachable")
        return 1
    ssh_opts = ["-o", "ConnectTimeout=8", "-o", "BatchMode=yes"]
    rsync_ssh = ["-e", "ssh -o ConnectTimeout=8 -o BatchMode=yes -o ServerAliveInterval=5"]
    if deploy:
        r = subprocess.run(["rsync", "-a", *rsync_ssh, os.path.abspath(__file__),
                            f"{ssh_target}:.qoder/task-metrics/bin/harness_scan.py"], timeout=120)
        print("deploy rc=", r.returncode)
    if rescan:
        r = subprocess.run(["ssh", *ssh_opts, ssh_target,
                            f"python3 ~/.qoder/task-metrics/bin/harness_scan.py backfill --days {days}"],
                           timeout=300)
        print("remote backfill rc=", r.returncode)
    subprocess.run(["ssh", *ssh_opts, ssh_target, "mkdir -p ~/.qoder/task-metrics/data/metrics"],
                   timeout=30)
    r = subprocess.run(["rsync", "-a", *rsync_ssh, "--include=*.jsonl", "--exclude=*",
                        f"{ssh_target}:.qoder/task-metrics/data/metrics/", dest + "/"], timeout=180)
    subprocess.run(["rsync", "-a", *rsync_ssh,
                    f"{ssh_target}:.qoder/task-metrics/data/events.jsonl",
                    os.path.join(dest, "tempo_events.jsonl")], timeout=60)
    if r.returncode == 0:
        with open(os.path.join(dest, ".last_pull"), "w", encoding="utf-8") as f:
            f.write(datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
        n = sum(1 for p in glob.glob(os.path.join(dest, "*.jsonl")) for _ in open(p))
        print(f"pull {peer}: {n} 条任务块 -> {dest}")
        refresh_fallback(peer, ssh_target)
    else:
        print(f"pull 失败 rc={r.returncode}")
    log_event("pull", peer=peer, rc=r.returncode)
    return r.returncode


def backfill_due(cfg):
    try:
        return time.time() - os.path.getmtime(os.path.join(DATA_DIR, ".last_backfill")) \
            >= cfg.get("backfill_interval_hours", 24) * 3600
    except Exception:
        return True


def maybe_backfill(cfg):
    """每天最多一次，在 Qoder 进程内补扫历史会话（钩子进程有转录读权限）。"""
    if not backfill_due(cfg):
        return 0
    stamp = os.path.join(DATA_DIR, ".last_backfill")
    try:
        if time.time() - os.path.getmtime(stamp) < cfg.get("backfill_interval_hours", 24) * 3600:
            return 0
    except Exception:
        pass
    cutoff = time.time() - cfg.get("backfill_days", 7) * 86400
    total = 0
    for f in glob.glob(os.path.expanduser("~/.qoder/projects/*/*.jsonl")) + codex_session_files():
        try:
            if os.path.getmtime(f) < cutoff:
                continue
            sid = codex_sid(f) if is_codex_path(f) else os.path.basename(f).rsplit(".", 1)[0]
            total += append_metrics(scan_file(f, sid))
        except Exception:
            continue
    try:
        with open(stamp, "w", encoding="utf-8") as fh:
            fh.write(datetime.now().strftime("%Y-%m-%dT%H:%M:%S"))
    except Exception:
        pass
    log_event("auto-backfill", records=total)
    return total


def tempo_turns(peer, day):
    """对端只有旧 tempo 事件时的粗粒度回合时长（无模型/工具拆分）。"""
    p = os.path.join(PEERS_DIR, peer, "tempo_events.jsonl")
    rows = []
    try:
        rows = [json.loads(l) for l in open(p, encoding="utf-8") if l.strip()]
    except Exception:
        return []
    cur, turns = {}, []
    for r in sorted(rows, key=lambda x: x.get("t") or 0):
        ev, sid = r.get("ev"), r.get("sid")
        if ev == "prompt":
            cur[sid] = {"start": r.get("t") or 0, "snip": clean_label(r.get("snippet") or "")[:28]}
        elif ev == "stop" and sid in cur:
            c = cur.pop(sid)
            turns.append({"at": c["start"], "seconds": max(0, (r.get("t") or 0) - c["start"]) / 1000,
                          "label": c["snip"]})
    out = []
    for t in turns:
        try:
            if datetime.fromtimestamp(t["at"] / 1000).strftime("%Y-%m-%d") == day:
                out.append(t)
        except Exception:
            continue
    return out


def peer_tempo_line(peer, day, yellow_min):
    turns = [t for t in tempo_turns(peer, day) if t["seconds"] >= yellow_min * 60]
    if not turns:
        return ""
    turns.sort(key=lambda t: -t["seconds"])
    parts = " / ".join(f"{datetime.fromtimestamp(t['at'] / 1000).strftime('%H:%M')} {fmt_min(t['seconds'])}"
                       for t in turns[:3])
    return f"{peer} 粗粒度 {len(turns)} 轮黄线以上：{parts}"


def status(day, cfg, oneline=False):
    hooks = hook_registry()
    st = day_stats(day) or {"tasks": 0, "hot": 0, "red": 0, "total": 0, "model": 0,
                            "tool": 0, "avg_thinking": 0, "avg_reopen": 0,
                            "avg_plan": 0, "blocked": 0}
    now = time.time()
    recent = load_events(now - 86400)
    by_kind = {}
    for e in recent:
        k = e.get("kind") or "?"
        d = by_kind.setdefault(k, {"n": 0, "last": 0, "hit": 0, "sent": 0})
        d["n"] += 1
        d["last"] = max(d["last"], e.get("t") or 0)
        d["hit"] += int(e.get("hit") or 0)
        d["sent"] += int(e.get("sent") or 0)

    if oneline:
        print(f"H {day[5:]} 黄{st['hot'] - st['red']}/红{st['red']} {fmt_min(st['total'])}"
              f" | scan{by_kind.get('scan', {}).get('n', 0)} 注入{by_kind.get('inject', {}).get('sent', 0)}")
        return

    print(f"Harness 状态  {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    if hooks:
        parts = " | ".join(f"{ev}→{mode}{'✓' if ok else '✗脚本缺失'}" for ev, mode, ok in hooks)
        print(f"组件  {len(hooks)} 处钩子：{parts}")
    else:
        print("组件  settings.json / 插件清单中未发现本脚本钩子 ✗")
    print(f"      阈值 黄 {cfg['yellow_line_minutes']}′ / 红 {cfg['red_line_minutes']}′"
          f" / 慢调用 {cfg['slow_tool_seconds']}s / 注入冷却 {cfg['inject_cooldown_hours']}h")
    if recent:
        bits = []
        for kind, label in (("scan", "scan"), ("inject", "注入"), ("post-tool", "PostTool"),
                            ("reminder", "进度提醒"), ("stop-retro", "强制复盘"),
                            ("notify", "桌面通知"), ("pull", "拉取")):
            d = by_kind.get(kind)
            if not d:
                continue
            extra = ""
            if kind == "inject":
                extra = f"（发出 {d['sent']}）"
            elif kind == "post-tool":
                extra = f"（sleep 拦截 {d['hit']}）"
            bits.append(f"{label} {d['n']} 次{extra}，最近 {fmt_ts(d['last'])}")
        print("近24h " + " | ".join(bits))
    else:
        print("近24h  无运行记录（本版起才记事件；钩子是否生效看本轮对话是否出现 [Harness] 文本）")

    print(f"今天  {st['tasks']} 个任务，黄线以上 {st['hot']}（红 {st['red']}），合计 {fmt_min(st['total'])}"
          f"；模型 {fmt_min(st['model'])} 工具 {fmt_min(st['tool'])}")
    todays = [r for r in load_day(day) if r.get("line") in ("red", "yellow")]
    all_today = load_day(day)
    hosts = {}
    for r in all_today:
        hosts[r["host"]] = hosts.get(r["host"], 0) + 1
    if len(hosts) > 1:
        print("      来源 " + " / ".join(f"{h} {n}" for h, n in sorted(hosts.items())))
    peer_dirs = [d for d in sorted(glob.glob(os.path.join(PEERS_DIR, "*"))) if os.path.isdir(d)]
    if peer_dirs:
        bits = []
        for d in peer_dirs:
            name = os.path.basename(d)
            n = sum(1 for r in all_today if r["host"] == name)
            stamp = ""
            try:
                stamp = "，上次拉取 " + open(os.path.join(d, ".last_pull"), encoding="utf-8").read().strip()[5:16]
            except Exception:
                stamp = "，尚未拉取"
            bits.append(f"{name} 今日 {n} 块{stamp}")
            if not n:
                coarse = peer_tempo_line(name, day, cfg["yellow_line_minutes"])
                if coarse:
                    bits.append(coarse)
        print("对端  " + " | ".join(bits))
    fam = {}
    for r in todays:
        for k, v in (r.get("families") or {}).items():
            fam[k] = fam.get(k, 0) + v
    if fam:
        print("      工具族 " + " / ".join(f"{k} {fmt_min(v)}"
                                          for k, v in sorted(fam.items(), key=lambda kv: -kv[1])[:4]))

    rules = load_rules() or DEFAULT_RULES
    print("规则  " + " / ".join(f"{r['id']} {r['name']}[{r['status']}]" for r in rules))

    routes = []
    for p in sorted(glob.glob(os.path.join(ROUTES_DIR, "*.jsonl"))):
        for line in open(p, encoding="utf-8"):
            try:
                routes.append(json.loads(line))
            except Exception:
                continue
    for r in routes[-2:]:
        print(f"调整  {r.get('id')} {clean_label(r.get('to') or '')}")

    print("趋势  日期    任务 黄+红  总时长  模型  工具  均思考  均重开  均计划  阻塞")
    days = []
    try:
        base = datetime.strptime(day, "%Y-%m-%d")
        for i in range(6, -1, -1):
            days.append((base - timedelta(days=i)).strftime("%Y-%m-%d"))
    except Exception:
        days = [day]
    suspect_days = []
    for d in days:
        s = day_stats(d)
        if not s:
            continue
        sus = sum(1 for r in load_day(d) if is_suspect(r))
        if sus:
            suspect_days.append(d[5:])
        print(f"      {d[5:]}  {s['tasks']:>4} {s['hot']:>4}  {fmt_min(s['total']):>6}"
              f" {fmt_min(s['model']):>5} {fmt_min(s['tool']):>5}"
              f" {s['avg_thinking']:>7.0f} {s['avg_reopen']:>6.1f} {s['avg_plan']:>6.1f} {s['blocked']:>5}"
              f"{'  ⚠' if sus else ''}")
    if suspect_days:
        print(f"      ⚠ {'、'.join(suspect_days)} 含跨天零空闲的长块（待对端回扫修正），该行时长偏高")


def load_turn_state():
    try:
        return json.load(open(TURN_STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_turn_state(st):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        st = dict(sorted(st.items(), key=lambda kv: -kv[1])[:200])
        with open(TURN_STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def turn_info(path):
    """本轮时长与起点：最近一次人类消息 → 转录末尾，返回 (秒数, 人类消息秒)。

    尾部按 256KB→2MB→16MB 放大读，找到人类消息即停：单块内容可能很大，
    固定小窗口会整段落在同一回合里，导致永远看不到人类消息。
    """
    human_ts, last_ts = 0.0, 0.0
    try:
        size = os.path.getsize(path)
    except Exception:
        return 0.0, 0.0
    for window in (262144, 2097152, 16777216):
        try:
            with open(path, "rb") as f:
                f.seek(max(0, size - window))
                chunk = f.read().decode("utf-8", "replace")
        except Exception:
            return 0.0, 0.0
        lines = chunk.splitlines()
        if size > window and lines:
            lines = lines[1:]
        human_ts, last_ts = 0.0, 0.0
        for line in lines:
            try:
                o = json.loads(line)
            except Exception:
                continue
            e = ts_epoch(o.get("timestamp") or "")
            if not e:
                continue
            last_ts = max(last_ts, e)
            if is_codex_path(path):
                for r in _codex_events(o):
                    if is_human_text(r):
                        human_ts = max(human_ts, ts_epoch(r.get("timestamp") or ""))
            elif is_human_text(o):
                human_ts = max(human_ts, e)
        if human_ts or window >= size:
            break
    if not human_ts or last_ts <= human_ts:
        return 0.0, human_ts
    return last_ts - human_ts, human_ts


def is_readonly_bash(cmd):
    """只读 Bash 判定：每段首 token 属于只读集合、且全串无写操作与落盘重定向。

    口径偏保守（宁可漏判、不误判）：heredoc、xargs、非 /dev/null 的重定向、
    解析不了的结构一律算「非只读」，避免把写操作算成导航。
    """
    cmd = (cmd or "").strip()
    if not cmd or "<<" in cmd:
        return False
    # 命令替换先剥壳，但壳里出现写操作照样判否（如 $(rm -rf x)）
    while True:
        m = CMD_SUB_RE.search(cmd)
        if not m:
            break
        inner = m.group(0).strip("$()` ")
        if any(t in WRITE_TOKENS for t in inner.split()) or ">" in inner:
            return False
        cmd = cmd[:m.start()] + " " + cmd[m.end():]
    body = HARMLESS_REDIR_RE.sub(" ", cmd)
    if ">" in body:
        return False
    for seg in SEG_SPLIT_RE.split(body):
        toks = seg.strip().split()
        while toks:
            t = toks[0]
            if re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):
                toks.pop(0)
                continue
            if t in ("cd", "sudo", "time", "nohup", "env", "command", "builtin"):
                toks.pop(0)
                while toks and toks[0].startswith("-"):
                    toks.pop(0)
                if t == "cd" and toks:
                    toks.pop(0)          # cd 的目录参数不是命令
                continue
            break
        if not toks:
            continue                     # 纯 cd / 纯变量赋值
        head = os.path.basename(toks[0])
        if head in WRITE_TOKENS or any(t in WRITE_TOKENS for t in toks[1:]):
            return False
        if head == "git":
            sub = next((t for t in toks[1:] if not t.startswith("-")), "")
            if sub not in GIT_READONLY_SUB:
                return False
            continue
        if head == "sed" and any(t.startswith("-i") for t in toks[1:]):
            return False
        if head == "find" and any(t in ("-delete", "-exec", "-execdir", "-ok") for t in toks):
            return False
        if head == "xargs" or head not in READONLY_CMDS:
            return False
    return True


TURN_COUNTER_DIR = os.path.join(DATA_DIR, ".turn_counter")


def counter_path(sid):
    safe = re.sub(r"[^A-Za-z0-9_-]", "_", sid or "unknown")
    return os.path.join(TURN_COUNTER_DIR, safe + ".json")


def load_counter(sid):
    """本轮计数器；超过 8 小时视为陈旧（漏了 UserPromptSubmit），返回 None 走转录回退。"""
    try:
        d = json.load(open(counter_path(sid), encoding="utf-8"))
        if time.time() - float(d.get("human") or 0) > 8 * 3600:
            return None
        return d
    except Exception:
        return None


def save_counter(sid, d):
    try:
        os.makedirs(TURN_COUNTER_DIR, exist_ok=True)
        p = counter_path(sid)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f)
        os.replace(tmp, p)
    except Exception:
        pass


def reset_counter(sid):
    """UserPromptSubmit 调用。返回 True 表示这是本会话首次提示（计数器原本不存在）。"""
    first = not os.path.exists(counter_path(sid))
    save_counter(sid, {"human": time.time(), "total": 0, "nav": 0, "scan": 0,
                       "lsp": 0, "sws": 0, "bash": 0, "ro_bash": 0, "deploy": 0})
    return first


def bump_counter(sid, name, inp):
    """PostToolUse 先自增再判定，避免「本次调用还没写进转录」的漏计。"""
    d = load_counter(sid)
    if d is None:
        d = {"human": time.time(), "total": 0, "nav": 0, "scan": 0,
             "lsp": 0, "sws": 0, "bash": 0, "ro_bash": 0, "deploy": 0}
    d["total"] = d.get("total", 0) + 1
    k = tool_kind(name)
    cmd = str(inp.get("command") or "") if isinstance(inp, dict) else ""
    if k in ("scan", "lsp", "sws"):
        d[k] = d.get(k, 0) + 1
    if k in ("scan", "lsp", "sws"):
        d["nav"] = d.get("nav", 0) + 1
    if name in BASH_TOOLS:
        d["bash"] = d.get("bash", 0) + 1
        if cmd and is_readonly_bash(cmd):
            d["ro_bash"] = d.get("ro_bash", 0) + 1
            d["nav"] = d.get("nav", 0) + 1
        if cmd and DEPLOY_CMD_RE.search(cmd.split("<<")[0]):
            d["deploy"] = d.get("deploy", 0) + 1
    save_counter(sid, d)
    return d


def tool_kind(name):
    """工具归类：scan=逐文件摸（Read/Grep/Glob 类），lsp=语言服务器桥，sws=语义检索。"""
    n = re.sub(r"[^a-z]", "", (name or "").lower())
    if "jdtls" in n or n.startswith("lsp"):
        return "lsp"
    if n.startswith("searchworkspace") or n.startswith("searchknowledge"):
        return "sws"
    if n in ("read", "readfile", "grep", "glob", "grepsearch", "findfiles", "listdir",
             "searchfile", "searchfiles", "listdirectory"):
        return "scan"
    if n in ("references", "definition", "hover", "renamesymbol", "diagnostics",
             "gotodefinition", "findreferences", "workspacesymbols", "documenthighlights"):
        return "lsp"
    return "other"


def turn_tool_mix(path):
    """本轮（最近一条人类消息之后）工具调用构成，用于 RULE-004/005 判定。"""
    mix = {"total": 0, "scan": 0, "lsp": 0, "sws": 0, "deploy": 0, "bash": 0,
           "nav": 0, "ro_bash": 0}
    try:
        size = os.path.getsize(path)
    except Exception:
        return mix
    calls = []
    for window in (262144, 2097152, 16777216):
        try:
            with open(path, "rb") as f:
                f.seek(max(0, size - window))
                chunk = f.read().decode("utf-8", "replace")
        except Exception:
            return mix
        lines = chunk.splitlines()
        if size > window and lines:
            lines = lines[1:]
        calls = []
        for line in lines:
            try:
                o = json.loads(line)
            except Exception:
                continue
            text = is_human_text(o)
            if text:
                if not label_is_injection(text):
                    calls = []
                continue
            msg = o.get("message") or {}
            content = msg.get("content")
            if msg.get("role") != "assistant" or not isinstance(content, list):
                continue
            for x in content:
                if not isinstance(x, dict) or x.get("type") != "tool_use":
                    continue
                inp = x.get("input") if isinstance(x.get("input"), dict) else {}
                calls.append((x.get("name") or "", str(inp.get("command") or ""),
                              json.dumps(inp, sort_keys=True, ensure_ascii=False)))
        if calls or window >= size:
            break
    for name, cmd, sig in calls:
        mix["total"] += 1
        k = tool_kind(name)
        if k in mix:
            mix[k] += 1
        if k in ("scan", "lsp", "sws"):
            mix["nav"] += 1
        if name in BASH_TOOLS:
            mix["bash"] += 1
            if cmd and is_readonly_bash(cmd):
                mix["ro_bash"] += 1
                mix["nav"] += 1
            if cmd and DEPLOY_CMD_RE.search(cmd.split("<<")[0]):
                mix["deploy"] += 1
    mix["last"] = calls[-1][0] + ":" + calls[-1][2][:300] if calls else ""
    return mix


def once_flag(sid, human, tag, cooldown):
    """同一回合同一类提醒的节流；成功后返回 True。"""
    key = f"{sid}:{int(human)}:{tag}"
    nst = load_notify_state()
    if key in nst and time.time() - float(nst[key].get("t", 0)) < cooldown:
        return False
    nst[key] = {"t": time.time()}
    save_notify_state(nst)
    return True


def load_notify_state():
    try:
        return json.load(open(NOTIFY_STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_notify_state(st):
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        st = dict(list(st.items())[-200:])
        with open(NOTIFY_STATE, "w", encoding="utf-8") as f:
            json.dump(st, f)
    except Exception:
        pass


def notify(title, body):
    """macOS 桌面通知：给用户在任务进行中就看到，不必等下一条消息。"""
    try:
        subprocess.Popen(
            ["osascript", "-e",
             f"display notification {json.dumps(body, ensure_ascii=False)}"
             f" with title {json.dumps(title, ensure_ascii=False)}"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except Exception:
        pass


def post_tool_reminder(data):
    if not solution_on("inline-reminders"):
        return ""
    cfg = load_config()
    name = pick(data, "tool_name", "toolName", "tool")
    inp = data.get("tool_input") or data.get("toolInput") or {}
    cmd = str(inp.get("command") or "") if isinstance(inp, dict) else ""
    sid = pick(data, "session_id", "sessionId")
    path = find_transcript(sid, pick(data, "transcript_path", "transcriptPath")) if sid else None
    if not path:
        # Codex Desktop 的 hook id 与 rollout 文件名不是同一个，按「刚写过的会话」兜底
        path = codex_recent_transcript()

    # RULE-003：前台 sleep 干等（heredoc 里的脚本文本不算）
    probe = cmd.split("<<")[0] if "<<" in cmd else cmd
    for m in SLEEP_CMD_RE.findall(probe):
        try:
            if float(m) >= 20:
                log_event("post-tool", hit=1, tag="sleep", sleep=m)
                return ("[Harness 提醒] 前台 sleep 等待会把整轮卡住。下次改后台命令"
                        "（run_in_background），完成时会收到通知，期间继续做别的。")
        except ValueError:
            pass

    cnt = bump_counter(sid, name, inp) if sid else None
    if not path:
        log_event("post-tool", hit=0, session=sid, tool=name,
                  hint=bool(pick(data, "transcript_path", "transcriptPath")))
        return ""
    el, human = turn_info(path)

    # RULE-005：部署/构建落前台 → 排队时间该用来写代码
    if (probe and DEPLOY_CMD_RE.search(probe)
            and not (isinstance(inp, dict) and inp.get("run_in_background"))):
        if once_flag(sid, human, "deploy-first", 1800):
            log_event("reminder", tag="deploy-first", session=sid)
            return ("[Harness 提醒] 部署/构建已在前台起：结果交给 "
                    "`python3 ~/.qoder/task-metrics/bin/deploy_watch.py --env <env> --apre <apre>` "
                    "或后台任务拿，排队这几分钟先写代码/补用例，别盯输出。")

    # RULE-004：导航多、语义检索少 → 先定位再读
    # 计数器优先（O(1)、不受转录大小影响）；缺失时回退转录尾窗。
    mix = cnt if cnt else turn_tool_mix(path)
    nav_n = mix.get("nav", 0)
    if nav_n >= 12 and mix.get("sws", 0) + mix.get("lsp", 0) == 0:
        tier = 2 if nav_n >= 24 else 1
        if once_flag(sid, human, f"locate-{tier}", 86400):
            log_event("reminder", tag="locate-first", session=sid,
                      nav=nav_n, scan=mix.get("scan", 0), ro_bash=mix.get("ro_bash", 0))
            return (f"[Harness 提醒] 本轮已 {nav_n} 次导航（Read/Grep/Glob + 只读 Bash）、"
                    "语言服务器和语义检索 0 次。下一条先一次到位：库里若有 lsp-* 工具，"
                    "先 references/definition 拿全部命中点；没有就 rg \"符号\" -A 6 一次拿全，"
                    "或一次 SearchWorkspace 顶掉 5 次逐文件翻；命中行再精读，"
                    "别再用 sed/head 分段读同一个文件。")

    # 轮内时长提醒：只在超过黄线后触发，同一会话每 6 分钟最多一次
    if el >= cfg["yellow_line_minutes"] * 60:
        key = f"{sid}:{int(human)}"
        nst = load_notify_state()
        if "yellow" not in nst.get(key, {}):
            nst.setdefault(key, {})["yellow"] = time.time()
            save_notify_state(nst)
            log_event("notify", channel="desktop", minutes=round(el / 60, 1))
            notify("任务已超黄线",
                   f"本轮已 {el / 60:.0f}′（黄线 {cfg['yellow_line_minutes']}′）。"
                   "要改需求或让 agent 先交付当前部分，现在说最省时。")
        st = load_turn_state()
        if time.time() - st.get(sid, 0) >= 360:
            st[sid] = time.time()
            save_turn_state(st)
            log_event("reminder", tag="long-turn", session=sid, minutes=round(el / 60, 1))
            if el >= cfg["red_line_minutes"] * 60:
                head = f"已连续 {el / 60:.0f}′，超过红线 {cfg['red_line_minutes']}′"
                tail = "先停手做三行复盘（超时点/根因/下次怎么拆并行），再继续。"
            else:
                head = f"已连续 {el / 60:.0f}′，超过黄线 {cfg['yellow_line_minutes']}′"
                tail = "先输出一行『已完成 X / 正在 Y / 还差 Z』，长命令转后台。"
            return f"[Harness 进度提醒] 本轮{head}。{tail}"
    log_event("post-tool", hit=0)
    return ""


def stop_gate(data):
    """Stop 时判定：≥10′ 发桌面通知；≥30′ 阻止收工、逼当场写三行复盘。

    每个用户回合只拦一次（键 = session:人类消息时间），避免自我循环。
    """
    if not solution_on("inline-reminders"):
        return ""
    cfg = load_config()
    sid = pick(data, "session_id", "sessionId")
    path = find_transcript(sid, pick(data, "transcript_path", "transcriptPath")) if sid else None
    if not path:
        return ""
    el, human = turn_info(path)
    if el < cfg["yellow_line_minutes"] * 60:
        return ""
    red_s = float(os.environ.get("HARNESS_RED_SECONDS") or cfg["red_line_minutes"] * 60)
    key = f"{sid}:{int(human)}"
    nst = load_notify_state()
    entry = nst.setdefault(key, {})
    if "yellow" not in entry:
        entry["yellow"] = time.time()
        log_event("notify", channel="desktop", minutes=round(el / 60, 1))
        body = (f"本轮共 {el / 60:.0f}′，{'超红线' if el >= cfg['red_line_minutes'] * 60 else '超黄线'}。" 
                "复盘已就绪，回来看一眼即可。")
        notify("本轮超线了", body)
    if el < red_s or "stop" in entry:
        save_notify_state(nst)
        return ""
    entry["stop"] = time.time()
    save_notify_state(nst)
    log_event("stop-retro", session=sid, minutes=round(el / 60, 1))
    return json.dumps({
        "decision": "block",
        "reason": (
            f"[Harness 红线] 本块已 {el / 60:.0f} 分钟（红线 {red_s / 60:.0f}′），"
            "不能直接收工。先在对话里输出三行复盘：① 超时点（时间花在哪个环节）"
            "② 根因（设计反复 / 串行等待 / 老坑重踩，对照 fast-loop）"
            "③ 同类任务下次怎么拆分并行；并说明『当前已交付什么 / 还差什么』。写完即可结束。"
        ),
    }, ensure_ascii=False)


def load_rules():
    try:
        return [json.loads(l) for l in open(RULES, encoding="utf-8") if l.strip()]
    except Exception:
        return []


def save_rules(rows):
    os.makedirs(DATA_DIR, exist_ok=True)
    with open(RULES, "w", encoding="utf-8") as f:
        for r in rows:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")


DEFAULT_RULES = [
    {"id": "RULE-001", "name": "决定锁定",
     "rule": "决定一旦写进 decisions 或任务清单，不在思考里重开；翻案须引新事实并写回文件",
     "detection": "思考中重开标记数（reopen_marks）",
     "intervention": "UserPromptSubmit 注入决定基线（已存在）",
     "process_metric": "reopen_marks / 任务", "outcome_metric": "用户打断或纠正次数",
     "status": "trial", "since": "2026-10-26", "evidence": "F-2026-10-26-01"},
    {"id": "RULE-002", "name": "计划外置",
     "rule": "先做什么后做什么写进任务清单，思考里只留当前一步；不预写文件级改动清单",
     "detection": "思考中编号计划项数（plan_items）",
     "intervention": "提示：计划进任务清单",
     "process_metric": "plan_items / 任务", "outcome_metric": "首次可验证证据分钟数",
     "status": "trial", "since": "2026-10-26", "evidence": "F-2026-10-26-01"},
    {"id": "RULE-003", "name": "长命令后台",
     "rule": "单次耗时 ≥60s 的命令（构建/部署/日志/等待）一律 run_in_background；不做 20 秒以上的 sleep 轮询；结果用完成通知或 deploy_watch 拿",
     "detection": "sleep>=20 的命令数（blocked_waits）、单次 ≥120s 的慢调用数（slow_tools）",
     "intervention": "PostToolUse 对 sleep>=20 即时提示；UserPromptSubmit 注入上任务慢调用数",
     "process_metric": "blocked_waits + slow_tools / 任务", "outcome_metric": "单命令最长前台等待秒数",
     "status": "trial", "since": "2026-10-26", "evidence": "F-2026-10-28-M1"},
    {"id": "RULE-004", "name": "先定位再读",
     "rule": "改代码前先定位：jdtls 桥 references/definition（穷举调用点）或 SearchWorkspace（语义命中），再 Read 精读命中行；不 grep 后逐文件通读",
     "detection": "本轮 Read/Grep/Glob ≥8 次且 jdtls+SearchWorkspace 为 0（tool_kind 计数）",
     "intervention": "PostToolUse 即时提示（8 次/20 次两档）",
     "process_metric": "逐文件摸调用数 / 块、LSP 调用占比", "outcome_metric": "≥40 调用块数、read+search 占当日比例",
     "status": "trial", "since": "2026-10-28", "evidence": "F-2026-10-28-M1"},
    {"id": "RULE-005", "name": "部署排队前移",
     "rule": "开工先起构建/预发部署，排队时间写代码；部署结果用 deploy_watch 或后台任务拿，不盯输出",
     "detection": "deploy/build 族耗时占当日比例；前台部署命令（未 run_in_background）",
     "intervention": "PostToolUse 对前台部署/构建命令即时提示（同一回合 30′ 一次）",
     "process_metric": "deploy+build 占当日比例、前台部署次数", "outcome_metric": "deploy 占比目标 <5%",
     "status": "trial", "since": "2026-10-28", "evidence": "F-2026-10-28-M1"},
]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", nargs="?", default="scan",
                    choices=["scan", "inject", "post-tool", "report", "rules",
                             "daily", "backfill", "status", "pull", "auto-backfill"])
    ap.add_argument("--days", type=int, default=1)
    ap.add_argument("--date", default=datetime.now().strftime("%Y-%m-%d"))
    ap.add_argument("--all", action="store_true", dest="show_all")
    ap.add_argument("--oneline", action="store_true", help="status 单行输出（状态栏用）")
    ap.add_argument("--host", default="", help="只看某台机器（daily/status）")
    ap.add_argument("--since", default="",
                    help="daily 只统计该时刻之后的块，可跨日（如 2026-10-27T17:00 或 17:00 表示当天 17:00）")
    ap.add_argument("--peer", default="", help="对端机器名（pull）")
    ap.add_argument("--ssh", default="", help="对端 SSH 目标（pull）")
    ap.add_argument("--deploy", action="store_true", help="pull 前先把本脚本推到对端")
    ap.add_argument("--rescan", action="store_true", help="pull 前在对端跑一次 backfill")
    ap.add_argument("--file", help="会话 jsonl 路径（手工分析用）")
    ap.add_argument("--session", help="会话 id（默认取 hook 输入）")
    ap.add_argument("--last", type=int, default=5)
    ap.add_argument("--print", action="store_true", dest="print_only",
                    help="只打印，不落盘")
    ap.add_argument("--json", action="store_true", help="rules list 以 JSON 输出")
    ap.add_argument("rest", nargs="*", help="rules: list|add|status 及其参数")
    args = ap.parse_args()
    cfg = load_config()

    if args.mode == "rules":
        rows = load_rules() or DEFAULT_RULES
        sub = args.rest[0] if args.rest else "list"
        if sub == "list":
            if args.json:
                print(json.dumps(rows, ensure_ascii=False, indent=2))
            else:
                for r in rows:
                    print(f"{r['id']} [{r['status']}] {r['name']} — {r['rule']}")
            return
        if sub == "add" and len(args.rest) > 1:
            row = json.loads(args.rest[1])
            rows.append(row)
            save_rules(rows)
            print("added", row.get("id"))
            return
        if sub == "status" and len(args.rest) > 2:
            for r in rows:
                if r["id"] == args.rest[1]:
                    r["status"] = args.rest[2]
            save_rules(rows)
            print(rows and "ok")
            return
        print("usage: rules list | rules add '<json>' | rules status <id> <trial|active|retired>")
        return

    if args.mode == "auto-backfill":
        maybe_backfill(cfg)
        return

    if args.mode == "backfill":
        cutoff = time.time() - args.days * 86400
        files = [f for f in glob.glob(os.path.expanduser("~/.qoder/projects/*/*.jsonl"))
                 + codex_session_files() if os.path.getmtime(f) >= cutoff]
        total = 0
        for f in files:
            sid = codex_sid(f) if is_codex_path(f) else os.path.basename(f).rsplit(".", 1)[0]
            try:
                total += append_metrics(scan_file(f, sid))
            except Exception:
                continue
        log_event("backfill", files=len(files), records=total)
        print(f"backfill: {len(files)} 个会话，更新 {total} 条任务块")
        return

    if args.mode == "daily":
        since = args.since
        if since and "T" not in since:
            since = f"{args.date}T{since}"
        daily(args.date, cfg, args.show_all, args.json, args.host, since)
        return

    if args.mode == "status":
        status(args.date, cfg, args.oneline)
        return

    if args.mode == "pull":
        pname, pssh = peer_defaults()
        peer = args.peer or pname
        ssh_target = args.ssh or pssh
        if not ssh_target:
            print("缺少 --ssh 或 ~/.agent-sync/config.env 的 PEER_SSH")
            return
        pull(peer, ssh_target, args.deploy, args.rescan, args.days if args.days > 1 else 8)
        return

    if args.mode == "report":
        recs = []
        for p in sorted(glob.glob(os.path.join(METRICS_DIR, "*.jsonl"))):
            if p.endswith("injected.jsonl"):
                continue
            for line in open(p, encoding="utf-8"):
                try:
                    recs.append(json.loads(line))
                except Exception:
                    continue
        recs.sort(key=lambda r: (r.get("start") or "", r.get("id") or ""))
        for r in recs[-args.last:]:
            print(f"{r['start']} {r['id']}  {fmt_min(act(r))} "
                  f"模型{fmt_min(r.get('model_seconds', 0))} 工具{fmt_min(r.get('tool_seconds', 0))} "
                  f"思考{r['thinking_chars']}/回复{r['reply_chars']} 阻塞{r['blocked_waits']} "
                  f"重开{r['reopen_marks']} 计划{r['plan_items']} {','.join(r['flags'])}")
        return

    data = read_stdin()

    if args.mode == "post-tool":
        msg = ""
        try:
            msg = post_tool_reminder(data)
        except Exception as e:
            log_event("error", mode="post-tool", reason=type(e).__name__)
        extra = solution_output("post-tool-use", data)
        text = "\n\n".join(x for x in (msg, extra) if x)
        if text:
            # PostToolUse 的裸 stdout 不进模型上下文，必须按钩子规范用 additionalContext
            print(json.dumps({"hookSpecificOutput": {"hookEventName": "PostToolUse",
                                                     "additionalContext": text}},
                             ensure_ascii=False))
        return

    sid = args.session or pick(data, "session_id", "sessionId") or "unknown"
    path = find_transcript(sid, args.file or pick(data, "transcript_path", "transcriptPath"))

    if args.mode == "inject":
        try:
            msg = inject(sid, cfg)
        except Exception as e:
            log_event("error", mode="inject", reason=type(e).__name__)
            return
        if msg:
            print(msg)
        return

    if not path:
        # Codex Desktop 的 hook session_id 与 rollout 文件名不是同一个 id，按「刚写过的会话」兜底
        path = codex_recent_transcript()
        if path:
            sid = codex_sid(path)
    if not path:
        log_event("scan", session=sid, records=0, reason="no-transcript")
        return
    try:
        records = scan_file(path, sid if sid != "unknown" else os.path.basename(path).rsplit(".", 1)[0])
    except Exception as e:
        log_event("scan", session=sid, records=0, reason=type(e).__name__)
        return
    if args.print_only:
        print(json.dumps(records, ensure_ascii=False, indent=2))
    else:
        append_metrics(records)
        log_event("scan", session=sid, records=len(records),
                  event=pick(data, "hook_event_name", "hookEventName") or "",
                  hot=sum(1 for r in records if r.get("line")))
        if backfill_due(cfg):
            subprocess.Popen([sys.executable, os.path.abspath(__file__), "auto-backfill"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
        if pick(data, "hook_event_name", "hookEventName") == "Stop":
            gate = stop_gate(data)
            if gate:
                print(gate)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
