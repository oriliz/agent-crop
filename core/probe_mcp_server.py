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
        "Bind a work, log progress, a blocker, or the finish. Start by binding and "
        "reporting expect_min. Keep one task_id per feature iteration; log micro-changes "
        "as phase=progress, not a new work. After end, delivery/reporting/screenshots do "
        "not need a new binding; use category=side-issue for follow-up questions. "
        "Keep summary/cause/next short."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "phase": {
                "type": "string",
                "enum": ["start", "progress", "blocked", "end"],
                "description": "start=binding/begin; progress=something changed; blocked=stuck; end=finished.",
            },
            "task_id": {
                "type": "string",
                "description": "Short 2-3 word work name, e.g. payment-design. Keep it stable within one iteration.",
            },
            "summary": {
                "type": "string",
                "description": "One sentence: what is happening right now.",
            },
            "cause": {
                "type": "string",
                "description": "Why it is slow or blocked (required for blocked). When verdict=justified, add how you might be wrong.",
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
            "goal": {"type": "string", "description": "One-line goal, <= 20 chars."},
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
                "description": "Self-review: task genuinely hard=justified, wrong path=not-justified. Add a one-line counter-check in cause when justified.",
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
            "sediment": {
                "type": "string",
                "description": "On end: where the lesson belongs (agents:<file>#<section> / skill:<name> / tool:<name> / hook:<name> / docs:<path> / none:<reason>).",
            },
        },
        "required": ["phase", "task_id", "summary"],
    },
}


ADJUST_TOOL = {
    "name": "crop_adjust",
    "description": (
        "Improvement ledger. action=record stores one adopted change: metric, evidence "
        "and the subjective note why it should help. Do NOT supply before/after numbers — "
        "the ledger derives them from the event log. action=ledger reads baseline/current, "
        "sample sizes, guardrails and status; the status is computed by rules, not by the model."
    ),
    "inputSchema": {
        "type": "object",
        "additionalProperties": False,
        "properties": {
            "action": {"type": "string", "enum": ["record", "ledger"]},
            "metric": {
                "type": "string",
                "enum": ["work_minutes", "reminder_per_turn", "report_latency_min", "unowned_tool_share"],
                "description": "Metric derived from the event log; add a new derivation in core for a new metric.",
            },
            "evidence": {"type": "string", "description": "Commit / PR / file / command (record, required)."},
            "note": {"type": "string", "description": "Record, required: one-line subjective reason why this change should help."},
            "work_id": {"type": "string"},
            "window": {"type": "string", "description": "Sampling window, e.g. 2026-10-20~11-03."},
            "days": {"type": "number", "description": "Ledger window in days (default 180)."},
            "artifact": {
                "type": "object",
                "description": (
                    "Record: where the adopted change actually landed, so the ledger can "
                    "verify it (file exists + sessions that read it). kind=file/skill/hook; "
                    "delivery=read (on demand) or injected (auto-loaded every session, e.g. AGENTS.md)."
                ),
                "properties": {
                    "kind": {"type": "string", "enum": ["file", "skill", "hook"]},
                    "path": {"type": "string", "description": "Repo-relative or absolute path; skill name for kind=skill."},
                    "anchor": {"type": "string", "description": "Optional section or line anchor, e.g. #§1."},
                    "delivery": {"type": "string", "enum": ["read", "injected"], "description": "Default read."},
                },
                "required": ["path"],
            },
        },
        "required": ["action"],
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
            respond(request_id, {"tools": [TOOL, HEALTH_TOOL, ADJUST_TOOL]})
        elif method == "tools/call":
            name = params.get("name")
            if name == "crop_adjust":
                arguments = params.get("arguments") or {}
                try:
                    if arguments.get("action") == "ledger":
                        ledger = core.improvement_ledger(float(arguments.get("days") or 180.0))
                        text = core.format_ledger(ledger)
                    else:
                        event = core.record_adjustment(arguments, context={"workspace": os.getcwd()})
                        text = f"recorded adjustment {event['metric']} (numbers are derived by the ledger)"
                    respond(request_id, {"content": [{"type": "text", "text": text}], "isError": False})
                except Exception as exc:
                    respond(request_id, {"content": [{"type": "text", "text": f"crop_adjust failed: {exc}"}], "isError": True})
                continue
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
