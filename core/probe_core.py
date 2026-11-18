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

import hashlib
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

try:
    import crop_voice as voice
except Exception:  # pragma: no cover - copy pools are optional at runtime
    voice = None

try:
    import crop_templates
except Exception:  # pragma: no cover - user templates are optional at runtime
    crop_templates = None

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
# 一个工作被鞭策这么多次后，收尾必须给改进（issue #43 / 主动复盘）。
RETRO_FORCE_NUDGES = 2
# Built-in types keep their own calibration history.  The list is not a closed
# enum: users can define their own short slug, and `explore` is first-class
# because research/triage estimates behave differently from implementation.
WORK_TYPES = ("design", "implement", "debug", "docs", "explore")
TYPE_SLUG = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
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
        "voice": os.path.join(root, "voice.json"),
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


_EVENT_CACHE: dict = {"key": None, "events": []}


def load_events(limit: int = MAX_EVENTS_READ, sid: str | None = None) -> list[dict]:
    """Read the event log once per file version.

    Global summaries call this dozens of times (per work, per window); without
    the cache every call re-parsed the whole JSONL and the dashboard crawled.
    """
    path = paths()["events"]
    try:
        stat = os.stat(path)
        key = (path, stat.st_mtime_ns, stat.st_size)
    except Exception:
        key = None
    if key != _EVENT_CACHE["key"]:
        events: list[dict] = []
        try:
            with open(path, encoding="utf-8") as handle:
                for line in handle:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except Exception:
                        continue
                    events.append(event)
        except Exception:
            events = []
        _EVENT_CACHE["key"] = key
        _EVENT_CACHE["events"] = events
    events = _EVENT_CACHE["events"]
    if sid is not None:
        events = [event for event in events if event.get("sid") == sid]
    return events[-limit:]


# ---------------------------------------------------------------------------
# Config


PERSONAS = ("owner", "senior", "colleague", "free")

DEFAULT_CONFIG = {
    "yellow_min": 10.0,
    "red_min": 30.0,
    "remind_min": 15.0,
    "user_facing": True,
    "max_open_tasks": 3,
    # 预估工时（expect_min）为时钟时的档位倍数；没报预期才退回 yellow_min / red_min。
    "warm_ratio": 0.8,
    "red_ratio": 1.5,
    "overdue_ratio": 2.5,
    # 鞭策风格：owner 奴隶主 / senior 和蔼的前辈 / colleague 经验丰富的同事 / free 放任自由的家长
    "persona": "owner",
}


def load_config() -> dict:
    config = dict(DEFAULT_CONFIG)
    try:
        stored = read_json(paths()["config"], {})
        if isinstance(stored, dict):
            for key, value in stored.items():
                if key == "persona" and isinstance(value, str) and value in PERSONAS:
                    config[key] = value
                elif key in config and isinstance(value, (int, float, bool)):
                    config[key] = value
    except Exception:
        pass
    for env, key, cast in (
        ("KIT_TASK_YELLOW_MIN", "yellow_min", float),
        ("KIT_TASK_RED_MIN", "red_min", float),
        ("KIT_TASK_REMIND_MIN", "remind_min", float),
        ("KIT_TASK_PERSONA", "persona", str),
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
    if report.get("sediment"):
        task["sediment"] = report["sediment"]
    if report.get("expect_min"):
        current = float(report["expect_min"])
        previous = task.get("expect_min")
        try:
            previous = float(previous) if previous else None
        except Exception:
            previous = None
        if previous and abs(previous - current) > 0.01:
            reason = report.get("cause") or report.get("summary") or ""
            task.setdefault("expect_history", []).append({
                "from": previous, "to": current, "reason": reason, "at": report["t"],
            })
            append_event({
                "ev": "estimate_revised", "sid": report.get("sid", ""),
                "work_id": task_id, "from": previous, "to": current, "reason": reason,
            })
        # 改口不回溯：新预期从改口这一刻重新给一个冷静窗口，别让“把预期改小”
        # 立刻把已经跑了 25 分钟的工作判成 overdue（卡片 82f42c50）。
        # 普通 progress 报同一个数字不算改口，不刷新时间点。
        if previous is None or (previous and abs(previous - current) > 0.01):
            task["expect_set_at"] = report["t"]
            if previous is not None:
                task["expect_revised_at"] = report["t"]
        task["expect_min"] = current
        task["expect_why"] = report.get("expect_why") or task.get("expect_why", "")
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


def pending_task(task_id: str, report: dict) -> None:
    """end 提交收尾：工作进入 pending_confirm，等人类确认才真正关闭。"""
    value = load_open()
    tasks = value.setdefault("tasks", {})
    task = tasks.get(task_id)
    if task is None:
        return
    task["status"] = "pending_confirm"
    task["pending_at"] = now_ms()
    task["summary"] = report.get("summary") or task.get("summary", "")
    for key in ("what", "cost", "next_time", "verdict", "sediment"):
        if report.get(key):
            task[key] = report[key]
    task["last_seen_at"] = int(report.get("t") or now_ms())
    save_open(value)


def pending_confirm_tasks(sid: str | None = None) -> list[dict]:
    """已提交收尾、等人类确认的工作（不参与认领，只用于提醒/面板）。"""
    value = load_open()
    tasks = [
        task for task in value.get("tasks", {}).values()
        if task.get("status") == "pending_confirm"
    ]
    tasks.sort(key=lambda task: -float(task.get("pending_at") or 0))
    if sid:
        same = [task for task in tasks if task.get("last_sid") == sid]
        return same or tasks
    return tasks


def confirm_works(task_ids: list[str], sid: str = "", actor: str = "human") -> dict:
    """人类确认收尾：批量关闭 pending_confirm 工作。"""
    value = load_open()
    tasks = value.get("tasks", {})
    confirmed: list[str] = []
    missing: list[str] = []
    for task_id in task_ids:
        task = tasks.get(task_id)
        if not task or task.get("status") != "pending_confirm":
            missing.append(task_id)
            continue
        confirmed.append(task_id)
    at = now_ms()
    for task_id in confirmed:
        owner_sid = sid or str(tasks[task_id].get("last_sid") or "")
        append_event({
            "ev": "work_confirm",
            "sid": owner_sid,
            "work_id": task_id,
            "confirmed_by": actor,
            "confirmed_at": at,
        })
        append_event({"ev": "work_close", "sid": owner_sid, "work_id": task_id,
                      "confirmed_by": actor})
        close_task(task_id)
    return {"confirmed": confirmed, "missing": missing, "confirmed_at": at}


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
    if work_type and not TYPE_SLUG.match(work_type):
        raise ValueError("type must be a short slug: 1-32 lowercase letters, digits or hyphens")
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
    expect_min = args.get("expect_min")
    if expect_min is None or str(expect_min).strip() == "":
        expect_min = None
    else:
        try:
            expect_min = float(expect_min)
        except Exception:
            raise ValueError("expect_min must be a number of minutes")
        if expect_min <= 0:
            raise ValueError("expect_min must be greater than 0")
        if expect_min > 1440:
            raise ValueError("expect_min must be at most 1440 minutes (24h)")
    expect_why = _text(args.get("expect_why"), "expect_why")
    what = _text(args.get("what"), "what")
    cost = _text(args.get("cost"), "cost")
    next_time = _text(args.get("next_time"), "next_time")
    artifact = _text(args.get("artifact"), "artifact")
    if phase == "end" and not what:
        raise ValueError("phase=end requires what: 用一句话写这次做了什么（cost / next_time 可选）")
    sediment = _text(args.get("sediment"), "sediment")
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
        "expect_min": expect_min,
        "expect_why": expect_why,
        "rework": rework,
        "sentiment": sentiment,
        "artifact": artifact,
        "what": what,
        "cost": cost,
        "next_time": next_time,
        "sediment": sediment,
    }


# ---------------------------------------------------------------------------
# Active time accounting
#
# A block is a wall-clock span between a prompt and the turn end, but work
# minutes are not wall minutes: a gap in observed activity or an explicit wait
# (the human left, an ask-human tool is open) is not agent time.  Issue #18
# tracks the three ways the old accounting overcharged: a dangling block
# crossing a session restart, a stop charged to "now" after hours of silence,
# and wait events that were recorded but never subtracted.

WAIT_EVENTS = ("user_wait", "idle_gap", "human_wait")


def _last_session_start(sid: str) -> int:
    floor = 0
    for event in load_events(sid=sid):
        if event.get("ev") == "session_start":
            floor = max(floor, int(event.get("t") or 0))
    return floor


def _valid_block_start(sid: str, value) -> int | None:
    """A block start only counts if it belongs to the current session turn."""
    try:
        start = int(value or 0)
    except Exception:
        return None
    if not start or start > now_ms():
        return None
    floor = _last_session_start(sid)
    # A host may record the prompt a few hundred ms before its session_start
    # in the same turn; only a start from a previous generation is stale.
    if floor and start < floor - 5000:
        return None
    return start


def _wait_span(event: dict) -> tuple[int, int] | None:
    kind = event.get("ev")
    stamp = int(event.get("t") or 0)
    if kind == "human_wait":
        start = int(event.get("started_at") or stamp)
        end = int(event.get("ended_at") or stamp)
    elif kind in ("user_wait", "idle_gap"):
        end = stamp
        start = end - int(event.get("waiting_ms") or event.get("gap_ms") or 0)
    else:
        return None
    if end <= start:
        return None
    return start, end


def _wait_ms(sid: str, start: int, end: int) -> int:
    """Merged wait milliseconds overlapping [start, end] for one session."""
    spans = []
    for event in load_events(sid=sid):
        if event.get("ev") not in WAIT_EVENTS:
            continue
        span = _wait_span(event)
        if not span:
            continue
        left, right = max(span[0], start), min(span[1], end)
        if right > left:
            spans.append((left, right))
    spans.sort()
    total, cur_left, cur_right = 0, None, None
    for left, right in spans:
        if cur_right is None or left > cur_right:
            if cur_right is not None:
                total += cur_right - cur_left
            cur_left, cur_right = left, right
        else:
            cur_right = max(cur_right, right)
    if cur_right is not None:
        total += cur_right - cur_left
    return total


def active_ms(sid: str, start: int, end: int) -> int:
    """Observed activity between start and end.

    Count only gaps between adjacent events (and the boundaries) that are
    shorter than IDLE_MIN; a longer silence is the human being away, not work.
    Explicit wait spans are subtracted even inside a short gap.
    """
    try:
        start, end = int(start), int(end)
    except Exception:
        return 0
    if end <= start:
        return 0
    points = {start, end}
    for event in load_events(sid=sid):
        stamp = int(event.get("t") or 0)
        if start <= stamp <= end:
            points.add(stamp)
    threshold = int(IDLE_MIN * 60000)
    active = 0
    ordered = sorted(points)
    for left, right in zip(ordered, ordered[1:]):
        if right - left <= threshold:
            active += right - left
    active -= _wait_ms(sid, start, end)
    return max(0, active)


def active_minutes(sid: str, start: int, end: int) -> float:
    return round(active_ms(sid, start, end) / 60000.0, 1)


def _just_returned(sid: str, window_ms: int = 90000) -> bool:
    """True when the newest wait event is fresh: do not nudge on the doorstep."""
    now = now_ms()
    for event in reversed(load_events(sid=sid)):
        span = _wait_span(event)
        if not span:
            continue
        return now - span[1] <= window_ms
    return False


def _write_block(sid: str, start: int, end: int, work_id: str = "",
                 abandoned: bool = False) -> dict:
    end = max(int(start), int(end))
    minutes = active_minutes(sid, start, end)
    tools = [
        event for event in load_events(sid=sid)
        if event.get("ev") == "tool" and start <= int(event.get("t") or 0) <= end
    ]
    event = {
        "ev": "stop", "sid": sid, "t": end, "block_start": start,
        "minutes": minutes, "work_id": work_id,
    }
    if abandoned:
        event["abandoned"] = True
    append_event(event)
    append_jsonl(os.path.join(paths()["blocks_dir"], f"{today()}.jsonl"), {
        "t": end,
        "host": host(),
        "sid": sid,
        "start": start,
        "end": end,
        "minutes": minutes,
        "wall_minutes": round(max(0.0, (end - start) / 60000.0), 1),
        "active_version": 1,
        "abandoned": bool(abandoned),
        "tools": len(tools),
        "failed_tools": sum(1 for event in tools if event.get("is_error")),
        "work_id": work_id,
    })
    if work_id:
        link_task(work_id, start, sid)
    return event


def _close_dangling(sid: str, end: int | None = None,
                    trust_end: bool = False) -> bool:
    """Close a prompt that never got its turn_end.

    A host restart is not evidence that the old turn kept running, so the
    default truncates at the last event we actually saw.  A host that is alive
    and explicitly ending the session (``trust_end``) may use the full span.
    """
    start = current_block_start(sid)
    if not start:
        return False
    boundary = int(end or now_ms())
    last = start
    for event in load_events(sid=sid):
        stamp = int(event.get("t") or 0)
        if start <= stamp <= boundary:
            last = max(last, stamp)
    stop_at = boundary if trust_end else min(boundary, max(last, start))
    work_id = resolve_binding(sid) or ""
    _write_block(sid, start, stop_at, work_id, abandoned=True)
    return True


# ---------------------------------------------------------------------------
# Report recording


def _work_signal_count(work_id: str, kinds: tuple[str, ...]) -> int:
    """Events of these kinds attributed to one work (issue #43 / proactive retro)."""
    if not work_id:
        return 0
    return sum(1 for event in load_events()
               if event.get("ev") in kinds and str(event.get("work_id") or "") == work_id)


