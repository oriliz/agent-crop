#!/usr/bin/env python3
"""Replay real Agent Crop data and assert invariants.

Usage: python3 tests/replay_check.py /path/to/data-copy

Copy production data first, never point this at the live directory:
    cp -R ~/.agentcrop/data /tmp/crop-replay-data
"""

import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "core"))


def main(data_dir):
    os.environ["KIT_DATA_DIR"] = data_dir
    import probe_core as core

    failures = []

    def check(name, ok, detail=""):
        print(f"[{'PASS' if ok else 'FAIL'}] {name}" + (f" — {detail}" if detail and not ok else ""))
        if not ok:
            failures.append(name)

    # 1. one active binding per session
    now = core.now_ms()
    active = [b for b in core.load_bindings().get("bindings", []) if float(b.get("expires_at") or 0) > now]
    per_sid = {}
    for item in active:
        per_sid.setdefault(item.get("sid"), []).append(item.get("work_id"))
    dupes = {sid: works for sid, works in per_sid.items() if len(works) > 1}
    check("每会话仅一条 active 绑定", not dupes, str(dupes))

    # 2. running the boot hooks twice must not change the number of bindings
    core.migrate_bindings()
    core.normalize_bindings()
    first = len(core.load_bindings().get("bindings", []))
    core.migrate_bindings()
    core.normalize_bindings()
    second = len(core.load_bindings().get("bindings", []))
    check("启动钩子幂等", first == second, f"{first} -> {second}")

    # 3. every open work with last_sid resolves to some binding
    tasks = core.load_open().get("tasks", {})
    missing = []
    for task in tasks.values():
        sid = task.get("last_sid")
        if sid and not core.resolve_binding(sid):
            missing.append(task.get("task_id"))
    check("有 last_sid 的工作都能解析到绑定", not missing, str(missing))

    # 4. work minutes never double count a block
    events = core.load_events(limit=500000)
    links_by_work = {}
    for event in events:
        if event.get("ev") == "task_link":
            links_by_work.setdefault(event.get("task_id"), set()).add(event.get("block_start"))
    inflated = []
    for work_id, starts in links_by_work.items():
        minutes = core.work_minutes(work_id)
        blocks = [s for s in starts if s]
        if minutes > len(blocks) * 24 * 60:
            inflated.append(work_id)
    check("工作时长没有重复计数", not inflated, str(inflated))

    # 5. every task_link after the fix carries a session (older rows are legacy debt)
    cutoff = 1794676800000  # 2026-11-15 02:40 local: sid was added to task_link
    orphans = [e for e in events
               if e.get("ev") == "task_link" and not e.get("sid") and int(e.get("t") or 0) >= cutoff]
    legacy = len([e for e in events if e.get("ev") == "task_link" and not e.get("sid")])
    check("修复后的 task_link 都带 sid", not orphans, f"{len(orphans)} 条")
    print(f"[INFO] 修复前遗留 {legacy - len(orphans)} 条无 sid 的 task_link（历史数据，不回填）")

    # 6. work_open / work_close are paired for closed works
    opened = {e.get("work_id") for e in events if e.get("ev") == "work_open"}
    closed = {e.get("work_id") for e in events if e.get("ev") == "work_close"}
    check("开过的都关过", closed <= opened, str(closed - opened))

    print()
    print(f"{len(failures)} 项失败" if failures else "全部通过")
    return 1 if failures else 0


if __name__ == "__main__":
    if len(sys.argv) != 2:
        print(__doc__)
        raise SystemExit(2)
    raise SystemExit(main(sys.argv[1]))
