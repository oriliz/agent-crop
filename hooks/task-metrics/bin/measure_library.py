#!/usr/bin/env python3
"""从近 N 个工作日的块数据里量化四类浪费，生成「提效措施库」单页（零 JS）。

每条措施都带：自动可识别的触发信号 → 具体动作 → 实测基线 → 保守预估节省。
"""
import argparse
import glob
import html
import json
import os
import statistics
import sys
from datetime import date, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402

MEAS_DIR = os.path.join(hs.DATA_DIR, "measures")
LEDGER = os.path.join(MEAS_DIR, "ledger.md")

# 验收口径：每周只看这 5 个，没降就是没落地
TRACK_KEYS = [
    ("churn_per_day", "高调用块/天（≥40 调用）", 1.0, "≤"),
    ("churn_min_per_day", "高调用块分钟/天", None, "≤"),
    ("red_n", "红线块数（≥30′）", 0, "=="),
    ("deploy_share", "deploy 占比", 0.05, "≤"),
    ("slow", "慢调用数（≥120s）", None, "≤"),
]


def track_metrics(st, ndays):
    return {
        "churn_per_day": st["churn_n"] / max(ndays, 1),
        "churn_min_per_day": st["churn_min"] / max(ndays, 1),
        "red_n": st["red_n"],
        "deploy_share": st["deploy"] / st["total"] if st["total"] else 0.0,
        "slow": st["slow"],
    }


def esc(s):
    return html.escape(str(s), quote=True)


def load_days(days):
    me = hs.machine_name()
    out = []
    for day in days:
        m = {}
        for r in hs.load_day(day):
            k = r.get("id", "")
            o = m.get(k)
            if o is None or (o.get("host") != me and r.get("host") == me):
                m[k] = r
        out += list(m.values())
    return out


def stats(recs):
    act = [hs.act(r) / 60 for r in recs]
    total = sum(act)
    s = sorted(act)
    top10 = sum(s[int(len(s) * 0.9):])
    churn = [r for r in recs if (r.get("tool_calls") or 0) >= 40 and hs.act(r) >= 180]
    churn_min = sum(hs.act(r) for r in churn) / 60
    deploy = sum((r.get("families") or {}).get("deploy", 0) for r in recs) / 60
    slow = sum(r.get("slow_tools") or 0 for r in recs)
    reds = [r for r in recs if hs.act(r) >= 1800]
    disc = [r for r in recs if hs.act(r) >= 180 and (r.get("tool_calls") or 0) <= 3
            and (r.get("model_seconds") or 0) / max(hs.act(r), 1) >= 0.85]
    return {
        "n": len(recs), "total": total, "p50": statistics.median(s) if s else 0,
        "top10": top10, "churn_n": len(churn), "churn_min": churn_min,
        "churn_avg": churn_min / len(churn) if churn else 0,
        "deploy": deploy, "slow": slow, "red_n": len(reds),
        "red_min": sum(hs.act(r) for r in reds) / 60, "disc_n": len(disc),
        "disc_min": sum(hs.act(r) for r in disc) / 60,
    }