def _nudge_tiers(work_id: str) -> list[str]:
    """Distinct nudge tiers recorded for one work (used to draft the retro)."""
    tiers: list[str] = []
    for event in load_events():
        if event.get("ev") not in ("reminder", "nudge"):
            continue
        if str(event.get("work_id") or "") != work_id:
            continue
        tier = str(event.get("tier") or "").strip()
        if tier and tier not in tiers:
            tiers.append(tier)
    return tiers


def _retro_pending(work_id: str, nudges: int, tiers: list[str]) -> dict:
    """A concrete retro record, not a reminder: problem + suggested direction + next step.

    The point of the proactive retro is that the improvement actually lands
    (improve field + crop_adjust ledger entry), so the system writes a pending
    record the agent must resolve.  A pure nudge could be ignored; this row
    stays visible until an adjustment closes it.
    """
    severe = any(tier in ("red", "overdue") for tier in tiers)
    suggested = ("先拆任务或先交付一个可验证的阶段，别一口气拖到超时"
                 if severe else
                 "检查是否卡在循环里，或把重复动作换成一次性方案")
    return {
        "work_id": work_id,
        "problem": f"被鞭策 {nudges} 次（{', '.join(tiers) or 'n/a'}）",
        "suggested_fix": suggested,
        "next": ("把这次的真实改进写进本次 end 的 improve，再调用 "
                 'crop_adjust(action="record", metric="work_minutes", evidence=..., '
                 "note=..., work_id=...) 建档；建档后这条待复盘会自动关闭。"),
    }


def record_report(args: dict, context: dict | None = None) -> dict:
    context = context or {}
    report = validate_report(args)
    runtime = read_runtime()
    sid = str(context.get("sid") or runtime.get("sid") or "unknown")
    workspace = str(context.get("workspace") or runtime.get("workspace") or os.getcwd())
    # Only use the shared runtime file when it belongs to this session; a stale
    # block_start from another session is exactly what made two works charge
    # the same wall clock in Issue #18.
    block_start = context.get("block_start")
    if not block_start and runtime.get("sid") == sid:
        block_start = runtime.get("block_start")
    block_start = _valid_block_start(sid, block_start)
    minutes = active_minutes(sid, block_start, now_ms()) if block_start else 0.0
    # Capture what the work looked like before this report mutates it: the
    # estimate revision note and the work-switch warning both need the old value.
    previous_open = load_open().get("tasks", {}) or {}
    previous_task = previous_open.get(report["task_id"]) or {}
    previous_expect = previous_task.get("expect_min")
    previous_changes = len(previous_task.get("expect_history") or [])
    switched_from = ""
    if report["phase"] == "start":
        old_binding = resolve_binding(sid)
        if old_binding and old_binding != report["task_id"]:
            old_task = previous_open.get(old_binding) or {}
            if (old_task.get("status", "open") in ("open", "pending_confirm")
                    and old_task.get("state", "open") == "open"):
                switched_from = old_binding
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
        "expect_min": report.get("expect_min"),
        "expect_why": report.get("expect_why", ""),
        "rework": report.get("rework", ""),
        "sentiment": report.get("sentiment", ""),
        "type": report.get("type", ""),
        "difficulty": report.get("difficulty", ""),
        "sediment": report.get("sediment", ""),
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

    # Link this report to the reminder it is answering, when there is one.
    # Reminders and reports used to be unrelated events, which forced the panel
    # to guess by timestamp; this makes new data map exactly.
    try:
        report_at = int(record.get("t") or 0)
        for prior in reversed(load_events(sid=sid)):
            if prior.get("ev") == "reminder":
                if report_at - int(prior.get("t") or 0) <= 2 * 60 * 60 * 1000:
                    record["reply_to"] = int(prior.get("t") or 0)
                break
            if prior.get("ev") in ("prompt", "stop"):
                break
    except Exception:
        pass
    record["origin"] = "reminder" if record.get("reply_to") else "self"
    if (report.get("expect_min") and previous_expect is not None
            and abs(float(previous_expect) - float(report["expect_min"])) > 0.01):
        record["estimate_revised"] = {
            "from": float(previous_expect),
            "to": float(report["expect_min"]),
            "count": previous_changes + 1,
        }
    if switched_from:
        record["switched_from"] = switched_from

    already_closed = (report["phase"] == "end"
                      and report["task_id"] not in (load_open().get("tasks") or {}))
    already_pending = (report["phase"] == "end"
                       and (load_open().get("tasks") or {}).get(
                           report["task_id"], {}).get("status") == "pending_confirm")
    append_jsonl(os.path.join(paths()["reports_dir"], f"{today()}.jsonl"), record)
    write_runtime(sid, workspace, block_start)
    if not already_closed and not already_pending:
        append_event({**record, "ev": "probe_report"})
    if report["phase"] == "end":
        if already_closed or already_pending:
            # 二次收尾（补沉淀/补小结）：不重复关单、不重复释放绑定
            if report.get("sediment"):
                append_event({"ev": "work_sediment", "sid": sid, "work_id": report["task_id"],
                              "sediment": report["sediment"]})
            return {**record, "already_closed": True}
        if report.get("what") or report.get("cost") or report.get("next_time"):
            append_event({
                "ev": "work_summary",
                "sid": sid,
                "work_id": report["task_id"],
                "verdict": report.get("verdict", ""),
        "improve": report.get("improve", ""),
        "expect_min": report.get("expect_min"),
        "expect_why": report.get("expect_why", ""),
        "rework": report.get("rework", ""),
        "sentiment": report.get("sentiment", ""),
                "what": report.get("what", ""),
                "cost": report.get("cost", ""),
                "next_time": report.get("next_time", ""),
                "artifact": report.get("artifact", ""),
            })
            append_jsonl(os.path.join(paths()["reports_dir"], f"{today()}.jsonl"), {
                "t": now_ms(), "host": host(), "sid": sid, "work_id": report["task_id"],
                "ev": "work_summary", "what": report.get("what", ""),
                "cost": report.get("cost", ""), "next_time": report.get("next_time", ""),
                "artifact": report.get("artifact", ""),
            })
        if report.get("sediment"):
            append_event({"ev": "work_sediment", "sid": sid, "work_id": report["task_id"],
                          "sediment": report["sediment"]})
        # B 方案：end 只是「提交收尾」——人类确认前不关闭，超时也不自动确认。
        pending_task(report["task_id"], report)
        append_event({"ev": "work_pending", "sid": sid, "work_id": report["task_id"],
                      "verdict": report.get("verdict", "")})
        # #43：收尾不立刻解绑——下一轮开头不该再被催 start，墙钟也要能跨轮累计。
        mark_binding_ended(sid, report["task_id"])
        record["pending_confirm"] = True
    else:
        _upsert_task(record, workspace)
        bind_session(sid, report["task_id"])
        if report["phase"] == "start":
            append_event({"ev": "work_claim", "sid": sid, "work_id": report["task_id"]})
            if switched_from:
                # 切工作不能让旧工作“静默失联”：留一条可追溯事件，工具结果里也会警告。
                append_event({"ev": "work_switch", "sid": sid,
                              "from": switched_from, "to": report["task_id"]})
            old_task = previous_open.get(switched_from) or {}
            if old_task.get("status") == "pending_confirm" and not _confirm_reminded(switched_from, "switch"):
                append_event({"ev": "confirm_reminder", "sid": sid, "task_id": switched_from,
                              "kind": "switch", "at": record["t"]})
                record["switched_pending"] = switched_from
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
    # 主动复盘：多次鞭策后必须给出改进；一路顺利则提示复盘「为什么顺利」。
    if report["phase"] == "end":
        nudges = _work_signal_count(report["task_id"], ("reminder", "nudge"))
        reworks = _work_signal_count(report["task_id"], ("self_review", "rework_signal"))
        has_improve = bool(str(report.get("improve") or "").strip())
        record["nudge_count"] = nudges
        if nudges >= RETRO_FORCE_NUDGES and not has_improve:
            tiers = _nudge_tiers(report["task_id"])
            pending = _retro_pending(report["task_id"], nudges, tiers)
            record["retro_due"] = True
            record["retro_pending"] = pending
            append_event({"ev": "retro_due", "sid": sid, "work_id": report["task_id"],
                          "nudges": nudges})
            append_event({"ev": "retro_pending", "sid": sid, **pending})
        elif nudges == 0 and reworks == 0 and not has_improve:
            record["retro_smooth"] = True
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
    # A turn that never got its turn_end (host restart, interrupted turn) must
    # not lend its start time to the next session.  Close it at the last event
    # we actually saw, then open the new session boundary.
    try:
        _close_dangling(sid, now_ms())
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
    # A new prompt ends any previous turn that never got its turn_end (host
    # without session_start, interrupted turn).  Close it at the last observed
    # event before the new block starts, so its minutes are not lost and its
    # start time cannot leak into this turn.
    try:
        _close_dangling(sid, now_ms())
    except Exception:
        pass
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
            # 时间归属只认本会话自己的绑定；别的会话的工作不能当兜底。
            work_id = resolve_binding(sid) or ""
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
        # A session boundary closes the previous generation of turns even if
        # the dangling prompt never got a synthetic stop (older data, raced
        # writes).  Never look past it.
        if kind in ("stop", "session_start"):
            return None
    return None


REPEAT_TOOL_THRESHOLD = int(os.environ.get("KIT_REPEAT_TOOL", "5"))


def record_tool(sid: str, name: str, is_error: bool, target: str | None = None) -> None:
    tool = str(name or "")[:80]
    work_id = resolve_binding(sid) or ""
    event = {"ev": "tool", "sid": sid, "name": tool, "is_error": bool(is_error), "t": now_ms()}
    if work_id:
        event["work_id"] = work_id
    # 产物归因与重复检测共用一次读取：target 只在读取类工具上有值，
    # 命中是历史台账与本次 target 的比对，结果落 artifact_hit 事件。
    history: list[dict] = []
    try:
        history = load_events(limit=0) if target else load_events(sid=sid)
    except Exception:
        history = []
    if target:
        try:
            _record_artifact_hits(sid, work_id, tool, target, history)
        except Exception:
            pass
    append_event(event)
    try:
        start = current_block_start(sid)
        base = [item for item in history if item.get("sid") == sid]
        recent = [
            item for item in (base + [event])
            if item.get("ev") == "tool" and (not start or int(item.get("t") or 0) >= start)
        ]
        tail = recent[-REPEAT_TOOL_THRESHOLD:]
        if len(tail) >= REPEAT_TOOL_THRESHOLD and all(event.get("name") == tool for event in tail):
            failures = sum(1 for event in tail if event.get("is_error"))
            churn = {
                "ev": "tool_churn", "sid": sid,
                "kind": "repeat-tool", "tool": tool,
                "count": len(tail), "failures": failures,
            }
            if work_id:
                churn["work_id"] = work_id
            append_event(churn)
    except Exception:
        pass


def record_stop(sid: str) -> None:
    start = current_block_start(sid)
    if not start:
        append_event({"ev": "stop", "sid": sid})
        return
    # Mechanical attribution: the block ends inside this session only, and the
    # minutes are observed activity rather than raw wall clock (Issue #18).
    work_id = resolve_binding(sid) or ""
    _write_block(sid, start, now_ms(), work_id)
    write_runtime(sid, read_runtime().get("workspace", ""), None)


def miss_nudge(streak: int, sid: str | None = None) -> str:
    """Missing-report nudge; streaks of 2+ switch to the harsher pool.

    With no live binding there is no work to补记: the situation is either the
    post-end cool-down (交付/汇报动作) or a session that never bound, which the
    binding hint already covers.  Nagging here was the "上一轮没登记" noise
    (card 0f9927d4).
    """
    if sid and (not resolve_binding(sid) or binding_ended(sid)):
        return ""
    tier = "miss_streak" if int(streak or 0) >= 2 else "miss"
    if voice is not None:
        try:
            text = voice.miss(int(streak or 0), paths().get("voice"))
            _record_nudge(sid or "unknown", "miss", tier, text, streak=int(streak or 0))
            return text
        except Exception:
            pass
    fallback = "[crop] 上一轮跑了挺久但没有登记。如果这个工作有进展或卡点，调 crop_update 写一行。"
    _record_nudge(sid or "unknown", "miss", tier, fallback, streak=int(streak or 0))
    return fallback


def _confirm_reminded(task_id: str, kind: str) -> bool:
    return any(event.get("ev") == "confirm_reminder"
               and event.get("task_id") == task_id
               and event.get("kind") == kind
               for event in load_events(limit=5000))


def pending_confirm_hint(context: dict | None = None) -> str:
    """待确认收尾的工作：首轮提醒一次；超过 30 分钟再提醒一次（不自动确认）。"""
    context = context or {}
    sid = str(context.get("sid") or "")
    pending = pending_confirm_tasks(sid or None)
    if not pending:
        return ""
    parts = []
    now = now_ms()
    timeout_ms = int(float(os.environ.get("KIT_CONFIRM_REMIND_MIN") or 30) * 60000)
    for task in pending[:3]:
        task_id = str(task.get("task_id") or "")
        if not task_id:
            continue
        pending_at = int(task.get("pending_at") or 0)
        if not _confirm_reminded(task_id, "start"):
            append_event({"ev": "confirm_reminder", "sid": sid, "task_id": task_id,
                          "kind": "start", "at": now})
            parts.append(
                f'[crop] 工作「{task_id}」已提交收尾，等待人类确认（crop_confirm）。'
                f'确认前不计为完成、计时继续。'
            )
            continue
        if pending_at and now - pending_at > timeout_ms and not _confirm_reminded(task_id, "timeout"):
            append_event({"ev": "confirm_reminder", "sid": sid, "task_id": task_id,
                          "kind": "timeout", "at": now})
            minutes = int((now - pending_at) / 60000)
            parts.append(
                f'[crop] 工作「{task_id}」提交收尾已经 {minutes} 分钟，人类还没确认。'
                f'再问用户一句：这个工作确认收尾了吗？要收尾就调 crop_confirm。'
            )
    return "\n".join(parts)


