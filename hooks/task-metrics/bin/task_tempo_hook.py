#!/usr/bin/env python3
"""任务节奏打点 hook。

用法（由 ~/.qoder/settings.json 的 hooks 调用）:
  task_tempo_hook.py user-prompt-submit   # 任务块起点：记录 + 超时红线检查 + 压力协议 + 决策落盘提醒
  task_tempo_hook.py stop                 # 助手回合结束：记录
  task_tempo_hook.py session-start        # 会话开始：注入已拍板决策基线（压缩后重注入同一通道）

约定：fail-open，任何异常静默退出 0；永不阻塞、永不改写输入。
数据：~/.qoder/task-metrics/data/events.jsonl；决策落盘 data/decisions/<date>.md
"""
import json
import os
import re
import subprocess
import sys
import time
from datetime import datetime

DATA_DIR = os.path.expanduser(os.environ.get("KIT_DATA_DIR") or os.path.join(
    os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "task-metrics", "data"))
EVENTS = os.path.join(DATA_DIR, "events.jsonl")
DECISIONS_DIR = os.path.join(DATA_DIR, "decisions")
RETROS_DIR = os.path.join(DATA_DIR, "retros")
DETOURS_DIR = os.path.join(DATA_DIR, "detours")
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


def run_solution_hooks(event, data):
    """方案的 hook.py 走固定入口分发；没有启用的方案时什么都不做。"""
    try:
        r = subprocess.run(
            [sys.executable, os.path.join(SOLUTIONS_DIR, "dispatch.py"), event],
            input=json.dumps(data, ensure_ascii=False), capture_output=True, text=True, timeout=6)
        return r.stdout.strip()
    except Exception:
        return ""


REDLINE_MIN = float(os.environ.get("TASK_TEMPO_REDLINE_MIN", "30"))

PRESSURE_RE = re.compile(
    r"只剩|来不及|倒计时|抓紧|赶时间|快快|赶不及|马上要开|马上要交|deadline|死线",
    re.IGNORECASE,
)
PRESSURE_COOLDOWN_MS = 5 * 60 * 1000
DAY_MS = 24 * 60 * 60 * 1000

PRESSURE_MSG = (
    "[时间压力协议] 检测到时间压力，第一动作是取舍而不是加速：\n"
    "① 给「保 / 砍 / 默认执行」三行选项 ② 明示放弃项 "
    "③ 禁碰已声明红线（契约快照/兜底造值等，以项目层声明为准）——捷径省 5 分钟，被拦返工赔 1 小时。等拍板再动。"
)


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


def extract(data):
    sid = pick(data, "session_id", "sessionId", "conversation_id") or "unknown"
    prompt = pick(data, "prompt", "user_prompt", "text")
    if not prompt:
        c = data.get("content") or data.get("message")
        if isinstance(c, list):
            prompt = " ".join(
                p.get("text", "") for p in c if isinstance(p, dict) and p.get("type") == "text"
            ).strip()
    return sid, prompt


