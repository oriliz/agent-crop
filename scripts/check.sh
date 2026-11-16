#!/usr/bin/env bash
# 推前检查：把这个仓库里能跑的都跑一遍。CI 跑的是同一套，别等 CI 告诉你。
# 用法：bash scripts/check.sh
set -u
cd "$(dirname "$0")/.." || exit 1
fail=0

step() { printf '\n== %s ==\n' "$1"; }

step "python · unittest"
python3 -m unittest discover -s tests -t . -q || fail=1

step "node · --test"
if [ ! -d node_modules ]; then
  # dsh/lib 里 import 了 @deepseek-ai/schemastery，测试需要它
  npm ci --no-audit --no-fund || fail=1
fi
node --test dsh/test/index.test.mjs || fail=1

step "probe smoke"
bash tests/probe-smoke.sh || fail=1

if [ "$fail" -ne 0 ]; then
  echo
  echo "FAIL：上面有红的，修完再推。"
  exit 1
fi
echo
echo "OK：三套检查都过了，可以推。"
