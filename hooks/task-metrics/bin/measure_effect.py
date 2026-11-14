#!/usr/bin/env python3
"""措施效果度量：把「导航调用多 → 往返多 → 时间多」这条链拆成可测的回合级指标。

口径（前后必须用同一份代码算，否则口径漂移会盖过真实变化）：
  回合    = 一条人类消息 → 下一条人类消息（排除 harness 自身注入）
  工作回合 = 回合内工具调用 ≥ 5（对话式短回合不参与中位数）
  nav     = 定位类调用：Read/Grep/Glob 类 + jdtls 桥 + 语义检索 + 只读 Bash
  只读 Bash = 每段首 token 都在只读集合、且无写操作/落盘重定向（见 hs.is_readonly_bash）
  往返间隔 = 同回合内相邻两次工具调用的时间差，只取 0 < gap ≤ 120s（>120s 是模型思考，不算往返）

用法：
  python3 bin/measure_effect.py --mark      # 标记措施生效起点（只写一次）
  python3 bin/measure_effect.py             # 打印当前对比
  python3 bin/measure_effect.py --append    # 追加到 data/measures/ledger.md
  python3 bin/measure_effect.py --json
"""
import argparse
import glob
import json
import os
import statistics
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402

MEAS_DIR = os.path.join(hs.DATA_DIR, "measures")
BASELINE_JSON = os.path.join(MEAS_DIR, "effect_baseline.json")
START_FILE = os.path.join(MEAS_DIR, "effect_start.txt")
LEDGER = os.path.join(MEAS_DIR, "ledger.md")

# 基线窗口：改进方案文档用的同一个窗口（本地时区）
BASE_FROM = datetime(2026, 10, 28, 17, 0)
BASE_TO = datetime(2026, 10, 29, 17, 0)
WORK_TURN_MIN_CALLS = 5
MAX_GAP = 120.0
# 判定门槛：少于这个量级，p50 的波动会超过 15%，结论不可信
MIN_DAYS = 3
MIN_WORK_TURNS = 30


def scan_period(t0, t1):
    """扫窗口内转录，返回回合级统计（零 token，纯本地）。"""
    paths = [f for f in glob.glob(os.path.expanduser("~/.qoder/projects/*/*.jsonl"))
             if os.path.getmtime(f) >= t0]
    turns, cur = [], None
    bash_n = ro_n = 0
    for p in paths:
        try:
            fh = open(p, errors="replace")
        except Exception:
            continue
        with fh:
            for line in fh:
                try:
                    o = json.loads(line)
                except Exception:
                    continue
                ts = hs.ts_epoch(o.get("timestamp") or "")
                if not ts or ts < t0 or ts > t1:
                    continue
                txt = hs.is_human_text(o)
                if txt:
                    if not hs.label_is_injection(txt):
                        cur = {"nav": 0, "sws": 0, "total": 0, "ts": []}
                        turns.append(cur)
                    continue
                msg = o.get("message") or {}
                content = msg.get("content")
                if msg.get("role") != "assistant" or not isinstance(content, list):
                    continue
                for x in content:
                    if not isinstance(x, dict) or x.get("type") != "tool_use":
                        continue
                    if cur is None:
                        cur = {"nav": 0, "sws": 0, "total": 0, "ts": []}
                        turns.append(cur)
                    name = x.get("name") or ""
                    inp = x.get("input") if isinstance(x.get("input"), dict) else {}
                    k = hs.tool_kind(name)
                    cur["total"] += 1
                    cur["ts"].append(ts)
                    if k in ("scan", "lsp", "sws"):
                        cur["nav"] += 1
                    if k == "sws":
                        cur["sws"] += 1
                    if name in hs.BASH_TOOLS:
                        bash_n += 1
                        if hs.is_readonly_bash(str(inp.get("command") or "")):
                            ro_n += 1
                            cur["nav"] += 1
    work = [t for t in turns if t["total"] >= WORK_TURN_MIN_CALLS]
    gaps = []
    for t in work:
        s = sorted(t["ts"])
        gaps += [b - a for a, b in zip(s, s[1:]) if 0 < b - a <= MAX_GAP]
    navs = [t["nav"] for t in work] or [0]
    totals = [t["total"] for t in work] or [0]
    return {
        "t0": t0, "t1": t1,
        "days": max((t1 - t0) / 86400.0, 1e-6),
        "turns": len(turns), "work_turns": len(work),
        "nav_p50": statistics.median(navs),
        "nav_p75": statistics.quantiles(navs, n=4)[2] if len(navs) > 3 else max(navs),
        "nav_p90": statistics.quantiles(navs, n=10)[8] if len(navs) > 9 else max(navs),
        "rt_p50": statistics.median(totals),
        "gap_p50": statistics.median(gaps) if gaps else 0.0,
        "gap_p25": statistics.quantiles(gaps, n=4)[0] if len(gaps) > 3 else (min(gaps) if gaps else 0.0),
    }


def load_baseline():
    try:
        return json.load(open(BASELINE_JSON, encoding="utf-8"))
    except Exception:
        return None