def binding_hint(sid: str) -> str:
    """What to say when a session used tools without a live binding.

    After an end, "bind before you work" is the wrong instruction: delivery,
    screenshots and the final report are follow-up, not a new work.  Give the
    two real exits — side-issue or a new start — instead of a blanket nag
    (cards d0007eaf / 9d5ee6b0).
    """
    last_report = None
    for event in reversed(load_events(sid=sid)):
        if event.get("ev") == "probe_report":
            last_report = event
            break
    ended = bool(last_report and last_report.get("phase") == "end")
    if crop_templates is not None:
        try:
            rendered = crop_templates.render("binding-hint", {
                "last_work": str((last_report or {}).get("task_id") or ""),
                "last_phase": "end" if ended else "",
                "example_task_id": "<短名>",
            })
        except Exception:
            rendered = None
        if rendered is not None:
            return _template_prefix(rendered)
    if ended:
        work_id = last_report.get("task_id") or "上一件工作"
        return (
            f'[crop] 上一件工作（{work_id}）已经收尾，当前没有绑定。'
            "交付、汇报、截图这类收尾动作不用再绑；只是收尾追问就记 "
            'category="side-issue"；确实是新工作，再 crop_update(phase="start", '
            'task_id="<短名>", summary="<一句话>", expect_min=预估分钟)。'
        )
    return (
        '[crop] 你已经在调用工具，但还没有绑定任何工作。先调 crop_update(phase="start", '
        'task_id="<2-3 个词的短名>", summary="<一句话>", expect_min=预估分钟) 再继续，'
        "这样这段时间才有归属；顺手报个预期时长，鞭子就按你的数字算。"
    )


def record_session_end(sid: str) -> None:
    try:
        _close_dangling(sid, now_ms(), trust_end=True)
    except Exception:
        pass
    append_event({"ev": "session_end", "sid": sid})


def _latest_work_for_session(sid: str) -> str | None:
    for event in reversed(load_events(sid=sid)):
        if event.get("ev") == "probe_report" and event.get("task_id"):
            return str(event["task_id"])
    return None


def _persona_nudge(tier: str, meta: dict, persona: str) -> str:
    """Neutral guidance for the non-slave-owner personas."""
    minutes = float(meta.get("m") or 0)
    expect = meta.get("e")
    header = f"[crop] 已经 {fmt_minutes(minutes)}"
    if expect:
        header += f"，你报的是 {fmt_minutes(float(expect))}"
    action = "顺手 crop_update 写一行：在做什么 / 卡在哪 / 下一步。"
    if persona == "senior":
        return (
            f"{header}。\n"
            "别一个人硬扛：卡住的地方随时可以问我，也可以先说说你打算怎么试，我们一起看。\n"
            + action
        )
    if persona == "colleague":
        return (
            f"{header}。\n"
            "要不要先拆一下？先把最小可验证的一步做出来，再回头补剩下的。\n"
            + action
        )
    return "" if persona == "free" else ""


def _nudge(tier: str, meta: dict) -> str:
    """Draw one nudge; plain wording is the fallback if the copy pools are gone."""
    persona = str(load_config().get("persona") or "owner")
    if persona == "free":
        return ""
    if persona in ("senior", "colleague"):
        return _persona_nudge(tier, meta, persona)
    if voice is not None:
        try:
            return voice.reminder(tier, meta, paths().get("voice"))
        except Exception:
            pass
    return format_reminder(float(meta.get("m") or 0), load_config())


def _cooldown_active(sid: str, minutes: float, tier: str = "", work_id: str = "") -> bool:
    """One nudge per remind_min window per work, whatever the tier.

    A tier upgrade used to bypass the cooldown, which is how one minute could
    produce both a yellow and an overdue (card 82f42c50).  ``tier`` is kept in
    the signature for callers, but only the window and the work matter.
    """
    window = float(minutes) * 60000
    for event in reversed(load_events(sid=sid)):
        if event.get("ev") != "reminder":
            continue
        if work_id and event.get("work_id") not in (None, work_id):
            continue
        return now_ms() - int(event.get("t") or 0) < window
    return False


def _voice_template_active(tier: str) -> bool:
    """True when voice/<tier>.md overrides the built-in pool."""
    if crop_templates is None:
        return False
    try:
        return bool(crop_templates.voice_lines(tier))
    except Exception:
        return False


def _record_nudge(sid: str, strategy: str, tier: str, text: str, **extra) -> None:
    """Audit what a nudge actually injected: tier, metric and the text itself."""
    try:
        row = {"ev": "nudge", "sid": sid, "strategy": strategy, "tier": tier,
               "source": "template" if _voice_template_active(tier) else "builtin",
               "text": str(text or "")[:500]}
        row.update(extra)
        append_event(row)
    except Exception:
        pass


def reminder_tier(health: dict, config: dict) -> str | None:
    """Which notch of the contract this work is at (None = stay quiet).

    With an estimate the ladder is relative to the AI's own number: nothing
    before 0.8E, warm up to E, yellow to 1.5E, red to 2.5E, overdue beyond.
    Without one we keep the legacy minutes so existing works still get nudged.
    """
    minutes = float(health.get("minutes") or 0)
    expect = health.get("expect_min")
    if expect:
        try:
            ratio = minutes / float(expect)
        except Exception:
            ratio = 0.0
        if ratio < float(config.get("warm_ratio", 0.8)):
            return None
        if ratio < 1.0:
            return "warm"
        if ratio < float(config.get("red_ratio", 1.5)):
            return "yellow"
        if ratio < float(config.get("overdue_ratio", 2.5)):
            return "red"
        return "overdue"
    if minutes >= float(config.get("yellow_min", 10.0)):
        return "yellow"
    return None


def _nudge_meta(health: dict, config: dict) -> dict:
    expect = health.get("expect_min")
    meta = {
        "m": float(health.get("minutes") or 0),
        "e": float(expect) if expect else None,
        "goal": health.get("goal") or "",
        "default": float(config.get("yellow_min", 10.0)),
        "line": "黄线",
        "changes": int(health.get("expect_changes") or 0),
    }
    if not expect:
        meta["no_expect"] = True
    return meta


def maybe_work_reminder(sid: str) -> str:
    config = load_config()
    # The human just came back from idle/ask-human: not the moment to crack the
    # whip.  The next step will re-evaluate against active minutes.
    if _just_returned(sid):
        return ""
    task_id = _latest_work_for_session(sid)
    if not task_id:
        return ""
    # 已收尾但仍保留的绑定（#43）：交付、汇报阶段不该再抽鞭子。
    if binding_ended(sid):
        return ""
    health = work_health(task_id)
    if not health:
        return ""
    # 刚刚改口：先按新数字重新起算，别在同一分钟里把旧预期攒下的超时全砸出来。
    revised_at = health.get("expect_revised_at")
    if revised_at:
        try:
            if now_ms() - int(revised_at) < float(config["remind_min"]) * 60000:
                return ""
        except Exception:
            pass
    tier = reminder_tier(health, config)
    if not tier:
        return ""
    if _cooldown_active(sid, float(config["remind_min"]), tier, work_id=task_id):
        return ""
    persona = str(config.get("persona") or "owner")
    if persona == "free":
        return ""
    append_event({
        "ev": "reminder", "sid": sid, "work_id": task_id, "tier": tier,
        "minutes": round(float(health.get("minutes") or 0), 1),
        "expect_min": health.get("expect_min"),
        "persona": persona,
        "kind": "work",
    })
    text = _nudge(tier, _nudge_meta(health, config))
    _record_nudge(sid, "timeout", tier, text, work_id=task_id, kind="work",
                  minutes=round(float(health.get("minutes") or 0), 1),
                  expect_min=health.get("expect_min"))
    return text


def maybe_reminder(sid: str) -> str:
    """One call per step: the estimate ladder wins, legacy minutes are the net."""
    config = load_config()
    if _just_returned(sid):
        return ""
    task_id = _latest_work_for_session(sid)
    health = work_health(task_id) if task_id else None
    has_estimate = bool(health and health.get("expect_min"))
    start = current_block_start(sid)
    if start:
        minutes = active_minutes(sid, start, now_ms())
        # 有预估时别把块时钟整段跳过：用墙钟兜底，防报个 3 分钟就永久关掉红线（#43）。
        red = 60.0 if has_estimate else float(config["red_min"])
        if minutes >= red and not _cooldown_active(sid, float(config["remind_min"]), "red"):
            persona = str(config.get("persona") or "owner")
            if persona == "free":
                return ""
            append_event({
                "ev": "reminder", "sid": sid, "block_start": start, "tier": "red",
                "minutes": round(minutes, 1), "persona": persona, "kind": "block",
            })
            text = _nudge("red", {
                "m": minutes, "e": None, "goal": "",
                "default": red, "line": "红线", "no_expect": True,
            })
            _record_nudge(sid, "timeout", "red", text, kind="block",
                          minutes=round(minutes, 1))
            return text
    return maybe_work_reminder(sid)


# ---------------------------------------------------------------------------
# Prompt text


def fmt_minutes(minutes: float) -> str:
    """Duration in the copy's voice ('3 小时 30 分'); plain fallback without pools."""
    if voice is not None:
        try:
            return voice.fmt_minutes(minutes)
        except Exception:
            pass
    try:
        value = int(round(float(minutes or 0)))
    except Exception:
        return "0 分钟"
    if value < 60:
        return f"{value} 分钟"
    hours, rest = divmod(value, 60)
    return f"{hours} 小时" if not rest else f"{hours} 小时 {rest} 分"


def ago(ms: int) -> str:
    try:
        minutes = max(0, int((now_ms() - int(ms)) / 60000))
    except Exception:
        return "未知"
    if minutes < 60:
        return f"{minutes} 分钟前"
    return f"{minutes // 60} 小时前"


TONE_HINT = (
    "说话方式：跟用户像同事吐槽，短句 + 自嘲，别客服腔；卡住或超时先自嘲一句，"
    "再说真实原因和下一步。梗是包装，技术结论、代码、风险提示照常严谨。"
)


ESTIMATE_HINT = (
    "绑定后先报预期（不阻断，但必须报）：crop_update 带 expect_min=多少分钟 + expect_why=一句为什么。"
    "提醒按你报的数字算——在你报的时长内不会有人抽鞭子；觉得报少了，就带着理由改。"
)


def _session_work_counts(sid: str | None) -> tuple[int, int]:
    """(本会话的未完成工作数, 其他会话的未完成工作数)。"""
    rows = [
        task for task in load_open().get("tasks", {}).values()
        if task.get("status", "open") == "open" and task.get("state", "open") == "open"
    ]
    if not sid:
        return len(rows), 0
    mine = sum(1 for task in rows if task.get("last_sid") == sid)
    return mine, len(rows) - mine


def _template_prefix(text: str) -> str:
    """Templates carry the body only; the ``[crop]`` marker is ours."""
    if not text:
        return text
    return text if text.lstrip().startswith("[crop]") else "[crop] " + text


def _candidate_lines(tasks: list[dict], count: int) -> list[str]:
    """Task rows + the "另有 N 条" tail, without the title line."""
    lines = []
    for task in tasks:
        lines.append(
            f"- {task.get('task_id')} — {task.get('goal') or task.get('title') or ''}"
            f"（{ago(task.get('last_seen_at', 0))}）"
        )
    extra = count - len(tasks)
    if extra > 0:
        lines.append(f"另有 {extra} 条，用 crop_works 查看或筛选。")
    return lines


def format_candidates(tasks: list[dict], total: int | None = None,
                      sid: str | None = None) -> str:
    """Unbound session: one imperative action first, then at most three candidates.

    本会话 / 其他会话分开数，别把全局待办当成“本会话的活”（card 9da932ed）。
    用户模板 ``start-hint`` 存在时替换整段（变量见 docs/DESIGN-CUSTOM-TEMPLATES.md）。
    """
    count = total if total is not None else len(tasks)
    mine, others = _session_work_counts(sid)
    if crop_templates is not None:
        try:
            rendered = crop_templates.render("start-hint", {
                "mine": str(mine),
                "others": str(others or count),
                "candidates": "\n".join(_candidate_lines(tasks, count)),
                "example_task_id": str(tasks[0].get("task_id") or "") if tasks else "",
                "limit": "3",
            })
        except Exception:
            rendered = None
        if rendered is not None:
            return _template_prefix(rendered)
    if sid and mine == 0:
        lines = [f"[crop] 本会话还没有绑定工作；其他会话有 {others or count} 条在跑。候选（最多 3 条）："]
    elif sid:
        lines = [f"[crop] 未完成的工作：本会话 {mine} 条 / 其他会话 {others} 条。最近活跃的是："]
    else:
        lines = [f"[crop] 未完成的工作有 {count} 条，最近活跃的是："]
    lines.extend(_candidate_lines(tasks, count))
    example = tasks[0].get("task_id")
    lines.append(
        "先选一条，再动手：接着做用 "
        f'crop_update(phase="start", task_id="{example}")；新开用 2-3 个词的短名。'
    )
    return "\n".join(lines)


def format_open_tasks(tasks: list[dict], total: int | None = None, sid: str | None = None) -> str:
    # A live binding decides the first-turn line: the bound work may have
    # dropped out of the three candidates (another session touched it, or newer
    # works pushed it past the limit), so ask the binding, not the list.
    if sid and resolve_binding(sid):
        bound_line = work_status_line(None, sid)
        if bound_line:
            return bound_line
    if not tasks:
        return ("[crop] 本机当前没有进行中的工作。新开一条："
                "crop_update(phase=\"start\", task_id=\"<短名>\", summary=\"<一句话>\", "
                "expect_min=预估分钟, expect_why=\"一句为什么\")。"
                "提醒按你报的时长算：在你自己报的范围内不会被打扰。")
    claimed = next((task for task in tasks if sid and task.get("last_sid") == sid), None)
    if claimed:
        return work_status_line(None, sid)
    return "\n".join([
        format_candidates(tasks, total, sid),
        ESTIMATE_HINT,
        "尽量让一个会话只对应一件工作；要做的其实是几件事就拆开，一件一件来。",
    ])


