"""Local dashboard for Agent Crop: stdlib-only HTTP server + one page."""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import datetime
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
header{padding:20px 28px;border-bottom:1px solid #23262d;display:flex;gap:16px;align-items:baseline;flex-wrap:wrap}
h1{font-size:18px;margin:0;font-weight:600}
.sub{color:#8b93a1;font-size:12px}
main{padding:24px 28px;display:grid;gap:18px;max-width:1000px}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:14px}
.card{background:#161920;border:1px solid #23262d;border-radius:10px;padding:14px 16px}
.card .k{color:#8b93a1;font-size:12px}
.card .v{font-size:22px;font-weight:600;margin-top:4px;font-variant-numeric:tabular-nums}
.card .n{color:#8b93a1;font-size:11px;margin-top:2px;min-height:16px}
section{background:#161920;border:1px solid #23262d;border-radius:10px;padding:16px}
h2{font-size:13px;color:#8b93a1;margin:0 0 12px;font-weight:600;letter-spacing:.04em}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 8px;border-bottom:1px solid #1e2128}
th{color:#8b93a1;font-weight:500;font-size:12px}
td.num{text-align:right;font-variant-numeric:tabular-nums}
th.num{text-align:right}
.muted{color:#8b93a1}
.range{background:#161920;border:1px solid #2a2f39;color:#aeb6c4;border-radius:7px;padding:5px 12px;font-size:12px;cursor:pointer}
.range.on{background:#233047;border-color:#3b5b8a;color:#dbe7ff}
.chart{display:flex;align-items:flex-end;gap:6px;height:180px;padding-top:6px}
.slot{flex:1;display:flex;flex-direction:column;justify-content:flex-end;align-items:center;height:100%;cursor:pointer;min-width:8px}
.slot:hover .bar{filter:brightness(1.25)}
.bar{width:100%;background:#3b5b8a;border-radius:3px 3px 0 0;min-height:2px}
.slot .cap{color:#6f7887;font-size:10px;margin-top:6px;white-space:nowrap}
.slot .val{color:#8b93a1;font-size:10px;margin-bottom:3px;font-variant-numeric:tabular-nums}
.dot{display:inline-block;width:7px;height:7px;border-radius:4px;margin-right:6px;vertical-align:middle}
footer{color:#6f7887;font-size:11px;padding:0 28px 24px}
#detail{white-space:pre-wrap;font-size:12px;line-height:1.7}
</style></head><body>
<header>
  <h1>Agent Crop · 全局</h1>
  <button class="range" data-days="1">今天</button>
  <button class="range on" data-days="7">7 天</button>
  <button class="range" data-days="30">30 天</button>
  <span class="sub" id="asof" style="margin-left:auto"></span>
</header>
<main>
  <div class="cards" id="cards"></div>
  <section><h2>每日活跃时长（分钟，点柱子看当天明细）</h2>
    <div class="chart" id="chart"></div>
    <div id="detail" class="muted" style="margin-top:12px">点上面任意一根柱子</div>
  </section>
  <section><h2>工作列表</h2>
    <table><thead><tr><th>灯</th><th>工作</th><th>类型</th><th>难度</th><th class="num">活跃</th><th class="num">等待</th></tr></thead>
      <tbody id="works"></tbody></table>
  </section>
</main>
<footer>灯色 = 该工作活跃时长 ÷ 同类型同难度基线：&lt; 0.6 绿 · 0.6–1.5 黄 · ≥ 1.5 红。数字由事件机械生成，归因只引用 AI 自述。</footer>
<script>
let rangeDays = 7;
const LAMP = {green:'#4ade80', yellow:'#eab308', red:'#ef4444'};

function esc(text){
  return String(text == null ? '' : text).replace(/[&<>"']/g, ch => (
    {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]
  ));
}
function fmtMinutes(value){
  if (value == null) return '—';
  const minutes = Math.round(value);
  if (minutes < 60) return minutes + ' 分';
  return Math.floor(minutes / 60) + ' 时 ' + (minutes % 60) + ' 分';
}
function dayLabel(date){
  const part = String(date || '').slice(5);
  return rangeDays <= 7 ? part : part;
}

document.querySelectorAll('button.range').forEach(button => button.addEventListener('click', () => {
  rangeDays = Number(button.dataset.days) || 7;
  document.querySelectorAll('button.range').forEach(other => other.classList.toggle('on', other === button));
  load();
}));

async function showDay(date){
  const response = await fetch('/api/day?date=' + date);
  const d = await response.json();
  if (d.error) { document.getElementById('detail').textContent = d.error; return; }
  const tools = (d.tools || []).map(([n, c]) => n + '×' + c).join('、') || '无';
  const works = (d.works || []).map(w => (w.goal || w.work_id) + '（' + Math.round(w.minutes) + ' 分）').join('、') || '无';
  document.getElementById('detail').textContent =
    d.date + '：' + d.blocks + ' 个回合，' + Math.round(d.minutes) + ' 分钟，失败 ' + d.failures + ' 次\\n' +
    '工具分布：' + tools + '\\n涉及工作：' + works;
}

function renderChart(period){
  const active = period.active_by_day || {};
  const start = new Date(period.start);
  start.setHours(0, 0, 0, 0);
  const slots = [];
  const values = [];
  const cursor = new Date(start);
  const today = new Date(); today.setHours(0, 0, 0, 0);
  while (cursor <= today && slots.length < 31) {
    const key = cursor.getFullYear() + '-' + String(cursor.getMonth() + 1).padStart(2, '0') + '-' + String(cursor.getDate()).padStart(2, '0');
    values.push({date: key, minutes: Number(active[key] || 0)});
    cursor.setDate(cursor.getDate() + 1);
  }
  const max = Math.max(1, ...values.map(item => item.minutes));
  document.getElementById('chart').innerHTML = values.map(item => {
    const height = Math.max(2, Math.round(item.minutes / max * 150));
    return '<div class="slot" data-date="' + item.date + '">' +
      '<div class="val">' + (item.minutes ? Math.round(item.minutes) : '') + '</div>' +
      '<div class="bar" style="height:' + height + 'px"></div>' +
      '<div class="cap">' + esc(dayLabel(item.date)) + '</div></div>';
  }).join('');
  document.querySelectorAll('.slot').forEach(slot =>
    slot.addEventListener('click', () => showDay(slot.dataset.date)));
}

function renderWorkList(rows){
  const body = document.getElementById('works');
  if (!rows || !rows.length) {
    body.innerHTML = '<tr><td colspan="6" class="muted">这段时间还没有工作记录。先用 probe_report 认领一条。</td></tr>';
    return;
  }
  body.innerHTML = rows.map(row => {
    const color = LAMP[row.level] || '#6b7280';
    const type = row.type || '<span class="muted">未标</span>';
    const difficulty = row.difficulty || '<span class="muted">未标</span>';
    return '<tr><td><span class="dot" style="background:' + color + '"></span>' + esc(row.level) + '</td>' +
      '<td>' + esc(row.goal || row.work_id) + '</td><td>' + type + '</td><td>' + difficulty + '</td>' +
      '<td class="num">' + fmtMinutes(row.minutes) + '</td>' +
      '<td class="num">' + (row.wait_minutes ? fmtMinutes(row.wait_minutes) : '—') + '</td></tr>';
  }).join('');
}

async function load(){
  const response = await fetch('/api/summary?days=' + rangeDays);
  const data = await response.json();
  const p = data.period || {};
  document.getElementById('asof').textContent = 'updated ' + new Date().toLocaleTimeString();
  const cards = [
    ['完成工作', p.completed || 0, '区间内收尾的工作'],
    ['总活跃时长', fmtMinutes(p.active_minutes), '不含等你回话'],
    ['平均每个工作', fmtMinutes(p.avg_active_minutes), '活跃时长 ÷ 完成数'],
    ['平均等待', fmtMinutes(p.avg_wait_minutes), '等待 ÷ 完成数'],
  ];
  document.getElementById('cards').innerHTML = cards.map(([k, v, note]) =>
    '<div class="card"><div class="k">' + k + '</div><div class="v">' + (v == null ? '—' : v) + '</div>' +
    '<div class="n">' + note + '</div></div>').join('');
  renderChart(p);
  renderWorkList(p.work_list);
}
load(); setInterval(load, 5000);
</script></body></html>
"""


def _range_start(days: float) -> int:
    """Natural-day start: 今天 = 0 点，7 天 = 含今天在内的 7 个自然日。"""
    days = max(1.0, float(days))
    return core.start_of_today_ms() - int((days - 1) * 86400000)


def _summary(days: float = 7.0) -> dict:
    days = float(days)
    if days <= 1:
        days = 1.0
    start = _range_start(days)
    return {
        "status": core.status_summary(),
        "period": core.period_summary(days, start=start),
        "daily": core.daily_breakdown(days, start=start),
        "types": core.type_breakdown(max(days, 30.0)),
    }


STARTED_AT = int(time.time() * 1000)


def _mtime_ms(path: str) -> int:
    try:
        return int(os.path.getmtime(path) * 1000)
    except OSError:
        return 0


def _health() -> dict:
    """Identity of this dashboard process, for the host's probe-and-reuse logic.

    The mtimes are the code this process is running, not what is on disk now; the
    host compares them with the files it would spawn and replaces only a stale
    process, so two DSH instances stop fighting over port 8765.
    """
    here = os.path.dirname(os.path.abspath(__file__))
    return {
        "ok": True,
        "pid": os.getpid(),
        "started_at": STARTED_AT,
        "serve_mtime": _mtime_ms(os.path.abspath(__file__)),
        "core_mtime": _mtime_ms(os.path.join(here, "probe_core.py")),
    }


LOOPBACK_ORIGIN = re.compile(r"^http://(127\.0\.0\.1|localhost|\[::1\])(:\d+)?$")


def _cors_headers(handler: BaseHTTPRequestHandler) -> list[tuple[str, str]]:
    """Only loopback origins may read this JSON from another origin.

    The DSH web client runs on a different port, so it needs CORS; a wildcard
    would let any page in the user's browser fetch the same data. Requests with
    no Origin header (curl, CLI, the same-origin page) need no CORS header at all.
    """
    origin = handler.headers.get("Origin")
    if origin and LOOPBACK_ORIGIN.match(origin):
        return [("Access-Control-Allow-Origin", origin), ("Vary", "Origin")]
    return []


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
            for name, value in _cors_headers(self):
                self.send_header(name, value)
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
            for name, value in _cors_headers(self):
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/health"):
            body = json.dumps(_health(), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for name, value in _cors_headers(self):
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/global"):
            days = 7.0
            if "days=" in self.path:
                try:
                    days = float(self.path.split("days=", 1)[1].split("&", 1)[0])
                except ValueError:
                    days = 7.0
            body = json.dumps(core.improvement_summary(days), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for name, value in _cors_headers(self):
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path.startswith("/api/summary"):
            days = 7.0
            if "days=" in self.path:
                try:
                    days = float(self.path.split("days=", 1)[1].split("&", 1)[0])
                except ValueError:
                    days = 7.0
            body = json.dumps(_summary(days), ensure_ascii=False).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            for name, value in _cors_headers(self):
                self.send_header(name, value)
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
    if host not in ("127.0.0.1", "localhost", "::1"):
        print(f"warning: {host} 不是回环地址，本机工作时间线会对局域网可见，只应临时使用", file=sys.stderr)
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