def load_events():
    try:
        with open(EVENTS, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []


def append(ev):
    os.makedirs(DATA_DIR, exist_ok=True)
    try:
        with open(EVENTS, "a", encoding="utf-8") as f:
            f.write(json.dumps(ev, ensure_ascii=False) + "\n")
    except Exception:
        pass


def day_of(ms):
    try:
        return datetime.fromtimestamp(ms / 1000).strftime("%Y-%m-%d")
    except Exception:
        return ""


def today():
    return day_of(time.time() * 1000)


def pressure_check(now, sid, prompt):
    if not prompt or not PRESSURE_RE.search(prompt):
        return ""
    injectable = True
    for e in reversed(load_events()):
        if (
            e.get("ev") == "pressure"
            and e.get("sid") == sid
            and e.get("injected")
        ):
            if now - e.get("t", 0) < PRESSURE_COOLDOWN_MS:
                injectable = False
            break
    append({"t": now, "ev": "pressure", "sid": sid, "injected": injectable,
            "snippet": prompt[:80]})
    return PRESSURE_MSG if injectable else ""


def decisions_nudge(now, sid):
    day = today()
    if os.path.exists(os.path.join(DECISIONS_DIR, day + ".md")):
        return ""
    first_today = None
    for e in load_events():
        if day_of(e.get("t", 0)) != day:
            continue
        if e.get("ev") == "decnudge":
            return ""
        if e.get("ev") == "prompt" and (first_today is None or e["t"] < first_today):
            first_today = e["t"]
    if first_today is None or now - first_today < REDLINE_MIN * 60000:
        return ""
    append({"t": now, "ev": "decnudge", "sid": sid})
    return (
        "[决策落盘提醒] 今日开工已超 " + str(int(REDLINE_MIN)) + " 分钟且尚无决策记录。"
        "把已拍板项落盘：\n"
        f"~/.qoder/task-metrics/data/decisions/{day}.md（|时间|决策|理由|状态|），"
        "此后已拍板项不重开辩论。"
    )


def decisions_baseline():
    found = []
    for off in (0, 1):
        day = day_of(time.time() * 1000 - off * DAY_MS)
        path = os.path.join(DECISIONS_DIR, day + ".md")
        try:
            with open(path, encoding="utf-8") as f:
                rows = [ln.rstrip() for ln in f if ln.strip().startswith("|")]
        except Exception:
            continue
        if rows:
            found.append((os.path.basename(path), rows))
    if not found:
        return ""
    out = ["[决策基线] 已拍板项不重开辩论，除非出现新事实；翻案须写回 decisions 文件。"]
    for name, rows in found:
        pending = [r for r in rows if "待定" in r]
        if pending:
            out.append(f"待定项（{name}）：")
            out.extend("  " + p for p in pending)
    return "\n".join(out)


def lsp_autoconfig(cwd):
    """会话首次落在某仓时，按主语言把 LSP 挂进该仓的 .mcp.json。"""
    try:
        here = os.path.dirname(os.path.abspath(__file__))
        r = subprocess.run(
            [sys.executable, os.path.join(here, "lsp_autoconfig.py"), "--workspace", cwd],
            capture_output=True, text=True, timeout=5)
        return r.stdout.strip()
    except Exception:
        return ""


def transcript_file(sid):
    if not sid:
        return ""
    root = os.path.join(os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "projects")
    try:
        for slug in os.listdir(root):
            p = os.path.join(root, slug, sid + ".jsonl")
            if os.path.isfile(p):
                return p
    except Exception:
        pass
    return ""


def transcript_workspaces(sid):
    """hook 事件里没有 cwd 时，从会话转录首行 workspace-directories 反推仓库。"""
    p = transcript_file(sid)
    if not p:
        return []
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            for i, line in enumerate(f):
                try:
                    e = json.loads(line)
                except Exception:
                    continue
                if e.get("type") == "workspace-directories":
                    return [d for d in (e.get("directories") or []) if os.path.isdir(d)]
                if i > 5:
                    break
    except Exception:
        pass
    return []


RETRO_MARKERS = ("超时点", "根因", "下次", "兑现")


def tail_chunk(sid, size=262144):
    p = transcript_file(sid)
    if not p:
        return ""
    try:
        # Stop 每轮都跑，只读尾部，避免整份转录反复解码
        with open(p, "rb") as f:
            f.seek(0, os.SEEK_END)
            f.seek(max(0, f.tell() - size))
            return f.read().decode("utf-8", "ignore")
    except Exception:
        return ""


def last_assistant_text(sid):
    """转录里最后一条助手文本；复盘通常写在触发告警后的那一轮回复里。"""
    chunk = tail_chunk(sid)
    if not chunk:
        return ""
    for line in reversed(chunk.splitlines()):
        try:
            e = json.loads(line)
        except Exception:
            continue
        if e.get("type") != "assistant":
            continue
        c = (e.get("message") or {}).get("content")
        if isinstance(c, str):
            return c
        if isinstance(c, list):
            return "\n".join(x.get("text", "") for x in c if isinstance(x, dict))
    return ""


def capture_retro(sid, now, prev):
    """Stop 时留存复盘原文：最近一条红线告警 + 本轮助手回复里出现复盘标记。"""
    red = [e for e in prev if e.get("ev") == "redline"
           and now - e.get("t", 0) <= 2 * 3600 * 1000]
    if not red:
        return
    last = red[-1]
    if any(e.get("ev") == "retro" and e.get("block_start") == last.get("block_start") for e in prev):
        return
    text = last_assistant_text(sid)
    if not text or sum(1 for m in RETRO_MARKERS if m in text) < 2:
        return
    rec = {"t": now, "ts": datetime.now().isoformat(timespec="seconds"), "sid": sid,
           "block_start": last.get("block_start"), "minutes": last.get("minutes"),
           "work_minutes": last.get("work_minutes"), "wait_minutes": last.get("wait_minutes"),
           "away_minutes": last.get("away_minutes"),
           "chars": len(text),
           "markers": [m for m in RETRO_MARKERS if m in text],
           "text": text[:6000]}
    try:
        os.makedirs(RETROS_DIR, exist_ok=True)
        with open(os.path.join(RETROS_DIR, datetime.now().date().isoformat() + ".jsonl"),
                  "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        append({"t": now, "ev": "retro", "sid": sid, "block_start": last.get("block_start")})
    except Exception:
        pass


DETOUR_RE = re.compile(r"走了弯路\s*[：:]\s*([^\n]{1,120}?)\s*(?:→|->|⇒)\s*([^\n]{1,200})")
ERROR_RE = re.compile(r'"is_error"\s*:\s*true')
RECOVER_RE = re.compile(r"(验证|复跑|通过|证据|实测|测试绿|恢复|不再报错|已解决)")
DETOUR_RULE = (
    "[弯路信号] 同一目标连续两次失败、或同一工具连续报错时，先声明再换路，一行即可：\n"
    "走了弯路：<卡在哪> → <改走什么>\n"
    "声明会在回合结束时被采集，用来比对「声明后是否真的换路并恢复」。"
)


def detour_rule(now, sid, prev):
    if any(e.get("ev") == "detour-rule" and e.get("sid") == sid for e in prev):
        return ""
    append({"t": now, "ev": "detour-rule", "sid": sid})
    return DETOUR_RULE


def capture_detour(sid, now, prev):
    """Stop 时采集弯路声明：现象 → 改走什么，附尾部窗口失败数与声明后的恢复判据。"""
    text = last_assistant_text(sid)
    if not text or "走了弯路" not in text:
        return
    block_start = None
    for e in reversed(prev):
        if e.get("ev") == "prompt":
            block_start = e.get("t")
            break
    errors = len(ERROR_RE.findall(tail_chunk(sid)))
    wrote = False
    for m in DETOUR_RE.finditer(text):
        phen = re.sub(r"\s+", " ", m.group(1)).strip()[:120]
        nxt = re.sub(r"\s+", " ", m.group(2)).strip()[:200]
        if not phen or not nxt:
            continue
        key = f"{block_start}:{phen}"
        if any(e.get("ev") == "detour" and e.get("key") == key for e in prev):
            continue
        rec = {"t": now, "ts": datetime.now().isoformat(timespec="seconds"), "sid": sid,
               "block_start": block_start, "phenomenon": phen, "next": nxt,
               "errors_in_tail": errors,
               "claim": bool(RECOVER_RE.search(nxt)),
               "verified": bool(RECOVER_RE.search(text[m.end():m.end() + 600]))}
        try:
            os.makedirs(DETOURS_DIR, exist_ok=True)
            with open(os.path.join(DETOURS_DIR, datetime.now().date().isoformat() + ".jsonl"),
                      "a", encoding="utf-8") as f:
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            append({"t": now, "ev": "detour", "sid": sid, "block_start": block_start, "key": key})
            wrote = True
        except Exception:
            pass
    return wrote


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = read_stdin()
    sid, prompt = extract(data)
    now = int(time.time() * 1000)

    if mode == "stop":
        append({"t": now, "ev": "stop", "sid": sid})
        prev = [e for e in load_events() if e.get("sid") == sid]
        capture_retro(sid, now, prev)
        if solution_on("detour-signal"):
            capture_detour(sid, now, prev)
        extra = run_solution_hooks("stop", data)
        if extra:
            print(extra)
        return

    if mode == "session-end":
        if sid == "unknown":
            tp = pick(data, "transcript_path", "transcriptPath")
            sid = os.path.basename(tp).rsplit(".", 1)[0] if tp else ""
        if not sid:
            return
        try:
            sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
            import taskfacts
            taskfacts.build(days=7, session=sid, quiet=True)
        except Exception:
            pass
        try:
            import contextlib
            import io
            import assets as assets_mod
            with contextlib.redirect_stdout(io.StringIO()):
                assets_mod.cmd_auto(assets_mod.load(), 7, session=sid)
        except Exception:
            pass
        return

    if mode == "session-start":
        out = []
        if solution_on("tempo-rules"):
            base = decisions_baseline()
            if base:
                out.append(base)
        if solution_on("lsp-autoconfig"):
            candidates = [pick(data, "cwd", "workspace"), os.getcwd()] + transcript_workspaces(sid)
            seen = []
            for d in candidates:
                if d and os.path.isdir(d) and os.path.abspath(d) not in seen:
                    seen.append(os.path.abspath(d))
            for d in seen[:3]:
                note = lsp_autoconfig(d)
                if note:
                    out.append(note)
        if solution_on("detour-signal"):
            rule = detour_rule(now, sid, [e for e in load_events() if e.get("sid") == sid])
            if rule:
                out.append(rule)
        extra = run_solution_hooks("session-start", data)
        if extra:
            out.append(extra)
        if out:
            print("\n\n".join(out))
        return

    if mode != "user-prompt-submit":
        return

    messages = []

    # 超时红线检查在记录本次 prompt 之前：按会话构建任务块，看上一块
    warning = ""
    block_start, block_end, block_minutes = None, None, 0.0
    try:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from task_retro import build_tasks
    except Exception:
        build_tasks = None
    if build_tasks is not None:
        prev = [e for e in load_events() if e.get("sid") == sid]
        tasks = build_tasks(prev, merge_gap_s=180)
        if tasks:
            last = tasks[-1]
            if last["stops"] > 0:
                minutes = (last["end"] - last["start"]) / 60000.0
                block_start, block_end = last["start"], last["end"]
                work_min = sum(last.get("turns") or []) / 60000.0
                wait_min = sum(last.get("waits") or []) / 60000.0
                # 本次 prompt 距上一 stop 的间隔：在合并窗口内算块内等待，超出则算你离开
                gap_min = max(0.0, (now - last["end"]) / 60000.0)
                away_min = gap_min
                if gap_min <= 3:
                    wait_min += gap_min
                    away_min = 0.0
                if minutes >= REDLINE_MIN:
                    # 同一块告警过只在本块又长 / 你又离开 REDLINE_MIN 之后才再提，避免每条消息都催
                    prior = [e for e in prev if e.get("ev") == "redline"
                             and e.get("block_start") == last["start"]]
                    grown = minutes - (prior[-1].get("minutes", 0.0) if prior else 0.0)
                    away_grown = away_min - (prior[-1].get("away_minutes", 0.0) if prior else 0.0)
                    if grown >= REDLINE_MIN or (prior and away_grown >= REDLINE_MIN):
                        longest = (last.get("max_turn_ms") or 0) / 60000.0
                        warning = (
                            f"[任务节奏红线] 上一任务块墙钟 {minutes:.0f} 分钟（红线 {REDLINE_MIN:.0f}）"
                            f" = agent 执行 {work_min:.0f} 分钟 + 等你 {wait_min:.0f} 分钟；"
                            f"块结束后你离开 {away_min:.0f} 分钟。\n"
                            f"{last['stops']} 个回合，最长回合 {longest:.0f} 分钟（已扣除等待）。\n"
                            f"先给四行复盘再开新任务：\n"
                            f"① 超时点：按上面的时间账写清是执行、等你、还是你离开（引用数字）\n"
                            f"② 根因：只选一个主因并附一句证据 —— serial-wait 串行等待 / repeat-pitfall 老坑重踩 / "
                            f"design-churn 设计反复 / decision-batching 决策没一次问全 / tool-misuse 工具用法错 / "
                            f"no-evidence 首轮无证据 / spec-drift 资料与实现不一致 / "
                            f"human-wait 等你理解或回复 / other 其他（自述）\n"
                            f"③ 下次怎么拆：下一块开工先做什么（可执行）\n"
                            f"④ 兑现检查：上次同类改法这次用上了吗（用上 / 没用上 / 没有同类）\n"
                            f"注意：等你和你离开的时间不是 agent 的工作量。等待合计超过 10 分钟时，"
                            f"① 必须单独写清这段时间你在做什么（理解/查资料/离开），② 主因是等你理解或回复就选 human-wait，"
                            f"③ 写怎么降低理解成本（先给结论、少术语、一次问全），"
                            f"不要把它算进 agent 的回查或工具耗时。\n"
                            f"取证：python3 ~/.qoder/task-metrics/bin/task_retro.py --session {sid}"
                        )
                        block_minutes = minutes
    if warning and solution_on("tempo-rules"):
        messages.append(warning)
        append({"t": now, "ev": "redline", "sid": sid, "block_start": block_start,
                "block_end": block_end, "minutes": round(block_minutes, 1),
                "work_minutes": round(work_min, 1), "wait_minutes": round(wait_min, 1),
                "away_minutes": round(away_min, 1)})

    p = pressure_check(now, sid, prompt) if solution_on("tempo-rules") else ""
    if p:
        messages.append(p)

    n = decisions_nudge(now, sid) if solution_on("tempo-rules") else ""
    if n:
        messages.append(n)

    append({"t": now, "ev": "prompt", "sid": sid, "snippet": prompt[:120]})

    extra = run_solution_hooks("user-prompt-submit", data)
    if extra:
        messages.append(extra)

    if messages:
        print("\n\n".join(messages))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