def format_start_hint(tasks: list[dict], total: int | None = None, sid: str | None = None) -> str:
    """First turn of a session: binding list + the tone it should talk in.

    The tone lives in the system prompt too, but that section only exists after
    the host reloads the plugin; this line makes a fresh session talk right even
    when the plugin load happened before the copy changed.
    """
    text = format_open_tasks(tasks, total, sid)
    return f"{text}\n{TONE_HINT}" if text else TONE_HINT


def format_reminder(minutes: float, config: dict) -> str:
    lines = [
        f"[crop] 红线告警：这一轮已经 {minutes:.0f} 分钟（红线 {float(config['red_min']):.0f}），用户等了很久还没拿到结果。",
        "先别急着继续，确认三件事：",
        "1. 还走在正确的路上吗？有没有更快的路径？",
        "2. 能不能先给用户一个阶段性交付？",
        "3. 安抚用户，并把判断写进 crop_update（cause + verdict）。",
        "请做两件事：",
    ]
    if config.get("user_facing", True):
        lines.append(
            "1. 对用户说一句简短的人话，例如：不好意思，这个做得有点久，我还在处理，"
            "后面会把这段记下来优化。"
        )
        lines.append(
            '2. 调用 crop_update：phase="blocked" 或 "progress"，task_id="...", '
            'summary="现在在做什么", cause="为什么慢", next="下一步", '
            'category="serial-wait|repeat-pitfall|design-churn|decision-batching|'
            'tool-misuse|no-evidence|spec-drift|human-wait|other"。'
        )
    else:
        lines.append(
            '1. 调用 crop_update：phase="blocked" 或 "progress"，task_id="...", '
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
    if bound_id and binding_ended(sid):
        return (f"[crop] 上一件工作（{bound_id}）已经收尾，本会话继续聊或交付都不用重新绑定；"
                "确实是新工作，再 crop_update(phase=\"start\", task_id=\"<短名>\", summary=\"<一句话>\")。")
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
        minutes = float(health.get("minutes", 0.0))
        expect = health.get("expect_min")
        note = "" if claimed else "（上个会话认领，仍在有效期内）"
        if expect:
            gap = minutes - float(expect)
            clock = (f"已进行 {fmt_minutes(minutes)} / 你报的 {fmt_minutes(expect)}"
                     + (f"（超 {fmt_minutes(gap)}）" if gap >= 0.5 else f"（还剩 {fmt_minutes(-gap)}）"))
            tail = "留意用户是否把话题岔到另一件事（分叉），或上一轮的产出是否被推翻（返工）；有变化就更新一行。"
        else:
            yellow = float(load_config().get("yellow_min", 10.0))
            overtime = "已超过黄线" if minutes >= yellow else "未超时"
            clock = f"已进行 {fmt_minutes(minutes)}，{overtime}（黄线 {fmt_minutes(yellow)}）"
            tail = ("还没报预期时长：开工或下次上报时给个数字（crop_update 带 expect_min），"
                    "鞭子就按你的数字算。留意分叉与返工；有变化就更新一行。")
        return (
            f"[crop] 当前工作：{aim}（{current.get('task_id')}）{note}。"
            f"{clock}；上次更新 {ago(current.get('last_seen_at', 0))}。{tail}"
        )
    count = open_works_count(workspace)
    return "\n".join([
        format_candidates(tasks, count, sid),
        ESTIMATE_HINT,
        "尽量让一个会话只对应一件工作；要做的其实是几件事就拆开，一件一件来。",
    ])


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


def _events_between(start_ms: int, end_ms: int, host: str | None = None) -> list[dict]:
    events = load_events(limit=200000)
    return [event for event in events
            if start_ms <= int(event.get("t") or 0) <= end_ms
            and (not host or event.get("host") == host)]


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
    days: float = 7.0, now: int | None = None, start: int | None = None,
    host: str | None = None,
) -> dict:
    """Range totals for the dashboard and the text report.

    `start` pins a natural-day window (the dashboard's 今天 / 7 天 / 30 天);
    without it the window stays rolling, which is what the CLI report uses.
    """
    end = now if now is not None else now_ms()
    start = int(start) if start is not None else end - int(days * 86400000)
    events = _events_between(start, end, host)
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


def _natural_start(days: float) -> int:
    """Natural-day window start: 今天 = 0 点，7 天 = 含今天在内的 7 个自然日。"""
    return start_of_today_ms() - int((max(1.0, float(days)) - 1) * 86400000)


def _window_metrics(start_ms: int, end_ms: int, host: str | None = None) -> dict:
    """Friction numbers for one window — the raw material of the global page.

    Only facts the event log already carries: no judgement, no inference. A
    metric whose denominator is 0 comes back as None so the panel can say
    "no sample" instead of drawing a fake trend.
    """
    events = _events_between(start_ms, end_ms, host)
    blocks = [event for event in events if event.get("ev") == "stop"]
    waits = [event for event in events if event.get("ev") == "user_wait"]
    tools = [event for event in events if event.get("ev") == "tool"]
    churn = [event for event in events if event.get("ev") in ("tool_churn", "rework_signal")]
    failures = [event for event in tools if event.get("is_error")]
    total = sum(float(block.get("minutes") or 0) for block in blocks)
    wait = sum(int(wait_event.get("waiting_ms") or 0) for wait_event in waits) / 60000.0
    active = max(0.0, total - wait)
    completed = len(_closed_works_between(start_ms, end_ms))
    return {
        "start": start_ms,
        "end": end_ms,
        "events": len(events),
        "blocks": len(blocks),
        "tools": len(tools),
        "failures": len(failures),
        "failure_rate": round(len(failures) / len(tools), 4) if tools else None,
        "churn": len(churn),
        "churn_per_block": round(len(churn) / len(blocks), 3) if blocks else None,
        "tools_per_block": round(len(tools) / len(blocks), 3) if blocks else None,
        "total_minutes": round(total, 1),
        "active_minutes": round(active, 1),
        "wait_minutes": round(wait, 1),
        "wait_ratio": round(wait / total, 4) if total > 0 else None,
        "completed": completed,
        "active_per_completed": round(active / completed, 1) if completed else None,
    }


def _median(values: list[float]) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return round(ordered[middle], 1)
    return round((ordered[middle - 1] + ordered[middle]) / 2, 1)


def improvement_summary(days: float = 7.0, host: str | None = None) -> dict:
    """Global page payload: friction this window against the one before it.

    The comparison window has the same length, so a 7-day panel compares with
    the previous 7 natural days. Both windows come from the same event log; a
    fresh install leaves the previous window empty (events = 0, metrics None)
    instead of inventing a baseline.
    """
    days = max(1.0, float(days))
    end = now_ms()
    start = _natural_start(days)
    previous_start = start - int(days * 86400000)

    current = _window_metrics(start, end, host)
    previous = _window_metrics(previous_start, start - 1, host)

    summary = period_summary(days, start=start, host=host)
    works = summary.get("work_list") or []
    per_work = [float(row.get("minutes") or 0) for row in works if row.get("minutes")]
    current["works"] = len(works)
    current["median_active_per_work"] = _median(per_work)

    events = _events_between(start, end, host)
    reports = [event for event in events if event.get("ev") == "probe_report"]
    improvements = []
    for report in reports:
        text = str(report.get("improve") or "").strip()
        if text:
            improvements.append({
                "work_id": report.get("work_id") or report.get("task_id") or "",
                "goal": report.get("goal") or "",
                "improve": text,
                "t": report.get("t"),
                "phase": report.get("phase") or "",
                "cause": str(report.get("cause") or "")[:160],
                "category": report.get("category") or "",
                "summary": str(report.get("summary") or "")[:160],
            })
    categories: dict[str, int] = {}
    for report in reports:
        category = str(report.get("category") or "").strip()
        if category:
            categories[category] = categories.get(category, 0) + 1

    hosts = sorted({str(event.get("host"))
                    for event in _events_between(start, end) if event.get("host")})
    return {
        "days": days,
        "start": start,
        "end": end,
        "host": host or "",
        "hosts": hosts,
        "current": current,
        "previous": previous,
        "daily": daily_breakdown(days, start=start, host=host),
        "work_list": works,
        "improvements": improvements,
        "categories": sorted(
            ({"category": key, "count": value} for key, value in categories.items()),
            key=lambda item: item["count"],
            reverse=True,
        ),
    }


def daily_breakdown(
    days: float = 14.0, now: int | None = None, start: int | None = None,
    host: str | None = None,
) -> list[dict]:
    end = now if now is not None else now_ms()
    start = int(start) if start is not None else end - int(days * 86400000)
    events = _events_between(start, end, host)
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
                label = str(event.get("goal") or event.get("summary") or "").strip()
                if label:
                    item["goal_full"] = label[:200]
                    if event.get("goal") or not item["goal"]:
                        item["goal"] = label[:24] + ("…" if len(label) > 24 else "")
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


def _work_titles_full(events: list[dict]) -> dict:
    titles: dict[str, str] = {}
    for event in events:
        if event.get("ev") != "probe_report":
            continue
        work_id = str(event.get("task_id") or event.get("work_id") or "")
        if not work_id:
            continue
        label = str(event.get("goal") or event.get("summary") or "").strip()
        if label:
            titles[work_id] = label[:200]
    return titles


def _work_titles(events: list[dict]) -> dict:
    """Friendly labels for AI-generated work ids (goal > summary), per work."""
    titles: dict[str, str] = {}
    for event in events:
        if event.get("ev") != "probe_report":
            continue
        work_id = str(event.get("task_id") or event.get("work_id") or "")
        if not work_id:
            continue
        label = str(event.get("goal") or event.get("summary") or "").strip()
        if label:
            short = label.strip()
            titles[work_id] = short[:20] + ('…' if len(short) > 20 else '')
    return titles


def _latest_reflection(events: list[dict]) -> dict:
    """Latest report that carries a judgement or a cause, for the panel's reflection card."""
    latest = {}
    for event in reversed(events):
        if event.get("ev") != "probe_report":
            continue
        verdict = str(event.get("verdict") or "")
        cause = str(event.get("cause") or "")
        improve = str(event.get("improve") or "")
        if not (verdict or cause or improve):
            continue
        latest = {
            "verdict": verdict,
            "cause": cause,
            "improve": improve,
            "at": int(event.get("t") or 0),
            "work_id": str(event.get("task_id") or event.get("work_id") or ""),
            "revisions": _expect_revisions(events, str(event.get("task_id") or "")),
        }
        break
    return latest


def _expect_revisions(events: list[dict], task_id: str) -> int:
    values = {
        event.get("expect_min") for event in events
        if event.get("ev") == "probe_report"
        and str(event.get("task_id") or "") == task_id
        and event.get("expect_min") is not None
    }
    return max(0, len(values) - 1)


def _session_stats(events: list[dict]) -> dict:
    reminders = [e for e in events if e.get("ev") == "reminder"]
    reports = [e for e in events if e.get("ev") == "probe_report"]
    tools = [e for e in events if e.get("ev") == "tool"]
    blocked = [e for e in reports if e.get("phase") == "blocked"]
    needs_fix = [e for e in reports if e.get("verdict") == "not-justified" and e.get("improve")]
    needs_idea = [e for e in reports if e.get("verdict") == "not-justified" and not e.get("improve")]
    return {
        "reminders": len(reminders),
        "overdue": sum(1 for e in reminders if e.get("tier") == "overdue"),
        "blocked": len(blocked),
        "rework": sum(1 for e in events if e.get("ev") in ("tool_churn", "rework_signal")),
        "failed_tools": sum(1 for e in tools if e.get("is_error")),
        "needs_fix": len(needs_fix),
        "needs_idea": len(needs_idea),
        "self_driven": sum(1 for e in reports if (e.get("origin") or "self") == "self" and e.get("cause")),
    }


def _session_interventions(events: list[dict]) -> list[dict]:
    """Every report that names a problem, newest first — the panel pairs these
    with whip markers so the timeline and the reflection list line up."""
    out = []
    for event in reversed(events):
        if event.get("ev") != "probe_report":
            continue
        cause = str(event.get("cause") or "")
        improve = str(event.get("improve") or "")
        verdict = str(event.get("verdict") or "")
        if not (cause or improve or verdict):
            continue
        out.append({
            "at": int(event.get("t") or 0),
            "work_id": str(event.get("task_id") or event.get("work_id") or ""),
            "phase": str(event.get("phase") or ""),
            "cause": cause[:200],
            "improve": improve[:200],
            "verdict": verdict,
            "category": str(event.get("category") or ""),
            "reply_to": event.get("reply_to"),
            "origin": event.get("origin") or ("reminder" if event.get("reply_to") else "self"),
        })
        if len(out) >= 8:
            break
    return out


def _session_improvements(events: list[dict]) -> list[dict]:
    """Improve / next_time lines raised in this session, newest first, de-duplicated."""
    out, seen = [], set()
    for event in reversed(events):
        kind = event.get("ev")
        work_id = str(event.get("work_id") or event.get("task_id") or "")
        text = ""
        source = ""
        if kind == "probe_report" and event.get("improve"):
            text, source = str(event.get("improve") or ""), "improve"
        elif kind == "work_summary" and event.get("next_time"):
            text, source = str(event.get("next_time") or ""), "next_time"
        text = text.strip()
        if not text or text in seen:
            continue
        seen.add(text)
        out.append({
            "text": text[:200], "work_id": work_id,
            "at": int(event.get("t") or 0), "source": source,
            "phase": str(event.get("phase") or ("end" if source == "next_time" else ""))[:20],
            "cause": str(event.get("cause") or "")[:160],
            "category": str(event.get("category") or "")[:40],
            "summary": str(event.get("summary") or event.get("what") or "")[:160],
        })
        if len(out) >= 5:
            break
    return out


