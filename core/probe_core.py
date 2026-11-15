#!/usr/bin/env python3
"""Shared core for Agent Crop.

One data model serves every host (Claude Code, DSH, Qoder, Codex).  All
functions fail open: a broken probe must never block a prompt or a turn.

Data location, in order:
  1. KIT_DATA_DIR
  2. $KIT_SHARED_HOME/data (default ~/.agentcrop/data)
  3. $KIT_IDE_HOME/task-metrics/data
"""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timedelta

try:
    import fcntl
except Exception:  # pragma: no cover - non-POSIX
    fcntl = None

DEFAULT_SHARED_HOME = os.path.expanduser("~/.agentcrop")
# Pre-rename installs used this path.  Only fall back to it when the new home
# does not exist yet, so an upgrade keeps seeing its old data exactly once.
LEGACY_SHARED_HOME = os.path.expanduser("~/.ai-coding-efficiency")

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
    "side-issue",
    "other",
)
WORK_START = os.environ.get("KIT_WORK_START", "10:00")
WORK_END = os.environ.get("KIT_WORK_END", "18:00")
IDLE_MIN = float(os.environ.get("KIT_IDLE_MIN", "15"))
CLAIM_TTL_MIN = float(os.environ.get("KIT_CLAIM_TTL_MIN", "120"))
VERDICTS = ("justified", "not-justified", "unknown")
REVIEW_LEVELS = ("redo", "major", "minor", "none")
SENTIMENTS = ("pos", "neu", "neg")
WORK_TYPES = ("design", "implement", "debug", "docs")
DIFFICULTIES = ("s", "m", "l")
MAX_TEXT = 600
MAX_EVENTS_READ = 4000


# ---------------------------------------------------------------------------
# Paths and host


def ide_home() -> str:
    return os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder")


def shared_home() -> str:
    explicit = os.environ.get("KIT_SHARED_HOME")
    if explicit:
        return os.path.expanduser(explicit)
    if not os.path.isdir(DEFAULT_SHARED_HOME) and os.path.isdir(LEGACY_SHARED_HOME):
        return LEGACY_SHARED_HOME
    return DEFAULT_SHARED_HOME


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
    fallback = os.path.join(tempfile.gettempdir(), "agent-crop-probe")
    os.makedirs(fallback, mode=0o700, exist_ok=True)
    return fallback


