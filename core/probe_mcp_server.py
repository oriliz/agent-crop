#!/usr/bin/env python3
"""Minimal stdio MCP server exposing the probe_report tool.

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

SERVER_NAME = "ai-coding-efficiency-kit"
SERVER_VERSION = "0.4.0"
PROTOCOL_VERSION = "2024-11-05"

TOOL = {
    "name": "probe_report",
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
        return {"content": [{"type": "text", "text": f"probe_report failed: {exc}"}], "isError": True}


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
            respond(request_id, {"tools": [TOOL]})
        elif method == "tools/call":
            if params.get("name") != "probe_report":
                fail(request_id, -32601, f"unknown tool: {params.get('name')}")
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
