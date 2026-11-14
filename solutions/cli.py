#!/usr/bin/env python3
"""方案开关 CLI。

  cli.py list                 列出全部方案与状态（含是否启用）
  cli.py status               只看当前启用列表
  cli.py review               近 7 天触发量 + 状态建议（每周复查用）
  cli.py enable  <id>         启用（立即生效，不重装）
  cli.py disable <id>         停用
"""
import json
import os
import sys
import time
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import kit_solutions as sol


def all_solutions():
    out = []
    for name in sorted(os.listdir(HERE)):
        p = os.path.join(HERE, name, "solution.json")
        if not os.path.isfile(p):
            continue
        try:
            with open(p, encoding="utf-8") as f:
                out.append(json.load(f))
        except Exception:
            continue
    return out


def _jsonl(path):
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                try:
                    yield json.loads(line)
                except Exception:
                    continue
    except Exception:
        return


def _recent_lsp_repos(days=7):
    """近期会话出现的仓库里，已有 .mcp.json 且含 lsp-* 的个数。"""
    root = os.path.join(os.path.expanduser(os.environ.get("KIT_IDE_HOME") or "~/.qoder"), "projects")
    cutoff = time.time() - days * 86400
    seen, ok = set(), set()
    try:
        for slug in os.listdir(root):
            d = os.path.join(root, slug)
            for fn in os.listdir(d):
                p = os.path.join(d, fn)
                if not fn.endswith(".jsonl") or os.path.getmtime(p) < cutoff:
                    continue
                with open(p, encoding="utf-8", errors="ignore") as f:
                    for i, line in enumerate(f):
                        try:
                            e = json.loads(line)
                        except Exception:
                            continue
                        if e.get("type") == "workspace-directories":
                            seen.update(x for x in (e.get("directories") or []) if os.path.isdir(x))
                        if i > 5:
                            break
    except Exception:
        pass
    for repo in seen:
        try:
            with open(os.path.join(repo, ".mcp.json"), encoding="utf-8") as f:
                servers = (json.load(f).get("mcpServers") or {})
            if any(k.startswith("lsp-") for k in servers):
                ok.add(repo)
        except Exception:
            continue
    return len(seen), len(ok)


def _taskfacts(days):
    try:
        here = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(here, "hooks", "task-metrics", "bin"))
        import taskfacts
        return taskfacts.summarize(taskfacts.load(days))
    except Exception:
        return None


def _assets(days):
    rows = {}
    path = os.path.join(sol.DATA_DIR, "assets.jsonl")
    try:
        if os.path.isfile(path):
            for line in open(path, encoding="utf-8", errors="ignore"):
                try:
                    r = json.loads(line)
                    rows[r["id"]] = r
                except Exception:
                    continue
    except Exception:
        return None
    cut = datetime.fromtimestamp(time.time() - days * 86400).date().isoformat()
    hits = sum(1 for r in rows.values()
               for h in (r.get("hits") or []) if h.split(":")[0] >= cut)
    hot = sorted(((sum(1 for h in (r.get("hits") or []) if h.split(":")[0] >= cut), r["id"])
                  for r in rows.values()), reverse=True)
    return {"total": len(rows), "hits": hits, "top": hot[:3]}


def _detours(days):
    out = []
    d = os.path.join(sol.DATA_DIR, "detours")
    if os.path.isdir(d):
        for fn in os.listdir(d):
            if fn.endswith(".jsonl"):
                for e in _jsonl(os.path.join(d, fn)):
                    if e.get("t", 0) / 1000 >= time.time() - days * 86400:
                        out.append(e)
    return out


