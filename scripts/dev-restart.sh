#!/usr/bin/env bash
# 重启 dev profile 的 web 实例，并等它真的起来。
# macOS 没有 util-linux 的 setsid，用 python 双 fork 脱离父进程；
# 否则脚本会被宿主进程一起带走（这个坑踩过一次）。
#
# 用法：scripts/dev-restart.sh [port] [profile]
#   scripts/dev-restart.sh 3082 dev
set -u
PORT="${1:-3082}"
PROFILE="${2:-dev}"
LOG="${TMPDIR:-/tmp}/dsh-${PORT}.log"
DSH_BIN="$(command -v dsh || true)"

if [ -z "$DSH_BIN" ]; then
  echo "找不到 dsh，先把 DSH 装好" >&2
  exit 1
fi

OLD="$(lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null | head -1)"
if [ -n "${OLD:-}" ]; then
  echo "停掉旧进程 pid=$OLD"
  kill "$OLD" 2>/dev/null || true
  for _ in $(seq 1 80); do
    lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1 || break
    sleep 0.25
  done
fi

echo "启动 dsh --profile $PROFILE --port $PORT"
python3 - "$DSH_BIN" "$PROFILE" "$PORT" "$LOG" <<'PY'
import os, sys
binary, profile, port, log = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4]
if os.fork() > 0:
    sys.exit(0)
os.setsid()
if os.fork() > 0:
    sys.exit(0)
out = os.open(log, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
os.dup2(out, 1)
os.dup2(out, 2)
os.execv(binary, [binary, '--profile', profile, '--port', port, '--no-open'])
PY

for _ in $(seq 1 80); do
  lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1 && break
  sleep 0.25
done

if ! lsof -nP -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1; then
  echo "端口 $PORT 没起来，看日志：$LOG" >&2
  exit 1
fi

URL="$(grep -o "http://127.0.0.1:${PORT}/?token=[A-Za-z0-9_-]*" "$LOG" | tail -1)"
echo "${URL:-端口 $PORT 已监听，但日志里没找到 token（看 $LOG）}"