def _latest_work_summary(events: list[dict], work_id: str) -> dict:
    """The AI's own closing note for one work (what / cost / next_time)."""
    for event in reversed(events):
        if event.get("ev") != "work_summary":
            continue
        if str(event.get("work_id") or "") != work_id:
            continue
        return {
            "what": str(event.get("what") or ""),
            "cost": str(event.get("cost") or ""),
            "next_time": str(event.get("next_time") or ""),
            "artifact": str(event.get("artifact") or ""),
            "at": int(event.get("t") or 0),
        }
    return {}


def _session_timeline(events: list[dict]) -> dict:
    """Render-ready slices for the session panel: runs, waits, whips and reports."""
    blocks, waits, whips, reports = [], [], [], []
    for event in events:
        kind = event.get("ev")
        try:
            at = int(event.get("t") or 0)
        except Exception:
            at = 0
        if not at:
            continue
        if kind == "stop":
            minutes = float(event.get("minutes") or 0)
            blocks.append({
                "kind": "block", "t": at,
                "start": at - int(minutes * 60000),
                "minutes": round(minutes, 1),
                "work_id": str(event.get("work_id") or ""),
            })
        elif kind == "user_wait":
            waits.append({
                "kind": "wait", "t": at,
                "ms": int(event.get("waiting_ms") or 0),
                "work_id": str(event.get("work_id") or ""),
            })
        elif kind == "reminder":
            whips.append({
                "kind": "whip", "t": at,
                "work_id": str(event.get("work_id") or ""),
                "persona": str(event.get("persona") or "owner"),
                "tier": str(event.get("tier") or ""),
                "minutes": event.get("minutes"),
                "expect_min": event.get("expect_min"),
            })
        elif kind == "probe_report":
            reports.append({
                "kind": "report", "t": at,
                "phase": str(event.get("phase") or ""),
                "work_id": str(event.get("task_id") or event.get("work_id") or ""),
                "cause": str(event.get("cause") or ""),
                "category": str(event.get("category") or ""),
                "verdict": str(event.get("verdict") or ""),
                "improve": str(event.get("improve") or ""),
                "summary": str(event.get("summary") or ""),
            })
    return {"blocks": blocks, "waits": waits, "whips": whips, "reports": reports}


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

    if current <= 0 and events:
        last_kind = events[-1].get("ev")
        if last_kind in ("prompt", "tool", "reminder", "user_wait"):
            last_prompt = next((e for e in reversed(events) if e.get("ev") == "prompt"), None)
            if last_prompt:
                current = max(0.0, (now_ms() - int(last_prompt.get("t") or 0)) / 60000.0)

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
    active = next((item for item in session_works if item.get("state") == "open"), None)
    pending_work = next((item for item in session_works if item.get("state") == "pending_confirm"), None)
    ended = next((item for item in session_works if item.get("state") == "closed"), None)
    if pending_work:
        pending_work = {**pending_work,
                        "report": _latest_work_summary(events, str(pending_work.get("work_id") or ""))}
    if ended:
        ended = {**ended, "report": _latest_work_summary(events, str(ended.get("work_id") or ""))}

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
        "pending_work": pending_work,
        "ended_work": ended,
        "recent_blocks": recent[-5:][::-1],
        "session_started_at": int(events[0].get("t") or 0) if events else 0,
        "persona": str(load_config().get("persona") or "owner"),
        "interventions": _session_interventions(events),
        "stats": _session_stats(events),
        "work_titles": _work_titles(events),
        "work_titles_full": _work_titles_full(events),
        "timeline": _session_timeline(events),
        "reflection": _latest_reflection(events),
        "improvements": _session_improvements(events),
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


_BLOCK_CACHE: dict = {"key": None, "table": {}}


def _block_minutes(days: int = 5) -> dict[int, float]:
    day_list = [(datetime.now() - timedelta(days=offset)).strftime("%Y-%m-%d") for offset in range(days)]
    key = []
    for day in day_list:
        path = os.path.join(paths()["blocks_dir"], f"{day}.jsonl")
        try:
            stat = os.stat(path)
            key.append((path, stat.st_mtime_ns, stat.st_size))
        except Exception:
            key.append((path, 0, 0))
    key = tuple(key)
    if key == _BLOCK_CACHE["key"]:
        return _BLOCK_CACHE["table"]
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
    _BLOCK_CACHE["key"] = key
    _BLOCK_CACHE["table"] = table
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
    ordered = sorted(table)
    from bisect import bisect_left

    def canonical(start: int) -> int:
        # DSH 的 JS block start 与 Python prompt 时间戳会差几毫秒：按 5 秒容差对齐。
        if start in table:
            return start
        pos = bisect_left(ordered, start)
        best = start
        gap_best = 5000
        for candidate in (ordered[pos - 1] if pos else None, ordered[pos] if pos < len(ordered) else None):
            if candidate is None:
                continue
            gap = abs(candidate - start)
            if gap <= gap_best:
                gap_best = gap
                best = candidate
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
        # A link can point at a block that has not closed yet (crop_report
        # start links the open block); do not add zero and then skip it below.
        if start in table:
            total += table[start]

    # the block that has not closed yet, on whichever session opened this work
    task = load_open().get("tasks", {}).get(work_id) or {}
    sid = task.get("sid") or next((sid_for(start) for start in linked if sid_for(start)), None)
    if sid:
        open_start = None
        for event in events:
            if event.get("sid") != sid:
                continue
            if event.get("ev") == "session_start":
                open_start = None
            elif event.get("ev") == "prompt":
                open_start = int(event.get("t") or 0)
            elif event.get("ev") == "stop":
                open_start = None
        if open_start and canonical(open_start) not in table:
            total += active_minutes(sid, open_start, now_ms())
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
    if work_id:
        task = next((item for item in works if item.get("task_id") == work_id), None)
        if task is None:
            # A finished work still explains what a session did; the panel
            # renders it as 已收尾 instead of pretending the session was idle.
            task = next(
                (item for item in _all_known_works() if item.get("task_id") == work_id),
                None,
            )
        if task is None:
            return None
    else:
        if not works:
            return None
        task = max(works, key=lambda item: int(item.get("last_seen_at") or 0))
    minutes = work_minutes(str(task.get("task_id") or ""))
    baseline, source = baseline_minutes(task.get("type"), task.get("difficulty"))
    ratio = round(minutes / baseline, 2) if baseline else 0.0
    level = "green" if ratio < 0.6 else ("yellow" if ratio < 1.5 else "red")
    expect = task.get("expect_min")
    try:
        expect = float(expect) if expect else None
    except Exception:
        expect = None
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
        # 主时钟：AI 自己报的预期工时；没有就退回 yellow_min / red_min
        "expect_min": expect,
        "expect_why": task.get("expect_why") or "",
        "expect_changes": len(task.get("expect_history") or []),
        "expect_set_at": task.get("expect_set_at"),
        "expect_revised_at": task.get("expect_revised_at"),
        "state": ("pending_confirm" if str(task.get("status") or "") == "pending_confirm"
                  else ("closed" if str(task.get("status") or "") == "closed" else "open")),
        "closed_at": int(task.get("closed_at") or 0) or None,
        "pending_at": int(task.get("pending_at") or 0) or None,
    }


def open_works_count(workspace: str | None = None) -> int:
    return len([
        task for task in load_open().get("tasks", {}).values()
        if task.get("status", "open") == "open" and task.get("state", "open") == "open"
    ])


def format_work_reminder(health: dict, config: dict) -> str:
    tier = reminder_tier(health, config) or "yellow"
    return _nudge(tier, _nudge_meta(health, config))


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


def mark_binding_ended(sid: str, work_id: str) -> None:
    """Keep the binding, flag it as ended.

    Releasing it at ``end`` made the next turn look unbound, so the host nudged
    the agent to start again — a start→end→start loop with empty check-ins
    (issue #43).  Keeping the row also lets the wall clock keep accumulating
    across turns instead of restarting from zero.
    """
    value = load_bindings()
    now = now_ms()
    for item in value.get("bindings", []):
        if item.get("sid") == sid and float(item.get("expires_at") or 0) > now:
            item["ended_at"] = now
            if work_id:
                item["work_id"] = work_id
    save_bindings(value)


def binding_ended(sid: str) -> bool:
    """True when the session's live binding points at a work that already ended."""
    value = load_bindings()
    now = now_ms()
    for item in value.get("bindings", []):
        if item.get("sid") == sid and float(item.get("expires_at") or 0) > now:
            return bool(item.get("ended_at"))
    return False


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


# ---------------------------------------------------------------------------
# Health: 采集工具在一线到底有没有在干活
#
# 回答的不是「AI 用了哪个工具」，而是「工具本身是不是哑了」。三条口径纪律：
# 1. 事件分四类（会话 / 工作 / 调用 / 按需），每类用各自的锚点算覆盖，不能混着比；
# 2. 按需事件（提醒、离开、枝节）不亮灯，只报计数——否则全是假警报；
# 3. 指标全部从原始事件派生，不新增采集、不落第二套真相。
# ---------------------------------------------------------------------------

# (事件, 显示名, 类别, 必需字段, 缺字段时关联的看板卡)
# 类别: session=每会话一次 / work=每工作一次 / call=每次调用 / on_demand=按需触发
_HEALTH_PASSIVE = (
    ("session_start", "会话开始", "session", ("sid",), ""),
    ("session_end", "会话结束", "session", ("sid",), ""),
    ("prompt", "回合开始", "session", ("sid",), ""),
    ("stop", "回合结束", "session", ("sid", "minutes"), ""),
    ("task_link", "归属链接", "work", ("task_id", "block_start"), ""),
    ("work_claim", "工作认领", "work", ("work_id", "sid"), ""),
    ("tool", "工具调用", "call", ("sid", "name", "work_id"), "c9cb276b"),
    ("reminder", "超时提醒", "on_demand", ("sid", "minutes"), ""),
    ("user_wait", "等人时长", "on_demand", ("sid", "waiting_ms"), ""),
    ("idle_gap", "离开间隔", "on_demand", ("sid", "gap_ms"), ""),
    ("work_archive", "工作归档", "on_demand", ("work_id",), ""),
)

# (事件, 显示名, 类别, 必需字段, 缺字段时关联的看板卡, 是否已上线)
_HEALTH_ACTIVE = (
    ("probe_report", "工作上报", "work", ("phase", "task_id", "summary"), "", True),
    ("work_close", "工作关闭", "work", ("work_id",), "", True),
    ("work_summary", "收尾小结", "ended", ("what",), "42ae110d", True),
    ("side_issue", "枝节记录", "on_demand", ("work_id", "note"), "", True),
    ("adjustment", "改进记录", "work", ("metric", "before", "after"), "fd0acdc3", True),
)

# 只有这些宿主会发对应事件；别的宿主的会话不该算进分母，否则永远显示「没采到」。
_HEALTH_HOST_SUPPORT = {
    "session_end": {"claude-code"},
}

HEALTH_SCOPE_LABEL = {
    "session": "会话",
    "work": "工作",
    "call": "调用",
    "ended": "收尾",
    "on_demand": "按需",
}

HEALTH_COMPLETE_OK = 0.95
HEALTH_COVERAGE_OK = {"session": 0.80, "work": 0.70, "ended": 0.80, "call": 0.90}
HEALTH_CLOSURE_OK = 0.70


def _health_field_ok(event: dict, field: str) -> bool:
    value = event.get(field)
    if value is None:
        return False
    if isinstance(value, str):
        return bool(value.strip())
    return True


def _health_completeness(events: list[dict], fields: tuple[str, ...]) -> tuple[float, dict[str, int]]:
    if not events:
        return 1.0, {}
    missing = {field: 0 for field in fields}
    for event in events:
        for field in fields:
            if not _health_field_ok(event, field):
                missing[field] += 1
    total = len(events) * len(fields)
    return 1.0 - (sum(missing.values()) / total), missing


def _health_lamp(
    klass: str, events: int, coverage: float | None, completeness: float, denominator: int
) -> str:
    """灯语义按类别分开：按需事件永远不亮红黄。"""
    if klass == "on_demand":
        return "gray"
    if events == 0:
        return "red" if denominator else "gray"
    if completeness < HEALTH_COMPLETE_OK:
        return "yellow"
    if coverage is None:
        return "green"
    threshold = HEALTH_COVERAGE_OK.get(klass, 0.80)
    return "green" if coverage >= threshold else "yellow"


def _health_trend(events: list[dict], start: int, end: int, days: int) -> list[dict]:
    counts: dict[str, int] = {}
    for event in events:
        stamp = int(event.get("t") or 0)
        if not stamp:
            continue
        day = datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
        counts[day] = counts.get(day, 0) + 1
    rows = []
    for offset in range(days - 1, -1, -1):
        stamp = end - offset * 86400000
        day = datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
        rows.append({"date": day, "count": counts.get(day, 0)})
    return rows