def review(days=7):
    data = sol.DATA_DIR
    now = time.time()
    tags = {}
    event_paths = [os.path.join(data, "harness_events.jsonl")]
    evdir = os.path.join(data, "events")
    if os.path.isdir(evdir):
        event_paths += [os.path.join(evdir, fn) for fn in os.listdir(evdir) if fn.endswith(".jsonl")]
    for p in event_paths:
        for e in _jsonl(p):
            if e.get("kind") == "reminder" and e.get("t", 0) >= now - days * 86400:
                k = e.get("tag") or "long-turn"
                tags[k] = tags.get(k, 0) + 1
    redline = sum(1 for e in _jsonl(os.path.join(data, "events.jsonl"))
                  if e.get("ev") == "redline" and e.get("t", 0) / 1000 >= now - days * 86400)
    gates = {"gate": 0, "ack": 0, "edit-before-ack": 0}
    gdir = os.path.join(data, "gates")
    if os.path.isdir(gdir):
        for fn in os.listdir(gdir):
            if fn.endswith(".jsonl"):
                for e in _jsonl(os.path.join(gdir, fn)):
                    if e.get("ev") in gates:
                        gates[e["ev"]] += 1
    repos, lsp_repos = _recent_lsp_repos(days)
    retros = []
    rdir = os.path.join(data, "retros")
    if os.path.isdir(rdir):
        for fn in os.listdir(rdir):
            if fn.endswith(".jsonl"):
                for e in _jsonl(os.path.join(rdir, fn)):
                    if e.get("t", 0) / 1000 >= now - days * 86400:
                        retros.append(e)
    with_cause = sum(1 for r in retros if "根因" in (r.get("markers") or []))
    with_commit = sum(1 for r in retros if "兑现" in (r.get("markers") or []))
    facts = _taskfacts(days)
    assets = _assets(days)
    detours = _detours(days)
    det_ok = sum(1 for d in detours if d.get("verified"))
    det_claim = sum(1 for d in detours if d.get("claim"))

    remind = " / ".join(f"{k} {v}" for k, v in sorted(tags.items())) or "0"
    print(f"近 {days} 天触发（截至 {datetime.now():%Y-%m-%d %H:%M}）")
    print(f"  tempo-rules        红线 {redline}")
    print(f"  inline-reminders   {remind}")
    print(f"  alignment-gate     门 {gates['gate']} / 已回答 {gates['ack']} / 先改后对 {gates['edit-before-ack']}")
    print(f"  lsp-autoconfig     近期仓库 {repos} 个，已配 LSP {lsp_repos} 个")
    print(f"  detour-signal      声明 {len(detours)} 条（自述已恢复 {det_claim} / 后续有证据 {det_ok}）")
    print(f"  复盘留存           {len(retros)} 条（有根因 {with_cause} / 有兑现 {with_commit}）")
    if facts:
        used = " / ".join(f"{k} {v}" for k, v in facts["flags"].items() if v) or "无"
        print(f"  任务事实           {facts['blocks']} 块，中位墙钟 {facts['median_minutes']} 分钟"
              f"（执行 {facts['median_work_minutes']}）；块内等你 {facts['wait_share']}%、"
              f"块后离开中位 {facts['away_median']} 分钟（≥10 分钟的 {facts['away_blocks']} 块），"
              f"失败率 {facts['failure_rate']}%，有验证 {facts['verify_rate']}%")
        print(f"                    方法：{used}")
    if assets:
        top = " / ".join(f"{k} {v}" for v, k in assets["top"] if v) or "无"
        print(f"  资产               {assets['total']} 条，近 {days} 天命中 {assets['hits']} 次（{top}）")
    print("  fast-loop          无事件埋点（skill 调用见资产命中）")
    print("\n建议")
    if redline:
        print("  tempo-rules → 试用中：有告警记录，补「复盘后是否改动」对照")
    if tags:
        print("  inline-reminders → 试用中：按 tag 看跟进率；出现反作用的规则先关单条")
        if tags.get("locate-first", 0) >= 3:
            print("  · locate-first 反复触发：考虑把「先定位」做成命令卡，而不是继续提示")
    if gates["gate"]:
        print("  alignment-gate → 试用中：看 ack 率；gate ≥3 且 ack=0 说明话术没被回答")
        if gates["edit-before-ack"]:
            print("  · 出现先改后对：门在生效，但回答成本偏高，收窄触发词")
    if lsp_repos:
        print("  lsp-autoconfig → 有产物：下个会话确认 lsp-* 工具是否出现在工具列表")
    if redline and not retros:
        print("  · 红线有记录但没有复盘原文：Stop 钩子没生效，或复盘不在触发后的那一轮回复里")
    if facts and (facts["wait_share"] >= 20 or facts["away_blocks"] >= 3):
        print(f"  · 块内等你 {facts['wait_share']}% / 块后离开 {facts['away_blocks']} 块 ≥10 分钟："
              f"复盘时先核 human-wait，别把等你/你离开的时间记成工具耗时")
    if retros and with_commit == 0:
        print("  · 复盘有原文但兑现检查为 0：兑现率仍然统计不出来")
    if detours and det_ok == 0:
        print("  detour-signal → 有声明但没恢复证据：看声明后是否真换了路，还是只写了句口号")
    if facts and facts["blocks"] and facts["verify_rate"] < 20:
        print("  · 有验证的块不足 20%：改动后先跑构建/测试，再做结论")
    if assets and assets["hits"] == 0:
        print("  · 资产近 " + str(days) + " 天 0 命中：查触发条件写得太窄，或资产本身没价值")
    print("  资产状态 → 有命中且行为变好升「已验证」，长期 0 命中降「已停用」：assets.py list")


def main():
    mode = sys.argv[1] if len(sys.argv) > 1 else "list"
    on = sol.enabled()
    if mode == "review":
        review(int(sys.argv[2]) if len(sys.argv) > 2 else 7)
        return
    if mode == "status":
        print("已启用：" + (", ".join(sorted(on)) if on else "（无，探针只采集不插话）"))
        print("配置文件：" + sol.CONFIG)
        return
    if mode in ("enable", "disable"):
        if len(sys.argv) < 3:
            print(f"用法：cli.py {mode} <id>")
            return
        sid = sys.argv[2]
        known = {m.get("id") for m in all_solutions()}
        if sid not in known:
            print(f"未知方案：{sid}；可用：" + ", ".join(sorted(x for x in known if x)))
            return
        sol.enable(sid) if mode == "enable" else sol.disable(sid)
        print(f"{'已启用' if mode == 'enable' else '已停用'}：{sid}")
        return
    rows = all_solutions()
    print(f"{'ID':<22}{'状态':<8}{'启用':<5}名称 / 证据")
    for m in rows:
        sid = m.get("id", "?")
        flag = "✓" if sid in on else "—"
        print(f"{sid:<22}{m.get('status','未验证'):<8}{flag:<5}{m.get('name','')}")
        if m.get("evidence"):
            print(f"{'':<22}{'':<8}{'':<5}证据：{m['evidence']}")
    print("\n开关：python3 solutions/cli.py enable|disable <id>（数据目录下 solutions.json，立即生效）")


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        print(f"出错：{type(e).__name__}: {e}")
        sys.exit(1)
