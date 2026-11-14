#!/usr/bin/env bash
# 安装/接线：skill 软链 + task-metrics 引擎软链 + settings.json hook 注册 + AGENTS.md 规则块。
# 幂等：可重复执行；改动前自动备份。
set -euo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TS="$(date +%Y%m%d-%H%M%S)"
DRY=0
IDES="${IDE_LIST:-qoder}"
WITH_SYNC=0

usage() {
  cat <<'EOF'
用法: ./install.sh [选项]

  --ide qoder,claude,cursor,codex   目标 IDE（逗号分隔，默认 qoder）
  --with-multi-machine-sync         追加安装多机协同模块
  --dry-run                         只打印将要做的改动
  -h, --help                        帮助
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ide) IDES="$2"; shift 2 ;;
    --with-multi-machine-sync) WITH_SYNC=1; shift ;;
    --dry-run) DRY=1; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数: $1" >&2; usage >&2; exit 2 ;;
  esac
done

say() { printf '%s\n' "$*"; }
run() {
  if [ "$DRY" -eq 1 ]; then say "[dry-run] $*"; else eval "$@"; fi
}

ide_home() {
  case "$1" in
    qoder) echo "$HOME/.qoder" ;;
    qoderwork) echo "$HOME/.qoderwork" ;;
    claude) echo "$HOME/.claude" ;;
    cursor) echo "$HOME/.cursor" ;;
    codex) echo "$HOME/.codex" ;;
    iflow) echo "$HOME/.iflow" ;;
    *) echo "" ;;
  esac
}

backup_if_needed() {
  local p="$1"
  [ -e "$p" ] || return 0
  [ -L "$p" ] && return 0
  run "mv '$p' '$p.bak-$TS'"
  say "  备份: $p -> $p.bak-$TS"
}

link_dir() {
  local src="$1" dst="$2"
  if [ -L "$dst" ]; then
    local cur; cur="$(readlink "$dst")"
    if [ "$cur" = "$src" ]; then say "  已是最新: $dst"; return 0; fi
    run "rm -f '$dst'"
  else
    backup_if_needed "$dst"
  fi
  run "mkdir -p '$(dirname "$dst")'"
  run "ln -s '$src' '$dst'"
  say "  链接: $dst -> $src"
}

