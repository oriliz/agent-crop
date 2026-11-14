#!/usr/bin/env bash
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if ! command -v python3 >/dev/null 2>&1; then
  echo "agentsync: 需要 python3（macOS 可用 xcode-select --install 安装）" >&2
  exit 1
fi
exec python3 "$HERE/agentsync.py" "$@"
