#!/usr/bin/env python3
"""Shared core for the AI coding efficiency probe.

One data model serves every host (Claude Code, DSH, Qoder, Codex).  All
functions fail open: a broken probe must never block a prompt or a turn.

Data location, in order:
  1. KIT_DATA_DIR
  2. $KIT_SHARED_HOME/data (default ~/.ai-coding-efficiency/data)
  3. $KIT_IDE_HOME/task-metrics/data
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime

try:
    import fcntl
except Exception:  # pragma: no cover - non-POSIX
    fcntl = None

DEFAULT_SHARED_HOME = os.path.expanduser("~/.ai-coding-efficiency")

PHASES = ("start", "progress", "blocked", "end")
CATEGORIES = (
    "serial-wait",
    "repeat-pitfall",
    "design-churn",
    "decision-batching",
    "tool-misuse",
    "no-evidence",
    "spec-drift",
    "human-wait",
    "other",
)
WORK_START = os.environ.get("KIT_WORK_START", "10:00")
WORK_END = os.environ.get("KIT_WORK_END", "18:00")
IDLE_MIN = float(os.environ.get("KIT_IDLE_MIN", "15"))
WORK_TYPES = ("design", "implement", "debug", "docs")
DIFFICULTIES = ("s", "m", "l")
MAX_TEXT = 600
MAX_EVENTS_READ = 4000


# ---------------------------------------------------------------------------
# Paths and host


def ide_home() -> str:
    return os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder")


def shared_home() -> str:
    return os.path.expanduser(os.environ.get("KIT_SHARED_HOME") or DEFAULT_SHARED_HOME)


def host() -> str:
    return os.environ.get("KIT_HOST") or "unknown"


def now_ms() -> int:
    return int(time.time() * 1000)


def today() -> str:
    return datetime.now().strftime("%Y-%m-%d")


def _writable_dir(path: str) -> bool:
    try:
        os.makedirs(path, mode=0o700, exist_ok=True)
        fd, probe = tempfile.mkstemp(prefix=".probe-", dir=path)
        os.close(fd)
        os.unlink(probe)
        return True
    except Exception:
        return False


def data_dir() -> str:
    candidates = [
        os.environ.get("KIT_DATA_DIR"),
        os.path.join(shared_home(), "data"),
        os.path.join(ide_home(), "task-metrics", "data"),
    ]
    for candidate in candidates:
        if candidate and _writable_dir(candidate):
            try:
                os.chmod(candidate, 0o700)
            except Exception:
                pass
            return candidate
    # Last resort: a private temp directory, still usable for this process.
    fallback = os.path.join(tempfile.gettempdir(), "ai-coding-efficiency-probe")
    os.makedirs(fallback, mode=0o700, exist_ok=True)
    return fallback


def paths(data: str | None = None) -> dict[str, str]:
    root = data or data_dir()
    return {
        "root": root,
        "events": os.path.join(root, "events.jsonl"),
        "open": os.path.join(root, "tasks", "open.json"),
        "closed_dir": os.path.join(root, "tasks", "closed"),
        "reports_dir": os.path.join(root, "reports"),
        "archive_dir": os.path.join(root, "tasks", "archive"),
        "blocks_dir": os.path.join(root, "blocks"),
        "runtime": os.path.join(root, "runtime"),
        "config": os.path.join(root, "config.json"),
    }


# ---------------------------------------------------------------------------
# Storage primitives


@contextmanager
def locked(path: str):
    """Advisory lock on a sidecar file; degrades to no lock when unavailable."""
    lock_path = path + ".lock"
    os.makedirs(os.path.dirname(lock_path), exist_ok=True)
    handle = None
    try:
        handle = open(lock_path, "a+", encoding="utf-8")
        if fcntl is not None:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        if handle is not None:
            try:
                if fcntl is not None:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            except Exception:
                pass
            handle.close()


def append_jsonl(path: str, record: dict) -> None:
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with locked(path):
            with open(path, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False) + "\n")
    except Exception:
        pass


def read_json(path: str, default):
    try:
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except Exception:
        return default


def write_json(path: str, value) -> None:
    try:
        os.makedirs(os.path.dirname(path), mode=0o700, exist_ok=True)
        with locked(path):
            temporary = path + ".tmp"
            with open(temporary, "w", encoding="utf-8") as handle:
                json.dump(value, handle, ensure_ascii=False, indent=2)
                handle.write("\n")
            os.replace(temporary, path)
    except Exception:
        pass


def append_event(event: dict) -> None:
    try:
        record = dict(event)
        record.setdefault("t", now_ms())
        record.setdefault("host", host())
        append_jsonl(paths()["events"], record)
    except Exception:
        pass


def load_events(limit: int = MAX_EVENTS_READ, sid: str | None = None) -> list[dict]:
    events: list[dict] = []
    try:
        with open(paths()["events"], encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except Exception:
                    continue
                if sid is not None and event.get("sid") != sid:
                    continue
                events.append(event)
    except Exception:
        return []
    return events[-limit:]


# ---------------------------------------------------------------------------
# Config


DEFAULT_CONFIG = {
    "red_min": 30.0,
    "remind_min": 15.0,
    "user_facing": True,
    "max_open_tasks": 3,
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        stored = read_json(paths()["config"], {})
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key in config and isinstance(value, (int, float, bool)):
                    config[key] = value
    except Exception:
        pass
    for env, key, cast in (
        ("KIT_TASK_RED_MIN", "red_min", float),
        ("KIT_TASK_REMIND_MIN", "remind_min", float),
    ):
        try:
            raw = os.environ.get(env)
            if raw:
                config[key] = cast(raw)
        except Exception:
            pass
    try:
        raw = os.environ.get("KIT_USER_FACING")
        if raw is not None:
            config["user_facing"] = raw.strip().lower() not in ("0", "false", "no", "off")
    except Exception:
        pass
    return config


# ---------------------------------------------------------------------------
# Runtime state (single active session per host process group)


def write_runtime(sid: str, workspace: str = "", block_start: int | None = None) -> None:
    try:
        write_json(os.path.join(paths()["runtime"], "current.json"), {
            "sid": sid,
            "workspace": workspace,
            "block_start": block_start,
            "host": host(),
            "updated_at": now_ms(),
        })
    except Exception:
        pass


def read_runtime() -> dict:
    value = read_json(os.path.join(paths()["runtime"], "current.json"), {})
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# Task registry


def normalize_task_id(value: str) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"[^a-z0-9_-]+", "-", text)
    text = re.sub(r"-{2,}", "-", text).strip("-_")
    return text[:64]


def load_open() -> dict:
    value = read_json(paths()["open"], {})
    if not isinstance(value, dict):
        return {"version": 1, "tasks": {}}
    tasks = value.get("tasks")
    if not isinstance(tasks, dict):
        value["tasks"] = {}
    value.setdefault("version", 1)
    return value


def save_open(value: dict) -> None:
    write_json(paths()["open"], value)


def open_tasks(workspace: str | None = None, limit: int = 3) -> list[dict]:
    value = load_open()
    tasks = [task for task in value.get("tasks", {}).values() if task.get("status", "open") == "open"]
    if workspace:
        tasks.sort(key=lambda task: 0 if task.get("workspace") == workspace else 1)
    else:
        tasks.sort(key=lambda task: task.get("last_seen_at", 0), reverse=True)
    return tasks[:limit]


def _upsert_task(report: dict, workspace: str) -> None:
    task_id = report["task_id"]
    value = load_open()
    tasks = value.setdefault("tasks", {})
    task = tasks.get(task_id) or {
        "task_id": task_id,
        "title": report.get("summary") or task_id,
        "workspace": workspace,
        "created_at": report["t"],
        "block_count": 0,
        "total_minutes": 0.0,
    }
    task["last_seen_at"] = report["t"]
    task["last_phase"] = report["phase"]
    task["summary"] = report.get("summary") or task.get("summary", "")
    if report.get("cause"):
        task["cause"] = report["cause"]
    if report.get("next"):
        task["next"] = report["next"]
    if report.get("category"):
        task["category"] = report["category"]
    if report.get("goal"):
        task["goal"] = report["goal"]
    if report.get("type"):
        task["type"] = report["type"]
    if report.get("difficulty"):
        task["difficulty"] = report["difficulty"]
    if not task.get("title") or task["title"] == task_id:
        task["title"] = report.get("summary") or task_id
    task["status"] = "open"
    tasks[task_id] = task
    save_open(value)


def close_task(task_id: str) -> None:
    value = load_open()
    tasks = value.setdefault("tasks", {})
    task = tasks.pop(task_id, None)
    if task is None:
        return
    task["status"] = "closed"
    task["closed_at"] = now_ms()
    save_open(value)
    append_jsonl(os.path.join(paths()["closed_dir"], f"{today()}.jsonl"), task)


def link_task(task_id: str, block_start: int | None) -> None:
    if not task_id or not block_start:
        return
    append_event({"ev": "task_link", "task_id": task_id, "block_start": block_start})


# ---------------------------------------------------------------------------
# Report validation


def _text(value, field: str, required: bool = False) -> str:
    if value is None:
        text = ""
    elif isinstance(value, str):
        text = value.strip()
    else:
        text = str(value).strip()
    if required and not text:
        raise ValueError(f"{field} is required")
    return text[:MAX_TEXT]


def validate_report(args: dict) -> dict:
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    phase = _text(args.get("phase"), "phase", True).lower()
    if phase not in PHASES:
        raise ValueError(f"phase must be one of {', '.join(PHASES)}")
    task_id = normalize_task_id(_text(args.get("task_id"), "task_id", True))
    if not task_id:
        raise ValueError("task_id is required")
    summary = _text(args.get("summary"), "summary", True)
    cause = _text(args.get("cause"), "cause")
    nxt = _text(args.get("next"), "next")
    category = _text(args.get("category"), "category").lower()
    if category and category not in CATEGORIES:
        raise ValueError(f"category must be one of {', '.join(CATEGORIES)}")
    goal = _text(args.get("goal"), "goal")
    work_type = _text(args.get("type"), "type").lower()
    if work_type and work_type not in WORK_TYPES:
        raise ValueError(f"type must be one of {', '.join(WORK_TYPES)}")
    difficulty = _text(args.get("difficulty"), "difficulty").lower()
    if difficulty and difficulty not in DIFFICULTIES:
        raise ValueError(f"difficulty must be one of {', '.join(DIFFICULTIES)}")
    what = _text(args.get("what"), "what")
    cost = _text(args.get("cost"), "cost")
    next_time = _text(args.get("next_time"), "next_time")
    if phase == "blocked" and (not cause or not category):
        raise ValueError("blocked reports require both cause and category")
    return {
        "phase": phase,
        "task_id": task_id,
        "summary": summary,
        "cause": cause,
        "next": nxt,
        "category": category,
        "goal": goal,
        "type": work_type,
        "difficulty": difficulty,
        "what": what,
        "cost": cost,
        "next_time": next_time,
    }


# ---------------------------------------------------------------------------
# Report recording


def record_report(args: dict, context: dict | None = None) -> dict:
    context = context or {}
    report = validate_report(args)
    runtime = read_runtime()
    sid = str(context.get("sid") or runtime.get("sid") or "unknown")
    workspace = str(context.get("workspace") or runtime.get("workspace") or os.getcwd())
    block_start = context.get("block_start") or runtime.get("block_start")
    minutes = 0.0
    if block_start:
        try:
            minutes = max(0.0, (now_ms() - int(block_start)) / 60000.0)
        except Exception:
            minutes = 0.0
    record = {
        "t": now_ms(),
        "host": host(),
        "sid": sid,
        "workspace": workspace,
        "task_id": report["task_id"],
        "phase": report["phase"],
        "summary": report["summary"],
        "cause": report["cause"],
        "next": report["next"],
        "category": report["category"],
        "goal": report.get("goal", ""),
        "type": report.get("type", ""),
        "difficulty": report.get("difficulty", ""),
        "turn": context.get("turn"),
        "step": context.get("step"),
        "block_start": block_start,
        "minutes": round(minutes, 1),
    }
    append_jsonl(os.path.join(paths()["reports_dir"], f"{today()}.jsonl"), record)
    append_event({**record, "ev": "probe_report"})
    if report["phase"] == "end":
        if report.get("what") or report.get("cost") or report.get("next_time"):
            append_event({
                "ev": "work_summary",
                "work_id": report["task_id"],
                "what": report.get("what", ""),
                "cost": report.get("cost", ""),
                "next_time": report.get("next_time", ""),
            })
            append_jsonl(os.path.join(paths()["reports_dir"], f"{today()}.jsonl"), {
                "t": now_ms(), "host": host(), "sid": sid, "work_id": report["task_id"],
                "ev": "work_summary", "what": report.get("what", ""),
                "cost": report.get("cost", ""), "next_time": report.get("next_time", ""),
            })
        close_task(report["task_id"])
    else:
        _upsert_task(record, workspace)
    link_task(report["task_id"], block_start)
    return record


# ---------------------------------------------------------------------------
# Task/block timing


def record_session_start(sid: str, workspace: str) -> None:
    append_event({"ev": "session_start", "sid": sid, "workspace": workspace})
    write_runtime(sid, workspace, None)


def _in_work_hours(at: datetime | None = None) -> bool:
    moment = at or datetime.now()
    try:
        start = datetime.strptime(WORK_START, "%H:%M").time()
        end = datetime.strptime(WORK_END, "%H:%M").time()
    except Exception:
        return True
    return start <= moment.time() <= end


def _last_kind(sid: str, kinds: tuple[str, ...]) -> dict | None:
    for event in reversed(load_events(sid=sid)):
        if event.get("ev") in kinds:
            return event
    return None


def record_prompt(sid: str, prompt: str, workspace: str) -> None:
    last_stop = _last_kind(sid, ("stop",))
    if last_stop:
        try:
            gap_ms = max(0, now_ms() - int(last_stop.get("t") or 0))
        except Exception:
            gap_ms = 0
        within = _in_work_hours()
        if gap_ms > IDLE_MIN * 60000 or not within:
            append_event({
                "ev": "idle_gap", "sid": sid, "gap_ms": gap_ms,
                "reason": "off-hours" if not within else "idle",
            })
        else:
            append_event({
                "ev": "user_wait", "sid": sid,
                "waiting_ms": gap_ms, "within_work_hours": within,
            })
    append_event({"ev": "prompt", "sid": sid, "workspace": workspace, "snippet": prompt[:160]})
    write_runtime(sid, workspace, now_ms())


def current_block_start(sid: str) -> int | None:
    events = load_events(sid=sid)
    for event in reversed(events):
        kind = event.get("ev")
        if kind == "prompt":
            return int(event.get("t") or 0) or None
        if kind == "stop":
            return None
    return None


REPEAT_TOOL_THRESHOLD = int(os.environ.get("KIT_REPEAT_TOOL", "5"))


def record_tool(sid: str, name: str, is_error: bool) -> None:
    tool = str(name or "")[:80]
    append_event({"ev": "tool", "sid": sid, "name": tool, "is_error": bool(is_error)})
    try:
        start = current_block_start(sid)
        recent = [
            event for event in load_events(sid=sid)
            if event.get("ev") == "tool" and (not start or int(event.get("t") or 0) >= start)
        ]
        tail = recent[-REPEAT_TOOL_THRESHOLD:]
        if len(tail) >= REPEAT_TOOL_THRESHOLD and all(event.get("name") == tool for event in tail):
            failures = sum(1 for event in tail if event.get("is_error"))
            append_event({
                "ev": "rework_signal", "sid": sid, "work_id": "",
                "kind": "repeat-tool", "tool": tool,
                "count": len(tail), "failures": failures,
            })
    except Exception:
        pass


def record_stop(sid: str) -> None:
    start = current_block_start(sid)
    if not start:
        append_event({"ev": "stop", "sid": sid})
        return
    end = now_ms()
    minutes = max(0.0, (end - start) / 60000.0)
    tools = [event for event in load_events(sid=sid) if event.get("ev") == "tool" and event.get("t", 0) >= start]
    append_event({"ev": "stop", "sid": sid, "block_start": start, "minutes": round(minutes, 1)})
    append_jsonl(os.path.join(paths()["blocks_dir"], f"{today()}.jsonl"), {
        "t": end,
        "host": host(),
        "sid": sid,
        "start": start,
        "end": end,
        "minutes": round(minutes, 1),
        "tools": len(tools),
        "failed_tools": sum(1 for event in tools if event.get("is_error")),
    })
    write_runtime(sid, read_runtime().get("workspace", ""), None)


def record_session_end(sid: str) -> None:
    append_event({"ev": "session_end", "sid": sid})


def maybe_reminder(sid: str) -> str:
    config = load_config()
    start = current_block_start(sid)
    if not start:
        return ""
    minutes = max(0.0, (now_ms() - start) / 60000.0)
    if minutes < float(config["red_min"]):
        return ""
    last = None
    for event in reversed(load_events(sid=sid)):
        if event.get("ev") == "reminder" and event.get("block_start") == start:
            last = int(event.get("t") or 0)
            break
    if last and now_ms() - last < float(config["remind_min"]) * 60000:
        return ""
    append_event({"ev": "reminder", "sid": sid, "block_start": start, "minutes": round(minutes, 1)})
    return format_reminder(minutes, config)


# ---------------------------------------------------------------------------
# Prompt text


def ago(ms: int) -> str:
    try:
        minutes = max(0, int((now_ms() - int(ms)) / 60000))
    except Exception:
        return "未知"
    if minutes < 60:
        return f"{minutes} 分钟前"
    return f"{minutes // 60} 小时前"


def format_open_tasks(tasks: list[dict]) -> str:
    if not tasks:
        return ""
    lines = ["[探针] 未完成任务（可续接）："]
    for task in tasks:
        lines.append(
            f"- {task.get('task_id')} — {task.get('title') or task.get('summary') or ''}"
            f"（最后活跃 {ago(task.get('last_seen_at', 0))}；下一步：{task.get('next') or '未记录'}）"
        )
    lines.append(
        "续接请沿用上面的 task_id；新任务请调用 probe_report("
        'phase="start", task_id="<kebab-slug>", summary="<一句话>")。'
    )
    return "\n".join(lines)


def format_start_hint(tasks: list[dict]) -> str:
    head = (
        "[探针] 开工前先认一下任务：如果是新任务，调用 probe_report("
        'phase="start", task_id="<kebab-slug>", summary="<一句话>")；'
        "如果是续接，沿用已有 task_id。"
    )
    body = format_open_tasks(tasks)
    return head + ("\n" + body if body else "")


def format_reminder(minutes: float, config: dict) -> str:
    lines = [
        f"[探针] 当前任务块已 {minutes:.0f} 分钟（红线 {float(config['red_min']):.0f}）。",
        "请做两件事：",
    ]
    if config.get("user_facing", True):
        lines.append(
            "1. 对用户说一句简短的人话，例如：不好意思，这个做得有点久，我还在处理，"
            "后面会把这段记下来优化。"
        )
        lines.append(
            '2. 调用 probe_report：phase="blocked" 或 "progress"，task_id="...", '
            'summary="现在在做什么", cause="为什么慢", next="下一步", '
            'category="serial-wait|repeat-pitfall|design-churn|decision-batching|'
            'tool-misuse|no-evidence|spec-drift|human-wait|other"。'
        )
    else:
        lines.append(
            '1. 调用 probe_report：phase="blocked" 或 "progress"，task_id="...", '
            'summary="现在在做什么", cause="为什么慢", next="下一步", category="..."。'
        )
    return "\n".join(lines)


# Completion detection is intentionally model-side: keyword matching misfires on
# questions and negations (2026-11-14: four false positives in one session).


# ---------------------------------------------------------------------------
# Status view (crop status)


def _start_of_today_ms() -> int:
    now = datetime.now()
    return int(now.replace(hour=0, minute=0, second=0, microsecond=0).timestamp() * 1000)


def status_summary(workspace: str | None = None) -> dict:
    runtime = read_runtime()
    sid = str(runtime.get("sid") or "")
    events = load_events(limit=5000, sid=sid) if sid else []
    today = [event for event in events if int(event.get("t") or 0) >= _start_of_today_ms()]
    kinds: dict[str, int] = {}
    for event in today:
        kind = str(event.get("ev") or "unknown")
        kinds[kind] = kinds.get(kind, 0) + 1
    block_start = runtime.get("block_start")
    minutes = 0.0
    if block_start:
        try:
            minutes = max(0.0, (now_ms() - int(block_start)) / 60000.0)
        except Exception:
            minutes = 0.0
    config = load_config()
    return {
        "host": host(),
        "sid": sid,
        "workspace": runtime.get("workspace", ""),
        "current_block_minutes": round(minutes, 1),
        "red_min": float(config["red_min"]),
        "today_events": len(today),
        "today_by_kind": kinds,
        "open_tasks": open_tasks(workspace, 5),
        "data_dir": data_dir(),
    }


def format_status(summary: dict) -> str:
    lines = [
        f"Crop 状态 — {summary.get('host', 'unknown')}  "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M')}",
    ]
    minutes = summary.get("current_block_minutes", 0)
    red = summary.get("red_min", 30)
    if minutes:
        flag = "（超红线）" if minutes >= red else ""
        lines.append(f"当前任务块  {minutes:.0f} 分钟{flag} / 红线 {red:.0f}")
    else:
        lines.append("当前任务块  空闲")
    kinds = summary.get("today_by_kind") or {}
    order = ("prompt", "tool", "stop", "reminder", "probe_report", "session_start")
    parts = [f"{name} {kinds[name]}" for name in order if kinds.get(name)]
    extra = sum(v for k, v in kinds.items() if k not in order)
    if extra:
        parts.append(f"其它 {extra}")
    lines.append(f"今日事件  {summary.get('today_events', 0)} 条" + ("：" + "、".join(parts) if parts else ""))
    tasks = summary.get("open_tasks") or []
    if tasks:
        lines.append("进行中的工作：")
        for task in tasks:
            title = task.get("title") or task.get("summary") or task.get("task_id")
            nxt = task.get("next") or "未记录"
            lines.append(f"  - {task.get('task_id')} — {title}（下一步：{nxt}）")
    else:
        lines.append("进行中的工作：无")
    lines.append(f"数据目录  {summary.get('data_dir', '')}")
    return "\n".join(lines)


def work_status_line(workspace: str | None = None) -> str:
    """One line for the turn-start injection: current work and how stale it is."""
    tasks = open_tasks(workspace, 3)
    if not tasks:
        return ""
    task = tasks[0]
    aim = task.get("goal") or task.get("title") or task.get("task_id")
    stale = ago(task.get("last_seen_at", 0))
    return (
        f"[crop] 当前工作：{aim}（{task.get('task_id')}）；上次更新 {stale}。"
        "状态有变就调 probe_report 更新一行。"
    )


# ---------------------------------------------------------------------------
# Work state machine: open -> suspended -> archived


def set_work_state(work_id: str, state: str) -> bool:
    work_id = normalize_task_id(work_id)
    value = load_open()
    tasks = value.setdefault("tasks", {})
    task = tasks.get(work_id)
    if not task:
        return False
    task["state"] = state
    task["state_changed_at"] = now_ms()
    save_open(value)
    append_event({"ev": "work_" + ("suspend" if state == "suspended" else "resume"),
                  "work_id": work_id})
    return True


def archive_work(work_id: str, reason: str = "manual") -> bool:
    work_id = normalize_task_id(work_id)
    value = load_open()
    tasks = value.setdefault("tasks", {})
    task = tasks.pop(work_id, None)
    if not task:
        return False
    task["state"] = "archived"
    task["archived_at"] = now_ms()
    task["archive_reason"] = reason
    save_open(value)
    append_jsonl(os.path.join(paths()["archive_dir"], f"{today()}.jsonl"), task)
    append_event({"ev": "work_archive", "work_id": work_id, "reason": reason})
    return True


def archive_stale(days: float = 14.0, now: int | None = None) -> list[str]:
    """Archive works suspended longer than `days`. Returns archived ids."""
    cutoff = (now if now is not None else now_ms()) - int(days * 86400000)
    value = load_open()
    tasks = value.get("tasks", {})
    def _changed_at(task: dict) -> int:
        value = task.get("state_changed_at")
        if value is None:
            value = task.get("last_seen_at")
        return int(value or 0)

    stale = [
        work_id for work_id, task in tasks.items()
        if task.get("state") == "suspended" and _changed_at(task) < cutoff
    ]
    for work_id in stale:
        archive_work(work_id, reason=f"suspended>{days:g}d")
    return stale


def works_needing_check(days: float = 1.0, now: int | None = None) -> list[dict]:
    """Open works untouched longer than `days` — the host asks the user about them."""
    cutoff = (now if now is not None else now_ms()) - int(days * 86400000)
    return [
        task for task in load_open().get("tasks", {}).values()
        if task.get("state", "open") == "open"
        and int(task.get("last_seen_at") or 0) < cutoff
    ]


# ---------------------------------------------------------------------------
# Report (weekly / arbitrary window)


def _events_between(start_ms: int, end_ms: int) -> list[dict]:
    events = load_events(limit=200000)
    return [event for event in events if start_ms <= int(event.get("t") or 0) <= end_ms]


def period_summary(days: float = 7.0, now: int | None = None) -> dict:
    end = now if now is not None else now_ms()
    start = end - int(days * 86400000)
    events = _events_between(start, end)
    blocks = [event for event in events if event.get("ev") == "stop"]
    reports = [event for event in events if event.get("ev") == "probe_report"]
    waits = [event for event in events if event.get("ev") == "user_wait"]
    gaps = [event for event in events if event.get("ev") == "idle_gap"]
    summaries = [event for event in events if event.get("ev") == "work_summary"]

    minutes_by_day: dict[str, float] = {}
    for block in blocks:
        day = datetime.fromtimestamp(int(block.get("t") or 0) / 1000).strftime("%Y-%m-%d")
        minutes_by_day[day] = minutes_by_day.get(day, 0.0) + float(block.get("minutes") or 0)

    works: dict[str, dict] = {}
    for report in reports:
        work_id = str(report.get("work_id") or report.get("task_id") or "")
        if not work_id:
            continue
        item = works.setdefault(work_id, {
            "work_id": work_id, "goal": "", "type": "", "difficulty": "",
            "updates": 0, "minutes": 0.0,
        })
        item["updates"] += 1
        item["minutes"] += float(report.get("minutes") or 0)
        for key in ("goal", "type", "difficulty"):
            if report.get(key):
                item[key] = report[key]

    top = sorted(works.values(), key=lambda item: item["minutes"], reverse=True)[:3]
    return {
        "days": days,
        "start": start,
        "end": end,
        "events": len(events),
        "blocks": len(blocks),
        "total_minutes": round(sum(float(block.get("minutes") or 0) for block in blocks), 1),
        "minutes_by_day": {day: round(value, 1) for day, value in sorted(minutes_by_day.items())},
        "works": sorted(works.values(), key=lambda item: item["minutes"], reverse=True),
        "top_works": top,
        "human_wait_ms": sum(int(event.get("waiting_ms") or 0) for event in waits),
        "idle_gaps": len(gaps),
        "summaries": summaries,
    }


def format_report(summary: dict) -> str:
    days = summary.get("days", 7)
    total = summary.get("total_minutes", 0)
    top = summary.get("top_works") or []
    lines = [f"Crop 周报（近 {days:g} 天）", ""]
    lines.append(
        f"这 {days:g} 天里，一共 {summary.get('blocks', 0)} 个回合、{round(total)} 分钟，"
        f"涉及 {len(summary.get('works') or [])} 个工作。"
    )
    if top:
        lines.append("")
        lines.append("最花时间的工作：")
        for item in top:
            goal = item.get("goal") or item.get("work_id")
            lines.append(f"  - {goal}（{round(item['minutes'])} 分钟，类型 {item.get('type') or '未标'}）")
    wait_min = (summary.get("human_wait_ms") or 0) / 60000
    if wait_min:
        lines.append("")
        lines.append(f"其中等你回话 {wait_min:.0f} 分钟，有 {summary.get('idle_gaps', 0)} 次长时间离开。")
    if summary.get("summaries"):
        lines.append("")
        lines.append("你的收尾小结：")
        for item in summary["summaries"][:3]:
            lines.append(f"  - {item.get('what') or item.get('work_id')}；花在 {item.get('cost') or '未记'}；下次 {item.get('next_time') or '未记'}")
    lines.append("")
    lines.append("（以上为事实层，文字版由 Agent 任务改写。）")
    return "\n".join(lines)


def daily_breakdown(days: float = 14.0, now: int | None = None) -> list[dict]:
    end = now if now is not None else now_ms()
    start = end - int(days * 86400000)
    events = _events_between(start, end)
    buckets: dict[str, dict] = {}
    for event in events:
        stamp = int(event.get("t") or 0)
        day = datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
        bucket = buckets.setdefault(day, {
            "date": day, "events": 0, "blocks": 0, "minutes": 0.0,
            "tools": 0, "failures": 0, "waits": 0, "rework": 0, "works": set(),
        })
        bucket["events"] += 1
        kind = event.get("ev")
        if kind == "stop":
            bucket["blocks"] += 1
            bucket["minutes"] += float(event.get("minutes") or 0)
        elif kind == "tool":
            bucket["tools"] += 1
            if event.get("is_error"):
                bucket["failures"] += 1
        elif kind == "user_wait":
            bucket["waits"] += 1
        elif kind == "rework_signal":
            bucket["rework"] += 1
        elif kind == "probe_report":
            work_id = event.get("work_id") or event.get("task_id")
            if work_id:
                bucket["works"].add(work_id)
    rows = []
    for day, bucket in sorted(buckets.items()):
        bucket["works"] = len(bucket["works"])
        bucket["minutes"] = round(bucket["minutes"], 1)
        rows.append(bucket)
    return rows


def similar_works(work_type: str, difficulty: str | None = None, days: float = 30.0) -> list[dict]:
    end = now_ms()
    start = end - int(days * 86400000)
    reports = [
        event for event in _events_between(start, end)
        if event.get("ev") == "probe_report" and event.get("type") == work_type
        and (not difficulty or event.get("difficulty") == difficulty)
    ]
    groups: dict[str, dict] = {}
    for report in reports:
        work_id = str(report.get("work_id") or "")
        if not work_id:
            continue
        item = groups.setdefault(work_id, {"work_id": work_id, "minutes": 0.0, "updates": 0})
        item["minutes"] += float(report.get("minutes") or 0)
        item["updates"] += 1
    return sorted(groups.values(), key=lambda item: item["minutes"], reverse=True)


def day_detail(date: str, now: int | None = None) -> dict:
    """Everything the dashboard needs for one day (YYYY-MM-DD)."""
    try:
        start = int(datetime.strptime(date, "%Y-%m-%d").timestamp() * 1000)
    except Exception:
        raise ValueError("date must be YYYY-MM-DD")
    end = start + 86400000
    events = [event for event in _events_between(start, end)]
    tools: dict[str, int] = {}
    failures = 0
    blocks = []
    works: dict[str, dict] = {}
    for event in events:
        kind = event.get("ev")
        if kind == "tool":
            tools[str(event.get("name") or "?")] = tools.get(str(event.get("name") or "?"), 0) + 1
            if event.get("is_error"):
                failures += 1
        elif kind == "stop":
            blocks.append({
                "t": event.get("t"), "minutes": float(event.get("minutes") or 0),
                "sid": event.get("sid"),
            })
        elif kind == "probe_report":
            work_id = str(event.get("work_id") or event.get("task_id") or "")
            if work_id:
                item = works.setdefault(work_id, {"work_id": work_id, "goal": "", "minutes": 0.0, "updates": 0})
                item["updates"] += 1
                item["minutes"] = round(item["minutes"] + float(event.get("minutes") or 0), 1)
                if event.get("goal"):
                    item["goal"] = event["goal"]
    return {
        "date": date,
        "events": len(events),
        "blocks": len(blocks),
        "minutes": round(sum(block["minutes"] for block in blocks), 1),
        "failures": failures,
        "tools": sorted(tools.items(), key=lambda pair: pair[1], reverse=True)[:10],
        "works": sorted(works.values(), key=lambda item: item["minutes"], reverse=True),
    }


def type_breakdown(days: float = 30.0, now: int | None = None) -> list[dict]:
    """Per work-type totals and median active minutes (same-type comparison)."""
    end = now if now is not None else now_ms()
    start = end - int(days * 86400000)
    reports = [event for event in _events_between(start, end) if event.get("ev") == "probe_report"]
    groups: dict[str, dict] = {}
    for report in reports:
        work_type = str(report.get("type") or "untyped")
        work_id = str(report.get("work_id") or "")
        if not work_id:
            continue
        group = groups.setdefault(work_type, {"type": work_type, "works": {}, "minutes": 0.0})
        group["works"][work_id] = round(group["works"].get(work_id, 0.0) + float(report.get("minutes") or 0), 1)
    rows = []
    for work_type, group in groups.items():
        values = sorted(group["works"].values())
        if not values:
            continue
        middle = len(values) // 2
        median = values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2
        rows.append({
            "type": work_type,
            "count": len(values),
            "minutes": round(sum(values), 1),
            "median_minutes": round(median, 1),
        })
    return sorted(rows, key=lambda row: row["minutes"], reverse=True)


def session_summary(sid: str, now: int | None = None) -> dict:
    """Everything a session-scoped panel needs: where the time went and what is off."""
    sid = str(sid)
    events = load_events(sid=sid)
    stop_at = now if now is not None else now_ms()
    day_start = _start_of_today_ms()
    blocks = [e for e in events if e.get("ev") == "stop"]
    tools = [e for e in events if e.get("ev") == "tool"]
    waits = [e for e in events if e.get("ev") == "user_wait"]
    reworks = [e for e in events if e.get("ev") == "rework_signal"]
    reports = [e for e in events if e.get("ev") == "probe_report"]

    tool_counts: dict[str, int] = {}
    for event in tools:
        name = str(event.get("name") or "?")
        tool_counts[name] = tool_counts.get(name, 0) + 1
    failures = [e for e in tools if e.get("is_error")]
    failure_counts: dict[str, int] = {}
    for event in failures:
        name = str(event.get("name") or "?")
        failure_counts[name] = failure_counts.get(name, 0) + 1

    block_minutes = sum(float(e.get("minutes") or 0) for e in blocks)
    wait_ms = sum(int(e.get("waiting_ms") or 0) for e in waits)
    work_minutes = max(0.0, block_minutes - wait_ms / 60000.0)

    runtime = read_runtime()
    current = 0.0
    if runtime.get("sid") == sid and runtime.get("block_start"):
        try:
            current = max(0.0, (now_ms() - int(runtime["block_start"])) / 60000.0)
        except Exception:
            current = 0.0

    total = work_minutes + wait_ms / 60000.0
    share = {
        "work": round(work_minutes / total, 3) if total else 0.0,
        "wait": round((wait_ms / 60000.0) / total, 3) if total else 0.0,
    }

    anomalies = []
    for name, count in sorted(failure_counts.items(), key=lambda pair: pair[1], reverse=True):
        if name.startswith("call_") or name == "tool":
            continue  # pre-v1 rows recorded the call id instead of the tool name
        anomalies.append({"kind": "failure", "tool": name, "count": count})
        if len(anomalies) >= 3:
            break
    for event in reworks[-3:]:
        anomalies.append({"kind": "rework", "tool": event.get("tool") or "", "count": int(event.get("count") or 0)})

    latest_work = ""
    goal = ""
    for report in reversed(reports):
        if report.get("task_id"):
            latest_work = str(report["task_id"])
            if report.get("goal"):
                goal = report["goal"]
                break

    recent = []
    try:
        block_file = os.path.join(paths()["blocks_dir"], f"{today()}.jsonl")
        with open(block_file, encoding="utf-8") as handle:
            for line in handle:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                if row.get("sid") == sid:
                    recent.append({
                        "minutes": round(float(row.get("minutes") or 0), 1),
                        "tools": int(row.get("tools") or 0),
                        "failed": int(row.get("failed_tools") or 0),
                    })
    except Exception:
        recent = []
    return {
        "sid": sid,
        "recent_blocks": recent[-5:][::-1],
        "current_minutes": round(current, 1),
        "blocks": len(blocks),
        "minutes": round(block_minutes, 1),
        "work_minutes": round(work_minutes, 1),
        "wait_minutes": round(wait_ms / 60000.0, 1),
        "share": share,
        "tools_total": len(tools),
        "failures": len(failures),
        "tool_top": sorted(tool_counts.items(), key=lambda pair: pair[1], reverse=True)[:5],
        "anomalies": anomalies[:3],
        "works": len({str(r.get("task_id")) for r in reports if r.get("task_id")}),
        "latest_work": latest_work,
        "goal": goal,
        "today_events": len([e for e in events if int(e.get("t") or 0) >= day_start]),
    }