def _health_daily(rows: list[dict], start: int, end: int, klass: str,
                  fields: tuple[str, ...], days: int = 7) -> list[dict]:
    """Per-day coverage/completeness for the trend sparkline (#58).

    Coverage only makes sense for "call" tools (did the event carry a work_id);
    other classes return None so the panel can draw a dashed line instead of
    pretending the value was 0.
    """
    buckets: dict[str, dict] = {}
    for event in rows:
        stamp = int(event.get("t") or 0)
        if not stamp:
            continue
        day = datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
        bucket = buckets.setdefault(day, {"events": 0, "covered": 0, "complete": 0})
        bucket["events"] += 1
        if klass == "call" and _health_field_ok(event, "work_id"):
            bucket["covered"] += 1
        if all(_health_field_ok(event, field) for field in fields):
            bucket["complete"] += 1
    out = []
    span = max(1, min(30, int(days)))
    for offset in range(span - 1, -1, -1):
        stamp = end - offset * 86400000
        day = datetime.fromtimestamp(stamp / 1000).strftime("%Y-%m-%d")
        bucket = buckets.get(day)
        if not bucket or not bucket["events"]:
            out.append({"date": day, "events": 0, "coverage": None, "completeness": None})
            continue
        events = bucket["events"]
        out.append({
            "date": day,
            "events": events,
            "coverage": round(bucket["covered"] / events, 3) if klass == "call" else None,
            "completeness": round(bucket["complete"] / events, 3),
        })
    return out


def _health_delta(current: int, previous: int) -> float | None:
    if not previous:
        return None
    return round((current - previous) / previous, 3)


def _eligible_sessions(events: list[dict]) -> set[str]:
    """太短的会话会把覆盖率系统性拉低：至少 2 次提问，或至少 1 个有分钟的回合。"""
    prompts: dict[str, int] = {}
    blocks: dict[str, int] = {}
    for event in events:
        sid = str(event.get("sid") or "")
        if not sid:
            continue
        if event.get("ev") == "prompt":
            prompts[sid] = prompts.get(sid, 0) + 1
        elif event.get("ev") == "stop" and float(event.get("minutes") or 0) > 0:
            blocks[sid] = blocks.get(sid, 0) + 1
    return {sid for sid in set(prompts) | set(blocks)
            if prompts.get(sid, 0) >= 2 or blocks.get(sid, 0) >= 1}


def _work_keys(event: dict) -> tuple[str, str]:
    return (
        str(event.get("sid") or ""),
        str(event.get("work_id") or event.get("task_id") or ""),
    )


def _binding_latency_stats(events: list[dict], eligible: set[str]) -> dict:
    """首次绑定之前就动手的会话比例——衡量「主动绑定」而不是靠提醒。

    每会话从第一条事件看到第一个 start/work_claim；之前的 tool 调用与 end 过的回合
    都算「先动手」。没有绑定记录的会话不进入分母（那是另一类问题）。
    """
    by_sid: dict[str, list[dict]] = {}
    for event in events:
        sid = str(event.get("sid") or "")
        if sid in eligible:
            by_sid.setdefault(sid, []).append(event)
    tools_before: list[float] = []
    blocks_before: list[float] = []
    for rows in by_sid.values():
        tools = 0
        blocks = 0
        bound = False
        for event in rows:
            kind = event.get("ev")
            if kind == "work_claim" or (kind == "probe_report" and event.get("phase") == "start"):
                bound = True
                break
            if kind == "tool":
                tools += 1
            if kind == "stop":
                blocks += 1
        if bound:
            tools_before.append(float(tools))
            blocks_before.append(float(blocks))
    sessions = len(tools_before)
    early = sum(1 for tools, blocks in zip(tools_before, blocks_before) if tools or blocks)
    return {
        "sessions": sessions,
        "median_tools_before": round(_median(tools_before), 1) if tools_before else None,
        "median_blocks_before": round(_median(blocks_before), 1) if blocks_before else None,
        "sessions_with_early_work": early,
        "early_rate": round(early / sessions, 3) if sessions else None,
    }


def health_summary(
    days: float = 7.0, now: int | None = None, start: int | None = None,
    host_filter: str | None = None,
) -> dict:
    """按工具汇总采集健康度，供面板「健康度」tab 使用。"""
    end = now if now is not None else now_ms()
    window_days = max(1, int(round(float(days))))
    span = max(int(float(days) * 86400000), 86400000)
    start = int(start) if start is not None else end - span
    previous_start = start - span

    raw_events = _events_between(previous_start, end)
    if host_filter:
        raw_events = [event for event in raw_events if event.get("host") == host_filter]
    events = [event for event in raw_events if start <= int(event.get("t") or 0) <= end]
    previous = [event for event in raw_events if previous_start <= int(event.get("t") or 0) < start]

    eligible = _eligible_sessions(events)
    binding = _binding_latency_stats(events, eligible)
    all_sessions = {str(event.get("sid")) for event in events if event.get("sid")}
    host_of: dict[str, str] = {}
    for event in events:
        sid = str(event.get("sid") or "")
        if sid and event.get("host"):
            host_of.setdefault(sid, str(event.get("host")))
    eligible_by_host: dict[str, set[str]] = {}
    for sid in eligible:
        eligible_by_host.setdefault(host_of.get(sid, ""), set()).add(sid)
    by_kind: dict[str, list[dict]] = {}
    for event in events:
        by_kind.setdefault(str(event.get("ev") or ""), []).append(event)
    prev_by_kind: dict[str, int] = {}
    for event in previous:
        kind = str(event.get("ev") or "")
        prev_by_kind[kind] = prev_by_kind.get(kind, 0) + 1

    works_started = {_work_keys(row) for row in by_kind.get("probe_report", [])
                     if row.get("phase") == "start" and _work_keys(row)[1]}
    works_ended = {_work_keys(row) for row in by_kind.get("probe_report", [])
                   if row.get("phase") == "end" and _work_keys(row)[1]}
    sedimented = set()
    for row in by_kind.get("probe_report", []):
        if row.get("phase") == "end" and str(row.get("sediment") or "").strip():
            sedimented.add(_work_keys(row))
    for row in by_kind.get("work_sediment", []):
        if str(row.get("sediment") or "").strip():
            sedimented.add(_work_keys(row))
    sediment_block = {
        "ended": len(works_ended),
        "with_sediment": len(works_ended & sedimented),
        "rate": (round(len(works_ended & sedimented) / len(works_ended), 3)
                 if works_ended else None),
    }
    denominators = {
        "session": len(eligible),
        "work": len(works_started),
        "ended": len(works_ended),
        "call": len(eligible),
        "on_demand": 0,
    }

    def coverage_counts(spec: tuple, rows: list[dict]) -> tuple[int, int] | None:
        kind, klass = spec[0], spec[2]
        if klass == "on_demand" or not rows:
            return None
        if klass == "call":
            # 调用的「覆盖」= 有多少次调用归属到了工作
            attributed = sum(1 for row in rows if _health_field_ok(row, "work_id"))
            return attributed, len(rows)
        if klass == "session":
            support = _HEALTH_HOST_SUPPORT.get(kind)
            if support:
                denominator = set()
                for host_name in support:
                    denominator |= eligible_by_host.get(host_name, set())
                if not denominator:
                    return None
                seen = {str(row.get("sid")) for row in rows
                        if row.get("sid") and host_of.get(str(row.get("sid"))) in support}
                return len(seen & denominator), len(denominator)
            if not eligible:
                return None
            seen = {str(row.get("sid")) for row in rows if row.get("sid")}
            return len(seen & eligible), len(eligible)
        anchor = works_ended if klass == "ended" else works_started
        if not anchor:
            return None
        seen = {_work_keys(row) for row in rows if _work_keys(row)[1]}
        return len(seen & anchor), len(anchor)

    def build_row(spec: tuple) -> dict:
        kind, label, klass, fields, card = spec[:5]
        shipped = spec[5] if len(spec) > 5 else True
        rows = by_kind.get(kind, [])
        counts = coverage_counts(spec, rows)
        coverage = (counts[0] / counts[1]) if counts and counts[1] else None
        completeness, missing = _health_completeness(rows, fields)
        support = _HEALTH_HOST_SUPPORT.get(kind)
        lamp_events = counts[0] if (support and counts) else len(rows)
        entry = {
            "tool": kind,
            "label": label,
            "class": klass,
            "scope": HEALTH_SCOPE_LABEL[klass],
            "events": len(rows),
            "session_coverage": None if coverage is None else round(coverage, 3),
            "coverage_numerator": None if counts is None else counts[0],
            "coverage_denominator": None if counts is None else counts[1],
            "field_completeness": round(completeness, 3),
            "missing_fields": {k: v for k, v in missing.items() if v},
            "trend": _health_trend(rows, start, end, min(window_days, 30)),
            "daily": _health_daily(rows, start, end, klass, fields, min(window_days, 30)),
            "delta_vs_previous": _health_delta(len(rows), prev_by_kind.get(kind, 0)),
            "card": card,
            "lamp": _health_lamp(klass, lamp_events, coverage, completeness,
                                 (counts[1] if counts else denominators.get(klass, 0))),
            "note": "",
        }
        support = _HEALTH_HOST_SUPPORT.get(kind)
        if support:
            entry["note"] = "仅 " + " / ".join(sorted(support)) + " 有对应钩子"
            if not counts or not counts[1]:
                entry["lamp"] = "gray"
        if not shipped:
            entry["status"] = "not-shipped"
            entry["lamp"] = "gray"
        return entry

    passive = [build_row(spec) for spec in _HEALTH_PASSIVE]
    active = [build_row(spec) for spec in _HEALTH_ACTIVE]

    starts = works_started
    ends = works_ended
    closure = (len(ends & starts) / len(starts)) if starts else None
    unclosed = sorted(starts - ends)  # 有 start 没有 end

    reminders = by_kind.get("reminder", [])
    reports = by_kind.get("probe_report", [])
    followed = 0
    for reminder in reminders:
        rsid = reminder.get("sid")
        rt = int(reminder.get("t") or 0)
        if any(row.get("sid") == rsid and rt <= int(row.get("t") or 0) <= rt + 10 * 60000
               for row in reports):
            followed += 1
    adherence = (followed / len(reminders)) if reminders else None

    report_row = next((row for row in active if row["tool"] == "probe_report"), None)
    if report_row is not None:
        report_row["stage_closure"] = None if closure is None else round(closure, 3)
        report_row["adherence"] = None if adherence is None else round(adherence, 3)
        if report_row["events"]:
            if closure is not None and closure >= HEALTH_CLOSURE_OK and \
                    report_row["field_completeness"] >= 0.9:
                report_row["lamp"] = "green"
            else:
                report_row["lamp"] = "yellow"

    summary_row = next((row for row in active if row["tool"] == "work_summary"), None)
    if summary_row is not None:
        summarized = {_work_keys(row) for row in by_kind.get("work_summary", [])
                      if _work_keys(row)[1]}
        hits = len(summarized & works_ended)
        coverage = (hits / len(works_ended)) if works_ended else None
        summary_row["session_coverage"] = None if coverage is None else round(coverage, 3)
        summary_row["coverage_numerator"] = hits if works_ended else None
        summary_row["coverage_denominator"] = len(works_ended) if works_ended else None
        if summary_row["events"]:
            summary_row["lamp"] = _health_lamp("ended", summary_row["events"], coverage,
                                               summary_row["field_completeness"], len(works_ended))
        else:
            summary_row["lamp"] = "red" if works_ended else "gray"

    diagnostics: list[dict] = []

    def diag(severity: str, tool: str, title: str, evidence: str, cause: str, card: str = "") -> None:
        diagnostics.append({
            "severity": severity, "tool": tool, "title": title,
            "evidence": evidence, "cause": cause, "card": card,
        })

    tool_row = next(row for row in passive if row["tool"] == "tool")
    if tool_row["events"]:
        missing_work = tool_row["missing_fields"].get("work_id")
        if missing_work:
            diag("warn", "tool", "工具调用没有归属到工作",
                 f"{missing_work}/{tool_row['events']} 条 tool 事件缺 work_id，时间会算到错误的账上",
                 "record_tool 只写 sid/name/is_error，没有解析当前绑定", tool_row["card"])
    if report_row is not None and closure is not None and closure < HEALTH_CLOSURE_OK:
        ids = sorted({work for _, work in unclosed})
        sample = "、".join(ids[:5])
        more = " 等" if len(ids) > 5 else ""
        diag("warn", "probe_report", "有开始、没收尾",
             f"未收尾 {len(unclosed)} 个（{len(ids)} 个 id）：{sample}{more}",
             "收尾提示弱，或 end 时没有可写的字段", "24a45ce8")
    if summary_row is not None and works_ended and summary_row["events"] == 0:
        diag("warn", "work_summary", "收尾一条小结都没有",
             f"{len(works_ended)} 个工作收尾，0 条 work_summary",
             "DSH 侧三句字段没进工具 schema，模型永远传不上来", "42ae110d")
    if report_row is not None and adherence is not None and adherence < 0.5:
        diag("warn", "probe_report", "提醒之后不跟进",
             f"{len(reminders)} 次提醒，10 分钟内上报 {followed} 次",
             "提醒的措辞或位置没被模型接住", "")
    for entry in passive:
        if entry["class"] in ("session", "work", "call") and entry["events"] == 0 \
                and denominators.get(entry["class"]):
            diag("warn", entry["tool"], f"{entry['label']}完全没有事件",
                 f"{entry['tool']}：0 条，但这类事件本应有 {denominators[entry['class']]} 个锚点",
                 "采集没上线，或这个宿主没有对应 hook", entry["card"])
    adjustment_row = next((row for row in active if row["tool"] == "adjustment"), None)
    if adjustment_row is not None and adjustment_row["events"] == 0 and len(works_ended) >= 5:
        diag("info", "adjustment", "还没有改进记录",
             f"{len(works_ended)} 个工作收尾，0 条 adjustment",
             "改进只停留在对话里，事后无法核对有没有效", adjustment_row["card"])
    if binding.get("sessions") and (binding.get("early_rate") or 0) > 0.5:
        diag("warn", "probe_report", "多数会话先动手后绑定",
             f"{binding.get('sessions_with_early_work', 0)}/{binding['sessions']} 个会话在首次绑定前"
             f"用过工具或结束过回合，中位 {binding.get('median_tools_before')} 次工具",
             "首轮提示是列表型，绑定动作排在候选后面", "e94d70d5")
    if len(eligible) < 3:
        diag("info", "all", "样本太少，先别下结论",
             f"有效会话只有 {len(eligible)} 个",
             "覆盖率在会话数少时波动很大", "")

    covered = [row["session_coverage"] for row in passive + active
               if row["session_coverage"] is not None and row.get("status") != "not-shipped"]
    required = [row for row in passive + active
                if row["class"] != "on_demand" and row.get("status") != "not-shipped"]
    required_ok = sum(1 for row in required if row["lamp"] == "green")
    abnormal = sum(1 for row in passive + active if row["lamp"] in ("red", "yellow"))
    hosts = sorted({str(event.get("host")) for event in events if event.get("host")})
    warns = [diag for diag in diagnostics if diag["severity"] in ("warn", "error")]
    if not eligible:
        headline = "这段时间没有有效会话，先攒点数据再判断"
    elif warns:
        headline = f"{len(warns)} 个问题，最该先修：{warns[0]['title']}"
    else:
        headline = "采集健康，没有发现异常"
    return {
        "generated_at": now_ms(),
        "window": {"days": window_days, "start": start, "end": end},
        "sessions": {"total": len(all_sessions), "eligible": len(eligible)},
        "works": {"started": len(works_started), "ended": len(works_ended)},
        "sediment": sediment_block,
        "binding": binding,
        "hosts": hosts,
        "passive": passive,
        "active": active,
        "diagnostics": diagnostics,
        "summary": {
            "has_previous": bool(prev_by_kind),
            "headline": headline,
            "coverage_median": round(_median(covered), 3) if covered else 0.0,
            "min_coverage": round(min(covered), 3) if covered else 0.0,
            "required_ok": required_ok,
            "required_total": len(required),
            "abnormal_tools": abnormal,
        },
    }


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return float(ordered[mid])
    return (ordered[mid - 1] + ordered[mid]) / 2.0


