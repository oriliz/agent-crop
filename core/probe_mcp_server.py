#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the crop_update and crop_health tools.

The Claude Code plugin starts this process through .mcp.json.  It speaks
newline-delimited JSON-RPC on stdin/stdout and writes only to stderr for logs.
"""

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

try:
    import probe_core as core
except Exception:  # pragma: no cover
    core = None

SERVER_NAME = "crop"
SERVER_VERSION = "0.4.0"
PROTOCOL_VERSION = "2024-11-05"

TOOL_NAME = "crop_update"
# 旧名保留为调用别名：升级后旧会话/旧配置仍能用，tools/list 只暴露新名。
LEGACY_TOOL_NAMES = {"crop", "probe_report"}

TOOL = {
    "name": TOOL_NAME,
    "description": (
        "Record what the current long task is doing, why it is slow or blocked, "
        "and what comes next. Call it when the probe asks, or when the user "
        "confirms the task is finished. Keep summary/cause/next short."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "phase": {
                "type": "string",
                "enum": ["start", "progress", "blocked", "end"],
                "description": "start a task, report progress, report a blocker, or end the task.",
            },
            "task_id": {
                "type": "string",
                "description": "Stable kebab-case id, e.g. design-doc-payment.",
            },
            "summary": {
                "type": "string",
                "description": "One sentence: what is happening right now.",
            },
            "cause": {
                "type": "string",
                "description": "Why it is taking long or is blocked (required for blocked).",
            },
            "next": {
                "type": "string",
                "description": "The next concrete action.",
            },
            "category": {
                "type": "string",
                "enum": [
                    "serial-wait",
                    "repeat-pitfall",
                    "design-churn",
                    "decision-batching",
                    "tool-misuse",
                    "no-evidence",
                    "spec-drift",
                    "human-wait",
                    "other",
                ],
                "description": "Failure category (required for blocked).",
            },
            "goal": {"type": "string", "description": "One-line goal of this work (keep it short, <= 20 chars)."},
            "goal": {"type": "string", "description": "One-line goal of this work."},
            "type": {
                "type": "string",
                "pattern": "^[a-z0-9][a-z0-9-]{0,31}$",
                "maxLength": 32,
                "description": (
                    "Work type. Built-ins: design / implement / debug / docs / explore; "
                    "you may also use your own short lowercase slug (letters, digits, hyphen)."
                ),
            },
            "difficulty": {"type": "string", "enum": ["s", "m", "l"]},
            "verdict": {
                "type": "string",
                "enum": ["justified", "not-justified", "unknown"],
                "description": "Whether the slowdown was reasonable vs. a wrong path.",
            },
            "improve": {"type": "string", "description": "One thing to do better next time."},
            "expect_min": {"type": "number", "description": "Your own estimate of this work in minutes."},
            "expect_why": {"type": "string", "description": "One sentence backing the estimate."},
            "rework": {"type": "string", "enum": ["redo", "major", "minor", "none"]},
            "sentiment": {"type": "string", "enum": ["pos", "neu", "neg"]},
            "what": {"type": "string", "description": "end: required — what was done."},
            "cost": {"type": "string", "description": "end: where the time went."},
            "next_time": {"type": "string", "description": "end: what to do better next time."},
            "artifact": {"type": "string", "description": "Optional: what this work produced."},
        },
        "required": ["phase", "task_id", "summary"],
    },
}


def send(message: dict) -> None:
    sys.stdout.write(json.dumps(message, ensure_ascii=False) + "\n")
    sys.stdout.flush()


def respond(request_id, result: dict) -> None:
    if request_id is None:
        return
    send({"jsonrpc": "2.0", "id": request_id, "result": result})


def fail(request_id, code: int, message: str) -> None:
    if request_id is None:
        return
    send({"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}})


HEALTH_TOOL = {
    "name": "crop_health",
    "description": (
        "Inspect how healthy the crop collectors are: per-tool coverage, field completeness, "
        "work closure, and generated diagnostics. Use it before trusting numbers or when asked "
        "whether collection is working."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "days": {"type": "number", "description": "Window in days (default 7)."},
            "host": {"type": "string", "description": "Optional host filter, e.g. dsh / claude-code."},
        },
    },
}

def call_tool(arguments: dict) -> dict:
    if core is None:
        return {"content": [{"type": "text", "text": "probe core unavailable"}], "isError": True}
    try:
        record = core.record_report(arguments, context={})
        text = (
            f"recorded {record['phase']} {record['task_id']}"
            f" ({record['minutes']:.0f} min in this block)"
        )
        return {"content": [{"type": "text", "text": text}], "isError": False}
    except Exception as exc:
        return {"content": [{"type": "text", "text": f"{TOOL_NAME} failed: {exc}"}], "isError": True}


def main() -> None:
    # readline (not iteration) so a single JSON-RPC message is served as soon as
    # its newline arrives; iterating a piped stdin can block on read-ahead.
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except Exception:
            continue
        method = message.get("method")
        request_id = message.get("id")
        params = message.get("params") or {}

        if method == "initialize":
            respond(request_id, {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
            })
        elif method in ("notifications/initialized", "initialized"):
            continue
        elif method == "tools/list":
            respond(request_id, {"tools": [TOOL, HEALTH_TOOL]})
        elif method == "tools/call":
            name = params.get("name")
            if name == "crop_health":
                arguments = params.get("arguments") or {}
                try:
                    summary = core.health_summary(
                        float(arguments.get("days") or 7.0),
                        host_filter=arguments.get("host") or None,
                    )
                    text = core.format_health(summary)
                    respond(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
                except Exception as exc:
                    respond(request_id, {"content": [{"type": "text", "text": f"crop_health failed: {exc}"}], "isError": True})
                continue
            if name != TOOL_NAME and name not in LEGACY_TOOL_NAMES:
                fail(request_id, -32601, f"unknown tool: {name}")
                continue
            respond(request_id, call_tool(params.get("arguments") or {}))
        elif method == "ping":
            respond(request_id, {})
        elif request_id is not None:
            fail(request_id, -32601, f"method not found: {method}")


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:  # stderr only; never corrupt the protocol stream
        print(f"probe_mcp_server: {exc}", file=sys.stderr)
    sys.exit(0)
