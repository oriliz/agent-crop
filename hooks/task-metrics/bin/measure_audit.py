#!/usr/bin/env python3
"""措施运行反查：钩子是否真在跑、每条规则触发几次、触发后有没有跟进、结果指标前后对比。

用法：
  python3 bin/measure_audit.py                  # 打印
  python3 bin/measure_audit.py --append         # 追加到 data/measures/ledger.md
  python3 bin/measure_audit.py --json           # 机读
"""
import argparse
import glob
import html
import json
import os
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402
import measure_library as ml  # noqa: E402

FOLLOW_WINDOW = 600          # 触发后多久内算「跟进」
RULES_START = "2026-10-28"   # 三条措施生效日
BEFORE_DAYS = ["2026-10-26", "2026-10-27", "2026-10-28"]


def tool_calls_in(path, t0, t1):
    out = []
    try:
        f = open(path, errors="replace")
    except Exception:
        return out
    with f:
        for line in f:
            try:
                o = json.loads(line)
            except Exception:
                continue
            ts = hs.ts_epoch(o.get("timestamp") or "")
            if not ts or ts < t0 or ts > t1:
                continue
            msg = o.get("message") or {}
            c = msg.get("content")
            if msg.get("role") != "assistant" or not isinstance(c, list):
                continue
            for x in c:
                if isinstance(x, dict) and x.get("type") == "tool_use":
                    inp = x.get("input") if isinstance(x.get("input"), dict) else {}
                    out.append((x.get("name") or "", inp, ts))
    return out


def follows_up(evt, kind):
    """触发后同会话 10 分钟内是否出现对应行为。"""
    sid = evt.get("session") or ""
    path = evt.get("transcript_path") or hs.find_transcript(sid)
    if not path:
        return None
    calls = tool_calls_in(path, evt["t"], evt["t"] + FOLLOW_WINDOW)
    if not calls:
        return False
    if kind == "locate":
        return any(hs.tool_kind(n) in ("lsp", "sws") for n, _, _ in calls)
    if kind == "deploy":
        return any(n in hs.BASH_TOOLS and i.get("run_in_background") for n, i, _ in calls)
    return None


def audit():
    evts = [e for e in hs.load_events(0) if e.get("t")]
    markers = [e["t"] for e in evts if e.get("kind") == "marker"]
    start = max(markers) if markers else int(
        datetime.strptime(RULES_START, "%Y-%m-%d").timestamp())
    win = [e for e in evts if e["t"] >= start]

    def on(day, src=None):
        return [e for e in (src if src is not None else win)
                if datetime.fromtimestamp(e["t"]).strftime("%Y-%m-%d") == day]

    today = date.today().isoformat()
    metric_days = sorted(os.path.basename(p)[:10]
                         for p in glob.glob(os.path.join(hs.METRICS_DIR, "*.jsonl"))
                         if os.path.basename(p)[:4].isdigit())
    days_after = [d for d in metric_days if RULES_START < d <= today]
    if today not in days_after:
        days_after.append(today)

    out = {"start": start, "days_after": days_after, "rules": {}, "health": {}, "outcome": {}}

    tags = {"locate-first": ("locate", "RULE-004 先定位再读"),
            "deploy-first": ("deploy", "RULE-005 部署排队前移"),
            "sleep": ("sleep", "RULE-003 长命令后台")}
    for tag, (kind, label) in tags.items():
        fires = [e for e in win if e.get("kind") == "reminder" and e.get("tag") == tag]
        if tag == "sleep":
            fires += [e for e in win if e.get("kind") == "post-tool"
                      and e.get("tag") == "sleep" and e.get("hit")]
        per_day = {d: sum(1 for e in fires
                          if datetime.fromtimestamp(e["t"]).strftime("%Y-%m-%d") == d)
                   for d in days_after}
        checks = [follows_up(e, kind) for e in fires]
        valid = [c for c in checks if c is not None]
        out["rules"][label] = {
            "tag": tag, "total": len(fires), "per_day": per_day,
            "checked": len(valid), "followed": sum(1 for c in valid if c),
            "rate": (sum(1 for c in valid if c) / len(valid)) if valid else None,
            "last": datetime.fromtimestamp(max([e["t"] for e in fires])).strftime("%m-%d %H:%M")
                    if fires else "",
        }

    health_days = sorted({datetime.fromtimestamp(x["t"]).strftime("%Y-%m-%d")
                          for x in evts if x["t"] >= int((datetime.now() - timedelta(days=7)).timestamp())})
    for d in health_days:
        e = on(d, evts)
        out["health"][d] = {
            "post_tool": sum(1 for x in e if x.get("kind") == "post-tool"),
            "scan": sum(1 for x in e if x.get("kind") == "scan"),
            "inject": sum(1 for x in e if x.get("kind") == "inject" and x.get("sent")),
            "errors": sum(1 for x in e if x.get("kind") == "error"),
            "no_transcript": sum(1 for x in e if x.get("kind") == "scan"
                                 and x.get("reason") == "no-transcript"),
        }

    def day_metrics(day):
        recs = ml.load_days([day])
        if not recs:
            return None
        st = ml.stats(recs)
        return {
            "n": st["n"], "total": st["total"],
            "churn": st["churn_n"], "churn_min": st["churn_min"],
            "deploy_share": st["deploy"] / st["total"] if st["total"] else 0,
            "slow": st["slow"], "red": st["red_n"],
            "blocked": sum(r.get("blocked_waits") or 0 for r in recs),
        }

    def agg(days):
        rows = [day_metrics(d) for d in days]
        rows = [r for r in rows if r]
        if not rows:
            return None
        n = len(rows)
        return {k: sum(r[k] for r in rows) / n for k in
                ("churn", "churn_min", "deploy_share", "slow", "red", "blocked")} | {"days": n}

    out["outcome"]["before"] = agg(BEFORE_DAYS)
    out["outcome"]["after"] = agg([d for d in days_after if d != today and day_metrics(d)])
    out["outcome"]["today"] = day_metrics(today)
    out["outcome"]["today_day"] = today
    return out


