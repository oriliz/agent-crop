#!/usr/bin/env python3
"""任务耗时复盘：按任务块计算每个任务耗时，标出超红线块。

数据源：~/.qoder/task-metrics/data/events.jsonl（由 task_tempo_hook.py 写入）

用法:
  task_retro.py                       # 最近 10 个任务（全部会话）
  task_retro.py --last 20             # 最近 20 个
  task_retro.py --session <sid>       # 只看某会话
  task_retro.py --redline 30          # 红线分钟数（默认 30）
  task_retro.py --merge-gap 180       # 相邻块合并阈值秒（默认 180，用于「继续」类短续指令）
  task_retro.py --json                # JSON 输出
"""
import argparse
import json
import os
import time
from datetime import datetime

DATA_DIR = os.path.expanduser(os.environ.get("KIT_DATA_DIR") or os.path.join(
    os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "task-metrics", "data"))
EVENTS = os.path.join(DATA_DIR, "events.jsonl")
TZ = datetime.now().astimezone().tzinfo


def fmt_ms(ms):
    return datetime.fromtimestamp(ms / 1000, TZ).strftime("%m-%d %H:%M")


def build_tasks(events, merge_gap_s):
    """prompt 开块；紧跟上一块结束（merge_gap 秒内、时间不回退）的新 prompt 视为同任务续指令。
    按文件顺序处理（hook 追加写入，时间单调）。events 需已按会话过滤。"""
    tasks = []
    cur = None
    turn_start = None
    for e in events:
        if e["ev"] == "prompt":
            gap_ok = (
                cur is not None
                and e["t"] >= cur["end"]
                and (e["t"] - cur["end"]) <= merge_gap_s * 1000
            )
            if gap_ok:
                cur["snippets"].append(e.get("snippet", ""))
                cur["waits"].append(e["t"] - cur["end"])
                turn_start = e["t"]
            else:
                cur = {"sid": e["sid"], "start": e["t"], "end": e["t"], "stops": 0,
                       "max_turn_ms": 0, "turns": [], "waits": [],
                       "snippets": [e.get("snippet", "")]}
                tasks.append(cur)
                turn_start = e["t"]
        elif e["ev"] == "stop" and cur is not None and e["t"] >= cur["start"]:
            turn = e["t"] - (turn_start if turn_start is not None else cur["start"])
            turn_start = e["t"]
            cur["turns"].append(max(0, turn))
            cur["max_turn_ms"] = max(cur["max_turn_ms"], max(0, turn))
            cur["end"] = e["t"]
            cur["stops"] += 1
    return tasks


def load():
    try:
        with open(EVENTS, encoding="utf-8") as f:
            return [json.loads(line) for line in f if line.strip()]
    except Exception:
        return []


def all_tasks(merge_gap_s=180):
    """按会话分组建块，再按开始时间合并排序。"""
    by_sid = {}
    for e in load():
        by_sid.setdefault(e.get("sid", "unknown"), []).append(e)
    tasks = []
    for sid, evs in by_sid.items():
        tasks.extend(build_tasks(evs, merge_gap_s))
    return sorted(tasks, key=lambda t: t["start"])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--session")
    ap.add_argument("--redline", type=float, default=30)
    ap.add_argument("--last", type=int, default=10)
    ap.add_argument("--merge-gap", type=float, default=180)
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()

    if a.session:
        evs = [e for e in load() if e.get("sid") == a.session]
        tasks = build_tasks(evs, a.merge_gap)
    else:
        tasks = all_tasks(a.merge_gap)
    if a.last > 0:
        tasks = tasks[-a.last:]

    out = []
    for t in tasks:
        dur_min = (t["end"] - t["start"]) / 60000.0
        first = (t["snippets"][0] if t["snippets"] else "")[:38]
        out.append({
            "session": t["sid"][:12],
            "start": fmt_ms(t["start"]),
            "end": fmt_ms(t["end"]),
            "duration_min": round(dur_min, 1),
            "turns": t["stops"],
            "max_turn_min": round(t["max_turn_ms"] / 60000.0, 1),
            "over_redline": dur_min >= a.redline,
            "task": first,
        })

    if a.json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
        return

    if not out:
        print("暂无打点数据（hook 从安装后开始记录）。")
        return

    print(f"{'开始':<13}{'耗时(分)':>8} {'回合':>4} {'最长回合':>8}  任务")
    for o in out:
        flag = " ⚠️超红线" if o["over_redline"] else ""
        print(f"{o['start']:<13}{o['duration_min']:>8} {o['turns']:>4} {o['max_turn_min']:>8}  {o['task']}{flag}")

    over = [o for o in out if o["over_redline"]]
    if over:
        print(f"\n{len(over)}/{len(out)} 个任务超过 {a.redline:.0f} 分钟红线。复盘三问：①超时点 ②根因 ③下次怎么拆分并行。")


if __name__ == "__main__":
    main()