def paths(data: str | None = None) -> dict[str, str]:
    root = data or data_dir()
    return {
        "root": root,
        "events": os.path.join(root, "events.jsonl"),
        "open": os.path.join(root, "tasks", "open.json"),
        "bindings": os.path.join(root, "tasks", "bindings.json"),
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
    "yellow_min": 10.0,
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
        ("KIT_TASK_YELLOW_MIN", "yellow_min", float),
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
    return text[:32]


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


def open_tasks(workspace: str | None = None, limit: int = 3, sid: str | None = None) -> list[dict]:
    """Open tasks, best candidate first.

    Rank: same session > same workspace > most recently touched.
    Sorting by workspace alone is not enough: when every task shares a workspace
    the stable sort keeps the dict's insertion order, so the oldest task wins.
    """
    value = load_open()
    tasks = [
        task for task in value.get("tasks", {}).values()
        if task.get("status", "open") == "open" and task.get("state", "open") == "open"
    ]

    def rank(task: dict) -> tuple:
        same_session = 0 if (sid and task.get("last_sid") == sid) else 1
        same_workspace = 0 if (not workspace or task.get("workspace") == workspace) else 1
        return (same_session, same_workspace, -float(task.get("last_seen_at", 0)))

    tasks.sort(key=rank)
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
    if report.get("sid"):
        task["last_sid"] = report["sid"]
    task["active_until"] = int(report["t"]) + int(CLAIM_TTL_MIN * 60000)
    task["summary"] = report.get("summary") or task.get("summary", "")
    if report.get("cause"):
        task["cause"] = report["cause"]
    if report.get("next"):
        task["next"] = report["next"]
    if report.get("category"):
        task["category"] = report["category"]
    if report.get("goal"):
        task["goal"] = report["goal"]
    if report.get("verdict"):
        task["verdict"] = report["verdict"]
    if report.get("type"):
        task["type"] = report["type"]
    if report.get("difficulty"):
        task["difficulty"] = report["difficulty"]
    if not task.get("title") or task["title"] == task_id:
        task["title"] = report.get("summary") or task_id
    task["status"] = "open"
    if report.get("sid"):
        task["sid"] = report["sid"]
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


def link_task(task_id: str, block_start: int | None, sid: str | None = None) -> None:
    if not task_id or not block_start:
        return
    event = {"ev": "task_link", "task_id": task_id, "block_start": block_start}
    if sid:
        event["sid"] = sid
    append_event(event)


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
    verdict = _text(args.get("verdict"), "verdict").lower()
    if verdict and verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {', '.join(VERDICTS)}")
    rework = _text(args.get("rework"), "rework").lower()
    if rework and rework not in REVIEW_LEVELS:
        raise ValueError(f"rework must be one of {', '.join(REVIEW_LEVELS)}")
    sentiment = _text(args.get("sentiment"), "sentiment").lower()
    if sentiment and sentiment not in SENTIMENTS:
        raise ValueError(f"sentiment must be one of {', '.join(SENTIMENTS)}")
    improve = _text(args.get("improve"), "improve")
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
        "verdict": verdict,
        "improve": improve,
        "rework": rework,
        "sentiment": sentiment,
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
        "verdict": report.get("verdict", ""),
        "improve": report.get("improve", ""),
        "rework": report.get("rework", ""),
        "sentiment": report.get("sentiment", ""),
        "type": report.get("type", ""),
        "difficulty": report.get("difficulty", ""),
        "turn": context.get("turn"),
        "step": context.get("step"),
        "block_start": block_start,
        "minutes": round(minutes, 1),
    }
    if report.get("category") == "side-issue":
        # 支线只记账，不认领、不改任务、不新建工作
        append_event({
            "ev": "side_issue",
            "t": record["t"],
            "host": record["host"],
            "sid": sid,
            "workspace": workspace,
            "work_id": report["task_id"],
            "note": (report.get("summary") or "")[:200],
            "suggest": report.get("next") or "later",
        })
        write_runtime(sid, workspace, block_start)
        return {**record, "side_issue": True}

    append_jsonl(os.path.join(paths()["reports_dir"], f"{today()}.jsonl"), record)
    write_runtime(sid, workspace, block_start)
    append_event({**record, "ev": "probe_report"})
    if report["phase"] == "end":
        if report.get("what") or report.get("cost") or report.get("next_time"):
            append_event({
                "ev": "work_summary",
                "sid": sid,
                "work_id": report["task_id"],
                "verdict": report.get("verdict", ""),
        "improve": report.get("improve", ""),
        "rework": report.get("rework", ""),
        "sentiment": report.get("sentiment", ""),
                "what": report.get("what", ""),
                "cost": report.get("cost", ""),
                "next_time": report.get("next_time", ""),
            })
            append_jsonl(os.path.join(paths()["reports_dir"], f"{today()}.jsonl"), {
                "t": now_ms(), "host": host(), "sid": sid, "work_id": report["task_id"],
                "ev": "work_summary", "what": report.get("what", ""),
                "cost": report.get("cost", ""), "next_time": report.get("next_time", ""),
            })
        append_event({"ev": "work_close", "sid": sid, "work_id": report["task_id"],
                      "verdict": report.get("verdict", "")})
        release_binding(sid)
        close_task(report["task_id"])
    else:
        _upsert_task(record, workspace)
        bind_session(sid, report["task_id"])
        if report["phase"] == "start":
            append_event({"ev": "work_open", "sid": sid, "work_id": report["task_id"],
                          "goal": report.get("goal", ""), "type": report.get("type", ""),
                          "difficulty": report.get("difficulty", "")})
    link_task(report["task_id"], block_start, sid)
    # 用户不满时，返工档次可以随这次上报一起带进来，省掉一次单独调用
    if report.get("rework"):
        try:
            record_review(
                {"rework": report.get("rework"), "sentiment": report.get("sentiment", ""),
                 "reason": report.get("cause") or report.get("summary") or "",
                 "work_id": report["task_id"]},
                {"sid": sid},
            )
        except ValueError:
            pass
    return record


# ---------------------------------------------------------------------------
# Task/block timing