def fmt_pct(v):
    return "—" if v is None else f"{v * 100:.1f}%"


def print_report(a):
    print(f"反查起点 {datetime.fromtimestamp(a['start']):%Y-%m-%d %H:%M}｜"
          f"观察日 {'/'.join(d[5:] for d in a['days_after'])}")
    print("\n【钩子运行实况】")
    for d, h in a["health"].items():
        print(f"  {d}: post-tool {h['post_tool']} 次 / scan {h['scan']} 次 / 注入 {h['inject']} 次"
              f" / 报错 {h['errors']} / 无转录 {h['no_transcript']}")
    print("\n【每条规则的触发与跟进】")
    for label, r in a["rules"].items():
        print(f"  {label}: 触发 {r['total']} 次"
              + (f"（最近 {r['last']}）" if r["last"] else "（尚未触发）")
              + f" | 跟进 {r['followed']}/{r['checked']}"
              + (f" = {fmt_pct(r['rate'])}" if r["rate"] is not None else ""))
        if r["per_day"]:
            print("    按天：" + " ".join(f"{d[5:]}={v}" for d, v in r["per_day"].items()))
    print("\n【结果指标：生效前 vs 生效后】")
    b, af = a["outcome"]["before"], a["outcome"]["after"]
    if b and af:
        pairs = [("高调用块/天", b["churn"], af["churn"], "{:.1f}"),
                 ("高调用块分钟/天", b["churn_min"], af["churn_min"], "{:.0f}"),
                 ("deploy 占比", b["deploy_share"], af["deploy_share"], "{:.1%}"),
                 ("慢调用/天", b["slow"], af["slow"], "{:.0f}"),
                 ("红线块/天", b["red"], af["red"], "{:.1f}"),
                 ("sleep 干等/天", b["blocked"], af["blocked"], "{:.1f}")]
        for name, x, y, f in pairs:
            d = y - x
            print(f"  {name:<14} {f.format(x):>8} → {f.format(y):>8}  "
                  f"{'↓' if d < 0 else ('↑' if d > 0 else '=')}{f.format(abs(d))}")
        print(f"  样本：前 {b['days']} 个完整日（10-26~28） vs 后 {af['days']} 个完整日"
              f"；今天（{a['outcome']['today_day']}）单列，不并入对比")
    else:
        print("  生效后还没有完整工作日，暂不对比（今天的数据单列）")
    t = a["outcome"]["today"]
    if t:
        print(f"\n【今天（{a['outcome']['today_day']}）观察值】"
              f"{t['n']} 块 / {t['total']:.0f}′，高调用块 {t['churn']} 个 / {t['churn_min']:.0f}′，"
              f"deploy {t['deploy_share']:.1%}，慢调用 {t['slow']}，sleep 干等 {t['blocked']}")


