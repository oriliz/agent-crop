#!/usr/bin/env python3
"""按时间窗口生成逐块明细单页（零 JS）：图表为主（时间轴/每小时/会话/最长块），表格折叠备查。"""
import argparse
import html
import os
import sys
from datetime import datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402


def esc(s):
    return html.escape(str(s if s is not None else ""), quote=True)


def load_window(since, until):
    d0 = datetime.strptime(since, "%Y-%m-%dT%H:%M")
    d1 = datetime.strptime(until, "%Y-%m-%dT%H:%M")
    days = set()
    d = d0
    while d.date() <= d1.date():
        days.add(d.strftime("%Y-%m-%d"))
        d += timedelta(days=1)
    recs = []
    for day in sorted(days):
        for r in hs.load_day(day):
            s = r.get("start") or ""
            if since <= s < until:
                recs.append(r)
    me = hs.machine_name()
    out = {}
    for r in recs:
        k = r.get("id", "")
        old = out.get(k)
        if old is None or (old.get("host") != me and r.get("host") == me):
            out[k] = r
    return sorted(out.values(), key=lambda r: r.get("start") or "")


def mmss(t):
    return (t or "")[11:16]


def minutes(sec):
    return f"{sec / 60:.1f}"


def fill(sec):
    if sec >= 1800:
        return "#b42318"
    return "#c98a1b" if sec >= 600 else "#3f6bd8"


def gantt(recs, since, until):
    recs = [r for r in recs if hs.act(r) >= 30]
    t0 = datetime.strptime(since, "%Y-%m-%dT%H:%M")
    t1 = datetime.strptime(until, "%Y-%m-%dT%H:%M")
    span = (t1 - t0).total_seconds() / 60
    W, left = 1120, 104
    iw = W - left - 18
    lane_order = []
    for r in recs:
        h = r.get("host", "?")
        if h not in lane_order:
            lane_order.append(h)
    lane_order.sort(key=lambda h: -sum(hs.act(r) for r in recs if r.get("host") == h))

    # 同一台机器上并行的块拆成子行，避免互相盖住
    layout = {}
    for h in lane_order:
        rows = []
        for r in [x for x in recs if x.get("host") == h]:
            try:
                s0 = datetime.fromisoformat(r["start"])
                e0 = datetime.fromisoformat(r["end"]) if r.get("end") else s0 + timedelta(seconds=hs.act(r))
            except Exception:
                continue
            for i, last in enumerate(rows):
                if last <= s0:
                    rows[i] = e0
                    break
            else:
                rows.append(e0)
                i = len(rows) - 1
            layout.setdefault(h, []).append((r, i, s0, e0))
        layout.setdefault(h, [])
        layout[h] = (layout.get(h, []), max(len(rows), 1))

    rowpitch, barh = 22, 16
    top = 26
    y_of = {}
    y = top
    for h in lane_order:
        _, nrows = layout[h]
        tall = max(nrows * rowpitch + 14, 42)
        y_of[h] = (y, tall)
        y += tall
    H = y + 26

    def x(dt):
        return left + (dt - t0).total_seconds() / 60 / span * iw

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="时间轴">']
    t = t0
    while t <= t1:
        xx = x(t)
        parts.append(f'<line x1="{xx:.1f}" y1="{top - 6}" x2="{xx:.1f}" y2="{y}" stroke="#e8e7e2" stroke-width="1"/>')
        if t.hour % 2 == 0:
            lab = t.strftime("%m-%d 00:00") if t.hour == 0 else t.strftime("%H:%M")
            parts.append(f'<text x="{xx:.1f}" y="{y + 16}" text-anchor="middle" '
                         f'font-size="11" fill="#6b7076">{lab}</text>')
        t += timedelta(hours=1)

    for h in lane_order:
        yy, tall = y_of[h]
        blocks, nrows = layout[h]
        sec = sum(hs.act(r) for r, _, _, _ in blocks)
        parts.append(f'<line x1="{left}" y1="{yy + tall - 6}" x2="{left + iw}" y2="{yy + tall - 6}" stroke="#f1f0eb"/>')
        parts.append(f'<text x="{left - 10}" y="{yy + 18}" text-anchor="end" font-size="12.5" '
                     f'font-weight="600" fill="#1a1c1f">{esc(h)}</text>')
        parts.append(f'<text x="{left - 10}" y="{yy + 34}" text-anchor="end" font-size="10.5" '
                     f'fill="#6b7076">{len(blocks)} 块 · {minutes(sec)}′</text>')
        for r, i, s0, e0 in blocks:
            x1, x2 = x(s0), x(e0)
            act = hs.act(r)
            p = (r.get("prompt") or r.get("label") or "").strip().replace("\n", " ")[:70]
            tip = f'{mmss(r["start"])}–{mmss(r.get("end"))} {minutes(act)}′ {h}｜{p or "（无首句）"}'
            parts.append(
                f'<rect x="{x1:.1f}" y="{yy + 4 + i * rowpitch}" width="{max(x2 - x1, 3.2):.1f}" '
                f'height="{barh}" rx="3" fill="{fill(act)}" opacity=".88"><title>{esc(tip)}</title></rect>')
    lx = left
    for lab, col in (("普通 <10′", "#3f6bd8"), ("黄线 ≥10′", "#c98a1b"), ("红线 ≥30′", "#b42318")):
        parts.append(f'<rect x="{lx}" y="8" width="10" height="10" rx="2" fill="{col}"/>')
        parts.append(f'<text x="{lx + 15}" y="17" font-size="11" fill="#6b7076">{esc(lab)}</text>')
        lx += 96
    parts.append('</svg>')
    return "".join(parts)