def format_health(summary: dict) -> str:
    """把 health_summary 的输出变成一段人能读、AI 也能直接吃的文本。"""
    window = summary.get("window") or {}
    sessions = summary.get("sessions") or {}
    works = summary.get("works") or {}
    stats = summary.get("summary") or {}
    lines = [
        f"Crop 健康度 · 近 {window.get('days', 7)} 天 · "
        f"{sessions.get('eligible', 0)} 个有效会话 · "
        f"工作收尾 {works.get('ended', 0)}/{works.get('started', 0)}",
        str(stats.get("headline") or ""),
    ]
    sediment = summary.get("sediment") or {}
    if sediment.get("ended"):
        rate = sediment.get("rate")
        suffix = f"（{float(rate):.0%}）" if rate is not None else ""
        lines.append(f"沉淀闭合：{sediment.get('with_sediment', 0)}/{sediment.get('ended', 0)}{suffix}")
    binding = summary.get("binding") or {}
    if binding.get("sessions"):
        lines.append(
            f"绑定前动手：{binding.get('sessions_with_early_work', 0)}/{binding['sessions']} 个会话"
            f"在首次绑定前用过工具或结束过回合（中位 {binding.get('median_tools_before')} 次工具）"
        )
    problems = [d for d in summary.get("diagnostics") or [] if d.get("severity") != "info"]
    for item in problems:
        card = f"（卡 {item['card']}）" if item.get("card") else ""
        lines.append(f"- {item['title']}：{item['evidence']}{card}")
    lines.append("按工具：")
    for row in (summary.get("passive") or []) + (summary.get("active") or []):
        mark = {"green": "绿", "yellow": "黄", "red": "红", "gray": "灰"}.get(row.get("lamp"), "?")
        if row.get("class") == "on_demand":
            detail = f"{row['events']} 次（按需）"
        elif row.get("status") == "not-shipped":
            detail = "未上线"
        else:
            cover = row.get("session_coverage")
            cover_text = "—" if cover is None else f"{row.get('coverage_numerator')}/{row.get('coverage_denominator')}"
            detail = f"覆盖 {cover_text} · 完整 {float(row.get('field_completeness') or 0):.0%}"
            if row.get("stage_closure") is not None:
                detail += f" · 闭环 {float(row['stage_closure']):.0%}"
            missing = row.get("missing_fields") or {}
            if missing:
                detail += " · 缺 " + "、".join(f"{k}×{v}" for k, v in missing.items())
        note = f"（{row['note']}）" if row.get("note") else ""
        lines.append(f"  [{mark}] {row.get('label')} {row.get('tool')}：{detail}{note}")
    return "\n".join(line for line in lines if line)


# ---------------------------------------------------------------------------
# Improvement ledger: adjustment events
#
# 「改进有没有效」不由 AI 自评：这里只记录「采纳了什么、前后值、证据」，
# 判定交给确定性规则（样本量、方向、护栏不劣化），报表与周报复用同一份数据。
# 指标值从原始事件派生，不为 ledger 新增快照事件（EVENTS.md §1.3）。

ARTIFACT_KINDS = ("file", "skill", "hook")
ARTIFACT_DELIVERIES = ("read", "injected")
ARTIFACT_STATUS_LABEL = {
    "unregistered": "未登记产物，无法验证",
    "missing": "产物文件不存在",
    "not-read": "产物存在，但还没有会话读到",
    "injected": "结构性注入（每会话自动加载）",
}


def _norm_path(path: str, workspace: str = "") -> str:
    text = str(path or "").strip()
    if not text:
        return ""
    text = os.path.expanduser(text)
    if not os.path.isabs(text) and workspace:
        text = os.path.join(os.path.expanduser(str(workspace)), text)
    # realpath 解析 /tmp → /private/tmp 这类符号链接，否则同一个文件会被当成两个路径。
    return os.path.realpath(os.path.normpath(text))


def _same_key(left: str, right: str) -> bool:
    a = str(left or "").strip().lower()
    b = str(right or "").strip().lower()
    if not a or not b:
        return False
    return a == b or os.path.basename(a) == os.path.basename(b)


def _parse_artifact(value) -> dict | None:
    """Accept an object, or "kind:path#anchor" (optionally "@injected")."""
    if value in (None, ""):
        return None
    if isinstance(value, str):
        text = str(value).strip()
        if not text:
            return None
        kind = "file"
        head, sep, tail = text.partition(":")
        if sep and head.strip() in ARTIFACT_KINDS:
            kind, text = head.strip(), tail
        anchor = ""
        if "#" in text:
            text, anchor = text.split("#", 1)
        delivery = "read"
        path = text.strip()
        if path.endswith("@injected"):
            path = path[:-len("@injected")].rstrip()
            delivery = "injected"
        if not path:
            raise ValueError("artifact path is required")
        return {"kind": kind, "path": path[:500],
                "anchor": anchor.strip()[:120], "delivery": delivery}
    if isinstance(value, dict):
        kind = str(value.get("kind") or "file").strip().lower()
        delivery = str(value.get("delivery") or "read").strip().lower()
        path = str(value.get("path") or "").strip()
        if kind not in ARTIFACT_KINDS:
            raise ValueError("artifact.kind must be one of: " + ", ".join(ARTIFACT_KINDS))
        if delivery not in ARTIFACT_DELIVERIES:
            raise ValueError("artifact.delivery must be one of: " + ", ".join(ARTIFACT_DELIVERIES))
        if not path:
            raise ValueError("artifact.path is required")
        return {"kind": kind, "path": path[:500],
                "anchor": str(value.get("anchor") or "").strip()[:120],
                "delivery": delivery}
    raise ValueError('artifact must be a string ("kind:path#anchor") or an object')


def _adjustment_id(adopted_at: int, metric: str, evidence: str, note: str) -> str:
    raw = f"{adopted_at}|{metric}|{evidence}|{note}".encode("utf-8")
    return "adj-" + hashlib.sha1(raw).hexdigest()[:12]


def _artifact_hit_ids(target: str, events: list[dict]) -> list[tuple[dict, dict]]:
    """Which registered artifacts does this tool target correspond to?"""
    text = str(target or "").strip()
    if not text:
        return []
    target_norm = _norm_path(text)
    found: list[tuple[dict, dict]] = []
    seen: set[str] = set()
    for event in events:
        if event.get("ev") != "adjustment":
            continue
        artifact = event.get("artifact")
        if not isinstance(artifact, dict):
            continue
        if str(artifact.get("delivery") or "read") == "injected":
            continue
        key = str(event.get("adjustment_id") or event.get("t") or "")
        if key in seen:
            continue
        kind = str(artifact.get("kind") or "file")
        path = str(artifact.get("path") or "")
        if kind == "skill":
            match = _same_key(text, path)
        else:
            artifact_norm = _norm_path(path, str(event.get("workspace") or ""))
            match = bool(artifact_norm) and (
                target_norm == artifact_norm
                or target_norm.startswith(artifact_norm + os.sep)
            )
        if match:
            seen.add(key)
            found.append((event, artifact))
    return found


def _record_artifact_hits(sid: str, work_id: str, tool: str, target: str,
                          events: list[dict]) -> None:
    for adjustment, artifact in _artifact_hit_ids(target, events):
        hit = {
            "ev": "artifact_hit",
            "sid": sid,
            "tool": str(tool or "")[:80],
            "target": str(target or "")[:500],
            "adjustment_id": str(adjustment.get("adjustment_id") or ""),
            "artifact": dict(artifact),
        }
        if work_id:
            hit["work_id"] = work_id
        append_event(hit)


def _artifact_report(artifact: dict | None, event: dict, events: list[dict]) -> dict:
    report = {
        "status": "unregistered", "label": ARTIFACT_STATUS_LABEL["unregistered"],
        "path": "", "anchor": "", "kind": "", "delivery": "", "exists": None,
        "hits": 0, "sessions": [], "first_hit": None, "last_hit": None,
    }
    if not isinstance(artifact, dict):
        return report
    path = str(artifact.get("path") or "")
    report.update({
        "path": path,
        "anchor": str(artifact.get("anchor") or ""),
        "kind": str(artifact.get("kind") or "file"),
        "delivery": str(artifact.get("delivery") or "read"),
    })
    adjustment_id = str(event.get("adjustment_id") or "")
    adopted = int(event.get("adopted_at") or event.get("t") or 0)
    hits = [
        item for item in events
        if item.get("ev") == "artifact_hit"
        and (str(item.get("adjustment_id") or "") == adjustment_id if adjustment_id
             else int(item.get("t") or 0) >= adopted)
    ]
    hits.sort(key=lambda item: int(item.get("t") or 0))
    sessions: list[str] = []
    for item in hits:
        sid = str(item.get("sid") or "")
        if sid and sid not in sessions:
            sessions.append(sid)
    report["hits"] = len(hits)
    report["sessions"] = sessions
    report["first_hit"] = int(hits[0].get("t") or 0) if hits else None
    report["last_hit"] = int(hits[-1].get("t") or 0) if hits else None

    if report["delivery"] == "injected":
        report["status"] = "injected"
    elif report["kind"] == "file" and path:
        workspace = str(event.get("workspace") or "")
        report["exists"] = os.path.exists(_norm_path(path, workspace))
        if not report["exists"]:
            report["status"] = "missing"
        else:
            report["status"] = "verified" if hits else "not-read"
    else:
        report["status"] = "verified" if hits else "not-read"
    report["label"] = ARTIFACT_STATUS_LABEL.get(report["status"], report["status"])
    if report["status"] == "verified":
        report["label"] = f"已读到（{len(hits)} 次 / {len(sessions)} 个会话）"
    return report


ADJUSTMENT_METRICS = {
    "work_minutes": "同类工作中位活跃分钟",
    "reminder_per_turn": "每回合提醒次数",
    "report_latency_min": "提醒到上报的中位延迟（分钟）",
    "unowned_tool_share": "未归属工作的工具调用占比",
}
LEDGER_MIN_SAMPLE = 20
LEDGER_GOOD_DELTA = -0.10
LEDGER_GUARDRAIL_SLACK = 0.10
LEDGER_WEEKS = 4
LEDGER_OBSERVE_DAYS = 7

LEDGER_STATUS_LABEL = {
    "met": "达标（连续 3 周改善）",
    "improving": "改善中（单周已改善，等连续 3 周）",
    "not-improved": "未改善",
    "insufficient": "样本不足",
    "no-baseline": "没有基线",
    "observing": "观察中（还没攒够一周数据）",
    "guardrail": "护栏劣化，不算改进",
}


def _stdev(values: list[float]) -> float:
    if len(values) < 2:
        return 0.0
    mean = sum(values) / len(values)
    return (sum((value - mean) ** 2 for value in values) / (len(values) - 1)) ** 0.5