def record_session_start(sid: str, workspace: str) -> None:
    try:
        migrate_bindings()
        normalize_bindings()
        mark_stale_works()
    except Exception:
        pass
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
        # 等待属于「刚结束的那个 block」的工作：否则看板上的等待列永远空着。
        work_id = str(last_stop.get("work_id") or "")
        if not work_id:
            work_id = resolve_binding(sid) or str((_claimed_work(sid) or {}).get("task_id") or "")
        ownership = {"work_id": work_id} if work_id else {}
        if gap_ms > IDLE_MIN * 60000 or not within:
            append_event({
                "ev": "idle_gap", "sid": sid, "gap_ms": gap_ms,
                "reason": "off-hours" if not within else "idle", **ownership,
            })
        else:
            append_event({
                "ev": "user_wait", "sid": sid,
                "waiting_ms": gap_ms, "within_work_hours": within, **ownership,
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
                "ev": "tool_churn", "sid": sid, "work_id": "",
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
    # 机械计时：块结束时按当前认领自动归属，probe_report 只负责补语义。
    work_id = resolve_binding(sid) or str((_claimed_work(sid) or {}).get("task_id") or "")
    append_event({
        "ev": "stop", "sid": sid, "block_start": start,
        "minutes": round(minutes, 1), "work_id": work_id,
    })
    append_jsonl(os.path.join(paths()["blocks_dir"], f"{today()}.jsonl"), {
        "t": end,
        "host": host(),
        "sid": sid,
        "start": start,
        "end": end,
        "minutes": round(minutes, 1),
        "tools": len(tools),
        "failed_tools": sum(1 for event in tools if event.get("is_error")),
        "work_id": work_id,
    })
    if work_id:
        link_task(work_id, start, sid)
    write_runtime(sid, read_runtime().get("workspace", ""), None)


def record_session_end(sid: str) -> None:
    append_event({"ev": "session_end", "sid": sid})


def _latest_work_for_session(sid: str) -> str | None:
    for event in reversed(load_events(sid=sid)):
        if event.get("ev") == "probe_report" and event.get("task_id"):
            return str(event["task_id"])
    return None


def maybe_work_reminder(sid: str) -> str:
    """Work-level red line: the current work is past 60% of its baseline."""
    config = load_config()
    task_id = _latest_work_for_session(sid)
    if not task_id:
        return ""
    health = work_health(task_id)
    if not health or health["minutes"] < float(config.get("yellow_min", 10.0)):
        return ""
    for event in reversed(load_events(sid=sid)):
        if event.get("ev") == "reminder" and event.get("work_id") == task_id:
            if now_ms() - int(event.get("t") or 0) < float(config["remind_min"]) * 60000:
                return ""
            break
    append_event({
        "ev": "reminder", "sid": sid, "work_id": task_id,
        "minutes": health["minutes"], "kind": "work",
    })
    return format_work_reminder(health, config)


def maybe_reminder(sid: str) -> str:
    config = load_config()
    start = current_block_start(sid)
    if not start:
        return maybe_work_reminder(sid)
    minutes = max(0.0, (now_ms() - start) / 60000.0)
    if minutes < float(config["red_min"]):
        return maybe_work_reminder(sid)
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


def format_open_tasks(tasks: list[dict], total: int | None = None, sid: str | None = None) -> str:
    # A live binding decides the first-turn line: the bound work may have
    # dropped out of the three candidates (another session touched it, or newer
    # works pushed it past the limit), so ask the binding, not the list.
    if sid and resolve_binding(sid):
        bound_line = work_status_line(None, sid)
        if bound_line:
            return bound_line
    if not tasks:
        return "[crop] 本机当前没有进行中的工作。新开一条：crop_report(phase=\"start\", task_id=\"<短名>\", summary=\"<一句话>\")"
    claimed = next((task for task in tasks if sid and task.get("last_sid") == sid), None)
    if claimed:
        return work_status_line(None, sid)
    count = total if total is not None else len(tasks)
    lines = [f"[crop] 未完成的工作有 {count} 条，最近活跃的是："]
    for task in tasks:
        lines.append(
            f"- {task.get('task_id')} — {task.get('goal') or task.get('title') or ''}"
            f"（{ago(task.get('last_seen_at', 0))}）"
        )
    extra = count - len(tasks)
    if extra > 0:
        lines.append(f"另有 {extra} 条，用 crop_works 查看或筛选。")
    example = tasks[0].get("task_id")
    lines.append(
        "哪个属于本会话？先判断是接着做还是新开（先绑定，再干活）：接着做用 "
        f'crop_report(phase="start", task_id="{example}")；新开用 2-3 个词的短名。'
    )
    lines.append("尽量让一个会话只对应一件工作；要做的其实是几件事就拆开，一件一件来。")
    return "\n".join(lines)


def format_start_hint(tasks: list[dict], total: int | None = None, sid: str | None = None) -> str:
    return format_open_tasks(tasks, total, sid)


def format_reminder(minutes: float, config: dict) -> str:
    lines = [
        f"[crop] 红线告警：这一轮已经 {minutes:.0f} 分钟（红线 {float(config['red_min']):.0f}），用户等了很久还没拿到结果。",
        "先别急着继续，确认三件事：",
        "1. 还走在正确的路上吗？有没有更快的路径？",
        "2. 能不能先给用户一个阶段性交付？",
        "3. 安抚用户，并把判断写进 crop_report（cause + verdict）。",
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


def start_of_today_ms() -> int:
    """Local midnight — the dashboard's「今天」and natural-day windows start here."""
    return _start_of_today_ms()


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


def _claimed_work(sid: str) -> dict | None:
    """This session's work: explicit claim first, else the single active work.

    Several sessions can share a workspace, so "the only active work" is only a
    safe fallback when there is exactly one. 块结束时用它自动归属时间。
    """
    tasks = [
        task for task in load_open().get("tasks", {}).values()
        if task.get("status", "open") == "open" and task.get("state", "open") == "open"
    ]
    if sid:
        for task in tasks:
            if task.get("last_sid") == sid:
                return task
    active = [task for task in tasks if float(task.get("active_until") or 0) > now_ms()]
    return active[0] if len(active) == 1 else None


def work_status_line(workspace: str | None = None, sid: str | None = None) -> str:
    """Turn-start line: plain prose when bound, candidate list when ambiguous."""
    tasks = open_tasks(workspace, 3, sid)
    bound_id = resolve_binding(sid) if sid else ""
    claimed = None
    if bound_id:
        # Read the bound task directly: it does not have to be a top-3 candidate.
        bound_task = load_open().get("tasks", {}).get(bound_id)
        if (bound_task
                and bound_task.get("status", "open") == "open"
                and bound_task.get("state", "open") == "open"):
            claimed = bound_task
    if not tasks and not claimed:
        return ""
    if not claimed:
        claimed = next((t for t in tasks if sid and t.get("last_sid") == sid), None)
    inherited = None
    if not claimed:
        now = now_ms()
        active = [t for t in tasks if float(t.get("active_until") or 0) > now]
        if len(active) == 1:
            inherited = active[0]
    current = claimed or inherited
    if current:
        aim = current.get("goal") or current.get("title") or current.get("task_id")
        health = work_health(current.get("task_id")) or {}
        minutes = health.get("minutes", 0.0)
        yellow = float(load_config().get("yellow_min", 10.0))
        overtime = "已超过黄线" if minutes >= yellow else "未超时"
        note = "" if claimed else "（上个会话认领，仍在有效期内）"
        return (
            f"[crop] 当前工作：{aim}（{current.get('task_id')}）{note}。"
            f"已进行 {minutes:.0f} 分钟，{overtime}（黄线 {yellow:.0f} 分钟）；上次更新 {ago(current.get('last_seen_at', 0))}。"
            "留意用户是否把话题岔到另一件事（分叉），或上一轮的产出是否被推翻（返工）；有变化就更新一行。"
        )
    count = open_works_count(workspace)
    lines = [f"[crop] 未完成的工作有 {count} 条，最近活跃的是："]
    for task in tasks:
        lines.append(
            f"- {task.get('task_id')} — {task.get('goal') or task.get('title') or ''}"
            f"（{ago(task.get('last_seen_at', 0))}）"
        )
    extra = count - len(tasks)
    if extra > 0:
        lines.append(f"另有 {extra} 条，用 crop_works 查看或筛选。")
    example = tasks[0].get("task_id")
    lines.append(
        "哪个属于本会话？先绑定再干活：接着做用 "
        f'crop_report(phase="start", task_id="{example}")；'
        "新开用 2-3 个词的短名。尽量一个会话只对应一件工作。"
    )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Work state machine: open -> suspended -> archived


def switch_claim(work_id: str, sid: str) -> bool:
    """Point this session at another work without touching its state.

    Focus and state are different things: switching away from a work does not
    mean it is suspended. Only the claim moves; the other task stays open.
    """
    if not sid:
        return False
    value = load_open()
    tasks = value.setdefault("tasks", {})
    target = tasks.get(normalize_task_id(work_id))
    if not target:
        return False
    for other in tasks.values():
        if other is not target and other.get("last_sid") == sid:
            other.pop("last_sid", None)
    target["last_sid"] = sid
    save_open(value)
    bind_session(sid, target["task_id"])
    append_event({"ev": "work_claim", "work_id": target["task_id"], "sid": sid})
    return True


def set_work_state(work_id: str, state: str, sid: str | None = None) -> bool:
    work_id = normalize_task_id(work_id)
    value = load_open()
    tasks = value.setdefault("tasks", {})
    task = tasks.get(work_id)
    if not task:
        return False
    task["state"] = state
    task["state_changed_at"] = now_ms()
    save_open(value)
    event = {"ev": "work_" + ("suspend" if state == "suspended" else "resume"), "work_id": work_id}
    if sid:
        event["sid"] = sid
    append_event(event)
    return True


def archive_work(work_id: str, reason: str = "manual", sid: str | None = None) -> bool:
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
    event = {"ev": "work_archive", "work_id": work_id, "reason": reason}
    if sid:
        event["sid"] = sid
    append_event(event)
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


def _closed_works_between(start_ms: int, end_ms: int) -> list[dict]:
    """Works finished in [start, end]: the dashboard's 完成工作."""
    works: list[dict] = []
    directory = paths()["closed_dir"]
    try:
        names = sorted(os.listdir(directory))
    except Exception:
        return works
    for name in names:
        if not name.endswith(".jsonl"):
            continue
        try:
            with open(os.path.join(directory, name), encoding="utf-8") as handle:
                for line in handle:
                    try:
                        task = json.loads(line)
                    except Exception:
                        continue
                    closed_at = int(task.get("closed_at") or 0)
                    if start_ms <= closed_at <= end_ms:
                        works.append(task)
        except Exception:
            continue
    return works


def _work_list(events: list[dict], completed: list[dict], start_ms: int) -> list[dict]:
    """One row per work for the dashboard table: 灯点 / 工作 / 类型 / 活跃 / 等待."""
    tasks: dict[str, dict] = {}
    for task in load_open().get("tasks", {}).values():
        if task.get("status", "open") == "open" and task.get("state", "open") == "open":
            tasks[str(task.get("task_id") or "")] = task
    finished = set()
    for task in completed:
        work_id = str(task.get("task_id") or "")
        if work_id:
            finished.add(work_id)
            tasks.setdefault(work_id, task)
    wait_ms: dict[str, int] = {}
    seen: set[str] = set()
    for event in events:
        work_id = str(event.get("work_id") or event.get("task_id") or "")
        if not work_id:
            continue
        seen.add(work_id)
        if event.get("ev") == "user_wait":
            wait_ms[work_id] = wait_ms.get(work_id, 0) + int(event.get("waiting_ms") or 0)

    minutes_cache: dict[str, float] = {}
    baseline_cache: dict[tuple, tuple] = {}
    rows = []
    for work_id, task in tasks.items():
        in_range = (
            work_id in seen
            or work_id in finished
            or int(task.get("last_seen_at") or 0) >= start_ms
        )
        if not in_range:
            continue
        if work_id not in minutes_cache:
            minutes_cache[work_id] = work_minutes(work_id)
        minutes = minutes_cache[work_id]
        key = (task.get("type"), task.get("difficulty"))
        if key not in baseline_cache:
            baseline_cache[key] = baseline_minutes(*key)
        baseline, source = baseline_cache[key]
        ratio = round(minutes / baseline, 2) if baseline else 0.0
        level = "green" if ratio < 0.6 else ("yellow" if ratio < 1.5 else "red")
        rows.append({
            "work_id": work_id,
            "goal": task.get("goal") or task.get("title") or task.get("summary") or "",
            "type": task.get("type") or "",
            "difficulty": task.get("difficulty") or "",
            "minutes": minutes,
            "wait_minutes": round(wait_ms.get(work_id, 0) / 60000.0, 1),
            "baseline": baseline,
            "baseline_source": source,
            "ratio": ratio,
            "level": level,
            "state": "done" if work_id in finished else task.get("state", "open"),
        })
    rows.sort(key=lambda row: row["minutes"], reverse=True)
    return rows[:50]


def period_summary(
    days: float = 7.0, now: int | None = None, start: int | None = None
) -> dict:
    """Range totals for the dashboard and the text report.

    `start` pins a natural-day window (the dashboard's 今天 / 7 天 / 30 天);
    without it the window stays rolling, which is what the CLI report uses.
    """
    end = now if now is not None else now_ms()
    start = int(start) if start is not None else end - int(days * 86400000)
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
    wait_by_day: dict[str, float] = {}
    for wait in waits:
        day = datetime.fromtimestamp(int(wait.get("t") or 0) / 1000).strftime("%Y-%m-%d")
        wait_by_day[day] = wait_by_day.get(day, 0.0) + int(wait.get("waiting_ms") or 0) / 60000.0

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

    total_minutes = sum(float(block.get("minutes") or 0) for block in blocks)
    wait_minutes = sum(int(event.get("waiting_ms") or 0) for event in waits) / 60000.0
    active_minutes = max(0.0, total_minutes - wait_minutes)
    completed = _closed_works_between(start, end)
    count = len(completed)
    day_keys = sorted(set(minutes_by_day) | set(wait_by_day))
    active_by_day = {
        day: round(max(0.0, minutes_by_day.get(day, 0.0) - wait_by_day.get(day, 0.0)), 1)
        for day in day_keys
    }

    top = sorted(works.values(), key=lambda item: item["minutes"], reverse=True)[:3]
    return {
        "days": days,
        "start": start,
        "end": end,
        "events": len(events),
        "blocks": len(blocks),
        "total_minutes": round(total_minutes, 1),
        "active_minutes": round(active_minutes, 1),
        "wait_minutes": round(wait_minutes, 1),
        "minutes_by_day": {day: round(value, 1) for day, value in sorted(minutes_by_day.items())},
        "active_by_day": active_by_day,
        "wait_by_day": {day: round(value, 1) for day, value in sorted(wait_by_day.items())},
        "completed": count,
        "completed_works": [
            {
                "work_id": task.get("task_id"),
                "goal": task.get("goal") or task.get("title") or "",
                "type": task.get("type") or "",
                "difficulty": task.get("difficulty") or "",
                "closed_at": task.get("closed_at"),
            }
            for task in completed
        ],
        "avg_active_minutes": round(active_minutes / count, 1) if count else None,
        "avg_wait_minutes": round(wait_minutes / count, 1) if count else None,
        "work_list": _work_list(events, completed, start),
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


def daily_breakdown(
    days: float = 14.0, now: int | None = None, start: int | None = None
) -> list[dict]:
    end = now if now is not None else now_ms()
    start = int(start) if start is not None else end - int(days * 86400000)
    events = _events_between(start, end)
    buckets: dict[str, dict] = {}
    for event in events:
        stamp = int(event.get("t") or 0)
        day = datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
        bucket = buckets.setdefault(day, {
            "date": day, "events": 0, "blocks": 0, "minutes": 0.0,
            "tools": 0, "failures": 0, "waits": 0, "rework": 0,
            "wait_minutes": 0.0, "works": set(),
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
            bucket["wait_minutes"] += int(event.get("waiting_ms") or 0) / 60000.0
        elif kind in ("tool_churn", "rework_signal"):
            bucket["rework"] += 1
        elif kind == "probe_report":
            work_id = event.get("work_id") or event.get("task_id")
            if work_id:
                bucket["works"].add(work_id)
    rows = []
    for day, bucket in sorted(buckets.items()):
        bucket["works"] = len(bucket["works"])
        bucket["minutes"] = round(bucket["minutes"], 1)
        bucket["wait_minutes"] = round(bucket["wait_minutes"], 1)
        bucket["active_minutes"] = round(max(0.0, bucket["minutes"] - bucket["wait_minutes"]), 1)
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
    reworks = [e for e in events if e.get("ev") in ("tool_churn", "rework_signal")]
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
    # works touched in this session, with health for each
    ordered: list[str] = []
    for report in reversed(reports):
        task_id = str(report.get("task_id") or "")
        if task_id and task_id not in ordered:
            ordered.append(task_id)
    session_works = []
    for task_id in ordered[:6]:
        health = work_health(task_id)
        if health:
            session_works.append(health)
    # never fall back to a work from another session: that reads as "wrong data"
    active = session_works[0] if session_works else None

    latest_cause = ""
    latest_category = ""
    latest_at = 0
    for report in reversed(reports):
        if report.get("cause") or report.get("category"):
            latest_cause = str(report.get("cause") or "")
            latest_category = str(report.get("category") or "")
            latest_at = int(report.get("t") or 0)
            break

    tool_rank = []
    for name, calls in tool_counts.items():
        if name.startswith("call_") or name == "tool":
            continue
        tool_rank.append({"name": name, "calls": calls, "failures": failure_counts.get(name, 0)})
    tool_rank.sort(key=lambda item: (item["failures"], item["calls"]), reverse=True)

    return {
        "tool_rank": tool_rank[:6],
        "latest_verdict": str(next((r.get("verdict") for r in reversed(reports) if r.get("verdict")), "")),
        "latest_cause": latest_cause,
        "latest_category": latest_category,
        "latest_at": latest_at,
        "sid": sid,
        "active_work": active,
        "session_works": session_works,
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


# ---------------------------------------------------------------------------
# Work health: active minutes, baseline, lamp

DIFFICULTY_DEFAULT_MINUTES = {"s": 10.0, "m": 30.0, "l": 90.0}
RETIRE_DAYS = float(os.environ.get("KIT_RETIRE_DAYS", "14"))


def _block_minutes(days: int = 5) -> dict[int, float]:
    table: dict[int, float] = {}
    for offset in range(days):
        day = (datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d")
        path = os.path.join(paths()["blocks_dir"], f"{day}.jsonl")
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    try:
                        row = json.loads(line)
                    except Exception:
                        continue
                    start = int(row.get("start") or 0)
                    if start:
                        table[start] = float(row.get("minutes") or 0)
        except Exception:
            continue
    return table


def work_minutes(work_id: str) -> float:
    """Active minutes of one work = closed linked blocks + the session's open block."""
    table = _block_minutes()
    events = load_events(limit=200000)
    prompts = [(int(event.get("t") or 0), event.get("sid")) for event in events if event.get("ev") == "prompt"]

    def sid_for(start: int):
        # the JS block start and the Python prompt timestamp differ by milliseconds
        for stamp, sid in prompts:
            if abs(stamp - start) < 5000:
                return sid
        return None
    def canonical(start: int) -> int:
        # DSH 的 JS block start 与 Python prompt 时间戳会差几毫秒：按 5 秒容差对齐。
        if start in table:
            return start
        best = start
        gap_best = 5000
        for key in table:
            gap = abs(key - start)
            if gap <= gap_best:
                gap_best = gap
                best = key
        return best

    total = 0.0
    # 一个 block 只归一个工作：对齐后同一 block 的多条 task_link，以最后一条为准。
    owner: dict[int, str] = {}
    for event in events:
        if event.get("ev") != "task_link":
            continue
        start = int(event.get("block_start") or 0)
        linked_work = str(event.get("task_id") or "")
        if not start or not linked_work:
            continue
        owner[canonical(start)] = linked_work
    linked: list[int] = [start for start, linked_work in owner.items() if linked_work == work_id]
    for start in linked:
        total += table.get(start, 0.0)

    # the block that has not closed yet, on whichever session opened this work
    task = load_open().get("tasks", {}).get(work_id) or {}
    sid = task.get("sid") or next((sid_for(start) for start in linked if sid_for(start)), None)
    if sid:
        open_start = None
        for event in events:
            if event.get("sid") != sid:
                continue
            if event.get("ev") == "prompt":
                open_start = int(event.get("t") or 0)
            elif event.get("ev") == "stop":
                open_start = None
        if open_start and open_start not in linked:
            total += max(0.0, (now_ms() - open_start) / 60000.0)
    return round(total, 1)


def _all_known_works() -> list[dict]:
    works = list(load_open().get("tasks", {}).values())
    for folder in ("closed_dir", "archive_dir"):
        directory = paths()[folder]
        try:
            for name in os.listdir(directory):
                if not name.endswith(".jsonl"):
                    continue
                with open(os.path.join(directory, name), encoding="utf-8") as handle:
                    for line in handle:
                        try:
                            works.append(json.loads(line))
                        except Exception:
                            continue
        except Exception:
            continue
    return works


def baseline_minutes(work_type: str | None, difficulty: str | None) -> tuple[float, str]:
    """Median active minutes of same type x difficulty; defaults when too few samples."""
    samples = []
    if work_type and difficulty:
        for task in _all_known_works():
            if task.get("type") != work_type or task.get("difficulty") != difficulty:
                continue
            minutes = work_minutes(str(task.get("task_id") or ""))
            if minutes > 0:
                samples.append(minutes)
    if len(samples) >= 3:
        samples.sort()
        middle = len(samples) // 2
        median = samples[middle] if len(samples) % 2 else (samples[middle - 1] + samples[middle]) / 2
        return round(median, 1), "history"
    return float(DIFFICULTY_DEFAULT_MINUTES.get(difficulty or "m", 30.0)), "default"


def work_health(work_id: str | None = None) -> dict | None:
    works = [task for task in load_open().get("tasks", {}).values() if task.get("state", "open") == "open"]
    if not works:
        return None
    if work_id:
        task = next((item for item in works if item.get("task_id") == work_id), None)
        if task is None:
            return None
    else:
        task = max(works, key=lambda item: int(item.get("last_seen_at") or 0))
    minutes = work_minutes(str(task.get("task_id") or ""))
    baseline, source = baseline_minutes(task.get("type"), task.get("difficulty"))
    ratio = round(minutes / baseline, 2) if baseline else 0.0
    level = "green" if ratio < 0.6 else ("yellow" if ratio < 1.5 else "red")
    return {
        "work_id": task.get("task_id"),
        "goal": task.get("goal") or task.get("title") or "",
        "type": task.get("type") or "",
        "difficulty": task.get("difficulty") or "",
        "minutes": minutes,
        "baseline": baseline,
        "baseline_source": source,
        "ratio": ratio,
        "level": level,
    }


def open_works_count(workspace: str | None = None) -> int:
    return len([
        task for task in load_open().get("tasks", {}).values()
        if task.get("status", "open") == "open" and task.get("state", "open") == "open"
    ])


def format_work_reminder(health: dict, config: dict) -> str:
    minutes = health.get("minutes", 0)
    baseline = health.get("baseline", 0)
    goal = health.get("goal") or health.get("work_id")
    return "\n".join([
        f"[crop] 黄线告警：{goal} 已经做了 {minutes:.0f} 分钟（黄线 {load_config().get('yellow_min', 10):.0f} 分钟）。",
        "先做三件事：",
        "1. 安抚用户一句：这个比预想久，我还在处理。",
        "2. 反思：现在的方法还对吗？有没有走错路、绕远路？",
        "3. 调 crop_report 写一行：cause=为什么慢；verdict=justified（活本来就难）或 not-justified（自己走错路）。",
    ])


def record_review(args: dict, context: dict | None = None) -> dict:
    """AI-judged rework level after user feedback. No keyword matching anywhere."""
    context = context or {}
    sid = str(context.get("sid") or "unknown")
    level = _text(args.get("rework"), "rework").lower()
    if level not in REVIEW_LEVELS:
        raise ValueError(f"rework must be one of {', '.join(REVIEW_LEVELS)}")
    sentiment = _text(args.get("sentiment"), "sentiment").lower()
    if sentiment and sentiment not in SENTIMENTS:
        raise ValueError(f"sentiment must be one of {', '.join(SENTIMENTS)}")
    reason = _text(args.get("reason"), "reason")
    work_id = normalize_task_id(args.get("work_id") or _latest_work_for_session(sid) or "")

    append_event({
        "ev": "self_review", "sid": sid, "work_id": work_id,
        "rework": level, "sentiment": sentiment, "reason": reason,
    })
    if work_id:
        value = load_open()
        task = value.get("tasks", {}).get(work_id)
        if task:
            task["last_review"] = {"rework": level, "sentiment": sentiment, "at": now_ms()}
            task["last_seen_at"] = now_ms()
            save_open(value)
    return {"work_id": work_id, "rework": level, "sentiment": sentiment, "reason": reason}


# ---------------------------------------------------------------------------
# Session <-> work bindings (many-to-many)

CLAIM_TTL_MIN = 30.0


def load_bindings() -> dict:
    path = paths()["bindings"]
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
            return data if isinstance(data, dict) else {"bindings": []}
    except Exception:
        return {"bindings": []}


def save_bindings(value: dict) -> None:
    write_json(paths()["bindings"], value)


def bind_session(sid: str, work_id: str, ttl_min: float = CLAIM_TTL_MIN) -> bool:
    """Point this session at a work; one active binding per session."""
    if not sid or not work_id:
        return False
    value = load_bindings()
    now = now_ms()
    kept = []
    for item in value.get("bindings", []):
        if item.get("sid") == sid:
            continue  # this session moves to the new work
        if float(item.get("expires_at") or 0) <= now:
            continue  # expired
        kept.append(item)
    kept.append({"sid": sid, "work_id": work_id, "claimed_at": now,
                 "expires_at": now + int(ttl_min * 60000)})
    value["bindings"] = kept
    save_bindings(value)
    # keep the legacy field in sync while old consumers still read it
    tasks = load_open().get("tasks", {})
    if work_id in tasks:
        tasks[work_id]["last_sid"] = sid
        tasks[work_id]["active_until"] = now + int(ttl_min * 60000)
        value_open = load_open()
        value_open["tasks"][work_id] = tasks[work_id]
        save_open(value_open)
    return True


def resolve_binding(sid: str) -> str:
    """The work this session is currently bound to, or an empty string."""
    now = now_ms()
    candidates = [
        item for item in load_bindings().get("bindings", [])
        if item.get("sid") == sid and float(item.get("expires_at") or 0) > now
    ]
    if not candidates:
        return ""
    newest = max(candidates, key=lambda item: int(item.get("claimed_at") or 0))
    return str(newest.get("work_id") or "")


def normalize_bindings() -> int:
    """One active binding per session: keep the newest, drop the rest."""
    value = load_bindings()
    now = now_ms()
    best: dict[str, dict] = {}
    dropped = 0
    for item in value.get("bindings", []):
        sid = item.get("sid")
        if not sid or float(item.get("expires_at") or 0) <= now:
            dropped += 1
            continue
        current = best.get(sid)
        if current is None or int(item.get("claimed_at") or 0) > int(current.get("claimed_at") or 0):
            if current is not None:
                dropped += 1
            best[sid] = item
        else:
            dropped += 1
    if dropped:
        value["bindings"] = list(best.values())
        save_bindings(value)
    return dropped


def release_binding(sid: str) -> None:
    value = load_bindings()
    now = now_ms()
    value["bindings"] = [item for item in value.get("bindings", [])
                         if not (item.get("sid") == sid and float(item.get("expires_at") or 0) > now)]
    save_bindings(value)


def migrate_bindings() -> int:
    """One-time: turn legacy last_sid into an active binding."""
    moved = 0
    value = load_bindings()
    have = {(item.get("sid"), item.get("work_id")) for item in value.get("bindings", [])}
    now = now_ms()
    newest: dict[str, dict] = {}
    for task in load_open().get("tasks", {}).values():
        sid = task.get("last_sid")
        if sid:
            keep = newest.get(sid)
            if keep is None or int(task.get("last_seen_at") or 0) > int(keep.get("last_seen_at") or 0):
                newest[sid] = task
    active_sids = {item.get("sid") for item in value.get("bindings", [])
                   if float(item.get("expires_at") or 0) > now}
    allowed = {sid: task.get("task_id") for sid, task in newest.items() if sid not in active_sids}
    for task in load_open().get("tasks", {}).values():
        sid, work_id = task.get("last_sid"), task.get("task_id")
        if not sid or not work_id or (sid, work_id) in have:
            continue
        if allowed.get(sid) != work_id:
            continue
        value.setdefault("bindings", []).append({
            "sid": sid, "work_id": work_id,
            "claimed_at": int(task.get("last_seen_at") or now),
            "expires_at": now + int(CLAIM_TTL_MIN * 60000),
        })
        moved += 1
    if moved:
        save_bindings(value)
    return moved


def record_human_wait(sid: str, reason: str, started_at: int, ended_at: int | None = None) -> dict:
    ended = int(ended_at or now_ms())
    waiting_ms = max(0, ended - int(started_at))
    event = {
        "ev": "human_wait", "sid": sid, "reason": reason or "ask-human",
        "started_at": int(started_at), "ended_at": ended, "waiting_ms": waiting_ms,
    }
    append_event(event)
    return event


# ---------------------------------------------------------------------------
# Silent retirement: no activity for two weeks -> pending retire, never auto-close


def mark_stale_works(days: float | None = None, now: int | None = None) -> list[str]:
    threshold = days if days is not None else RETIRE_DAYS
    cutoff = (now if now is not None else now_ms()) - int(threshold * 86400000)
    value = load_open()
    marked: list[str] = []
    for task in value.get("tasks", {}).values():
        if task.get("state", "open") != "open":
            continue
        if int(task.get("last_seen_at") or 0) >= cutoff:
            continue
        task["state"] = "pending_retire"
        task["pending_retire_at"] = now_ms()
        marked.append(str(task.get("task_id")))
        append_event({"ev": "work_pending_retire", "work_id": task.get("task_id"),
                      "days": threshold})
    if marked:
        save_open(value)
    return marked


def pending_retire_works() -> list[dict]:
    return [task for task in load_open().get("tasks", {}).values()
            if task.get("state") == "pending_retire"]


def format_pending_retire(limit: int = 5) -> str:
    works = pending_retire_works()
    if not works:
        return ""
    names = "、".join(f"{w.get('task_id')}（{ago(w.get('last_seen_at', 0))}）" for w in works[:limit])
    more = f" 等 {len(works)} 条" if len(works) > limit else ""
    return (
        f"[crop] 有长期没动的工作待清退：{names}{more}。"
        "问用户一句：这些还要继续吗？要继续 / 先挂起 / 确认清退。"
    )
