#!/usr/bin/env python3
"""方案分发：宿主只接探针的固定入口，方案的 hook 由这里按启用列表调用。

用法：dispatch.py <event>
  event: session-start | user-prompt-submit | post-tool-use | stop
方案约定：solutions/<id>/solution.json + hook.py；hook.py 从 stdin 收宿主事件 JSON，
stdout 输出的文本会被拼进本轮注入；超时 5s，异常静默跳过。
"""
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kit_solutions as sol


def handlers():
    out = []
    for name in sorted(os.listdir(HERE)):
        d = os.path.join(HERE, name)
        meta_p, hook_p = os.path.join(d, "solution.json"), os.path.join(d, "hook.py")
        if not (os.path.isdir(d) and os.path.isfile(meta_p) and os.path.isfile(hook_p)):
            continue
        try:
            with open(meta_p, encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        out.append((meta.get("id") or name, meta, hook_p))
    return out


def main():
    event = sys.argv[1] if len(sys.argv) > 1 else ""
    raw = "" if sys.stdin.isatty() else sys.stdin.read()
    on = sol.enabled()
    outs = []
    for sid, meta, hook in handlers():
        if sid not in on or event not in (meta.get("events") or []):
            continue
        try:
            r = subprocess.run([sys.executable, hook, event], input=raw, capture_output=True,
                               text=True, timeout=5,
                               env={**os.environ, "KIT_SOLUTION_ID": sid})
            if r.stdout.strip():
                outs.append(r.stdout.strip())
        except Exception:
            continue
    if outs:
        print("\n\n".join(outs))


if __name__ == "__main__":
    try:
        main()
    except Exception:
        pass
    sys.exit(0)