MEASURES = [
    ("长命令后台化",
     "单次工具耗时 ≥60s，且块内调用 ≤3 次",
     "命令一律 run_in_background；期间写注释、补用例或跑下一条；用 deploy_watch 轮询而不是 sleep 干等",
     "long_cmd", "0.5", "已落 2026-10-28（hook 即时提示 + 回看注入）"),
    ("先定位再读",
     "块内调用 ≥40 次，且 read+search ≥ 块时长 40%",
     "改代码前先 jdtls 桥 definition/references 定位到符号与调用点，再 Read 精读；禁止 grep 后逐文件通读",
     "churn", "0.25", "已落 2026-10-28（hook 第 8 / 20 次提示）"),
    ("契约 10 行前置",
     "连续 ≥3 轮纯讨论（无工具 / 无编辑）后才进入编码",
     "第二轮讨论结束时把「输入 / 输出 / 注入点 / 不变量 / 反例」写进 data/decisions/<date>.md，编码只照单执行",
     "discuss", "0.5", "待落（10-27 HAI、10-28 SDK 两次同型返工）"),
    ("红线前主动拆段",
     "预估 >15′ 或块已到 20′ 仍未收口",
     "拆成 ≤10′ 三段：改代码 / 验证（后台）/ 汇总；每 10′ 一行「已完成 X / 正在 Y / 还差 Z」",
     "red", "0.2", "部分落地（有告警，无强制拆段）"),
    ("部署排队前移",
     "deploy / build 族耗时占当日 ≥8%",
     "开工第一步先起构建或预发部署，排队时间写代码；部署结果用 deploy_watch 拿，不阻塞对话",
     "deploy", "0.4", "已落 2026-10-28（前台部署即时提示）"),
    ("复盘与报表自动化",
     "每天固定的复盘/周报耗时 ≥10′",
     "窗口裁剪 daily --since + 逐块明细页 + 数据截止行，一条命令生成；人只读结论",
     "report", "0.75", "已落（window_detail.py + --since）"),
    ("跨机同步有界",
     "pull 无超时 / 数据截止时间未标注",
     "rsync+ssh 全链路 ConnectTimeout + 失败写 findings；报告固定输出「数据截止」行",
     "sync", "1.0", "已落（超时 + .fallback_ssh）"),
    ("口径自检",
     "other 族占比 >40%，或出现 0 活动但墙上 ≥2′ 的块",
     "补 family 分类覆盖；0 活动块单独标注，不进任何占比结论",
     "measure", "0", "待落"),
]


def render(days, st, au_html=""):
    def share(v):
        return f"{v / st['total'] * 100:.0f}%" if st["total"] else "0%"

    rows = []
    for name, sig, act, key, disc, status in MEASURES:
        if key == "long_cmd":
            base = f"{st['slow']} 次慢调用 / {len(days)} 天（最长单条 8.5′）"
            save = 10
        elif key == "churn":
            base = f"{st['churn_n']} 块 / {st['churn_min']:.0f}′（{share(st['churn_min'])}），均 {st['churn_avg']:.1f}′"
            save = st["churn_min"] / len(days) * float(disc)
        elif key == "discuss":
            base = f"{st['disc_n']} 个纯讨论块 / {st['disc_min']:.0f}′"
            save = st["disc_min"] / len(days) * float(disc)
        elif key == "red":
            base = f"{st['red_n']} 块 ≥30′ / {st['red_min']:.0f}′"
            save = st["red_min"] / len(days) * float(disc)
        elif key == "deploy":
            base = f"deploy {st['deploy']:.0f}′（{share(st['deploy'])}）"
            save = st["deploy"] / len(days) * float(disc)
        elif key == "report":
            base = "每天手工复盘 ~40′，现在一条命令出页"
            save = 15
        elif key == "sync":
            base = "10-27 pull 挂 181s 被杀；mini 数据停在 14:22"
            save = 3
        else:
            base = "other 族 57%、mini 5 个 0 活动块"
            save = 0
        save_txt = f"{save:.0f}′/天" if save >= 1 else "（防误判）"
        rows.append(
            f'<tr><td><b>{esc(name)}</b></td><td class="mono">{esc(sig)}</td>'
            f'<td class="topic">{esc(act)}</td><td class="num">{esc(base)}</td>'
            f'<td class="num">{esc(save_txt)}</td><td class="st">{esc(status)}</td></tr>')

    day_txt = " / ".join(d[5:] for d in days)
    return f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>提效措施库 · 近 {len(days)} 个工作日</title>
