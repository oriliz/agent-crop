"""Local dashboard for Agent Crop: stdlib-only HTTP server + one page."""

from __future__ import annotations

import json
import os
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import probe_core as core  # noqa: E402

PAGE = """<!doctype html>
<html lang="zh"><head><meta charset="utf-8">
<title>Agent Crop</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>
:root{color-scheme:dark}
body{margin:0;font:14px/1.5 -apple-system,system-ui,sans-serif;background:#0f1115;color:#e6e6e6}
header{padding:20px 28px;border-bottom:1px solid #23262d;display:flex;gap:16px;align-items:baseline}
h1{font-size:18px;margin:0;font-weight:600}
.sub{color:#8b93a1;font-size:12px}
main{padding:24px 28px;display:grid;gap:18px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:14px}
.card{background:#161920;border:1px solid #23262d;border-radius:10px;padding:14px 16px}
.card .k{color:#8b93a1;font-size:12px}
.card .v{font-size:22px;font-weight:600;margin-top:4px}
section{background:#161920;border:1px solid #23262d;border-radius:10px;padding:16px}
h2{font-size:13px;color:#8b93a1;margin:0 0 12px;font-weight:600;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #1e2128}
th{color:#8b93a1;font-weight:500}
tr.day{cursor:pointer}
tr.day:hover{background:#1b1f27}
.muted{color:#8b93a1}
ul{margin:0;padding-left:18px}
li{margin:4px 0}
#detail{white-space:pre-wrap}
</style></head><body>
<header><h1>Agent Crop</h1><span class="sub" id="asof"></span></header>
<main>
  <div><button class="range" data-days="7">7 天</button>
    <button class="range" data-days="14">14 天</button>
    <button class="range" data-days="30">30 天</button></div>
  <div class="cards" id="cards"></div>
  <section><h2>近 14 天（点一行看当天明细）</h2>
    <table><thead><tr><th>日期</th><th>回合</th><th>分钟</th><th>工具</th><th>失败</th><th>工作</th></tr></thead>
    <tbody id="days"></tbody></table></section>
  <section><h2>当天明细</h2><div id="detail" class="muted">点上面任意一行</div></section>
  <section><h2>同类工作对比（30 天，按类型）</h2>
    <table><thead><tr><th>类型</th><th>工作数</th><th>合计</th><th>中位时长</th></tr></thead><tbody id="types"></tbody></table></section>
  <section><h2>最花时间的三个工作（14 天）</h2><ul id="top"></ul></section>
  <section><h2>进行中的工作</h2><ul id="tasks"></ul></section>
</main>
<script>
let rangeDays = 14;
document.querySelectorAll('button.range').forEach(button => button.addEventListener('click', () => {
  rangeDays = Number(button.dataset.days) || 14;
  load();
}));
async function showDay(date, row){
  document.querySelectorAll('tr.day').forEach(el=>el.style.background='');
  row.style.background='#1b1f27';
  const r = await fetch('/api/day?date=' + date); const d = await r.json();
  const tools = (d.tools||[]).map(([n,c])=>`${n}×${c}`).join('、') || '无';
  const works = (d.works||[]).map(w=>`${w.goal||w.work_id}（${Math.round(w.minutes)} 分）`).join('、') || '无';
  document.getElementById('detail').textContent =
    `${d.date}：${d.blocks} 个回合，${Math.round(d.minutes)} 分钟，工具 ${d.events} 个事件，失败 ${d.failures} 次\n工具分布：${tools}\n涉及工作：${works}`;
}
async function load(){
  const r = await fetch('/api/summary?days=' + rangeDays); const d = await r.json();
  const s = d.status||{}, p = d.period||{};
  document.getElementById('asof').textContent = 'updated ' + new Date().toLocaleTimeString();
  const cards = [
    ['当前任务块', (s.current_block_minutes||0).toFixed(0) + ' 分'],
    ['今日事件', s.today_events||0],
    ['近 7 天回合', p.blocks||0],
    ['近 7 天时长', Math.round(p.total_minutes||0) + ' 分'],
    ['等你回话', Math.round((p.human_wait_ms||0)/60000) + ' 分'],
  ];
  document.getElementById('cards').innerHTML = cards.map(([k,v])=>`<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  document.getElementById('days').innerHTML = (d.daily||[]).slice().reverse().map(row=>
    `<tr class="day" data-date="${row.date}"><td>${row.date.slice(5)}</td><td>${row.blocks}</td><td>${Math.round(row.minutes)}</td><td>${row.tools}</td><td>${row.failures}</td><td>${row.works}</td></tr>`).join('');
  document.querySelectorAll('tr.day').forEach(row=>row.addEventListener('click',()=>showDay(row.dataset.date,row)));
  document.getElementById('types').innerHTML = (d.types||[]).map(row=>
    `<tr><td>${row.type}</td><td>${row.count}</td><td>${Math.round(row.minutes)} 分</td><td>${Math.round(row.median_minutes)} 分</td></tr>`).join('') || '<tr><td colspan="4" class="muted">暂无（先在 probe_report 里带 type）</td></tr>';
  document.getElementById('top').innerHTML = (p.top_works||[]).map(w=>`<li>${w.goal||w.work_id} <span class="muted">${Math.round(w.minutes)} 分</span></li>`).join('') || '<li class="muted">暂无</li>';
  document.getElementById('tasks').innerHTML = (s.open_tasks||[]).map(t=>`<li>${t.task_id} — ${t.title||t.summary||''}</li>`).join('') || '<li class="muted">暂无</li>';
}
load(); setInterval(load, 5000);
</script></body></html>
"""


def _summary(days: float = 14.0) -> dict:
    return {
        "status": core.status_summary(),
        "period": core.period_summary(days),
        "daily": core.daily_breakdown(days),
        "types": core.type_breakdown(max(days, 30.0)),
    }


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/day"):
            date = "1970-01-01"
            if "date=" in self.path:
                date = self.path.split("date=", 1)[1].split("&", 1)[0]
            try:
                payload = core.day_detail(date)
            except Exception as error:
                body = json.dumps({"error": str(error)}, ensure_ascii=False).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/session"):
            sid = ""
            if "sid=" in self.path:
                sid = self.path.split("sid=", 1)[1].split("&", 1)[0]
            payload = core.session_summary(sid)
            body = json.dumps(payload, ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/summary"):
            days = 14.0
            if "days=" in self.path:
                try:
                    days = float(self.path.split("days=", 1)[1].split("&", 1)[0])
                except ValueError:
                    days = 14.0
            body = json.dumps(_summary(days), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        body = PAGE.encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):  # quiet
        pass


def serve(host: str = "127.0.0.1", port: int = 8765) -> None:
    server = ThreadingHTTPServer((host, port), Handler)
    print(f"Agent Crop dashboard: http://{host}:{port}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Agent Crop local dashboard")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    options = parser.parse_args()
    serve(options.host, options.port)