for ide in $(echo "$IDES" | tr ',' ' '); do
  HOME_IDE="$(ide_home "$ide")"
  if [ -z "$HOME_IDE" ]; then
    say "跳过未知 IDE: $ide"; continue
  fi
  say "== $ide ($HOME_IDE)"
  run "mkdir -p '$HOME_IDE'"

  # 1) skill 软链
  for s in "$KIT_DIR"/skills/*/; do
    [ -d "$s" ] || continue
    link_dir "${s%/}" "$HOME_IDE/skills/$(basename "$s")"
  done

  # 2) 计时引擎：只链 bin，数据目录留在本地
  run "mkdir -p '$HOME_IDE/task-metrics/data'"
  link_dir "$KIT_DIR/hooks/task-metrics/bin" "$HOME_IDE/task-metrics/bin"

  # 2.1) 新装默认开两条干预；已有开关文件不动，避免把手工关掉的重新打开
  if [ "$DRY" -eq 1 ]; then
    say "[dry-run] 新装默认启用 tempo-rules、inline-reminders"
  else
    KIT_IDE_HOME="$HOME_IDE" python3 - <<'PY'
import json, os
home = os.environ["KIT_IDE_HOME"]
p = os.path.join(home, "task-metrics", "data", "solutions.json")
if os.path.exists(p):
    print("  已有开关配置，未改动:", p)
else:
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w", encoding="utf-8") as f:
        json.dump({"enabled": ["inline-reminders", "tempo-rules", "detour-signal"]}, f, ensure_ascii=False, indent=2)
    print("  默认启用: inline-reminders, tempo-rules, detour-signal")
PY
  fi

  # 3) hook 注册
  if [ "$DRY" -eq 1 ]; then
    say "[dry-run] 合并 hook 到 $HOME_IDE/settings.json"
  else
    [ -f "$HOME_IDE/settings.json" ] && cp "$HOME_IDE/settings.json" "$HOME_IDE/settings.json.bak-$TS"
    KIT_IDE_HOME="$HOME_IDE" python3 - <<'PY'
import json, os, sys
home = os.environ["KIT_IDE_HOME"]
settings_path = os.path.join(home, "settings.json")
bin_dir = os.path.join(home, "task-metrics", "bin")
data_dir = os.path.join(home, "task-metrics", "data")

def cmd(script, mode):
    return (f'KIT_IDE_HOME="{home}" python3 "{os.path.join(bin_dir, script)}" {mode}')

wanted = {
    "SessionStart": [("startup|resume|compact", cmd("task_tempo_hook.py", "session-start"), 5)],
    "UserPromptSubmit": [("*", cmd("task_tempo_hook.py", "user-prompt-submit"), 5),
                         ("*", cmd("harness_scan.py", "inject"), 5)],
    # harness_scan 的 post-tool 用 '*'：计数器要吃全所有工具（语义检索/LSP 不在固定名单里），
    # 漏计会让 RULE-004 误判成「一次语义检索都没有」；方案分发也在这里，别再加第二个入口。
    "PostToolUse": [("*", cmd("harness_scan.py", "post-tool"), 5)],
    "Stop": [("*", cmd("task_tempo_hook.py", "stop"), 5),
             ("*", cmd("harness_scan.py", "scan"), 10)],
    # SessionEnd 再跑一次任务事实/资产命中：中途退出（非 Stop 正常收尾）也能落数
    "SessionEnd": [("*", cmd("harness_scan.py", "scan"), 10),
                   ("*", cmd("task_tempo_hook.py", "session-end"), 15)],
}

try:
    with open(settings_path, encoding="utf-8") as f:
        data = json.load(f)
except FileNotFoundError:
    data = {}
hooks = data.setdefault("hooks", {})


def key(command):
    """按「脚本名 + 模式」判重：已手工接线的旧路径写法不会重复注册。"""
    for script in ("task_tempo_hook.py", "harness_scan.py"):
        if script in command:
            return script, command.rsplit(" ", 1)[-1]
    return None


added = 0
for event, entries in wanted.items():
    groups = hooks.setdefault(event, [])
    in_event = {key(h.get("command") or "")
                for g in groups for h in g.get("hooks", [])}
    for matcher, command, timeout in entries:
        if key(command) in in_event:
            continue
        groups.append({"matcher": matcher,
                       "hooks": [{"type": "command", "command": command, "timeout": timeout}]})
        in_event.add(key(command))
        added += 1
with open(settings_path, "w", encoding="utf-8") as f:
    json.dump(data, f, ensure_ascii=False, indent=2)
print(f"  hook 注册: 新增 {added} 条（已存在的跳过）")
PY
  fi

  # 4) 规则块注入
  KIT_IDE_HOME="$HOME_IDE" KIT_DIR="$KIT_DIR" DRY="$DRY" python3 - <<'PY'
import os
home = os.environ["KIT_IDE_HOME"]
kit = os.environ["KIT_DIR"]
dry = os.environ.get("DRY") == "1"
agents = os.path.join(home, "AGENTS.md")
block = open(os.path.join(kit, "rules", "task-tempo.md"), encoding="utf-8").read().strip()
begin, end = "<!-- AI-CODING-LOOP:BEGIN -->", "<!-- AI-CODING-LOOP:END -->"
try:
    cur = open(agents, encoding="utf-8").read()
except FileNotFoundError:
    cur = ""
# 兼容迁移：旧版规则块标记（TASK-TEMPO）在写入新版前摘掉，避免同一规则出现两份
for ob, oe in (("<!-- TASK-TEMPO:BEGIN", "<!-- TASK-TEMPO:END -->"),):
    while ob in cur and oe in cur:
        head, rest = cur.split(ob, 1)
        idx = rest.find(oe)
        if idx < 0:
            break
        cur = head + rest[idx + len(oe):]
new = block.replace("$KIT_IDE_HOME", home)
if begin in cur and end in cur:
    head, rest = cur.split(begin, 1)
    _, tail = rest.split(end, 1)
    out = head + new + tail
else:
    out = (cur.rstrip() + "\n\n" if cur.strip() else "") + new + "\n"
if out == cur:
    print("  规则块: 已是最新")
elif dry:
    print("[dry-run] 写入规则块到", agents)
else:
    open(agents, "w", encoding="utf-8").write(out)
    print("  规则块: 已写入", agents)
PY
done

# 5) 可选：多机协同
if [ "$WITH_SYNC" -eq 1 ]; then
  SRC="$KIT_DIR/optional/multi-machine-sync"
  link_dir "$SRC/bin" "$HOME/.agent-sync/bin"
  run "mkdir -p '$HOME/.agent-sync/state' '$HOME/.agent-sync/shared'"
  if [ ! -f "$HOME/.agent-sync/config.env" ]; then
    run "cp '$SRC/config.env.example' '$HOME/.agent-sync/config.env'"
    say "  生成配置: ~/.agent-sync/config.env（请填入对端名称与 ssh 目标）"
  else
    say "  配置已存在，未覆盖: ~/.agent-sync/config.env"
  fi
  say "  注意：多机协同的 SessionStart/SessionEnd hook 需按需自行注册（见模块 README）"
fi

say ""
say "完成。自查："
say "  python3 \"$HOME_IDE/task-metrics/bin/harness_scan.py\" status"
say "  python3 \"$HOME_IDE/task-metrics/bin/harness_dashboard.py\""
