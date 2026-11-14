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


def record_prompt(sid: str, prompt: str, workspace: str) -> None:
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


def record_tool(sid: str, name: str, is_error: bool) -> None:
    append_event({"ev": "tool", "sid": sid, "name": str(name or "")[:80], "is_error": bool(is_error)})


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