<style>
:root{{--ink:#1a1c1f;--mut:#6b7076;--line:#e5e4df;--bg:#f7f6f3;--card:#fff;
--yel:#b7791f;--red:#b42318;--ok:#1f6f43;--acc:#1f4feb}}
*{{box-sizing:border-box}}
body{{margin:0;background:var(--bg);color:var(--ink);
font:15px/1.6 -apple-system,BlinkMacSystemFont,"PingFang SC","Segoe UI",sans-serif}}
.wrap{{max-width:1240px;margin:0 auto;padding:32px 22px 64px}}
.eyebrow{{font-size:12px;letter-spacing:.14em;text-transform:uppercase;color:var(--mut)}}
h1{{font-size:26px;margin:6px 0 6px;letter-spacing:-.01em}}
.meta{{color:var(--mut);font-size:13px;margin-bottom:22px}}
.cards{{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:12px;margin:0 0 22px}}
.card{{background:var(--card);border:1px solid var(--line);border-left:4px solid var(--acc);
border-radius:10px;padding:14px 16px}}
.card.bad{{border-left-color:var(--red)}}.card.warn{{border-left-color:var(--yel)}}.card.ok{{border-left-color:var(--ok)}}
.card .k{{font-size:12px;color:var(--mut);letter-spacing:.04em}}
.card .v{{font-size:24px;font-weight:650;margin-top:4px;font-variant-numeric:tabular-nums}}
.card .s{{font-size:12.5px;color:var(--mut);margin-top:2px}}
section{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:20px 22px;margin-bottom:18px}}
h2{{font-size:17px;margin:0 0 4px}}
.sub{{color:var(--mut);font-size:13px;margin:0 0 14px}}
table{{width:100%;border-collapse:collapse;font-size:13px}}
th{{text-align:left;font-weight:600;color:var(--mut);font-size:12px;letter-spacing:.03em;
border-bottom:1px solid var(--line);padding:8px;background:var(--card)}}
td{{padding:9px 8px;border-bottom:1px solid #f0efea;vertical-align:top}}
.num{{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}}
.mono{{font-family:ui-monospace,SFMono-Regular,Menlo,monospace;font-size:12px;color:#40454b}}
.topic{{max-width:430px;overflow-wrap:anywhere}}
.st{{font-size:12.5px;color:var(--mut);max-width:210px}}
ol{{margin:6px 0 0 18px;padding:0}}
li{{margin:6px 0}}
code{{background:#efeee9;padding:1px 6px;border-radius:5px;font-size:12.5px}}
footer{{color:var(--mut);font-size:12.5px;line-height:1.7}}
@media(max-width:760px){{.topic{{max-width:240px}}}}
</style></head><body><div class="wrap">
<div class="eyebrow">Harness · 提效措施库</div>
<h1>把复盘变成动作：{len(MEASURES)} 条措施和它们的实测底数</h1>
<div class="meta">底数窗口：{esc(day_txt)}（{st['n']} 块 / {st['total']:.0f}′，本机 + mini 去重）· 生成 {esc(date.today().isoformat())}
 · 预估节省 = 实测分钟 × 折扣系数（保守），不叠加跨措施的重复部分</div>

<div class="cards">
  <div class="card bad"><div class="k">长块垄断</div><div class="v">{st['top10'] / st['total'] * 100:.0f}%</div>
    <div class="s">最长的 10% 块占了 {st['top10']:.0f}′ / {st['total']:.0f}′；中位块只有 {st['p50']:.1f}′</div></div>
  <div class="card warn"><div class="k">高调用块（读写翻找）</div><div class="v">{st['churn_n']} 块</div>
    <div class="s">{st['churn_min']:.0f}′（{share(st['churn_min'])}），平均每块 {st['churn_avg']:.1f}′，每天约 {st['churn_min'] / len(days):.0f}′</div></div>
  <div class="card ok"><div class="k">部署/构建</div><div class="v">{st['deploy']:.0f}′</div>
    <div class="s">占 {share(st['deploy'])}，每天约 {st['deploy'] / len(days):.0f}′；排队时间本可写代码</div></div>
</div>

<section><h2>措施清单</h2>
<p class="sub">每条都能被指标自动识别触发，动作具体到命令或模板，落地后可用右列基线复核。</p>
<table>
<thead><tr><th>措施</th><th>触发信号</th><th>具体动作</th><th class="num">实测基线</th>
<th class="num">预估节省</th><th>状态</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
</section>

<section><h2>本轮已落 3 条（2026-10-28）</h2>
<ol>
<li><b>先定位再读</b>：{st['churn_n']} 块 / {st['churn_min']:.0f}′ 是最大的一块可压缩时间，按 25% 折扣算每天约 {st['churn_min'] / len(days) * 0.25:.0f}′。落地方式：hook 在每轮 Read/Grep/Glob 第 8 次（第 20 次再提醒一次）且 0 次 LSP/语义检索时即时提示。</li>
<li><b>部署排队前移</b>：deploy 占 {share(st['deploy'])}。落地方式：前台部署/构建命令一出现就提示改后台 + 排队期间写代码。</li>
<li><b>长命令后台化</b>：sleep 干等与 ≥120s 慢调用。落地方式：命令位 sleep 即时提示，回看行注入上周慢调用数。</li>
</ol>
<p class="sub" style="margin-top:10px">下一批（契约前置、红线拆段、口径自检）不急着上，避免同时改太多规则导致都不生效。</p>
</section>

<section><h2>怎么证明有效</h2>
<p class="sub">每周固定看 4 个数，任一没有下降就说明措施没落地，而不是"效果不明显"。</p>
<ul>
<li>高调用块数（触发信号 <code>tool_calls≥40 且 ≥3′</code>）：目标从 {st['churn_n']} 块/{len(days)} 天降到每天 ≤1 块。</li>
<li>读数命令：<code>python3 bin/measure_library.py --track</code>，与基线对比并自动追加到 <code>data/measures/ledger.md</code>。</li>
<li>单命令最长等待：目标 &lt;60s（用 <code>deploy_watch</code> / 后台任务代替干等）。</li>
<li>红线块数（≥30′）：近 {len(days)} 天 {st['red_n']} 块，目标 0。</li>
<li>deploy 占比：{share(st['deploy'])} → 目标 &lt;5%。</li>
</ul>
</section>

{au_html}
<section><h2>口径与边界</h2>
<footer>
· 基线只取最近 {len(days)} 个有数据的工作日（{esc(day_txt)}），更早数据来自旧版扫描器，存在重复计数，不参与任何"省了多少"的计算。<br>
· 「预估节省」是保守折算，不是承诺值；每周用上面 4 个数复核，无效就换措施，而不是加码执行。<br>
· 措施库按"触发信号"组织，意味着它可以被 hook 自动提示——这是把它从文档变成体系的关键一步。
</footer></section>
</div></body></html>"""


def pick_days(n):
    today = date.today()
    days = []
    d = today
    while len(days) < n and (today - d).days < 30:
        if d.weekday() < 5:
            days.append(d.isoformat())
        d -= timedelta(days=1)
    days.reverse()
    return days


def fmt_metric(k, v):
    if k == "deploy_share":
        return f"{v * 100:.1f}%"
    if k in ("red_n", "slow"):
        return f"{v:.0f}"
    return f"{v:.1f}"


def snapshot(days, st, note=""):
    os.makedirs(MEAS_DIR, exist_ok=True)
    p = os.path.join(MEAS_DIR, f"baseline-{date.today().isoformat()}.json")
    row = {"date": date.today().isoformat(), "days": days, "n": st["n"],
           "total": round(st["total"], 1), "note": note}
    row.update({k: round(v, 3) for k, v in track_metrics(st, len(days)).items()})
    with open(p, "w", encoding="utf-8") as f:
        json.dump(row, f, ensure_ascii=False, indent=1)
    return p


def track(days, st, note=""):
    """对比最早基线，打印增量并追加一条读数到 ledger.md。"""
    files = sorted(glob.glob(os.path.join(MEAS_DIR, "baseline-*.json")))
    if not files:
        print("没有基线，先跑 --snapshot")
        return 1
    base = json.load(open(files[0], encoding="utf-8"))
    cur = track_metrics(st, len(days))
    lines, passed = [], 0
    for k, name, target, op in TRACK_KEYS:
        if base.get(k) is None:
            continue
        b, c = round(float(base[k]), 3), round(float(cur[k]), 3)
        delta = round(c - b, 3)
        arrow = "↓" if delta < 0 else ("↑" if delta > 0 else "=")
        if target is None:
            verdict = "—"
        else:
            ok = c <= target if op == "≤" else c == target
            passed += 1 if ok else 0
            verdict = "达标" if ok else "未达"
        tgt = "" if target is None else (f"{target * 100:.0f}%" if k == "deploy_share" else f"{target:g}")
        lines.append(f"| {name} | {fmt_metric(k, b)} | {fmt_metric(k, c)} | "
                     f"{arrow}{fmt_metric(k, abs(delta))} | {tgt or '—'} | {verdict} |")
        print(f"{name:<22} {fmt_metric(k, b):>8} → {fmt_metric(k, c):>8}  {arrow}  {verdict}")
    head = (f"\n### {date.today().isoformat()} 读数（窗口 {'/'.join(d[5:] for d in days)}，"
            f"{st['n']} 块 / {st['total']:.0f}′；基线 {base['date']}）\n"
            "| 指标 | 基线 | 现在 | 变化 | 目标 | 判定 |\n|---|---|---|---|---|---|\n")
    body = "\n".join(lines) + "\n"
    if note:
        body += f"\n备注：{note}\n"
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(head + body)
    print(f"\n已追加读数 → {LEDGER}（达标 {passed}/{len([k for k in TRACK_KEYS if k[2] is not None])}）")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", default="0", type=int,
                    help="0 = 用最早基线的工作日窗口（页面数字与台账一致）；>0 = 最近 N 个工作日")
    ap.add_argument("--out", default=os.path.expanduser("~/.qoder/task-metrics/measures.html"))
    ap.add_argument("--snapshot", action="store_true", help="把当前窗口指标写成基线")
    ap.add_argument("--track", action="store_true", help="对比最早基线并把读数追加进 ledger.md")
    ap.add_argument("--note", default="", help="写进快照/读数的备注")
    a = ap.parse_args()
    base_files = sorted(glob.glob(os.path.join(MEAS_DIR, "baseline-*.json")))
    if a.days <= 0 and base_files:
        days = json.load(open(base_files[0], encoding="utf-8")).get("days") or pick_days(3)
    else:
        days = pick_days(a.days or 3)
    recs = load_days(days)
    if not recs:
        print("没有数据")
        return 1
    st = stats(recs)
    if a.snapshot:
        p = snapshot(days, st, a.note)
        print(f"基线 → {p}（{len(days)} 天 / {st['n']} 块 / {st['total']:.0f}′）")
        return 0
    if a.track:
        return track(days, st, a.note)
    au_html = ""
    try:
        import measure_audit as ma
        au_html = ma.render_html(ma.audit())
    except Exception as e:  # 反查失败不影响主页面
        au_html = f"<section><h2>运行实况（反查）</h2><p class='sub'>反查不可用：{html.escape(type(e).__name__)}</p></section>"
    try:
        import measure_ab as mb
        au_html += mb.render_html(mb.collect(
            mb.pick_days(0), hs.ts_epoch(ma.RULES_START + "T00:00:00")))
    except Exception as e:
        au_html += f"<section><h2>A/B 对照</h2><p class='sub'>对照不可用：{html.escape(type(e).__name__)}</p></section>"
    with open(a.out, "w", encoding="utf-8") as f:
        f.write(render(days, st, au_html))
    print(f"{a.out}（{len(days)} 天 / {st['n']} 块 / {st['total']:.0f}′）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