def save_baseline(b):
    os.makedirs(MEAS_DIR, exist_ok=True)
    with open(BASELINE_JSON, "w", encoding="utf-8") as f:
        json.dump(b, f, ensure_ascii=False, indent=2)


def start_epoch():
    try:
        return float(open(START_FILE, encoding="utf-8").read().strip())
    except Exception:
        return 0.0


def mark():
    os.makedirs(MEAS_DIR, exist_ok=True)
    if start_epoch():
        print(f"起点已存在：{datetime.fromtimestamp(start_epoch()):%Y-%m-%d %H:%M}")
        return
    b = load_baseline()
    if not b:
        b = scan_period(BASE_FROM.timestamp(), BASE_TO.timestamp())
        save_baseline(b)
    with open(START_FILE, "w", encoding="utf-8") as f:
        f.write(str(int(datetime.now().timestamp())))
    print(f"基线已固化并标记起点 {datetime.now():%Y-%m-%d %H:%M}"
          f"（基线 {b['work_turns']} 个工作回合）")


def measure():
    b = load_baseline()
    if not b:
        b = scan_period(BASE_FROM.timestamp(), BASE_TO.timestamp())
        save_baseline(b)
    st = start_epoch()
    now = datetime.now().timestamp()
    if not st:
        return {"ready": False, "reason": "未标记生效起点，先跑 --mark", "baseline": b}
    a = scan_period(st, now)
    d_nav = b["nav_p50"] - a["nav_p50"]
    turns_per_day = a["work_turns"] / max(a["days"], 0.5)   # 起点刚标记时别除出天文数字
    saved_calls = max(0.0, d_nav) * turns_per_day
    saved_min = saved_calls * a["gap_p50"] / 60.0
    # 三重保守：导航差取下界（基线 p50 - 当前 p75）、间隔取下四分位
    lower_calls = max(0.0, b["nav_p50"] - a["nav_p75"]) * turns_per_day
    lower_min = lower_calls * a["gap_p25"] / 60.0
    ready = a["days"] >= MIN_DAYS and a["work_turns"] >= MIN_WORK_TURNS
    if not ready:
        verdict = "样本不足：仅记趋势，不出判定"
    elif a["nav_p50"] <= b["nav_p50"] * 0.85:
        verdict = "有效：导航调用中位数下降 ≥15%"
    elif a["nav_p50"] <= b["nav_p50"] * 1.10:
        verdict = "持平：变化在 ±10~15% 内，证据不足以说有效"
    else:
        verdict = "异常：导航调用反而上升，按反作用处理（回看适用条件）"
    return {"ready": ready, "verdict": verdict, "baseline": b, "after": a,
            "turns_per_day": turns_per_day, "saved_calls_per_day": saved_calls,
            "saved_min_per_day": saved_min, "lower_min_per_day": lower_min,
            "elapsed_days": a["days"]}


def fmt(r):
    if not r.get("ready") and "after" not in r:
        return r.get("reason", "")
    b, a = r["baseline"], r["after"]
    lines = [
        f"【效果度量】基线 {datetime.fromtimestamp(b['t0']):%m-%d %H:%M}"
        f"→{datetime.fromtimestamp(b['t1']):%m-%d %H:%M}"
        f" vs 生效后 {datetime.fromtimestamp(a['t0']):%m-%d %H:%M}"
        f"→{datetime.fromtimestamp(a['t1']):%m-%d %H:%M}"
        f"（{r['elapsed_days']:.1f} 天）",
        f"  工作回合      {b['work_turns']:>4} → {a['work_turns']:>4}",
        f"  nav/工作回合  p50 {b['nav_p50']:.1f} → {a['nav_p50']:.1f}"
        f" | p75 {b['nav_p75']:.1f} → {a['nav_p75']:.1f}"
        f" | p90 {b['nav_p90']:.1f} → {a['nav_p90']:.1f}",
        f"  往返次数/回合 p50 {b['rt_p50']:.1f} → {a['rt_p50']:.1f}",
        f"  往返间隔      p50 {b['gap_p50']:.1f}s → {a['gap_p50']:.1f}s"
        f" | p25 {a['gap_p25']:.1f}s",
    ]
    if r["ready"]:
        lines.append(
            f"  估算节省      {r['saved_min_per_day']:.1f} 分钟/天"
            f"（{r['saved_calls_per_day']:.1f} 次/天 × {a['gap_p50']:.1f}s）"
            f"｜下限 {r['lower_min_per_day']:.1f} 分钟/天")
    else:
        lines.append("  估算节省      样本未达标，不出数（避免拿噪声当结论）")
    lines.append(f"  判定          {r['verdict']}")
    return "\n".join(lines)


def append_ledger(r):
    os.makedirs(MEAS_DIR, exist_ok=True)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(f"\n## {datetime.now():%Y-%m-%d %H:%M} 措施效果度量\n\n```\n{fmt(r)}\n```\n")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--mark", action="store_true")
    ap.add_argument("--append", action="store_true")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    if a.mark:
        mark()
        return
    r = measure()
    if a.json:
        print(json.dumps(r, ensure_ascii=False, indent=2, default=str))
    else:
        print(fmt(r))
    if a.append:
        append_ledger(r)


if __name__ == "__main__":
    main()
