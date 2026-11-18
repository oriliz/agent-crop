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



_DEFAULT_START_HINT = """[crop] 未完成的工作：本会话 {{mine}} 条 / 其他会话 {{others}} 条。最近活跃的是：
{{candidates}}
先选一条，再动手：接着做用 crop_update(phase="start", task_id="{{example_task_id}}")；新开用 2-3 个词的短名。"""

_DEFAULT_BINDING_HINT = """[crop] 你已经在调用工具，但还没有绑定任何工作。先调 crop_update(phase="start", task_id="<2-3 个词的短名>", summary="<一句话>", expect_min=预估分钟) 再继续，这样这段时间才有归属；顺手报个预期时长，鞭子就按你的数字算。"""


def _write_template_defaults(args: dict) -> dict:
    """Write built-in defaults into the template directory (opt-in)."""
    templates = getattr(core, "crop_templates", None)
    if templates is None:
        return {"written": [], "error": "templates module unavailable"}
    files = {
        "start-hint": _DEFAULT_START_HINT,
        "binding-hint": _DEFAULT_BINDING_HINT,
    }
    files.update({str(k): str(v) for k, v in (args.get("files") or {}).items()})
    voice = getattr(core, "voice", None)
    pools = getattr(voice, "POOLS", {}) if voice is not None else {}
    for tier, lines in pools.items():
        files[f"voice:{tier}"] = "\n---\n".join(str(line) for line in lines)
    root = templates.templates_dir()
    written = []
    for name, body in files.items():
        relative = templates._FILES.get(name)  # noqa: SLF001 - single writer by design
        if not relative:
            continue
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            continue  # never clobber what the user already edited
        path.write_text(body.strip() + "\n", encoding="utf-8")
        written.append(name)
    templates.clear_cache()
    return {"written": written, "dir": str(root)}


def dispatch(payload: dict):
    op = payload.get("op")
    context = payload.get("context") or {}
    workspace = payload.get("workspace") or context.get("workspace") or os.getcwd()
    sid = str(context.get("sid") or "unknown")

    if core is None:
        raise RuntimeError("probe core unavailable")

    if op == "template_render":
        args = payload.get("args") or {}
        templates = getattr(core, "crop_templates", None)
        if templates is None:
            return None
        variables = {str(k): str(v) for k, v in (args.get("variables") or {}).items()}
        return templates.render(str(args.get("name") or ""), variables)
    if op == "template_get":
        args = payload.get("args") or {}
        templates = getattr(core, "crop_templates", None)
        if templates is None:
            return None
        name = str(args.get("name") or "")
        body = templates.load(name)
        return {"name": name, "body": body, "overridden": body is not None}
    if op == "template_defaults":
        return _write_template_defaults(payload.get("args") or {})
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
    if op == "binding_hint":
        return core.binding_hint(sid)
    if op == "health_text":
        args = payload.get("args") or {}
        summary = core.health_summary(
            float(args.get("days") or 7.0),
            host_filter=args.get("host") or None,
        )
        return core.format_health(summary)
    if op == "health":
        args = payload.get("args") or {}
        return core.health_summary(
            float(args.get("days") or 7.0),
            host_filter=args.get("host") or None,
        )
    if op == "record_review":
        return core.record_review(payload.get("args") or {}, context)
    if op == "record_adjustment":
        return core.record_adjustment(payload.get("args") or {}, context)
    if op == "adjustments":
        return core.load_adjustments(int(payload.get("limit") or 20))
    if op == "ledger":
        return core.improvement_ledger(float(payload.get("days") or 180.0))
    if op == "record_report":
        return core.record_report(payload.get("args") or {}, context)
    if op == "record_session_start":
        core.record_session_start(sid, workspace)
        return True
    if op == "record_prompt":
        core.record_prompt(sid, payload.get("text") or "", workspace)
        return True
    if op == "record_tool":
        core.record_tool(sid, payload.get("name") or "unknown", bool(payload.get("is_error")),
                         payload.get("target") or None)
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
        return core.miss_nudge(int(payload.get("streak") or 1), sid)
    if op == "work_status":
        return core.work_status_line(workspace, sid)
    if op == "work_suspend":
        return core.set_work_state(payload.get("work_id") or "", "suspended")
    if op == "work_resume":
        return core.set_work_state(payload.get("work_id") or "", "open")
    if op == "pending_confirm_hint":
        return core.pending_confirm_hint(payload.get("context") or {})
    if op == "work_confirm":
        args = payload.get("args") or {}
        return core.confirm_works(
            [str(item) for item in (args.get("task_ids") or [])],
            str(args.get("sid") or ""),
        )
    if op == "work_archive":
        return core.archive_work(payload.get("work_id") or "", payload.get("reason") or "manual")
    if op == "work_archive_stale":
        return core.archive_stale(float(payload.get("days") or 14))
    if op == "global":
        args = payload.get("args") or {}
        return core.improvement_summary(
            max(1.0, float(args.get("days") or 7)),
            host=str(args.get("host") or "") or None,
        )
    if op == "day":
        args = payload.get("args") or {}
        return core.day_detail(str(args.get("date") or core.today()))
    if op == "session":
        args = payload.get("args") or {}
        return core.session_summary(str(args.get("sid") or ""))
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
