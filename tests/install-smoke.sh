#!/usr/bin/env bash
# 安装/卸载冒烟：全部在临时 HOME 里跑，不碰真实配置。
# 用途：改完 install.sh / uninstall.sh / 规则块 / hooks 后先跑这个，再上真机。
set -uo pipefail

KIT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAIL=0
ok()   { printf '  ok   %s\n' "$1"; }
bad()  { printf '  FAIL %s\n' "$1"; FAIL=1; }
check(){ if eval "$2" >/dev/null 2>&1; then ok "$1"; else bad "$1"; fi; }

hook_count() {  # $1=settings.json  $2=grep 关键词（空=全部）
  python3 - "$1" "${2:-}" <<'PY'
import json, sys
p, kw = sys.argv[1], sys.argv[2]
try:
    d = json.load(open(p, encoding="utf-8"))
except FileNotFoundError:
    print(0); raise SystemExit
cmds = [h.get("command", "") for gs in (d.get("hooks") or {}).values()
        for g in gs for h in (g.get("hooks") or [])]
print(sum(1 for c in cmds if kw in c) if kw else len(cmds))
PY
}

echo "== 1. 干净环境（无 ~/.qoder）双 IDE 安装"
CLEAN=$(mktemp -d /tmp/kit-smoke-clean-XXXXXX)
HOME="$CLEAN" "$KIT_DIR/install.sh" --ide qoder,cursor >/dev/null 2>&1
for ide in .qoder .cursor; do
  check "$ide 技能已链接"        "[ -L '$CLEAN/$ide/skills/fast-loop' ]"
  check "$ide harness 已链接"    "[ -L '$CLEAN/$ide/task-metrics/bin' ]"
  check "$ide 注册 8 条 hook"    "[ \$(hook_count '$CLEAN/$ide/settings.json') -eq 8 ]"
  check "$ide 规则块存在"        "grep -q 'AI-CODING-LOOP:BEGIN' '$CLEAN/$ide/AGENTS.md'"
done

echo "== 2. 数据不落到仓库，落在本机"
HOME="$CLEAN" python3 "$CLEAN/.qoder/task-metrics/bin/harness_scan.py" status >/dev/null 2>&1
check "数据目录在 IDE home 内"   "[ -d '$CLEAN/.qoder/task-metrics/data' ]"
check "仓库内无 data 目录"       "[ ! -d '$KIT_DIR/hooks/task-metrics/data' ]"

echo "== 3. 已有配置的用户：安装不破坏、可重复安装"
SEED=$(mktemp -d /tmp/kit-smoke-seed-XXXXXX)
mkdir -p "$SEED/.qoder/skills/fast-loop"
echo old > "$SEED/.qoder/skills/fast-loop/OLD.md"
printf '{"hooks":{"Stop":[{"matcher":"*","hooks":[{"type":"command","command":"echo mine"}]}]},"permissions":{"allow":["Bash"]}}\n' > "$SEED/.qoder/settings.json"
printf '# 我的规则\n\n保留这段。\n' > "$SEED/.qoder/AGENTS.md"
HOME="$SEED" "$KIT_DIR/install.sh" --ide qoder >/dev/null 2>&1
HOME="$SEED" "$KIT_DIR/install.sh" --ide qoder >/dev/null 2>&1
check "重复安装不重复注册（8+1）" "[ \$(hook_count '$SEED/.qoder/settings.json') -eq 9 ]"
check "用户自有 hook 保留"        "[ \$(hook_count '$SEED/.qoder/settings.json' 'echo mine') -eq 1 ]"
check "用户 permissions 保留"     "grep -q 'permissions' '$SEED/.qoder/settings.json'"
check "规则块只有一份"            "[ \$(grep -c 'AI-CODING-LOOP:BEGIN' '$SEED/.qoder/AGENTS.md') -eq 1 ]"
check "旧技能有备份"              "ls '$SEED/.qoder/skills/' | grep -q 'fast-loop.bak-'"

echo "== 4. 卸载：精确摘除并还原用户原状"
HOME="$SEED" "$KIT_DIR/uninstall.sh" --ide qoder >/dev/null 2>&1
check "只剩用户自有 hook"          "[ \$(hook_count '$SEED/.qoder/settings.json') -eq 1 ]"
check "规则块已摘除"               "! grep -q 'AI-CODING-LOOP' '$SEED/.qoder/AGENTS.md'"
check "AGENTS.md 用户内容保留"     "grep -q '保留这段' '$SEED/.qoder/AGENTS.md'"
check "旧技能已还原"               "[ -f '$SEED/.qoder/skills/fast-loop/OLD.md' ]"
check "数据目录默认保留"           "[ -d '$SEED/.qoder/task-metrics/data' ]"

rm -rf "$CLEAN" "$SEED"
[ "$FAIL" -eq 0 ] && echo "全部通过" || { echo "存在失败项"; exit 1; }
