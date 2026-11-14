#!/usr/bin/env python3
"""A/B 对照：同难度分桶里，「改进生效」和「改进没生效」的块级效率差多少。

不能随机把任务分成两组，所以对照组取「规则触发了但 10 分钟内没跟进」的块——
同一套检测、同一类任务，差别只在有没有照做。分桶按编辑量与部署占比，
避免拿简单任务和复杂任务比。样本不足时只累计、不下结论。
"""
import argparse
import html
import glob
import json
import os
import statistics
import sys
from datetime import date

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import harness_scan as hs  # noqa: E402
import measure_audit as ma  # noqa: E402
import measure_library as ml  # noqa: E402

MIN_N = 5          # 每桶每组最少块数，低于此只累计不判定
RULES = [
    ("locate-first", "RULE-004 先定位再读"),
    ("deploy-first", "RULE-005 部署排队前移"),
]
LEDGER = os.path.join(hs.DATA_DIR, "measures", "ledger.md")


def esc(s):
    return html.escape(str(s), quote=True)


def edit_n(rec):
    return (rec.get("families") or {}).get("edit", 0)


def deploy_n(rec):
    fam = rec.get("families") or {}
    return fam.get("deploy", 0) + fam.get("build", 0)


def cohort(rec):
    e = edit_n(rec)
    eb = "0" if e == 0 else ("1-4" if e < 5 else "5+")
    return f"{'有部署' if deploy_n(rec) else '无部署'}·改{eb}"


def applicable(tag, rec):
    """规则适用范围：不该照做的任务不进对照池，避免用豁免任务拉低对照组。"""
    if tag == "locate-first":
        return edit_n(rec) >= 1
    if tag == "deploy-first":
        return deploy_n(rec) >= 1
    return True


def load_blocks(days):
    # 0 活动 / 跨天待核块不是有效任务样本
    return [b for b in ml.load_days(days) if hs.act(b) > 0 and not hs.is_suspect(b)]


def start_epoch(events):
    """起算点取规则生效日与最近一次 marker 的较晚者——干跑/测试触发的提醒不算样本。"""
    t0 = hs.ts_epoch(ma.RULES_START + "T00:00:00")
    marks = [e.get("t") or 0 for e in events if e.get("kind") == "marker"]
    return max([t0] + marks)


def arm_of(tag, blocks, events):
    """块 → 'A'（触发且跟进）/ 'B'（触发未跟进）；未触发的块不参与。"""
    spans = []
    for b in blocks:
        t0, t1 = hs.ts_epoch(b.get("start")), hs.ts_epoch(b.get("end"))
        if t0 and t1 >= t0:
            spans.append((t0, t1, b))
    out = {}
    for evt in events:
        if evt.get("tag") != tag or evt.get("kind") != "reminder" or not evt.get("session"):
            continue
        t = evt.get("t") or 0
        for t0, t1, b in spans:
            if b.get("session") != evt["session"] or not (t0 <= t <= t1):
                continue
            followed = ma.follows_up(evt, "locate" if tag == "locate-first" else "deploy")
            prev = out.get(b["id"])
            if prev != "A":
                out[b["id"]] = "A" if followed else "B"
            break
    return out


def med(vals):
    return statistics.median(vals) if vals else None


def collect(days, since_epoch=0.0):
    all_events = hs.load_events(since_epoch)
    since_epoch = max(since_epoch, start_epoch(all_events))
    blocks = load_blocks(days)
    events = [e for e in all_events if (e.get("t") or 0) >= since_epoch]
    res = {}
    for tag, name in RULES:
        arms = arm_of(tag, blocks, events)
        pools = {}
        for b in blocks:
            if not applicable(tag, b):
                continue
            arm = arms.get(b["id"])
            if not arm:
                continue
            p = pools.setdefault(cohort(b), {"A": [], "B": []})
            p[arm].append(b)
        rows = []
        for ck in sorted(pools):
            p = pools[ck]
            a = [hs.act(x) / 60 for x in p["A"]]
            c = [hs.act(x) / 60 for x in p["B"]]
            ac = [x.get("tool_calls") or 0 for x in p["A"]]
            cc = [x.get("tool_calls") or 0 for x in p["B"]]
            rows.append({
                "cohort": ck,
                "n_a": len(a), "n_b": len(c),
                "med_a": med(a), "med_b": med(c),
                "calls_a": med(ac), "calls_b": med(cc),
                "delta": (med(a) - med(c)) if a and c else None,
                "verdict": ("可比" if len(a) >= MIN_N and len(c) >= MIN_N else "样本不足"),
            })
        res[name] = {"tag": tag, "rows": rows,
                     "total_a": sum(r["n_a"] for r in rows),
                     "total_b": sum(r["n_b"] for r in rows)}
    return res


