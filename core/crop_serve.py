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
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:14px}
.card{background:#161920;border:1px solid #23262d;border-radius:10px;padding:14px 16px}
.card .k{color:#8b93a1;font-size:12px}
.card .v{font-size:24px;font-weight:600;margin-top:4px}
section{background:#161920;border:1px solid #23262d;border-radius:10px;padding:16px}
h2{font-size:13px;color:#8b93a1;margin:0 0 12px;font-weight:600;letter-spacing:.04em}
.bars{display:flex;align-items:flex-end;gap:6px;height:120px}
.bar{flex:1;background:#3b82f6;border-radius:3px 3px 0 0;min-height:2px}
.labels{display:flex;gap:6px;color:#5b6270;font-size:10px;margin-top:6px}
.labels span{flex:1;text-align:center}
ul{margin:0;padding-left:18px}
li{margin:4px 0}
.muted{color:#8b93a1}
</style></head><body>
<header><h1>Agent Crop</h1><span class="sub" id="asof"></span></header>
<main>
  <div class="cards" id="cards"></div>
  <section><h2>近 14 天 每分钟总量</h2><div class="bars" id="bars"></div><div class="labels" id="labels"></div></section>
  <section><h2>最花时间的三个工作</h2><ul id="top"></ul></section>
  <section><h2>进行中的工作</h2><ul id="tasks"></ul></section>
</main>
<script>
async function load(){
  const r = await fetch('/api/summary'); const d = await r.json();
  const s = d.status || {}, p = d.period || {};
  document.getElementById('asof').textContent = 'updated ' + new Date().toLocaleTimeString();
  const cards = [
    ['当前任务块', (s.current_block_minutes||0).toFixed(0) + ' 分'],
    ['今日事件', s.today_events || 0],
    ['近 7 天回合', p.blocks || 0],
    ['近 7 天时长', Math.round(p.total_minutes||0) + ' 分'],
    ['等你回话', Math.round((p.human_wait_ms||0)/60000) + ' 分'],
  ];
  document.getElementById('cards').innerHTML = cards.map(([k,v]) =>
    `<div class="card"><div class="k">${k}</div><div class="v">${v}</div></div>`).join('');
  const days = Object.keys(p.minutes_by_day||{});
  const vals = Object.values(p.minutes_by_day||{});
  const max = Math.max(1, ...vals);
  document.getElementById('bars').innerHTML = vals.map(v =>
    `<div class="bar" style="height:${Math.round(v/max*100)}%" title="${v} 分"></div>`).join('');
  document.getElementById('labels').innerHTML = days.map(d => `<span>${d.slice(5)}</span>`).join('');
  document.getElementById('top').innerHTML = (p.top_works||[]).map(w =>
    `<li>${w.goal||w.work_id} <span class="muted">${Math.round(w.minutes)} 分</span></li>`).join('') || '<li class="muted">暂无</li>';
  document.getElementById('tasks').innerHTML = (s.open_tasks||[]).map(t =>
    `<li>${t.task_id} — ${t.title||t.summary||''}</li>`).join('') || '<li class="muted">暂无</li>';
}
load(); setInterval(load, 3000);
</script></body></html>
"""


def _summary() -> dict:
    return {"status": core.status_summary(), "period": core.period_summary(14)}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/api/summary"):
            body = json.dumps(_summary(), ensure_ascii=False).encode()
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
