#!/usr/bin/env python3
"""工作节奏看板：单文件 HTML，零 JS，数据来自 harness_scan 的 metrics + peers。"""
import argparse
import glob
import html
import json
import os
import sys
import webbrowser
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402

DEFAULT_OUT = os.path.expanduser(os.environ.get("KIT_DASHBOARD_OUT") or os.path.join(
    os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "task-metrics",
    "dashboard.html"))


def load_milestones():
    """提效节点：data/milestones.json 形如 [["2026-10-26","计划启动","#17191c"], ...]。"""
    try:
        with open(os.path.join(hs.DATA_DIR, "milestones.json"), encoding="utf-8") as f:
            return [tuple(x) for x in json.load(f)]
    except Exception:
        return []


# 提效计划节点：图上画竖线，明细表按此分「启动前 / 启动后」两段对比
MILESTONES = load_milestones()
PLAN_START = MILESTONES[0][0] if MILESTONES else ""
MILESTONE_HINT = "、".join(f"{d} {label}" for d, label, _ in MILESTONES)


def dedupe(recs):
    """同一 session:turn 只留一条；跨机镜像里出现的副本优先保留本机记录。"""
    me = hs.machine_name()
    out = {}
    for r in recs:
        k = r.get("id", "")
        old = out.get(k)
        if old is None or (old.get("host") != me and r.get("host") == me):
            out[k] = r
    return list(out.values())


def day_summary(day):
    recs = dedupe(hs.load_day(day))
    if not recs:
        return {"day": day, "empty": True}
    hot = [r for r in recs if r.get("line") in ("red", "yellow")]
    reds = [r for r in hot if r.get("line") == "red"]
    suspect = [r for r in recs if hs.is_suspect(r)]
    clean = [r for r in recs if not hs.is_suspect(r)]
    total = sum(hs.act(r) for r in clean)
    model = sum(r.get("model_seconds") or 0 for r in clean)
    tool = sum(r.get("tool_seconds") or 0 for r in clean)
    idle = sum(r.get("idle_seconds") or 0 for r in clean)
    fam = {}
    for r in clean:
        for k, v in (r.get("families") or {}).items():
            fam[k] = fam.get(k, 0) + v
    hosts = {}
    for r in clean:
        hosts[r.get("host", "?")] = hosts.get(r.get("host", "?"), 0) + hs.act(r)
    n = len(clean) or len(recs)
    return {
        "day": day,
        "tasks": len(recs),
        "hot": len(hot),
        "red": len(reds),
        "total": int(total),
        "model": int(model),
        "tool": int(tool),
        "idle": int(idle),
        "slow": sum(r.get("slow_tools") or 0 for r in recs),
        "avg_think": sum(r.get("thinking_chars") or 0 for r in clean) / n,
        "avg_reopen": sum(r.get("reopen_marks") or 0 for r in clean) / n,
        "blocked": sum(r.get("blocked_waits") or 0 for r in clean),
        "families": fam,
        "hosts": hosts,
        "hot_max": max((hs.act(r) for r in clean), default=0),
        "suspect_n": len(suspect),
        "suspect_seconds": sum(hs.act(r) for r in suspect),
        "suspect": [{"host": r.get("host"), "start": r.get("start"), "end": r.get("end"),
                     "seconds": hs.act(r)} for r in suspect],
    }


def h(sec):
    return f"{sec / 3600:.2f}h"


def m(sec):
    return f"{sec / 60:.0f}′"


def esc(s):
    return html.escape(str(s), quote=True)


# ---------- SVG 图表 ----------

def mark_lines(parts, marks, x_of, y_top, y_bottom):
    for idx, label, color in marks:
        x = x_of(idx)
        parts.append(f'<line x1="{x:.1f}" y1="{y_top - 6}" x2="{x:.1f}" y2="{y_bottom:.1f}" '
                     f'stroke="{color}" stroke-width="1.4" stroke-dasharray="4 3" opacity=".7"/>')
        parts.append(f'<text x="{x:.1f}" y="{y_top - 10}" text-anchor="middle" font-size="10.5" '
                     f'font-weight="700" fill="{color}">{esc(label)}</text>')