def fmt(v, unit="′"):
    return "—" if v is None else f"{v:.1f}{unit}"


def _table(res):
    out = []
    for name, d in res.items():
        out.append(f"<h3>{esc(name)}</h3>")
        if not d["rows"]:
            out.append('<p class="sub">还没有触发记录：对照池为空，等规则在真实任务里生效后自动积累。</p>')
            continue
        out.append('<div class="tw"><table><thead><tr><th>难度桶</th><th class="num">A 有改进</th>'
                   '<th class="num">B 未跟进</th><th class="num">A 中位时长</th><th class="num">B 中位时长</th>'
                   '<th class="num">A 中位调用</th><th class="num">B 中位调用</th><th>判定</th></tr></thead><tbody>')
        for r in d["rows"]:
            out.append(
                f'<tr><td>{esc(r["cohort"])}</td><td class="num">{r["n_a"]}</td><td class="num">{r["n_b"]}</td>'
                f'<td class="num">{fmt(r["med_a"])}</td><td class="num">{fmt(r["med_b"])}</td>'
                f'<td class="num">{fmt(r["calls_a"], "")}</td><td class="num">{fmt(r["calls_b"], "")}</td>'
                f'<td class="st">{esc(r["verdict"])}</td></tr>')
        out.append("</tbody></table></div>")
    return "".join(out)


def render_html(res):
    return f"""<section id="ab"><h2>A/B 对照（同难度分桶）</h2>
<p class="sub">A = 规则触发且 10 分钟内真的照做；B = 只触发没照做——同一套检测、同类任务，差别只在执行。每桶任一组 &lt;{MIN_N} 块只累计不判定。</p>
{_table(res)}
<footer>· 不是随机实验：B 组是「该做没做」，仍有选择偏差，所以只做同桶对比、样本够了才给判定；跨桶数字不做比较。<br>
· 只纳入规则适用范围（如 先定位再读 只算有编辑的任务），豁免任务不进对照池。</footer>
</section>"""


def to_md(res):
    lines = []
    for name, d in res.items():
        lines.append(f"### {name}")
        if not d["rows"]:
            lines.append("触发 0 次，A/B 池为空，等真实任务积累。")
            continue
        lines.append("| 难度桶 | A 组块数 | B 组块数 | A 中位时长 | B 中位时长 | 差值 | 判定 |")
        lines.append("|---|---|---|---|---|---|---|")
        for r in d["rows"]:
            lines.append(f"| {r['cohort']} | {r['n_a']} | {r['n_b']} | {fmt(r['med_a'])} | "
                         f"{fmt(r['med_b'])} | {fmt(r['delta'])} | {r['verdict']} |")
    return "\n".join(lines)


def append_md(res):
    os.makedirs(os.path.dirname(LEDGER), exist_ok=True)
    body = to_md(res)
    with open(LEDGER, "a", encoding="utf-8") as f:
        f.write(f"\n## A/B 对照 {date.today().isoformat()}\n\n{body}\n")


def pick_days(n):
    out = []
    for p in sorted(glob.glob(os.path.join(hs.METRICS_DIR, "*.jsonl"))):
        d = os.path.basename(p)[:-6]
        if len(d) == 10 and d[4] == "-" and d[7] == "-" and d >= ma.RULES_START:
            out.append(d)
    return out[-n:] if n and n < len(out) else out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=0, help="只看最近 N 天（默认从规则生效日起全看）")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--append", action="store_true", help="把对照结果追加进 ledger.md")
    a = ap.parse_args()
    days = pick_days(a.days)
    if not days:
        print("规则生效日之后没有数据")
        return 1
    res = collect(days, hs.ts_epoch(ma.RULES_START + "T00:00:00"))
    if a.json:
        print(json.dumps(res, ensure_ascii=False, indent=1))
        return 0
    print(f"A/B 对照 · 窗口 {' / '.join(d[5:] for d in days)}")
    print(to_md(res))
    if a.append:
        append_md(res)
        print(f"\n已追加 {LEDGER}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
