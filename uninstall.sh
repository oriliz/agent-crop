#!/usr/bin/env bash
# 回滚：摘掉本套件注册的 hook、规则块与软链。默认保留数据目录。
set -euo pipefail

TS="$(date +%Y%m%d-%H%M%S)"
DRY=0
PURGE=0
IDES="${IDE_LIST:-qoder}"

usage() {
  cat <<'EOF'
用法: ./uninstall.sh [选项]

  --ide qoder,claude,cursor,codex   目标 IDE（逗号分隔，默认 qoder）
  --purge                           连本地数据目录一起删
  --dry-run                         只打印将要做的改动
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --ide) IDES="$2"; shift 2 ;;
    --purge) PURGE=1; shift ;;
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

for ide in $(echo "$IDES" | tr ',' ' '); do
  HOME_IDE="$(ide_home "$ide")"
  [ -n "$HOME_IDE" ] || continue
  say "== $ide ($HOME_IDE)"

  # 1) 摘 hook
  if [ -f "$HOME_IDE/settings.json" ] && [ "$DRY" -eq 0 ]; then
    cp "$HOME_IDE/settings.json" "$HOME_IDE/settings.json.bak-$TS"
    KIT_IDE_HOME="$HOME_IDE" python3 - <<'PY'
import json, os
home = os.environ["KIT_IDE_HOME"]
path = os.path.join(home, "settings.json")
needle = os.path.join(home, "task-metrics", "bin")
data = json.load(open(path, encoding="utf-8"))
hooks = data.get("hooks", {})
removed = 0
for event in list(hooks):
    groups = []
    for g in hooks[event]:
        kept = [h for h in g.get("hooks", []) if needle not in (h.get("command") or "")]
        removed += len(g.get("hooks", [])) - len(kept)
        if kept:
            g = dict(g)
            g["hooks"] = kept
            groups.append(g)
    if groups:
        hooks[event] = groups
    else:
        hooks.pop(event)
json.dump(data, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
print(f"  hook 摘除: {removed} 条")
PY
  elif [ "$DRY" -eq 1 ]; then
    say "[dry-run] 从 $HOME_IDE/settings.json 摘除 task-metrics hooks"
  fi

  # 2) 摘规则块
  KIT_IDE_HOME="$HOME_IDE" DRY="$DRY" python3 - <<'PY'
import os
home = os.environ["KIT_IDE_HOME"]
dry = os.environ.get("DRY") == "1"
path = os.path.join(home, "AGENTS.md")
begin, end = "<!-- AI-CODING-LOOP:BEGIN -->", "<!-- AI-CODING-LOOP:END -->"
if not os.path.exists(path):
    raise SystemExit
cur = open(path, encoding="utf-8").read()
if begin in cur and end in cur:
    while begin in cur and end in cur:
        head, rest = cur.split(begin, 1)
        _, tail = rest.split(end, 1)
        cur = head + tail
    out = cur.rstrip() + "\n"
    if dry:
        print("[dry-run] 从 AGENTS.md 摘除规则块")
    else:
        open(path, "w", encoding="utf-8").write(out)
        print("  规则块: 已摘除")
PY

  # 3) 摘软链（存在 .bak-* 时恢复最近一份）
  for p in "$HOME_IDE"/skills/* "$HOME_IDE/task-metrics/bin"; do
    [ -L "$p" ] || continue
    run "rm -f '$p'"
    latest="$(ls -dt "$p".bak-* 2>/dev/null | head -1 || true)"
    if [ -n "$latest" ]; then
      run "mv '$latest' '$p'"
      say "  恢复: $p <- $(basename "$latest")"
    else
      say "  移除: $p"
    fi
  done

  if [ "$PURGE" -eq 1 ]; then
    run "rm -rf '$HOME_IDE/task-metrics/data'"
    say "  已删除数据目录（--purge）"
  else
    say "  数据目录保留: $HOME_IDE/task-metrics/data"
  fi
done

say ""
say "完成。"
