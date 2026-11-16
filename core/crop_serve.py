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
.tab{background:#161920;border:1px solid #2a2f39;color:#aeb6c4;border-radius:7px;padding:5px 12px;font-size:12px;cursor:pointer}
.tab.on{background:#233047;border-color:#3b5b8a;color:#dbe7ff}
.view[hidden]{display:none}
.diag{display:grid;grid-template-columns:10px 1fr;gap:8px;padding:8px 0;border-bottom:1px solid #1e2128}
.diag:last-child{border-bottom:0}
.diag .t{font-size:13px}
.diag .e{color:#8b93a1;font-size:12px;margin-top:2px}
.diag .c{color:#6f7887;font-size:11px;margin-top:2px}
.trend{color:#6f7887;font-size:11px;letter-spacing:.04em}
.mini{color:#6f7887;font-size:10px;margin-top:2px}
.hint{color:#6f7887;font-size:11px;margin:-6px 0 10px;line-height:1.6}
tr.group td{color:#6f7887;font-size:11px;padding-top:14px;border-bottom:1px solid #23262d;letter-spacing:.04em}
tr.row-red td{background:rgba(239,68,68,.12)}
tr.row-red td:first-child{box-shadow:inset 3px 0 0 #ef4444}
tr.row-red .meter{outline:1px solid rgba(239,68,68,.35)}
.meter{position:relative;height:16px;background:#1e2128;border-radius:4px;overflow:hidden;min-width:96px}
.meter i{display:block;height:100%;opacity:.5}
.meter b{position:absolute;inset:0;display:flex;align-items:center;justify-content:center;font-weight:500;font-size:11px;color:#dbe7ff}
.spark{display:flex;align-items:flex-end;gap:2px;height:18px}
.spark i{display:block;width:6px;background:#3b5b8a;border-radius:1px 1px 0 0;min-height:2px}
.spark i.zero{background:#23262d}
.headline{background:#161920;border:1px solid #23262d;border-radius:10px;padding:10px 14px;font-size:13px;display:flex;align-items:center;gap:12px}
.copy{margin-left:auto;background:#233047;border:1px solid #3b5b8a;color:#dbe7ff;border-radius:7px;padding:4px 10px;font-size:12px;cursor:pointer;white-space:nowrap}
.copy:active{transform:translateY(1px)}
.tag{display:inline-block;border:1px solid #2a2f39;color:#8b93a1;border-radius:4px;padding:0 5px;font-size:10px;margin-left:6px;vertical-align:1px}
tr.sect td{color:#8b93a1;font-size:11px;padding:12px 8px 4px;border-bottom:1px solid #23262d;letter-spacing:.04em;cursor:pointer}
tr.sect .n{color:#6f7887;margin-left:6px}
tbody.sect-group.closed tr.row{display:none}
.spark-row{display:flex;align-items:center;gap:8px}
.spark-row .delta{font-size:11px}
.health-switch{display:flex;align-items:center;gap:8px}
.mode{background:#161920;border:1px solid #2a2f39;color:#aeb6c4;border-radius:7px;padding:5px 12px;font-size:12px;cursor:pointer}
.mode.on{background:#233047;border-color:#3b5b8a;color:#dbe7ff}
.health-switch .copy{margin-left:auto}
.strip{background:#161920;border:1px solid #23262d;border-radius:10px;padding:12px 14px;display:grid;gap:8px}
.strip-bar{display:flex;gap:3px;height:14px}
.strip-bar i{flex:1;border-radius:3px;min-width:4px}
.strip-legend{display:flex;gap:16px;color:#8b93a1;font-size:12px;flex-wrap:wrap}
.strip-legend b{color:#e6e6e6;font-weight:600}
.cockpit{display:grid;grid-template-columns:repeat(auto-fit,minmax(215px,1fr));gap:10px}
.tile{background:#161920;border:1px solid #23262d;border-radius:10px;padding:10px 12px;display:grid;gap:6px}
.tile-head{display:flex;align-items:center;gap:6px;font-size:12.5px}
.tile-head b{font-weight:600}
.tile-value{font-size:20px;font-variant-numeric:tabular-nums}
.tile-bar{height:8px;background:#1e2128;border-radius:4px;overflow:hidden}
.tile-bar i{display:block;height:100%}
.tile-foot{color:#6f7887;font-size:11px;line-height:1.5}
.tile.tone-red{border-color:#ef4444;background:rgba(239,68,68,.12);box-shadow:0 0 0 1px rgba(239,68,68,.35)}
.tile.tone-yellow{border-color:rgba(234,179,8,.5)}
.tile.tone-gray{opacity:.8;border-style:dashed}
.tile.tone-green .dot{background:#4ade80}
.tile.tone-red .dot{background:#ef4444}
.tile.tone-yellow .dot{background:#eab308}
.tile.tone-gray .dot{background:#6b7280}
.tile.tone-red .tile-value{color:#ff9b9b}
.compose-row{display:flex;align-items:center;gap:24px;flex-wrap:wrap}
.donut{width:148px;height:148px;border-radius:50%;position:relative;flex:none;background:conic-gradient(#23262d 0 100%)}
.donut-hole{position:absolute;inset:24px;background:#161920;border-radius:50%;display:grid;place-items:center;text-align:center;line-height:1.2}
.donut-hole b{font-size:24px;font-variant-numeric:tabular-nums}
.donut-hole span{color:#8b93a1;font-size:11px}
.compose-legend{display:grid;gap:8px;font-size:13px}
.legend-item{display:flex;align-items:center;gap:10px}
.legend-item b{font-variant-numeric:tabular-nums;min-width:28px;text-align:right}
.compose-note{color:#8b93a1;font-size:12px;margin-left:auto;max-width:360px;text-align:right;line-height:1.6}
.diag-actions{display:flex;justify-content:flex-end;margin-top:10px}
select.host{background:#161920;border:1px solid #2a2f39;color:#aeb6c4;border-radius:7px;padding:4px 8px;font-size:12px}
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
  <button class="tab on" data-view="overview">总览</button>
  <button class="tab" data-view="health">健康度</button>
  <select class="host" id="host" title="只看某个宿主"><option value="">全部宿主</option></select>
  <label class="sub" style="display:flex;align-items:center;gap:6px;cursor:pointer"><input type="checkbox" id="only-issues"> 只看异常</label>
  <span class="sub" id="asof" style="margin-left:auto"></span>
</header>
<main>
  <div id="view-overview" class="view">
  <div class="cards" id="cards"></div>
  <section><h2>每日活跃时长（分钟，点柱子看当天明细）</h2>
    <div class="chart" id="chart"></div>
    <div id="detail" class="muted" style="margin-top:12px">点上面任意一根柱子</div>
  </section>
  <section><h2>工作列表</h2>
    <table><thead><tr><th>灯</th><th>工作</th><th>类型</th><th>难度</th><th class="num">活跃</th><th class="num">等待</th></tr></thead>
      <tbody id="works"></tbody></table>
  </section>
  </div>
  <div id="view-health" class="view" hidden>
    <div class="cards" id="health-cards"></div>
    <section>
      <h2>健康构成</h2>
      <div class="compose-row">
        <div class="donut" id="health-donut"><div class="donut-hole"><b id="donut-center">—</b><span>正常</span></div></div>
        <div class="compose-legend" id="donut-legend"></div>
        <div class="compose-note" id="compose-note"></div>
      </div>
    </section>
    <section><h2>被动采集</h2>
      <div class="hint">覆盖 = 该类事件在自己锚点上的比例（会话 / 工作 / 收尾，悬停看 n/N）；趋势 = 每天的事件条数；红底 = 现在就该处理；灰条 = 按需，条件触发不评判。</div>
      <table class="health-table" id="table-passive"></table>
    </section>
    <section><h2>AI 主动上报</h2>
      <div class="hint">闭环 = 有开始的工作里已收尾的比例；覆盖 = 工作类口径；趋势同左。</div>
      <table class="health-table" id="table-active"></table>
    </section>
    <section><h2>诊断 · 哪些工具在一线是哑的</h2>
      <div id="health-diags"></div>
      <div class="diag-actions"><button class="copy" id="copy-diagnosis" title="复制成一段话，可直接丢给 AI 去修">复制修复指令</button></div>
    </section>
  </div>
</main>
<footer>灯色 = 该工作活跃时长 ÷ 同类型同难度基线：&lt; 0.6 绿 · 0.6–1.5 黄 · ≥ 1.5 红。数字由事件机械生成，归因只引用 AI 自述。<br>健康度：环形图 = 正常 / 偏低 / 要处理 / 按需+未上线 的构成；表格按必采、按需、未上线分组，覆盖条颜色 = 状态，红条所在行加红底。「只看异常」收掉正常行；「复制修复指令」把问题、证据、卡片拼成一段话，可直接丢给 AI。指标全部从事件派生，不新增采集。</footer>
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
    body.innerHTML = '<tr><td colspan="6" class="muted">这段时间还没有工作记录。先用 crop_update 认领一条。</td></tr>';
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

let view = 'overview';
let onlyIssues = false;
let lastHealth = null;

function setView(next){
  view = next;
  document.querySelectorAll('button.tab').forEach(button => button.classList.toggle('on', button.dataset.view === view));
  document.getElementById('view-overview').hidden = view !== 'overview';
  document.getElementById('view-health').hidden = view !== 'health';
  load();
}
document.querySelectorAll('button.tab').forEach(button =>
  button.addEventListener('click', () => setView(button.dataset.view)));

const hostSelect = document.getElementById('host');
if (hostSelect) hostSelect.addEventListener('change', () => load());
const issueToggle = document.getElementById('only-issues');
if (issueToggle) issueToggle.addEventListener('change', () => {
  onlyIssues = issueToggle.checked;
  if (lastHealth) renderHealth(lastHealth);
});

function pct(value){ return value == null ? '—' : Math.round(value * 100) + '%'; }

function missingNote(row){
  const entries = Object.entries(row.missing_fields || {});
  if (!entries.length) return '';
  return '<div class="mini">缺 ' + entries.map(([key, value]) => esc(key) + '×' + value).join('、') + '</div>';
}

function meterCell(options){
  const value = options.value;
  if (value == null) return '<td><span class="muted">' + (options.empty || '—') + '</span></td>';
  const width = Math.max(0, Math.min(100, Math.round(value * 100)));
  const note = options.note ? '<div class="mini">' + options.note + '</div>' : '';
  return '<td><div class="meter" style="background:' + options.color + (options.color === '#ef4444' ? '33' : '1f') +
    '" title="' + esc(options.title || '') + '"><i style="width:' + width + '%;background:' + options.color +
    '"></i><b>' + pct(value) + '</b></div>' + note + '</td>';
}

function coverageCell(row){
  if (row.status === 'not-shipped') return '<td><span class="muted">未上线</span></td>';
  if (row['class'] === 'on_demand') return '<td><span class="muted">—</span></td>';
  const thresholds = {session: 0.8, work: 0.7, ended: 0.8, call: 0.9};
  const value = row.session_coverage;
  const threshold = thresholds[row['class']] || 0.8;
  const color = value == null ? '#6b7280' : (value >= threshold ? '#4ade80' : (value > 0 ? '#eab308' : '#ef4444'));
  const note = row.coverage_denominator == null ? '' : row.coverage_numerator + ' / ' + row.coverage_denominator;
  return meterCell({value: value, color: color, note: note, title: note});
}

function completenessCell(row){
  if (!row.events) return '<td><span class="muted">—</span></td>';
  const value = row.field_completeness;
  const color = value >= 0.95 ? '#e6e6e6' : (value > 0 ? '#eab308' : '#ef4444');
  return '<td style="color:' + color + '">' + pct(value) + missingNote(row) + '</td>';
}

function closureCell(row, works){
  if (row.stage_closure == null) return '<td><span class="muted">—</span></td>';
  const value = row.stage_closure;
  const color = value >= 0.7 ? '#4ade80' : '#eab308';
  const note = '已收尾 ' + (works.ended || 0) + ' / 开始 ' + (works.started || 0);
  return meterCell({value: value, color: color, note: note, title: note});
}

function sparkline(rows){
  if (!rows || !rows.length) return '—';
  const max = Math.max(1, ...rows.map(row => row.count));
  return '<div class="spark" title="每天的事件条数（7 天，悬停看某一天）">' + rows.map(row =>
    '<i' + (row.count ? '' : ' class="zero"') + ' style="height:' + Math.max(2, Math.round(row.count / max * 18)) +
    'px" title="' + esc(row.date + '：' + row.count + ' 条') + '"></i>').join('') + '</div>';
}

function deltaText(row){
  const delta = row.delta_vs_previous;
  if (delta == null) return row.events ? '<span class="muted">无上周</span>' : '—';
  if (delta === 0) return '持平';
  const up = delta > 0;
  const color = up ? '#4ade80' : '#ef4444';
  return '<span style="color:' + color + '">' + (up ? '↑' : '↓') + Math.abs(Math.round(delta * 100)) + '%</span>';
}

function redRow(row){
  return row.lamp === 'red' || (row.events > 0 && row.session_coverage === 0);
}

function rowHtml(row, showClosure, hasPrevious, works){
  return '<tr class="row' + (redRow(row) ? ' row-red' : '') + '">' +
    '<td>' + esc(row.label) + '<span class="muted" style="margin-left:6px">' + esc(row.tool) + '</span>' +
    (row.note ? '<div class="mini">' + esc(row.note) + '</div>' : '') + '</td>' +
    '<td class="muted">' + esc(row.scope || '') + '</td>' +
    '<td class="num">' + row.events + '</td>' +
    coverageCell(row) +
    completenessCell(row) +
    (showClosure ? closureCell(row, works) : '') +
    '<td>' + sparkline(row.trend) + '</td>' +
    (hasPrevious ? '<td class="num">' + deltaText(row) + '</td>' : '') + '</tr>';
}

function groupOf(row){
  if (row.status === 'not-shipped') return 'not-shipped';
  if (row['class'] === 'on_demand') return 'on-demand';
  return 'required';
}

function renderTable(tableId, rows, showClosure, hasPrevious, works){
  const definitions = [
    {id: 'required', title: '必采 · 直接决定数据能不能用'},
    {id: 'on-demand', title: '按需 · 条件触发，只看有没有在采'},
    {id: 'not-shipped', title: '未上线'},
  ];
  const visible = onlyIssues ? rows.filter(row => row.lamp === 'red' || row.lamp === 'yellow') : rows;
  let head = '<thead><tr><th>工具</th><th>口径</th><th class="num">事件</th><th>覆盖</th><th>完整</th>' +
    (showClosure ? '<th>闭环</th>' : '') + '<th>趋势（7 天）</th>' + (hasPrevious ? '<th class="num">比上周</th>' : '') +
    '</tr></thead>';
  let body = '';
  definitions.forEach(definition => {
    const items = visible.filter(row => groupOf(row) === definition.id);
    if (!items.length) return;
    body += '<tr class="sect"><td colspan="' + (showClosure ? 8 : 7) + '">' + definition.title +
      '<span class="n">' + items.length + '</span></td></tr>';
    body += items.map(row => rowHtml(row, showClosure, hasPrevious, works)).join('');
  });
  const span = showClosure ? 8 : 7;
  document.getElementById(tableId).innerHTML = head + '<tbody>' +
    (body || '<tr><td colspan="' + span + '" class="muted">没有异常。</td></tr>') + '</tbody>';
}

function renderDonut(data){
  const rows = (data.passive || []).concat(data.active || []);
  const thresholds = {session: 0.8, work: 0.7, ended: 0.8, call: 0.9};
  const counts = {green: 0, yellow: 0, red: 0, gray: 0};
  rows.forEach(row => {
    if (row.status === 'not-shipped' || row['class'] === 'on_demand') { counts.gray += 1; return; }
    if (redRow(row)) counts.red += 1;
    else if (row.lamp === 'yellow') counts.yellow += 1;
    else counts.green += 1;
  });
  const total = rows.length || 1;
  const order = [['green', '#4ade80'], ['yellow', '#eab308'], ['red', '#ef4444'], ['gray', '#3a3f4a']];
  let cursor = 0;
  const stops = [];
  order.forEach(([key, color]) => {
    const share = counts[key] / total * 100;
    if (!counts[key]) return;
    stops.push(color + ' ' + cursor + '% ' + (cursor + share) + '%');
    cursor += share;
  });
  document.getElementById('health-donut').style.background =
    'conic-gradient(' + (stops.length ? stops.join(',') : '#23262d 0 100%') + ')';
  document.getElementById('donut-center').textContent = counts.green + ' / ' + rows.length;
  const legend = [
    ['#4ade80', '正常', counts.green],
    ['#eab308', '偏低', counts.yellow],
    ['#ef4444', '要处理', counts.red],
    ['#3a3f4a', '按需 / 未上线', counts.gray],
  ];
  document.getElementById('donut-legend').innerHTML = legend.map(([color, label, count]) =>
    '<div class="legend-item"><span class="dot" style="background:' + color + '"></span>' +
    '<b>' + count + '</b><span class="muted">' + label + '</span></div>').join('');
  document.getElementById('compose-note').textContent = (data.summary || {}).headline || '';
}

function syncHostOptions(hosts){
  const select = document.getElementById('host');
  if (!select) return;
  const current = select.value;
  const options = ['<option value="">全部宿主</option>'].concat(
    hosts.map(host => '<option value="' + esc(host) + '">' + esc(host) + '</option>'));
  select.innerHTML = options.join('');
  select.value = hosts.includes(current) ? current : '';
}

function renderDiags(data){
  const diags = data.diagnostics || [];
  document.getElementById('health-diags').innerHTML = diags.length ? diags.map(diagnostic => {
    const color = diagnostic.severity === 'info' ? '#6b7280'
      : (diagnostic.severity === 'error' ? '#ef4444' : '#eab308');
    const card = diagnostic.card ? '<span class="tag">卡 ' + esc(diagnostic.card) + '</span>' : '';
    return '<div class="diag"><span class="dot" style="background:' + color + '"></span><div>' +
      '<div class="t">' + esc(diagnostic.title) + '</div>' +
      '<div class="e">' + esc(diagnostic.evidence) + '</div>' +
      '<div class="c">' + esc(diagnostic.cause) + card + '</div></div></div>';
  }).join('') : '<div class="muted">没有发现问题。</div>';
}

function repairBriefing(data){
  const s = data.summary || {};
  const lines = ['Crop 健康度：' + (s.headline || '')];
  (data.diagnostics || []).filter(d => d.severity !== 'info').forEach(d => {
    lines.push('- ' + d.title + '：' + d.evidence + (d.card ? '（卡 ' + d.card + '）' : ''));
  });
  return lines.join('\\n');
}

function copyRepairBriefing(){
  if (!lastHealth) return;
  const text = repairBriefing(lastHealth);
  const done = () => {
    const button = document.getElementById('copy-diagnosis');
    button.textContent = '已复制';
    setTimeout(() => { button.textContent = '复制修复指令'; }, 1500);
  };
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(done).catch(() => fallbackCopy(text, done));
  } else {
    fallbackCopy(text, done);
  }
}

function fallbackCopy(text, done){
  const area = document.createElement('textarea');
  area.value = text;
  document.body.appendChild(area);
  area.select();
  try { document.execCommand('copy'); done(); } catch (error) { /* ignore */ }
  document.body.removeChild(area);
}

document.getElementById('copy-diagnosis').addEventListener('click', copyRepairBriefing);

function renderHealth(data){
  lastHealth = data;
  const s = data.summary || {};
  const sessions = data.sessions || {};
  const works = data.works || {};
  const hasPrevious = Boolean(s.has_previous);
  const cards = [
    ['有效会话', sessions.eligible || 0, '共 ' + (sessions.total || 0) + ' 个，短会话不计'],
    ['工作', (works.ended || 0) + ' / ' + (works.started || 0), '已收尾 / 已开始'],
    ['必采达标', (s.required_ok || 0) + ' / ' + (s.required_total || 0), '覆盖与字段都达标才算'],
    ['异常工具', s.abnormal_tools || 0, '红 + 黄，按需事件不计'],
  ];
  document.getElementById('health-cards').innerHTML = cards.map(([k, v, note]) =>
    '<div class="card"><div class="k">' + k + '</div><div class="v">' + (v == null ? '—' : v) + '</div>' +
    '<div class="n">' + note + '</div></div>').join('');
  renderDonut(data);
  renderTable('table-passive', data.passive || [], false, hasPrevious, works);
  renderTable('table-active', data.active || [], true, hasPrevious, works);
  renderDiags(data);
  syncHostOptions(data.hosts || []);
}

async function loadHealth(){
  const select = document.getElementById('host');
  const host = select ? select.value : '';
  const query = '/api/tool-health?days=' + rangeDays + (host ? '&host=' + encodeURIComponent(host) : '');
  const response = await fetch(query);
  const data = await response.json();
  document.getElementById('asof').textContent = 'updated ' + new Date().toLocaleTimeString();
  renderHealth(data);
}

async function load(){
  if (view === 'health') { await loadHealth(); return; }
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
        if self.path.startswith("/api/tool-health"):
            days = 7.0
            if "days=" in self.path:
                try:
                    days = float(self.path.split("days=", 1)[1].split("&", 1)[0])
                except ValueError:
                    days = 7.0
            days = max(1.0, days)
            host = None
            if "host=" in self.path:
                host = self.path.split("host=", 1)[1].split("&", 1)[0] or None
            payload = core.health_summary(days, start=_range_start(days), host_filter=host)
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
