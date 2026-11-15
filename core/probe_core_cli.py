#!/usr/bin/env python3
"""One-shot JSON bridge used by the DSH plugin.

Reads one JSON object from stdin:
  {"op": "...", "args": {...}, "context": {...}, "workspace": "...", "text": "..."}

Writes one JSON object to stdout:
  {"ok": true, "result": ...} or {"ok": false, "error": "..."}

Every failure is contained; the host plugin treats a non-ok result as a no-op.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import probe_core as core
except Exception:  # pragma: no cover
    core = None


def dispatch(payload: dict):
    op = payload.get("op")
    context = payload.get("context") or {}
    workspace = payload.get("workspace") or context.get("workspace") or os.getcwd()
    sid = str(context.get("sid") or "unknown")

    if core is None:
        raise RuntimeError("probe core unavailable")

    if op == "record_human_wait":
        return core.record_human_wait(
            sid,
            str(payload.get("reason") or "ask-human"),
            int(payload.get("started_at") or 0),
            int(payload.get("ended_at") or 0) or None,
        )
    if op == "pending_retire":
        return core.format_pending_retire()
    if op == "mark_stale":
        return core.mark_stale_works()
    if op == "binding":
        return core.resolve_binding(sid)
    if op == "record_review":
        return core.record_review(payload.get("args") or {}, context)
    if op == "record_report":
        return core.record_report(payload.get("args") or {}, context)
    if op == "record_session_start":
        core.record_session_start(sid, workspace)
        return True
    if op == "record_prompt":
        core.record_prompt(sid, payload.get("text") or "", workspace)
        return True
    if op == "record_tool":
        core.record_tool(sid, payload.get("name") or "unknown", bool(payload.get("is_error")))
        return True
    if op == "record_stop":
        core.record_stop(sid)
        return True
    if op == "record_session_end":
        core.record_session_end(sid)
        return True
    if op == "open_tasks":
        return core.open_tasks(workspace, int(payload.get("limit") or 3))
    if op == "start_hint":
        tasks = core.open_tasks(workspace, int(payload.get("limit") or 3), sid)
        return core.format_start_hint(tasks, core.open_works_count(), sid)
    if op == "works":
        tasks = core.open_tasks(workspace, int(payload.get("limit") or 20), sid)
        return tasks
    if op == "open_tasks_text":
        tasks = core.open_tasks(workspace, int(payload.get("limit") or 3))
        return core.format_open_tasks(tasks)
    if op == "maybe_reminder":
        return core.maybe_reminder(sid)

    if op == "miss_nudge":
        return core.miss_nudge(int(payload.get("streak") or 1))
    if op == "work_status":
        return core.work_status_line(workspace, sid)
    if op == "work_suspend":
        return core.set_work_state(payload.get("work_id") or "", "suspended")
    if op == "work_resume":
        return core.set_work_state(payload.get("work_id") or "", "open")
    if op == "work_archive":
        return core.archive_work(payload.get("work_id") or "", payload.get("reason") or "manual")
    if op == "work_archive_stale":
        return core.archive_stale(float(payload.get("days") or 14))
    if op == "first_prompt":
        return not any(event.get("ev") == "prompt" for event in core.load_events(sid=sid))
    raise ValueError(f"unknown op: {op}")


def main() -> None:
    try:
        payload = json.load(sys.stdin)
        print(json.dumps({"ok": True, "result": dispatch(payload)}, ensure_ascii=False))
    except Exception as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