def hourly(recs, since, hours=24):
    t0 = datetime.strptime(since, "%Y-%m-%dT%H:%M")
    buckets = [{"m": 0, "t": 0, "n": 0} for _ in range(hours)]
    for r in recs:
        try:
            s = datetime.fromisoformat(r["start"])
        except Exception:
            continue
        idx = int((s - t0).total_seconds() // 3600)
        if not 0 <= idx < hours:
            continue
        b = buckets[idx]
        b["m"] += r.get("model_seconds") or 0
        b["t"] += r.get("tool_seconds") or 0
        b["n"] += 1
    mx = max((b["m"] + b["t"] for b in buckets), default=1) / 60 or 1
    W, left, bot, top = 1120, 56, 30, 14
    H = 240
    ih = H - top - bot
    bw = (W - left - 14) / hours

    def y(v):
        return top + ih - (v / 60 / mx) * ih

    parts = [f'<svg viewBox="0 0 {W} {H}" role="img" aria-label="每小时模型与工具">']
    for g in range(0, int(mx) + 1, max(1, int(mx / 4))):
        yy = y(g * 60)
        parts.append(f'<line x1="{left}" y1="{yy:.1f}" x2="{W - 14}" y2="{yy:.1f}" stroke="#eeede8"/>'
                     f'<text x="{left - 8}" y="{yy + 4:.1f}" text-anchor="end" font-size="10.5" '
                     f'fill="#6b7076">{g}′</text>')
    for i, b in enumerate(buckets):
        x = left + i * bw + 2
        w = bw - 4
        m, tl = b["m"] / 60, b["t"] / 60
        ym = y(m * 60)
        yt = y((m + tl) * 60)
        t = t0 + timedelta(hours=i)
        lab = t.strftime("%H")
        parts.append(f'<rect x="{x:.1f}" y="{yt:.1f}" width="{w:.1f}" height="{max(ym - yt, 0):.1f}" '
                     f'fill="#3f6bd8" opacity=".9"><title>{t.strftime("%m-%d %H:00")} 模型 {m:.1f}′</title></rect>')
        parts.append(f'<rect x="{x:.1f}" y="{ym:.1f}" width="{w:.1f}" height="{max(top + ih - ym, 0):.1f}" '
                     f'fill="#a9bcf0" opacity=".95"><title>{t.strftime("%m-%d %H:00")} 工具 {tl:.1f}′ · {b["n"]} 块</title></rect>')
        if i % 2 == 0:
            parts.append(f'<text x="{x + w / 2:.1f}" y="{H - 10}" text-anchor="middle" font-size="10.5" '
                         f'fill="#6b7076">{lab}</text>')
    parts.append(f'<text x="{left}" y="{top + 4}" font-size="11" fill="#3f6bd8">■ 模型</text>')
    parts.append(f'<text x="{left + 52}" y="{top + 4}" font-size="11" fill="#8fa4e8">■ 工具</text>')
    parts.append('</svg>')
    return "".join(parts)


def hbars(items, W=1120, rowh=26, unit="′", color="#3f6bd8", right=130):
    mx = max((v for _, v, _ in items), default=1) or 1
    left = 190
    H = rowh * len(items) + 10
    iw = W - left - right
    parts = [f'<svg viewBox="0 0 {W} {H}" role="img">']
    for i, (lab, v, sub) in enumerate(items):
        y = i * rowh + 4
        w = v / mx * iw
        parts.append(f'<text x="{left - 10}" y="{y + 14}" text-anchor="end" font-size="12" '
                     f'fill="#1a1c1f">{esc(lab)}</text>')
        parts.append(f'<rect x="{left}" y="{y + 3}" width="{w:.1f}" height="15" rx="3" fill="{color}" opacity=".85"/>')
        parts.append(f'<text x="{W - 8}" y="{y + 15}" text-anchor="end" font-size="11.5" '
                     f'fill="#6b7076">{v:.1f}{unit}{sub}</text>')
    parts.append('</svg>')
    return "".join(parts)


def build(since, until, recs):
    active = sum(hs.act(r) for r in recs)
    model = sum(r.get("model_seconds") or 0 for r in recs)
    tool = sum(r.get("tool_seconds") or 0 for r in recs)
    idle = sum(r.get("idle_seconds") or 0 for r in recs)
    reds = [r for r in recs if hs.act(r) >= 1800]
    yellows = [r for r in recs if 600 <= hs.act(r) < 1800]
    slow = sum(r.get("slow_tools") or 0 for r in recs)
    blocked = sum(r.get("blocked_waits") or 0 for r in recs)
    hosts = {}
    for r in recs:
        h = r.get("host", "?")
        hosts.setdefault(h, [0, 0])
        hosts[h][0] += 1
        hosts[h][1] += hs.act(r)
    fam = {}
    for r in recs:
        for k, v in (r.get("families") or {}).items():
            fam[k] = fam.get(k, 0) + v
    sess = {}
    for r in recs:
        s = (r.get("session") or "?")[:8]
        sess.setdefault(s, {"n": 0, "sec": 0, "first": "", "first_at": "9999"})
        e = sess[s]
        e["n"] += 1
        e["sec"] += hs.act(r)
        p = (r.get("prompt") or r.get("label") or "").strip()
        if p and (r.get("start") or "") < e["first_at"]:
            e["first_at"] = r.get("start") or ""
            e["first"] = p

    zero = [r for r in recs if hs.act(r) == 0]
    host_txt = " · ".join(f"{h} {n} 块 / {minutes(s)}′（{s / active * 100:.0f}%）"
                          for h, (n, s) in sorted(hosts.items(), key=lambda x: -x[1][1]))

    sess_items = [(s, e["sec"] / 60, f' · {e["n"]} 块')
                  for s, e in sorted(sess.items(), key=lambda x: -x[1]["sec"])[:10]]
    top_items = []
    for r in sorted(recs, key=lambda r: -hs.act(r))[:12]:
        p = (r.get("prompt") or r.get("label") or "").strip().replace("\n", " ")[:34]
        top_items.append((f'{mmss(r.get("start"))} {r.get("host", "")[:4]}', hs.act(r) / 60, f' | {p}'))
    fam_items = [(k, v / 60, f' · {v / active * 100:.0f}%')
                 for k, v in sorted(fam.items(), key=lambda x: -x[1])]

    trs = []
    for r in recs:
        sec = hs.act(r)
        cls = "red" if sec >= 1800 else ("yel" if sec >= 600 else "")
        p = (r.get("prompt") or r.get("label") or "").strip()
        if len(p) > 64:
            p = p[:64] + "…"
        if sec == 0:
            cell = '<span class="dim" title="无模型/工具活动：多为等待或离开">0′</span>'
        elif sec < 30:
            cell = '<span class="dim">&lt;0.5′</span>'
        else:
            cell = minutes(sec)
        trs.append(
            f'<tr class="{cls}">'
            f'<td class="mono">{esc(mmss(r.get("start")))}–{esc(mmss(r.get("end")))}</td>'
            f'<td>{esc(r.get("host", "?"))}</td>'
            f'<td class="num">{cell}</td>'
            f'<td class="num">{minutes(r.get("model_seconds") or 0)}</td>'
            f'<td class="num">{minutes(r.get("tool_seconds") or 0)}</td>'
            f'<td class="num">{r.get("slow_tools") or 0}</td>'
            f'<td class="num">{r.get("reopen_marks") or 0}</td>'
            f'<td class="num">{(r.get("thinking_chars") or 0) // 1000}</td>'
            f'<td class="num">{r.get("tool_calls") or 0}</td>'
            f'<td class="mono dim">{(r.get("session") or "")[:8]}</td>'
            f'<td class="topic">{esc(p) or "<span class=dim>（无首句）</span>"}</td>'
            f'</tr>')

    srows = []
    for s, e in sorted(sess.items(), key=lambda x: -x[1]["sec"]):
        srows.append(
            f'<tr><td class="mono">{esc(s)}</td><td class="num">{e["n"]}</td>'
            f'<td class="num">{minutes(e["sec"])}</td>'
            f'<td class="num">{e["sec"] / active * 100:.0f}%</td>'
            f'<td class="topic">{esc(e["first"][:80])}</td></tr>')

    last_pull = "未知"
    peer_name = (hs.peer_defaults()[0] or "peer")
    lp = os.path.join(hs.PEERS_DIR, peer_name, ".last_pull")
    if os.path.exists(lp):
        last_pull = open(lp, encoding="utf-8").read().strip()
    peer_suffix = ""
    if peer_name != "peer":
        peer_suffix = f" + {esc(peer_name)} 镜像，{esc(peer_name)} 已同步至 {esc(last_pull)}"
    dash = os.path.join(os.path.dirname(hs.DATA_DIR), "dashboard.html")
    dashboard_href = "file://" + os.path.abspath(dash)

    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>逐块明细 · {esc(since[:10])} → {esc(until[:10])}</title>
<style>
:root{{--ink:#1a1c1f;--mut:#6b7076;--line:#e5e4df;--bg:#f7f6f3;--card:#fff;
--yel:#b7791f;--red:#b42318;--ok:#1f6f43;--acc:#1f4feb}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",sans-serif}}
.wrap{{max-width:1220px;margin:0 auto;padding:32px 22px 64px}}
.eyebrow{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut)}}
h1{{font-size:26px;margin:6px 0 6px;letter-spacing:-.01em}}
.meta{{color:var(--mut);font-size:13px;margin-bottom:22px}}
.meta code{{background:#efeee9;padding:1px 6px;border-radius:5px;font-size:12px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;margin:0 0 22px}}
.card{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--acc);
border-radius:10px;padding:14px 16px}}
.card.bad{{border-left-color:var(--red)}}.card.warn{{border-left-color:var(--yel)}}.card.ok{{border-left-color:var(--ok)}}
.card .k{{font-size:12px;color:var(--mut);letter-spacing:.04em}}
.card .v{{font-size:24px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}}
.card .s{{font-size:12.5px;color:var(--mut);margin-top:2px}}
nav{{display:flex;gap:8px;flex-wrap:wrap;margin:0 0 20px}}
nav a{{font-size:13px;color:var(--acc);text-decoration:none;border:1px solid var(--line);
background:var(--card);padding:5px 11px;border-radius:999px}}
a{{color:var(--acc)}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}}
h2{{font-size:17px;margin:0 0 4px}}
.sub{{color:var(--mut);font-size:13px;margin:0 0 14px}}
.chart{{overflow-x:auto}}
.chart svg{{display:block;min-width:760px;width:100%;height:auto}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;font-weight:600;color:var(--mut);font-size:12px;letter-spacing:.03em;
border-bottom:1px solid var(--line);padding:8px 8px;position:sticky;top:0;background:var(--card)}}
td{{padding:6px 8px;border-bottom:1px solid #f0efea;vertical-align:top}}
tr.red td:first-child{{box-shadow:inset 3px 0 0 var(--red)}}
tr.yel td:first-child{{box-shadow:inset 3px 0 0 var(--yel)}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px}}
.num{{text-align:right;font-variant-numeric:tabular-nums}}
.dim{{color:var(--mut)}}
.topic{{max-width:520px;overflow-wrap:anywhere}}
.fg{{display:grid;grid-template-columns:110px 1fr 130px;gap:10px;align-items:center;margin:5px 0;font-size:13px}}
.fk{{color:var(--mut)}}
.fb{{background:#efeee9;border-radius:4px;height:9px;overflow:hidden}}
.fb i{{display:block;height:100%;background:var(--acc);opacity:.75}}
.fv{{text-align:right;font-variant-numeric:tabular-nums;color:var(--mut)}}
.scroll{{max-height:620px;overflow:auto;border:1px solid var(--line);border-radius:8px}}
details summary{{cursor:pointer;font-size:14px;font-weight:600;padding:6px 0;color:var(--ink)}}
details[open] summary{{margin-bottom:10px}}
footer{{color:var(--mut);font-size:12.5px;line-height:1.7;margin-top:8px}}
@media(max-width:720px){{.topic{{max-width:200px}}.fg{{grid-template-columns:84px 1fr 110px}}}}
</style></head><body><div class="wrap">
<div class="eyebrow">Harness · 逐块明细</div>
<h1>{esc(since[:10])} 17:00 → {esc(until[:10])} 17:00 的每块任务</h1>
<div class="meta">数据源：agent 侧钩子（本机{peer_suffix}）
 · 生成 {esc(datetime.now().strftime('%Y-%m-%d %H:%M'))} · 汇总看板
<a href="{esc(dashboard_href)}">dashboard.html</a></div>

<div class="cards">
  <div class="card"><div class="k">活跃时间</div><div class="v">{minutes(active)}′</div>
    <div class="s">{len(recs)} 块 · {len(hosts)} 台机器 · {len(sess)} 个会话</div></div>
  <div class="card bad"><div class="k">红线 / 黄线块</div><div class="v">{len(reds)} / {len(yellows)}</div>
    <div class="s">红 ≥30′，黄 ≥10′；慢调用 {slow} 次，阻塞 {blocked} 次</div></div>
  <div class="card warn"><div class="k">模型 : 工具</div><div class="v">{model / 60:.0f}′ : {tool / 60:.0f}′</div>
    <div class="s">模型 {model / active * 100 if active else 0:.0f}% · 工具 {tool / active * 100 if active else 0:.0f}% · 空档 {minutes(idle)}′</div></div>
</div>

<nav>
  <a href="#timeline">时间轴</a>
  <a href="#hourly">每小时</a>
  <a href="#sessions">会话</a>
  <a href="#family">工具族</a>
  <a href="#longest">最长块</a>
  <a href="#blocks">明细表</a>
</nav>

<section id="timeline"><h2>时间轴：每块落在哪一刻</h2>
<p class="sub">横轴 24 小时，每条泳道一台机器，一块一个色块（鼠标悬停看首句）。空白 = 没有任务在跑。{host_txt}</p>
<div class="chart">{gantt(recs, since, until)}</div>
<p class="sub" style="margin:10px 0 0">{len([r for r in recs if hs.act(r) < 30])} 个不足 30 秒的微块未画入时间轴（它们在表里仍可见）。</p>
</section>

<section id="hourly"><h2>每小时：模型与工具</h2>
<p class="sub">柱子越高越忙；深色是模型（想），浅色是工具（跑）。按块开始时刻归桶。</p>
<div class="chart">{hourly(recs, since)}</div>
</section>

<section id="sessions"><h2>会话：时间集中在谁身上</h2>
<p class="sub">前 4 个会话占 {sum(e['sec'] for _, e in sorted(sess.items(), key=lambda x: -x[1]['sec'])[:4]) / active * 100 if active else 0:.0f}%；其余多为"问一句看一眼"的碎片。</p>
<div class="chart">{hbars(sess_items)}</div>
</section>

<section id="family"><h2>工具族</h2>
<p class="sub">一个块可同时计入多个族，合计会超过活跃时间。</p>
{f'<div class="chart">{hbars(fam_items, color="#5b7cd9")}</div>'}
</section>

<section id="longest"><h2>最长的 12 块</h2>
<p class="sub">黄线 10′、红线 30′ 是两条横标尺；今天最长的块是 {minutes(max((hs.act(r) for r in recs), default=0))}′。</p>
<div class="chart">{hbars(top_items, color="#3f6bd8", right=330)}</div>
</section>

<section id="blocks"><h2>逐块明细表</h2>
<p class="sub">图看趋势，表查细节。按开始时间正序；左侧色条：红 ≥30′、黄 ≥10′。</p>
<details><summary>展开 {len(recs)} 块原始表</summary>
<div class="scroll"><table>
<thead><tr><th>起止</th><th>机器</th><th class="num">活跃′</th><th class="num">模型′</th>
<th class="num">工具′</th><th class="num">慢调用</th><th class="num">重开</th><th class="num">思考k</th>
<th class="num">工具次数</th><th>会话</th><th>首句</th></tr></thead>
<tbody>{''.join(trs)}</tbody></table></div>
</details>
</section>

<section id="note"><h2>口径与边界</h2>
<footer>
· 活跃 = 墙上时间 − 单段 &gt;10′ 空档；跨机镜像按 <code>id</code> 去重、优先本机记录。<br>
· 时间轴宽度按真实起始时刻与结束时刻绘制，色块窄说明块短，不是缺失；同一台机器上并行的块会拆到不同子行，互不遮挡。<br>
· <span class="dim">0′</span> = 该块无模型/工具活动：多半是在等你回复或人离开，少数是采集缺失（本窗口 {len(zero)} 块，其中墙上 ≥2′ 的 {len([r for r in zero if (r.get('wall_seconds') or 0) >= 120])} 块）。<br>
· 「慢调用」「重开」「思考k」来自钩子打点：慢调用 = 单次工具 &gt; 阈值；重开 = 文本里"重新/再来/回退"类词频。<br>
· IDE 侧（Qoder IDE transcript）不在本页：本页只统计 CLI 会话与钩子打点。
</footer></section>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", required=True, help="YYYY-MM-DDTHH:MM")
    ap.add_argument("--until", required=True, help="YYYY-MM-DDTHH:MM")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    recs = load_window(a.since, a.until)
    if not recs:
        print("窗口内无数据")
        return 1
    page = build(a.since, a.until, recs)
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(page)
    print(f"{a.out}（{len(recs)} 块，{sum(hs.act(r) for r in recs) / 60:.1f}′）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
