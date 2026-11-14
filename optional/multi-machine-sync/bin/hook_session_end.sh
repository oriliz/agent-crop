#!/usr/bin/env bash
# Qoder SessionEnd 钩子：为刚结束的会话生成摘要，并由 orchestrator 后台同步。
set -u
SCRIPT="$HOME/.agent-sync/bin/agentsync.py"
[[ -f "$SCRIPT" ]] || exit 0
exec python3 "$SCRIPT" session-end --quiet