def stacked_bars(rows, series, colors, title, label_fmt=m, avg_key="avg", marks=None):
    """rows: [{'label','v':{series:value},'avg':float}]; 简单堆叠柱 + 虚线均线。"""
    W, H = 960, 300
    left, right, top, bottom = 52, 16, 28, 46
    iw, ih = W - left - right, H - top - bottom
    maxv = max([sum(r["v"].get(s, 0) for s in series) for r in rows]
               + [r.get(avg_key, 0) for r in rows] + [1])
    slot = iw / max(1, len(rows))
    bw = min(46, slot * 0.62)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">']
    # 网格
    for i in range(5):
        y = top + ih * i / 4
        val = maxv * (4 - i) / 4
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{W - right}" y2="{y:.1f}" stroke="#e2e5ea" stroke-width="1"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#5a636e">{label_fmt(val)}</text>')
    for i, r in enumerate(rows):
        x = left + slot * i + (slot - bw) / 2
        y = top + ih
        for s in series:
            v = r["v"].get(s, 0)
            hh = ih * v / maxv
            y -= hh
            if hh > 0.5:
                parts.append(f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw:.1f}" height="{hh:.1f}" fill="{colors[s]}" rx="2"/>')
        total = sum(r["v"].get(s, 0) for s in series)
        if total > 0:
            lbl = label_fmt(total)
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{y - 6:.1f}" text-anchor="middle" font-size="10.5" fill="#5a636e">{lbl}</text>')
        every = max(1, -(-len(rows) // 16))
        if i % every == 0 or i == len(rows) - 1:
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{H - bottom + 18}" text-anchor="middle" font-size="11" fill="#5a636e">{esc(r["label"])}</text>')
        if r.get("badge"):
            by = top + ih + 8
            parts.append(f'<text x="{x + bw / 2:.1f}" y="{by}" text-anchor="middle" font-size="10" fill="#c22929" font-weight="700">{esc(r["badge"])}</text>')
    if any(r.get(avg_key) for r in rows):
        pts = []
        for i, r in enumerate(rows):
            x = left + slot * i + slot / 2
            y = top + ih * (1 - r.get(avg_key, 0) / maxv)
            pts.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="#17191c" stroke-width="1.6" stroke-dasharray="5 4" opacity=".55"/>')
    if marks:
        mark_lines(parts, marks, lambda i: left + slot * i + slot / 2, top, top + ih)
    parts.append(f'<text x="{left}" y="16" font-size="12" fill="#17191c" font-weight="600">{esc(title)}</text>')
    parts.append("</svg>")
    return "".join(parts)


def line_chart(rows, keys, colors, title, unit="", W=960, H=240, marks=None):
    left, right, top, bottom = 52, 16, 28, 40
    iw, ih = W - left - right, H - top - bottom
    maxv = max([max([r.get(k, 0) for k in keys] + [0]) for r in rows] + [1])
    maxv = maxv * 1.15
    step = iw / max(1, len(rows) - 1)
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="{esc(title)}">']
    for i in range(4):
        y = top + ih * i / 3
        val = maxv * (3 - i) / 3
        parts.append(f'<line x1="{left}" y1="{y:.1f}" x2="{W - right}" y2="{y:.1f}" stroke="#e2e5ea"/>')
        parts.append(f'<text x="{left - 8}" y="{y + 4:.1f}" text-anchor="end" font-size="11" fill="#5a636e">{val:.0f}{unit}</text>')
    for k in keys:
        pts = []
        for i, r in enumerate(rows):
            x = left + step * i
            y = top + ih * (1 - r.get(k, 0) / maxv)
            pts.append(f"{x:.1f},{y:.1f}")
        parts.append(f'<polyline points="{" ".join(pts)}" fill="none" stroke="{colors[k]}" stroke-width="2.2" stroke-linejoin="round"/>')
        for pt in pts:
            x, y = pt.split(",")
            parts.append(f'<circle cx="{x}" cy="{y}" r="2.6" fill="{colors[k]}"/>')
    parts.append(f'<text x="{left}" y="16" font-size="12" fill="#17191c" font-weight="600">{esc(title)}</text>')
    every = max(1, -(-len(rows) // 8))
    for i, r in enumerate(rows):
        if i % every and i != len(rows) - 1:
            continue
        x = left + step * i
        parts.append(f'<text x="{x:.1f}" y="{H - bottom + 18}" text-anchor="middle" font-size="11" fill="#5a636e">{esc(r["label"])}</text>')
    if marks:
        mark_lines(parts, marks, lambda i: left + step * i, top, top + ih)
    parts.append("</svg>")
    return "".join(parts)


def hbars(items, total, color="#1f4feb"):
    if not items:
        return '<p class="muted small">无工具族数据</p>'
    out = ['<div class="hbars">']
    for k, v in items:
        pct = 100 * v / max(1, total)
        out.append(
            f'<div class="hb"><span class="hbl">{esc(k)}</span>'
            f'<span class="hbt"><span class="hbf" style="width:{pct:.1f}%;background:{color}"></span></span>'
            f'<span class="hbv">{m(v)}</span></div>'
        )
    out.append("</div>")
    return "".join(out)


# ---------- 页面 ----------

CSS = """
:root{--bg:#f5f6f8;--card:#fff;--ink:#17191c;--muted:#5a636e;--line:#e2e5ea;--accent:#1f4feb;--accent-weak:#eef2fe;--ok:#0d7a45;--ok-bg:#e7f5ed;--bad:#c22929;--bad-bg:#fdeceb;--warn:#9a5b00;--warn-bg:#fff3e2;--code-bg:#f2f4f7;--radius:12px;--radius-s:8px}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.7 -apple-system,BlinkMacSystemFont,"PingFang SC","Hiragino Sans GB","Microsoft YaHei",sans-serif}
main{max-width:1040px;margin:0 auto;padding:40px 22px 80px}
a{color:var(--accent);text-decoration:none}
a:hover{text-decoration:underline}
code{background:var(--code-bg);padding:1px 6px;border-radius:5px;font-family:ui-monospace,Menlo,monospace;font-size:.88em}
.eyebrow{color:var(--accent);font-weight:700;letter-spacing:.12em;font-size:12px;text-transform:uppercase}
h1{font-size:30px;line-height:1.3;margin:8px 0 6px;letter-spacing:-.01em}
.sub{color:var(--muted);margin:0 0 18px;font-size:13.5px}
.statbar{display:flex;flex-wrap:wrap;gap:10px;margin:0 0 20px}
.stat{flex:1 1 200px;background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:12px 16px}
.stat b{display:block;font-size:21px;line-height:1.35}
.stat span{color:var(--muted);font-size:12.5px}
.stat.ok{border-left:4px solid var(--ok)}.stat.bad{border-left:4px solid var(--bad)}.stat.warn{border-left:4px solid var(--warn)}
nav{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:10px 16px;margin-bottom:20px;font-size:13.5px;display:flex;flex-wrap:wrap;gap:4px 14px}
section{background:var(--card);border:1px solid var(--line);border-radius:var(--radius);padding:24px 28px;margin-top:16px;scroll-margin-top:16px}
h2{font-size:19px;margin:0 0 12px;display:flex;align-items:baseline;gap:10px}
h2 .no{color:var(--accent);font-size:13px;font-weight:800;font-family:ui-monospace,Menlo,monospace}
h3{font-size:14.5px;margin:18px 0 6px}
p{margin:8px 0}
ul,ol{margin:8px 0;padding-left:22px}
li{margin:4px 0}
.muted{color:var(--muted)}.small{font-size:13px}
.t{display:inline-block;padding:1px 9px;border-radius:99px;font-size:12.5px;font-weight:600;white-space:nowrap}
.t.ok{background:var(--ok-bg);color:var(--ok)}.t.bad{background:var(--bad-bg);color:var(--bad)}.t.warn{background:var(--warn-bg);color:var(--warn)}
.tw{overflow-x:auto;margin:12px 0}
table{border-collapse:collapse;width:100%;font-size:13.5px}
th{text-align:left;font-size:11.5px;letter-spacing:.06em;color:var(--muted);text-transform:uppercase;border-bottom:2px solid var(--line);padding:8px 10px;white-space:nowrap}
td{border-bottom:1px solid var(--line);padding:8px 10px;vertical-align:top;white-space:nowrap}
tr:last-child td{border-bottom:none}
td.wrap{white-space:normal;min-width:180px}
.chart{margin:6px 0 4px}
.chart svg{width:100%;height:auto;display:block}
.legend{display:flex;flex-wrap:wrap;gap:14px;font-size:12.5px;color:var(--muted);margin:2px 0 6px}
.legend i{display:inline-block;width:10px;height:10px;border-radius:2px;margin-right:5px;vertical-align:-1px}
.hbars{display:flex;flex-direction:column;gap:7px;margin:10px 0}
.hb{display:flex;align-items:center;gap:10px;font-size:13px}
.hbl{width:82px;color:var(--muted);text-align:right;flex:none}
.hbt{flex:1;background:var(--code-bg);border-radius:4px;height:10px;overflow:hidden}
.hbf{display:block;height:100%;border-radius:4px}
.hbv{width:64px;flex:none;font-variant-numeric:tabular-nums}
.split{display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:12px;margin:12px 0}
.two{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:14px;margin:6px 0}
.box{border:1px solid var(--line);border-radius:var(--radius-s);padding:12px 14px}
.box h4{margin:0 0 4px;font-size:13.5px}
.box p{margin:2px 0;font-size:13px;color:var(--muted)}
.box b{font-size:18px}
details{margin:14px 0 6px;border-top:1px dashed var(--line);padding-top:10px}
summary{cursor:pointer;font-size:13px;color:var(--accent);font-weight:600}
@media(max-width:640px){main{padding:22px 12px 60px}section{padding:18px 14px}h1{font-size:24px}}
"""


def month_days(month):
    y, mth = map(int, month.split("-"))
    start = datetime(y, mth, 1).date()
    nxt = datetime(y + (mth == 12), 1 if mth == 12 else mth + 1, 1).date()
    last = datetime.now().date()
    end = min(last, nxt - timedelta(days=1))
    if end < start:
        return []
    return [(start + timedelta(days=i)).isoformat() for i in range((end - start).days + 1)]


def prev_month(month):
    y, mth = map(int, month.split("-"))
    return f"{y - 1}-12" if mth == 1 else f"{y}-{mth - 1:02d}"


def build(days=14, month=None):
    end = datetime.now().date()
    if month:
        day_list = month_days(month) or [(end - timedelta(days=i)).isoformat()
                                         for i in range(days - 1, -1, -1)]
        window_txt = f"{month} 月度 · {len(day_list)} 天（截至 {day_list[-1][5:]}）"
    else:
        day_list = [(end - timedelta(days=i)).isoformat() for i in range(days - 1, -1, -1)]
        window_txt = f"近 {days} 天"
    rows = [day_summary(d) for d in day_list]
    filled = [r for r in rows if not r.get("empty")]
    scope_txt = f"本窗口 {len(filled)} 个有数据天" if month else f"近 {days} 天"
    cfg = hs.load_config()

    totals = {k: sum(r.get(k, 0) for r in filled) for k in ("total", "model", "tool", "idle", "tasks", "hot", "red", "slow")}
    fam = {}
    for r in filled:
        for k, v in r["families"].items():
            fam[k] = fam.get(k, 0) + v
    fam_items = sorted(fam.items(), key=lambda kv: -kv[1])
    zh = {"test": "测试", "build": "构建", "deploy": "部署", "install": "安装", "network": "网络",
          "search": "检索", "read": "读取", "edit": "编辑", "browser": "浏览器",
          "other-shell": "Shell 其他", "other": "其他工具"}
    fam_items = [(zh.get(k, k), v) for k, v in fam_items]

    marks = []
    for d, label, color in MILESTONES:
        for i, r in enumerate(filled):
            if r["day"] >= d:
                marks.append((i, label, color))
                break

    today = rows[-1]
    last7 = [r for r in filled[-7:]]
    avg7 = sum(r["total"] for r in last7) / max(1, len(last7))
    model_share = 100 * totals["model"] / max(1, totals["model"] + totals["tool"])

    hooks = hs.hook_registry()
    hook_ok = sum(1 for _, _, ok in hooks if ok)
    events = hs.load_events(datetime.now().timestamp() - 86400)
    scan_n = sum(1 for e in events if e.get("kind") == "scan")
    post_n = sum(1 for e in events if e.get("kind") == "post-tool")
    peers = [(os.path.basename(d), read_text(os.path.join(d, ".last_pull")))
             for d in sorted(glob.glob(os.path.join(hs.PEERS_DIR, "*"))) if os.path.isdir(d)]

    # 图表 1：每日活跃时长（模型/工具堆叠）+ 7 日均线
    roll = {}
    for i in range(len(rows)):
        win = rows[max(0, i - 6):i + 1]
        roll[rows[i]["day"]] = sum(r.get("total", 0) for r in win) / len(win)
    bar_rows = [{
        "label": r["day"][5:], "v": {"model": r.get("model", 0), "tool": r.get("tool", 0)},
        "avg": roll.get(r["day"], 0),
        "badge": f"红{r['red']}" if r.get("red") else (f"黄{r['hot']}" if r.get("hot") else ""),
    } for r in filled]
    chart1 = stacked_bars(bar_rows, ["model", "tool"],
                          {"model": "#1f4feb", "tool": "#e08a2e"},
                          "每日活跃时长（模型 / 工具，分钟）",
                          label_fmt=lambda v: f"{v / 60:.0f}′", marks=marks)

    # 图表 2：每日黄线/红线任务数
    hot_rows = [{
        "label": r["day"][5:],
        "v": {"yellow": max(0, r.get("hot", 0) - r.get("red", 0)), "red": r.get("red", 0)},
    } for r in filled]
    chart2 = stacked_bars(hot_rows, ["yellow", "red"],
                          {"yellow": "#e6a23c", "red": "#c22929"},
                          "每日超线任务数（黄 / 红）", label_fmt=lambda v: f"{v:.0f}", marks=marks)

    # 图表 3：过程指标趋势（双图各自标尺）
    trend_rows = [{"label": r["day"][5:], "think": r["avg_think"] / 1000,
                   "reopen": r["avg_reopen"]} for r in filled]
    chart3a = line_chart(trend_rows, ["think"], {"think": "#1f4feb"},
                         "每块平均思考字数（k）", unit="k", W=470, H=230, marks=marks)
    chart3b = line_chart(trend_rows, ["reopen"], {"reopen": "#c22929"},
                         "每块平均「重开」词频", unit="", W=470, H=230, marks=marks)

    # 明细表
    trs = []
    for r in reversed(filled):
        r1 = r["day"] == end.isoformat()
        tag = ' <span class="t warn">今日</span>' if r1 else ""
        warn = ""
        if r.get("suspect_n"):
            warn = f" <span class='t warn' title='跨天零空闲的长块已从统计中剔除'>⚠ +{m(r['suspect_seconds'])}</span>"
        trs.append(
            f"<tr><td>{esc(r['day'])}{tag}</td><td>{r['tasks']}</td>"
            f"<td>{r['hot']} <span class='muted'>(红 {r['red']})</span></td>"
            f"<td>{m(r['total'])}{warn}</td><td>{100 * r['model'] // max(1, r['total'])}% / {100 * r['tool'] // max(1, r['total'])}%</td>"
            f"<td>{r['avg_think'] / 1000:.1f}k</td><td>{r['avg_reopen']:.1f}</td>"
            f"<td>{r['blocked']}</td><td>{r['slow']}</td>"
            f"<td class='wrap'>{esc(host_line(r['hosts']))}</td></tr>"
        )

    def stage(recs):
        n = len(recs)
        if not n:
            return None
        tasks = sum(r["tasks"] for r in recs)
        total = sum(r["total"] for r in recs)
        model = sum(r["model"] for r in recs)
        tool = sum(r["tool"] for r in recs)
        return {
            "days": n, "per_day": total / n, "tasks_day": tasks / n,
            "hot": sum(r["hot"] for r in recs), "red": sum(r["red"] for r in recs),
            "hot_rate": 100 * sum(r["hot"] for r in recs) / max(1, tasks),
            "model_share": 100 * model / max(1, model + tool),
            "think": sum(r["avg_think"] * r["tasks"] for r in recs) / max(1, tasks) / 1000,
            "reopen": sum(r["avg_reopen"] * r["tasks"] for r in recs) / max(1, tasks),
            "blocked": sum(r["blocked"] for r in recs) / max(1, tasks),
            "from": recs[0]["day"][5:], "to": recs[-1]["day"][5:],
        }

    before = stage([r for r in filled if PLAN_START and r["day"] < PLAN_START])
    after = stage([r for r in filled if not PLAN_START or r["day"] >= PLAN_START])

    def dcell(cur, prev, lower_better=True, unit="%", digits=0):
        if not prev:
            return "<span class='muted'>—</span>"
        diff = (cur - prev) if unit == "pt" else (cur - prev) / prev * 100
        good = diff < 0 if lower_better else diff > 0
        cls = "ok" if good and abs(diff) >= 1 else ("bad" if not good and abs(diff) >= 1 else "warn")
        return f"<span class='t {cls}'>{diff:+.{digits}f}{unit}</span>"

    if before and after:
        def row(name, b, a, d=""):
            return (f"<tr><td>{name}</td><td>{b}</td>"
                    f"<td>{a}</td><td>{d}</td></tr>")
        stage_rows = [
            row("有数据天数", f"{before['days']} 天", f"{after['days']} 天", ""),
            row("日均活跃时长", m(before["per_day"]), m(after["per_day"]),
                dcell(after["per_day"], before["per_day"])),
            row("日均任务块", f"{before['tasks_day']:.0f}", f"{after['tasks_day']:.0f}",
                dcell(after["tasks_day"], before["tasks_day"])),
            row("超线任务（黄含红）", f"{before['hot']}（红 {before['red']}）",
                f"{after['hot']}（红 {after['red']}）", dcell(after["hot"], before["hot"])),
            row("超线率", f"{before['hot_rate']:.0f}%", f"{after['hot_rate']:.0f}%",
                dcell(after["hot_rate"], before["hot_rate"], unit="pt")),
            row("模型占比", f"{before['model_share']:.0f}%", f"{after['model_share']:.0f}%", ""),
            row("均思考（k 字/块）", f"{before['think']:.1f}", f"{after['think']:.1f}",
                dcell(after["think"], before["think"])),
            row("均重开（次/块）", f"{before['reopen']:.1f}", f"{after['reopen']:.1f}",
                dcell(after["reopen"], before["reopen"])),
            row("阻塞等待（次/块）", f"{before['blocked']:.2f}", f"{after['blocked']:.2f}",
                dcell(after["blocked"], before["blocked"])),
        ]
        stage_html = (
            "<div class='tw'><table><thead><tr><th>指标</th>"
            f"<th>启动前 {before['from']}–{before['to']}</th>"
            f"<th>启动后 {after['from']}–{after['to']}</th><th>变化</th></tr></thead>"
            f"<tbody>{''.join(stage_rows)}</tbody></table></div>"
            f"<p class='small muted'>「启动后」只有 {after['days']} 天，看方向不看幅度；"
            "绿 = 变好，红 = 变差，琥珀 = 变动不足 1%。"
            "<br>注意口径：启动前含旧版扫描器产出的天数（重复计块、跨天假账已剔除但仍偏大），"
            "所以「日均活跃」「超线任务数」只能看方向；逐块均值（均思考 / 均重开 / 超线率）不受影响，可信。</p>")
    else:
        stage_html = ("<p class='small muted'>未配置提效节点（data/milestones.json），"
                      "配置后可看启动前后对比。</p>")

    mom_html = ""
    if month:
        prev = prev_month(month)
        prov = [day_summary(d) for d in month_days(prev)] if month_days(prev) else []
        pf = [r for r in prov if not r.get("empty")]
        cdays = max(1, len(filled))
        if pf:
            ptot = sum(r["total"] for r in pf)
            phot = sum(r["hot"] for r in pf)
            pred = sum(r["red"] for r in pf)
            pdays = max(1, len(pf))

            def delta(a, b):
                return f"{100 * (a - b) / b:+.0f}%" if b else "—"

            mom_html = (
                f"<h3>月度对比（{prev} → {month}）</h3><div class='split'>"
                f"<div class='box'><h4>总活跃</h4><b>{m(ptot)} → {m(totals['total'])}</b><p>{delta(totals['total'], ptot)}</p></div>"
                f"<div class='box'><h4>日均（有数据天）</h4><b>{m(ptot / pdays)} → {m(totals['total'] / cdays)}</b>"
                f"<p>{delta(totals['total'] / cdays, ptot / pdays)}</p></div>"
                f"<div class='box'><h4>超线任务</h4><b>{phot} → {totals['hot']}</b><p>红线 {pred} → {totals['red']}</p></div>"
                f"</div>")
        else:
            mom_html = (
                f"<h3>本月累计（{month}）</h3><div class='split'>"
                f"<div class='box'><h4>总活跃</h4><b>{m(totals['total'])}</b><p>{cdays} 个有数据天</p></div>"
                f"<div class='box'><h4>日均</h4><b>{m(totals['total'] / cdays)}</b><p>模型 {100 * totals['model'] // max(1, totals['model'] + totals['tool'])}% / 工具 {100 * totals['tool'] // max(1, totals['model'] + totals['tool'])}%</p></div>"
                f"<div class='box'><h4>超线任务</h4><b>{totals['hot']}</b><p>红线 {totals['red']} · 慢调用 {totals['slow']} 次</p></div>"
                f"</div><p class='muted' style='font-size:12.5px'>上月（{prev}）无本地数据，暂不做月度对比。</p>")
    peer_txt = "；".join(f"{p} 数据 {ts}" for p, ts in peers) or "无对端"
    suspects = [(r["day"], s) for r in filled for s in r.get("suspect", [])]
    suspect_html = ""
    if suspects:
        items = "；".join(
            f"{esc(day)} [{esc(s['host'])}] {esc(s['start'][5:16])}→{esc(s['end'][5:16])} {m(s['seconds'])}"
            for day, s in suspects
        )
        suspect_html = (f"<li>⚠ 异常块 {len(suspects)} 个（跨天零空闲、单块 ≥8h，视为中断后恢复未记空档）："
                        f"{items}。扫描器已修复，等对端下次回扫后自动替换。</li>")

    doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="UTF-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>工作节奏看板 · {end.isoformat()}</title>
<style>{CSS}</style></head><body><main>
<header>
  <div class="eyebrow">Harness · 工作节奏</div>
  <h1>工作节奏看板</h1>
  <p class="sub">{esc(window_txt)} · 生成于 {datetime.now().strftime('%m-%d %H:%M')} · 口径 active（墙上时间 − 空闲间隔）· 黄线 {cfg['yellow_line_minutes']}′ / 红线 {cfg['red_line_minutes']}′ · 合并 {esc(hs.machine_name())} 与对端</p>
  <div class="statbar">
    <div class="stat ok"><b>{m(today.get('total', 0))}</b><span>今日活跃时长（{today.get('tasks', 0)} 个任务块）</span></div>
    <div class="stat {'bad' if today.get('red') else 'warn'}"><b>{today.get('hot', 0)} / {today.get('red', 0)}</b><span>今日超线任务（黄 / 红）</span></div>
    <div class="stat"><b>{m(avg7)}</b><span>近 7 日日均活跃时长</span></div>
    <div class="stat {'ok' if hook_ok == len(hooks) and hooks else 'bad'}"><b>{hook_ok}/{len(hooks)}</b><span>钩子注册且脚本就位 · 近24h scan {scan_n} 次 / post-tool {post_n} 次</span></div>
  </div>
  <nav>
    <a href="#s1">§1 每日时长</a><a href="#s2">§2 超线任务</a><a href="#s3">§3 过程指标</a>
    <a href="#s4">§4 时间构成</a><a href="#s5">§5 改进前后</a><a href="#s6">§6 数据健康</a>
  </nav>
</header>

<section id="s1"><h2><span class="no">§1</span>每日活跃时长</h2>
  <div class="legend"><span><i style="background:#1f4feb"></i>模型生成</span><span><i style="background:#e08a2e"></i>工具执行</span><span><i style="background:#17191c"></i>7 日均线（虚线）</span><span>｜竖直虚线 = 提效计划节点</span><span>柱顶数字 = 当日总时长（′）· 柱下标记 = 超线任务（红 n / 黄 n）</span></div>
  <div class="chart">{chart1}</div>
  <p class="small muted">读法：堆叠中模型占比高 = 设计反复/思考多；工具占比高 = 等待与执行重。本窗口模型 {m(totals['model'])} / 工具 {m(totals['tool'])}，模型占 {model_share:.0f}%。口径是多机、并行会话按任务块求和（不是墙上时钟），单日超过 1440′ 属正常。</p>
</section>

<section id="s2"><h2><span class="no">§2</span>超线任务</h2>
  <div class="legend"><span><i style="background:#e6a23c"></i>黄线 ≥{cfg['yellow_line_minutes']}′</span><span><i style="background:#c22929"></i>红线 ≥{cfg['red_line_minutes']}′</span></div>
  <div class="chart">{chart2}</div>
  <p class="small muted">{scope_txt}共 {totals['hot']} 个超线任务（红 {totals['red']}），占全部 {totals['tasks']} 个任务块的 {100 * totals['hot'] // max(1, totals['tasks'])}%。</p>
</section>

<section id="s3"><h2><span class="no">§3</span>过程指标</h2>
  <div class="two"><div class="chart">{chart3a}</div><div class="chart">{chart3b}</div></div>
  <p class="small muted">均思考与均重开下降 = 边写边改在收敛；若同时工具占比上升，说明时间从设计挪到了执行。</p>
</section>

<section id="s4"><h2><span class="no">§4</span>时间构成与慢调用</h2>
  <div class="split">
    <div class="box"><h4>模型 / 工具</h4><b>{m(totals['model'])} / {m(totals['tool'])}</b><p>占比 {100 - model_share:.0f}% 工具 / {model_share:.0f}% 模型</p></div>
    <div class="box"><h4>被剔除的空闲</h4><b>{m(totals['idle'])}</b><p>单段 &gt;15′ 的空档 + 超 60′ 的单次调用</p></div>
    <div class="box"><h4>慢调用</h4><b>{totals['slow']} 次</b><p>单次工具执行 ≥{cfg['slow_tool_seconds']}″</p></div>
  </div>
  {mom_html}
  <h3>工具族累计</h3>
  {hbars(fam_items, max(1, sum(v for _, v in fam_items)))}
</section>

<section id="s5"><h2><span class="no">§5</span>改进前后对比</h2>
  <p class="small muted">分界点 = 提效计划首个节点（{PLAN_START or '未配置'}）。§1–§3 图上的竖虚线即节点标记（{MILESTONE_HINT or '未配置'}）。</p>
  {stage_html}
  <details><summary>展开每日明细（原始表）</summary>
    <div class="tw"><table>
      <thead><tr><th>日期</th><th>任务块</th><th>超线</th><th>活跃</th><th>模型/工具</th><th>均思考</th><th>均重开</th><th>阻塞</th><th>慢调用</th><th>机器分布</th></tr></thead>
      <tbody>{''.join(trs)}</tbody>
    </table></div>
  </details>
  <p class="small muted">机器分布按活跃时长；缺日期的天无会话数据（非 0 工作）。带 ⚠ 的日期含「跨天且零空闲」的长块，已从 §1/§4 统计中剔除，明细见 §6。</p>
</section>

<section id="s6"><h2><span class="no">§6</span>数据健康</h2>
  <ul class="small">
    <li>钩子：{''.join(f"<span class='t {'ok' if ok else 'bad'}'>{esc(ev)}→{esc(mode)}</span> " for ev, mode, ok in hooks) or '<span class="t bad">未注册</span>'}</li>
    <li>近 24h：scan {scan_n} 次 · post-tool {post_n} 次 · inject {sum(1 for e in events if e.get('kind') == 'inject')} 次</li>
    <li>对端：{esc(peer_txt)}（看板合并的是最近一次 pull 的镜像；对端指标按它自己最后一次回扫的脚本版本生成，版本落后时数值偏大）</li>
    <li>刷新：<code>python3 ~/.qoder/task-metrics/bin/harness_dashboard.py</code>（拉对端加 <code>--pull</code>）</li>
    {suspect_html}
  </ul>
</section>

<p class="small muted">数据源：~/.qoder/task-metrics/data/metrics + peers · 由 harness_dashboard.py 生成 · 页面零 JS 零依赖</p>
</main></body></html>"""
    return doc


def read_text(path):
    try:
        return open(path, encoding="utf-8").read().strip()
    except Exception:
        return "-"


def host_line(hosts):
    if not hosts:
        return "-"
    parts = sorted(hosts.items(), key=lambda kv: -kv[1])
    if len(parts) == 1:
        return f"{parts[0][0]} {m(parts[0][1])}"
    return " / ".join(f"{k} {m(v)}" for k, v in parts)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=14)
    ap.add_argument("--month", help="月度视图，如 2026-10；默认取近 --days 天")
    ap.add_argument("--out", default=DEFAULT_OUT)
    ap.add_argument("--pull", action="store_true", help="先从对端拉一次数据")
    ap.add_argument("--open", action="store_true", help="生成后用浏览器打开")
    a = ap.parse_args()
    if a.pull:
        peer, ssh = hs.peer_defaults()
        hs.pull(peer, ssh or f"ori@{peer}", deploy=True)
    doc = build(a.days, a.month)
    out = os.path.abspath(os.path.expanduser(a.out))
    os.makedirs(os.path.dirname(out), exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        f.write(doc)
    print(out)
    if a.open:
        webbrowser.open("file://" + os.path.abspath(a.out))


if __name__ == "__main__":
    main()