def render_html(a):
    e = html.escape
    rows = []
    for label, r in a["rules"].items():
        rate = "—" if r["rate"] is None else f"{r['rate'] * 100:.0f}%"
        per_day = " ".join(f"{d[5:]}={v}" for d, v in r["per_day"].items()) or "—"
        rows.append(f"<tr><td><b>{e(label)}</b></td><td class='num'>{r['total']}</td>"
                    f"<td class='num'>{r['followed']}/{r['checked']}</td>"
                    f"<td class='num'>{rate}</td><td class='mono'>{e(per_day)}</td>"
                    f"<td class='mono'>{e(r['last']) or '—'}</td></tr>")
    hrows = []
    for d, h in a["health"].items():
        cls = "bad" if h["errors"] else ""
        hrows.append(f"<tr class='{cls}'><td class='mono'>{e(d)}</td><td class='num'>{h['post_tool']}</td>"
                     f"<td class='num'>{h['scan']}</td><td class='num'>{h['inject']}</td>"
                     f"<td class='num'>{h['errors']}</td><td class='num'>{h['no_transcript']}</td></tr>")
    b, af, t = a["outcome"]["before"], a["outcome"]["after"], a["outcome"]["today"]
    orows = []
    if b and af:
        for name, k, f in [("高调用块/天", "churn", "{:.1f}"), ("高调用块分钟/天", "churn_min", "{:.0f}"),
                           ("deploy 占比", "deploy_share", "{:.1%}"), ("慢调用/天", "slow", "{:.0f}"),
                           ("红线块/天", "red", "{:.1f}"), ("sleep 干等/天", "blocked", "{:.1f}")]:
            d = af[k] - b[k]
            mark = "ok" if d < 0 else ("bad" if d > 0 else "")
            orows.append(f"<tr><td>{e(name)}</td><td class='num'>{f.format(b[k])}</td>"
                         f"<td class='num'>{f.format(af[k])}</td>"
                         f"<td class='num {mark}'>{'↓' if d < 0 else ('↑' if d > 0 else '=')}"
                         f"{f.format(abs(d))}</td></tr>")
    else:
        orows.append("<tr><td colspan='4' class='supp'>生效后还没有完整工作日，暂不对比；今天的观察值见下方。</td></tr>")
    today_line = (f"今天（{e(a['outcome']['today_day'])}）观察值：{t['n']} 块 / {t['total']:.0f}′，"
                  f"高调用块 {t['churn']} 个 / {t['churn_min']:.0f}′，deploy {t['deploy_share']:.1%}，"
                  f"慢调用 {t['slow']}，sleep 干等 {t['blocked']}") if t else ""
    return f"""<section id="audit"><h2>运行实况（反查）</h2>
<p class="sub">反查起点 {datetime.fromtimestamp(a['start']):%Y-%m-%d %H:%M}；「跟进」= 触发后同会话 10 分钟内出现对应行为（LSP/语义检索、后台化）。</p>
<h3>每条规则的触发与跟进</h3>
<table><thead><tr><th>规则</th><th class="num">触发次数</th><th class="num">跟进</th><th class="num">跟进率</th><th>按天</th><th>最近一次</th></tr></thead>
<tbody>{''.join(rows)}</tbody></table>
<h3 style="margin-top:18px">钩子运行</h3>
<table><thead><tr><th>日期</th><th class="num">post-tool</th><th class="num">scan</th><th class="num">注入</th><th class="num">报错</th><th class="num">无转录</th></tr></thead>
<tbody>{''.join(hrows)}</tbody></table>
<h3 style="margin-top:18px">结果指标：生效前 vs 生效后</h3>
<table><thead><tr><th>指标</th><th class="num">前（{b['days'] if b else 0} 日）</th><th class="num">后（{af['days'] if af else 0} 日）</th><th class="num">变化</th></tr></thead>
<tbody>{''.join(orows)}</tbody></table>
<p class="sub" style="margin-top:8px">{today_line}</p>
<footer>口径：前 = 10-26~28 三个完整工作日；后 = 生效次日起的完整工作日；今天单列不并入。<br>
归因边界：三条同时生效、且与任务类型混杂，单个指标的升降不能单独归给某条规则；只有「触发→跟进」链路成立、且指标同向变化时才算有效。</footer>
</section>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--append", action="store_true", help="追加到 ledger.md")
    args = ap.parse_args()
    a = audit()
    if args.json:
        print(json.dumps(a, ensure_ascii=False, indent=1))
        return 0
    print_report(a)
    if args.append:
        with open(ml.LEDGER, "a", encoding="utf-8") as f:
            f.write(f"\n### {date.today().isoformat()} 运行反查\n\n")
            for label, r in a["rules"].items():
                f.write(f"- {label}：触发 {r['total']} 次，跟进 {r['followed']}/{r['checked']}"
                        f"（{fmt_pct(r['rate'])}）\n")
            for d, h in a["health"].items():
                f.write(f"- {d} 钩子：post-tool {h['post_tool']} / scan {h['scan']}"
                        f" / 注入 {h['inject']} / 报错 {h['errors']} / 无转录 {h['no_transcript']}\n")
            b, af = a["outcome"]["before"], a["outcome"]["after"]
            if b and af:
                f.write(f"- 结果：高调用块 {b['churn']:.1f}→{af['churn']:.1f}/天，"
                        f"deploy {b['deploy_share']:.1%}→{af['deploy_share']:.1%}，"
                        f"慢调用 {b['slow']:.0f}→{af['slow']:.0f}/天\n")
        print(f"\n已追加 → {ml.LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