def record_adjustment(args: dict, context: dict | None = None) -> dict:
    """Record one adopted improvement — the claim, not the numbers.

    数值（before/after）一律由报表从事件日志派生；这里只收三样东西：
    选哪个指标、改动证据、以及 AI/人为什么认为它会改善的**主观判断**。
    指标不在派生表里就拒收——要加新指标，先在 core 写派生口径，
    而不是让调用方随手塞一个数字。
    """
    context = context or {}
    sid = str(context.get("sid") or "unknown")
    workspace = str(context.get("workspace") or context.get("cwd") or "").strip()
    metric = str(args.get("metric") or "").strip().lower()
    if metric not in ADJUSTMENT_METRICS:
        raise ValueError(
            "metric must be one of: " + ", ".join(sorted(ADJUSTMENT_METRICS))
            + "（指标值由事件派生；新指标需要先在 core 加派生口径）"
        )
    evidence = str(args.get("evidence") or "").strip()
    if not evidence:
        raise ValueError("evidence is required: commit / PR / file / command，说明改了什么")
    note = str(args.get("note") or "").strip()
    if not note:
        raise ValueError("note is required: 一句话说明你为什么认为这条改动会改善指标（这就是 AI 该提供的主观判断）")
    artifact = _parse_artifact(args.get("artifact"))
    adopted_at = int(args.get("adopted_at") or now_ms())
    event = {
        "ev": "adjustment",
        "t": now_ms(),
        "host": host(),
        "sid": sid,
        "workspace": workspace,
        "work_id": normalize_task_id(str(args.get("work_id") or "")) or "",
        "adjustment_id": _adjustment_id(adopted_at, metric, evidence, note),
        "metric": metric,
        "direction": "lower",  # 四个内置指标都是越低越好
        "artifact": artifact,
        "evidence": evidence,
        "window": str(args.get("window") or "").strip(),
        "adopted_at": adopted_at,
        "note": note,
    }
    append_event(event)
    # 建档即关闭同工作的待复盘（#46）：改进真正落进台账，而不只是被提醒。
    target = str(event.get("work_id") or "")
    if target:
        resolved = {str(row.get("work_id") or "") for row in load_events()
                    if row.get("ev") == "retro_resolved"}
        pending = [row for row in load_events()
                   if row.get("ev") == "retro_pending"
                   and str(row.get("work_id") or "") == target
                   and target not in resolved]
        if pending:
            append_event({"ev": "retro_resolved", "sid": sid, "work_id": target,
                          "metric": metric, "evidence": evidence})
            event["retro_resolved"] = True
    return event


def load_adjustments(limit: int = 50, work_id: str | None = None) -> list[dict]:
    rows = [event for event in load_events() if event.get("ev") == "adjustment"]
    if work_id:
        rows = [row for row in rows if row.get("work_id") == work_id]
    rows.sort(key=lambda row: int(row.get("adopted_at") or row.get("t") or 0), reverse=True)
    return rows[:limit] if limit else rows


def _find_work_record(work_id: str) -> dict | None:
    task = load_open().get("tasks", {}).get(work_id)
    if task:
        return task
    if not work_id:
        return None
    for task in _closed_works_between(0, now_ms()):
        if task.get("task_id") == work_id:
            return task
    return None


def _metric_value(metric: str, start: int, end: int, work_type: str = "") -> tuple[float | None, int]:
    """(值, 样本量)；指标值只从原始事件派生，未知指标返回 (None, 0)。"""
    events = _events_between(start, end)
    if metric == "work_minutes":
        works = _closed_works_between(start, end)
        samples = [
            float(task.get("total_minutes") or 0) for task in works
            if (not work_type or task.get("type") == work_type)
        ]
        samples = [value for value in samples if value > 0]
        return (round(_median(samples), 1) if samples else None), len(samples)
    if metric == "reminder_per_turn":
        turns = sum(1 for event in events if event.get("ev") == "prompt")
        reminders = sum(1 for event in events if event.get("ev") == "reminder")
        return (round(reminders / turns, 2) if turns else None), turns
    if metric == "report_latency_min":
        by_sid: dict[str, list[dict]] = {}
        for event in events:
            if event.get("ev") in ("reminder", "probe_report"):
                by_sid.setdefault(str(event.get("sid") or ""), []).append(event)
        latencies: list[float] = []
        for rows in by_sid.values():
            pending = None
            for row in rows:
                if row.get("ev") == "reminder":
                    pending = row
                elif pending is not None:
                    delta = (int(row.get("t") or 0) - int(pending.get("t") or 0)) / 60000.0
                    if 0 <= delta <= 120:
                        latencies.append(delta)
                    pending = None
        return (round(_median(latencies), 1) if latencies else None), len(latencies)
    if metric == "unowned_tool_share":
        tools = [event for event in events if event.get("ev") == "tool"]
        if not tools:
            return None, 0
        missing = sum(1 for event in tools if not str(event.get("work_id") or ""))
        return round(missing / len(tools), 3), len(tools)
    return None, 0


def _weekly_values(metric: str, start: int, end: int,
                   work_type: str = "") -> list[dict]:
    """把 [start, end) 按 7 天切桶；指标值逐周派生，最后不满一周的桶标 complete=False。"""
    out: list[dict] = []
    week = 7 * 86400000
    cursor = start
    while cursor < end:
        stop = min(cursor + week, end)
        value, samples = _metric_value(metric, cursor, stop - 1, work_type)
        out.append({
            "start": cursor, "end": stop, "value": value, "samples": samples,
            "complete": stop - cursor >= week,
        })
        cursor = stop
    return out


def _ledger_row(event: dict, end: int, events: list[dict] | None = None) -> dict:
    adopted = int(event.get("adopted_at") or event.get("t") or 0)
    metric = str(event.get("metric") or "")
    work_id = str(event.get("work_id") or "")
    work = _find_work_record(work_id) if work_id else None
    work_type = str((work or {}).get("type") or "")
    span = LEDGER_WEEKS * 7 * 86400000
    baseline_start = adopted - span

    baseline_weeks = _weekly_values(metric, baseline_start, adopted, work_type)
    baseline_values = [week["value"] for week in baseline_weeks if week["value"] is not None]
    baseline_samples = sum(week["samples"] for week in baseline_weeks)
    baseline_median = _median(baseline_values) if baseline_values else None
    sigma = _stdev(baseline_values) if len(baseline_values) >= 2 else 0.0

    after_weeks = _weekly_values(metric, adopted, end, work_type)
    after_samples = sum(week["samples"] for week in after_weeks)
    complete_after = [week for week in after_weeks
                      if week["complete"] and week["value"] is not None]
    # 判定只用完整周：不满一周的桶样本少，容易被一两件事带偏。
    judged_after = complete_after[-1]["value"] if complete_after else None
    age_days = (end - adopted) / 86400000

    # 改善阈值：基线 10% 与 4 周周间波动 1σ 取大者（卡片 0edeaa8c 的口径）
    threshold = 0.0
    if baseline_median:
        threshold = max(abs(baseline_median) * abs(LEDGER_GOOD_DELTA), sigma)

    def good(value: float | None) -> bool:
        return (baseline_median is not None and value is not None
                and value <= baseline_median - threshold)

    guardrails = {}
    for name in ("reminder_per_turn", "report_latency_min", "unowned_tool_share"):
        if name == metric:
            continue
        before_g, before_gn = _metric_value(name, baseline_start, adopted)
        after_g, after_gn = _metric_value(name, adopted, end)
        worse = None
        if (before_g is not None and after_g is not None and before_g > 0 and after_gn):
            worse = after_g > before_g * (1 + LEDGER_GUARDRAIL_SLACK)
        guardrails[name] = {
            "before": before_g, "after": after_g,
            "samples": [before_gn, after_gn], "worse": worse,
        }

    if baseline_median is None:
        status = "no-baseline"
    elif not complete_after:
        status = "observing"
    elif min(baseline_samples, after_samples) < LEDGER_MIN_SAMPLE:
        status = "insufficient"
    elif any(item.get("worse") for item in guardrails.values()):
        status = "guardrail"
    elif len(complete_after) >= 3 and all(good(week["value"]) for week in complete_after[-3:]):
        status = "met"
    elif good(judged_after):
        status = "improving"
    else:
        status = "not-improved"

    delta = None
    if judged_after is not None and baseline_median:
        delta = (judged_after - baseline_median) / abs(baseline_median)

    return {
        "work_id": work_id,
        "metric": metric,
        "metric_label": ADJUSTMENT_METRICS.get(metric, metric),
        "direction": str(event.get("direction") or "lower"),
        "adopted_at": adopted,
        # 兼容旧字段名：before/after 现在是派生值，不是调用方塞的
        "before": baseline_median,
        "after": judged_after,
        "samples": {"before": baseline_samples, "after": after_samples},
        "weeks": {
            "baseline": len(baseline_weeks),
            "after": len(after_weeks),
            "complete_after": len(complete_after),
        },
        "sigma": round(sigma, 3),
        "threshold": round(threshold, 3),
        "delta": None if delta is None else round(delta, 3),
        "status": status,
        "status_label": LEDGER_STATUS_LABEL.get(status, status),
        "guardrails": guardrails,
        "evidence": str(event.get("evidence") or ""),
        "window": str(event.get("window") or ""),
        "note": str(event.get("note") or ""),
        "artifact": _artifact_report(event.get("artifact"), event, events or []),
        "age_days": round(age_days, 1),
        "rollback": status != "met" and age_days > LEDGER_WEEKS * 7,
    }


def improvement_ledger(days: float = 180.0, limit: int = 20) -> dict:
    """Recent adjustments, judged by data — not by the AI's own verdict."""
    end = now_ms()
    start = end - int(float(days) * 86400000)
    all_events = load_events(limit=0)
    events = [
        event for event in all_events
        if event.get("ev") == "adjustment" and start <= int(event.get("t") or 0) <= end
    ]
    rows = [_ledger_row(event, end, all_events) for event in events]
    rows.sort(key=lambda row: row["adopted_at"], reverse=True)

    return {
        "generated_at": end,
        "window": {"days": int(float(days)), "start": start, "end": end},
        "min_sample": LEDGER_MIN_SAMPLE,
        "good_delta": LEDGER_GOOD_DELTA,
        "rows": rows[:limit],
        "total": len(rows),
        "rollback_hints": [row for row in rows if row.get("rollback")][:5],
    }


def _fmt_short(ms: int | None) -> str:
    try:
        return datetime.fromtimestamp(int(ms or 0) / 1000).strftime("%m-%d %H:%M")
    except Exception:
        return "未知"


def format_ledger(ledger: dict) -> str:
    rows = ledger.get("rows") or []
    lines = [
        f"改进台账 · 近 {ledger.get('window', {}).get('days', 0)} 天 · {ledger.get('total', 0)} 条"
        f"（样本 <{ledger.get('min_sample')} 只给数字，不下结论）",
    ]
    if not rows:
        return "\n".join(lines + ["还没有 adjustment 记录。采纳改进时用 crop adjust 记一条。"])
    for row in rows:
        before = "无基线" if row.get("before") is None else row.get("before")
        after = "暂无数据" if row.get("after") is None else row.get("after")
        delta = "" if row.get("delta") is None else f"，变化 {row['delta']:+.0%}"
        lines.append(
            f"- [{row.get('status_label')}] {row.get('metric_label')}：基线 {before} → 当前 {after}{delta}"
            f"（样本 {row.get('samples', {}).get('before', 0)}/{row.get('samples', {}).get('after', 0)}，"
            f"观察 {row.get('age_days')} 天）"
        )
        if row.get("note"):
            lines.append(f"  假设：{row['note']}")
        if row.get("guardrails"):
            worse = [name for name, item in row["guardrails"].items() if item.get("worse")]
            if worse:
                lines.append("  护栏变差：" + "、".join(worse) + "（任一项变差即不算改进）")
        if row.get("evidence"):
            lines.append(f"  证据：{row['evidence']}")
        artifact = row.get("artifact") or {}
        if artifact.get("status"):
            where = artifact.get("path") or "（未登记）"
            if artifact.get("anchor"):
                where += "#" + artifact["anchor"]
            lines.append(f"  产物：{where} —— {artifact.get('label')}")
            if artifact.get("sessions"):
                shown = "、".join(str(sid)[:8] for sid in artifact["sessions"][:5])
                more = "" if len(artifact["sessions"]) <= 5 else f" 等 {len(artifact['sessions'])} 个会话"
                first = _fmt_short(artifact.get("first_hit")) if artifact.get("first_hit") else "未知"
                lines.append(f"  读到会话：{shown}{more}（首次 {first}）")
    for row in ledger.get("rollback_hints") or []:
        lines.append(
            f"- 回滚提示：{row.get('metric_label')}（{row.get('work_id') or '未挂工作'}）"
            f"已满 {LEDGER_WEEKS} 周仍未达标，可以回滚；回滚前先看证据：{row.get('evidence')}"
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Injection preview (`crop preview`)

_PREVIEW_VARS = {
    "protocol": {"persona": "owner"},
    "start-hint": {
        "mine": "1", "others": "2",
        "candidates": "- demo-work — 示例工作（3 分钟前）",
        "example_task_id": "demo-work", "limit": "3",
    },
    "binding-hint": {"last_work": "demo-work", "last_phase": "end", "example_task_id": "<短名>"},
    "end-hint": {"work_id": "demo-work"},
}


def preview_injections() -> str:
    """Render every injection point with sample variables.

    Used by `crop preview` so a user can see exactly what would be injected
    before and after editing a template.  A point without a template file is
    reported as using the built-in default (the CLI cannot reproduce the full
    runtime text because some of it is assembled by the host).
    """
    lines = ["# 注入预览（示例变量）", ""]
    for name in ("protocol", "start-hint", "binding-hint", "end-hint"):
        body = None
        if crop_templates is not None:
            try:
                body = crop_templates.render(name, _PREVIEW_VARS.get(name, {}))
            except Exception:
                body = None
        if body is None:
            lines.extend([f"## {name}（内置默认，未覆盖）", ""])
        elif body == "":
            lines.extend([f"## {name}（已关闭：none）", ""])
        else:
            lines.extend([f"## {name}", "", _template_prefix(body), ""])
    if crop_templates is not None:
        for tier in crop_templates.VOICE_TIERS:
            try:
                custom = crop_templates.voice_lines(tier)
            except Exception:
                custom = None
            if custom:
                lines.extend([f"## voice/{tier}（自定义，{len(custom)} 条）", "", custom[0], ""])
            else:
                lines.extend([f"## voice/{tier}（内置文案池）", ""])
    return "\n".join(lines)
