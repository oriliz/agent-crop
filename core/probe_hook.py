#!/usr/bin/env python3
"""Claude Code hook entry point for the efficiency probe.

Usage (from hooks/claude-hooks.json):
  probe_hook.py session-start
  probe_hook.py user-prompt-submit
  probe_hook.py post-tool
  probe_hook.py stop
  probe_hook.py session-end

Reads the hook JSON from stdin.  Always exits 0: the probe never blocks a
prompt or a turn.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import probe_core as core
except Exception:  # pragma: no cover
    core = None


def read_input() -> dict:
    try:
        if sys.stdin is None or sys.stdin.isatty():
            return {}
        raw = sys.stdin.read() or ""
        return json.loads(raw) if raw.strip() else {}
    except Exception:
        return {}


def pick(data: dict, *keys):
    for key in keys:
        value = data.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def output_text(text: str) -> None:
    if text:
        print(text)


def output_post_tool(text: str) -> None:
    if text:
        print(json.dumps({
            "hookSpecificOutput": {
                "hookEventName": "PostToolUse",
                "additionalContext": text,
            }
        }, ensure_ascii=False))


def main() -> None:
    if core is None:
        return
    mode = sys.argv[1] if len(sys.argv) > 1 else ""
    data = read_input()
    sid = pick(data, "session_id", "sessionId") or "unknown"
    workspace = pick(data, "cwd", "workspace") or os.getcwd()

    if mode == "session-start":
        core.record_session_start(sid, workspace)
        tasks = core.open_tasks(workspace, core.load_config().get("max_open_tasks", 3))
        output_text(core.format_open_tasks(tasks))
        return

    if mode == "user-prompt-submit":
        prompt = pick(data, "prompt", "user_prompt", "userPrompt")
        first = not any(event.get("ev") == "prompt" for event in core.load_events(sid=sid))
        core.record_prompt(sid, prompt, workspace)
        lines = []
        if first:
            tasks = core.open_tasks(workspace, core.load_config().get("max_open_tasks", 3))
            lines.append(core.format_start_hint(tasks))
        output_text("\n\n".join(line for line in lines if line))
        return

    if mode == "post-tool":
        name = pick(data, "tool_name", "toolName", "tool") or "unknown"
        response = data.get("tool_response") or data.get("toolResponse")
        is_error = False
        if isinstance(response, dict):
            is_error = bool(response.get("is_error") or response.get("isError"))
        tool_input = data.get("tool_input") or data.get("toolInput") or {}
        target = ""
        if isinstance(tool_input, dict):
            lowered = str(name).lower()
            if lowered in ("read", "read_file", "view", "open", "cat"):
                keys = ("file_path", "filePath", "path", "file")
            elif lowered == "skill":
                keys = ("name", "skill")
            elif lowered in ("grep", "search"):
                keys = ("path", "file_path")
            else:
                keys = ()
            for key in keys:
                value = tool_input.get(key)
                if isinstance(value, str) and value.strip():
                    target = value.strip()
                    break
        core.record_tool(sid, name, is_error, target or None)
        output_post_tool(core.maybe_reminder(sid))
        return

    if mode == "stop":
        if data.get("stop_hook_active"):
            return
        core.record_stop(sid)
        return

    if mode == "session-end":
        core.record_session_end(sid)
        return


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
