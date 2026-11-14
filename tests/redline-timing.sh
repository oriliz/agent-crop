#!/usr/bin/env bash
# 红线时间账回放：验证超时告警能区分 agent 执行 / 块内等你 / 块后你离开，并带 human-wait 归因。
# 两个场景都锚定当前时间构造事件，不读真实数据、不写真实目录。
set -uo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOOK="$KIT_DIR/hooks/task-metrics/bin/task_tempo_hook.py"
FACTS="$KIT_DIR/hooks/task-metrics/bin/taskfacts.py"
FAIL=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; FAIL=1; }

run_case() {  # $1=名字 $2=turns(逗号) $3=waits(逗号) $4=块后间隔秒
  local name="$1" turns="$2" waits="$3" tail="$4"
  local dir; dir=$(mktemp -d /tmp/kit-timing-XXXXXX)
  mkdir -p "$dir/data"
  printf '{"enabled":["tempo-rules"]}\n' > "$dir/data/solutions.json"
  python3 - "$dir/data" "$name" "$turns" "$waits" "$tail" <<'PY'
import json, os, sys, time
d, sid, turns, waits, tail = sys.argv[1], sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5])
turns = [int(x) for x in turns.split(",")]
waits = [int(x) for x in waits.split(",")]
total = sum(turns) + sum(waits)
base = time.time() - total - tail
T = lambda s: int((base + s) * 1000)
ev = [{"t": T(0), "ev": "prompt", "sid": sid, "snippet": "回放"}]
cur = 0
for i, turn in enumerate(turns):
    cur += turn
    ev.append({"t": T(cur), "ev": "stop", "sid": sid})
    if i < len(turns) - 1 and i < len(waits):
        cur += waits[i]
        ev.append({"t": T(cur), "ev": "prompt", "sid": sid, "snippet": "继续"})
open(os.path.join(d, "events.jsonl"), "w").write("".join(json.dumps(e) + "\n" for e in ev))
PY
  local out
  out=$(printf '{"session_id":"%s","prompt":"继续"}' "$name" | \
        KIT_DATA_DIR="$dir/data" python3 "$HOOK" user-prompt-submit 2>/dev/null)
  printf '%s\n' "$out" > "$dir/out.txt"
  KIT_DATA_DIR="$dir/data" python3 "$FACTS" backfill --days 3 --session "$name" >/dev/null 2>&1
  printf '%s' "$dir"
}

echo "== 场景 1：执行 56 分钟，块后你离开 48 分钟"
D1=$(run_case timing-away "480,480,480,480,480,480,472" "20,20,20,20,20,20" 2865)
OUT1=$(cat "$D1/out.txt")
[[ "$OUT1" == *"agent 执行 56 分钟"* ]] && ok "执行 56 分钟" || bad "执行 56 分钟：$OUT1"
[[ "$OUT1" == *"等你 2 分钟"* ]]        && ok "块内等你 2 分钟" || bad "块内等你 2 分钟"
[[ "$OUT1" == *"你离开 48 分钟"* ]]     && ok "块后离开 48 分钟" || bad "块后离开 48 分钟"
[[ "$OUT1" == *"human-wait"* ]]         && ok "原因菜单含 human-wait" || bad "原因菜单含 human-wait"
[[ "$OUT1" == *"不要把它算进"* ]]       && ok "带误归因禁令" || bad "带误归因禁令"
grep -q '"work_minutes": 55.9' "$D1/data/events.jsonl" && ok "红线事件记 work=55.9" || bad "红线事件记 work"
grep -q '"away_minutes": 47.8' "$D1/data/events.jsonl" && ok "红线事件记 away=47.8" || bad "红线事件记 away"
F1=$(cat "$D1"/data/taskfacts/*.jsonl)
[[ "$F1" == *'"away_minutes": 47.8'* ]] && ok "任务事实带离开时长" || bad "任务事实带离开时长"

echo "== 场景 2：执行 10 分钟，块内等你 30 分钟"
D2=$(run_case timing-wait "60,60,60,60,60,60,60,60,60,60" "180,180,180,180,180,180,180,180,180" 60)
OUT2=$(cat "$D2/out.txt")
[[ "$OUT2" == *"agent 执行 10 分钟"* ]] && ok "执行 10 分钟" || bad "执行 10 分钟：$OUT2"
[[ "$OUT2" == *"等你 28 分钟"* ]]       && ok "块内等你 28 分钟（含最后 60 秒）" || bad "块内等你 28 分钟"
[[ "$OUT2" == *"你离开 0 分钟"* ]]      && ok "块内等待不算离开" || bad "块内等待不算离开"

rm -rf "$D1" "$D2"
exit "$FAIL"
