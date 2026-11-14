#!/usr/bin/env bash
# Qoder SessionStart 钩子：后台补摘要/同步，前台打印对端交接简报。
set -u
SCRIPT="$HOME/.agent-sync/bin/agentsync.py"
[[ -f "$SCRIPT" ]] || exit 0
exec python3 "$SCRIPT" session-start
