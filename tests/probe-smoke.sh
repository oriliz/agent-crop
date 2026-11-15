#!/usr/bin/env bash
# Smoke test for the v0.4 probe core, Claude hooks and the DSH JSON bridge.
# Uses only a temp data directory; never touches ~/.agentcrop.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DATA="$(mktemp -d)"
trap 'rm -rf "$DATA"' EXIT

export KIT_DATA_DIR="$DATA"
export KIT_HOST="smoke"

echo "== session-start =="
printf '%s' '{"session_id":"smoke-1","cwd":"/tmp/smoke","hook_event_name":"SessionStart"}' \
  | python3 "$ROOT/core/probe_hook.py" session-start

echo "== first prompt =="
OUT="$(printf '%s' '{"session_id":"smoke-1","prompt":"做一个设计文档","cwd":"/tmp/smoke","hook_event_name":"UserPromptSubmit"}' \
  | python3 "$ROOT/core/probe_hook.py" user-prompt-submit)"
printf '%s\n' "$OUT" | grep -q '\[crop\]' || { echo "missing start hint"; exit 1; }

echo "== backdate the block to 35 minutes =="
python3 - "$DATA" <<'PY'
import json, os, sys
data = sys.argv[1]
events = os.path.join(data, 'events.jsonl')
rows = [json.loads(line) for line in open(events, encoding='utf-8') if line.strip()]
now = int(__import__('time').time() * 1000)
for row in rows:
    if row.get('ev') == 'prompt':
        row['t'] = now - 35 * 60 * 1000
open(events, 'w', encoding='utf-8').write(
    ''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in rows)
)
runtime = os.path.join(data, 'runtime', 'current.json')
state = json.load(open(runtime, encoding='utf-8'))
state['block_start'] = now - 35 * 60 * 1000
open(runtime, 'w', encoding='utf-8').write(json.dumps(state, ensure_ascii=False))
PY

echo "== post-tool reminder =="
REMINDER="$(printf '%s' '{"session_id":"smoke-1","tool_name":"Bash","tool_response":{"stdout":"ok"},"hook_event_name":"PostToolUse"}' \
  | python3 "$ROOT/core/probe_hook.py" post-tool)"
printf '%s\n' "$REMINDER" | grep -q 'crop_report' || { echo "missing reminder"; exit 1; }

echo "== report via the DSH bridge =="
printf '%s' '{"op":"record_report","args":{"phase":"blocked","task_id":"Smoke Test!","summary":"查资料","cause":"资料太散","next":"先定目录","category":"tool-misuse"},"context":{"sid":"smoke-1","workspace":"/tmp/smoke"}}' \
  | python3 "$ROOT/core/probe_core_cli.py" | grep -q '"ok": true' || { echo "bridge failed"; exit 1; }

DAY="$(date +%F)"
grep -q '"task_id": "smoke-test"' "$DATA/reports/$DAY.jsonl"
grep -q '"host": "smoke"' "$DATA/events.jsonl"

echo "== stop =="
printf '%s' '{"session_id":"smoke-1","hook_event_name":"Stop","stop_hook_active":false}' \
  | python3 "$ROOT/core/probe_hook.py" stop
test -s "$DATA/blocks/$DAY.jsonl"

echo "PASS: probe smoke test"
