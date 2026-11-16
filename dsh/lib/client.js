/**
 * DSH web client half for Agent Crop.
 *
 * Two surfaces, one data source served by the DSH host itself on its own port
 * (`/api/agent-crop/*`, relayed from the Python core — no separate dashboard
 * process, so every host instance has its own endpoint):
 *
 *  - a conversation.view tab next to 轨迹: is this work going to finish smoothly,
 *    what does the AI itself say about the slowdown, and which tool is the
 *    bottleneck — scoped to the session in front of you;
 *  - a global panel (sidebar row + center-column page, the same seats the
 *    shipped 插件 / 任务看板 pages ride): active minutes by day, the work list and
 *    a per-day breakdown — scoped to everything.
 *
 * Hand-written in the client module system's lazy-CJS factory format.
 *
 * Every section of both surfaces is a frosted-glass card. The recipe is
 * deliberately the same one the wallpaper skins paint behind message text
 * (rgb(242 245 250 / bubble-alpha) in light, rgb(16 22 42 / bubble-alpha * .8)
 * in dark, blurred by bubble-blur), so these views read as part of the page
 * instead of a flat panel over the illustration. Both knobs come from the skin
 * center's Background card and fall back to its defaults; on a skinless install
 * the fallbacks still give a readable card, and the tokens below follow the
 * active theme either way.
 */
window.__ModuleLoader__.load({
  id: 'agent-crop-dsh',
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    const React = require('react');
    const h = React.createElement;

    const VIEW_ID = 'crop';
    const PANEL_ID = 'crop-global';
    const PANEL_ORDER = 25;
    // The host answers these itself (same origin, one per host instance);
    // there is no separate dashboard process to talk to.
    const API = '/api/agent-crop';
    const ENDPOINT = `${API}/session`;
    const STYLE_ID = 'agent-crop-crop-view-style';

    function lampColor(ratio) {
      const r = Math.max(0, Math.min(1.5, ratio || 0));
      const hue = r < 0.6 ? 140 - (140 - 48) * (r / 0.6) : 48 - 48 * ((r - 0.6) / 0.9);
      return `hsl(${hue.toFixed(0)} 72% 52%)`;
    }

    const VERDICT = {
      justified: { label: '合理超时', color: 'hsl(48 72% 52%)' },
      'not-justified': { label: '不合理', color: 'hsl(0 72% 52%)' },
      unknown: { label: '说不清', color: 'rgba(255,255,255,.4)' },
    };

    /* Enum values are wire vocabulary; the panel is Chinese. Keep one table
       so every surface (works table, pitfalls, reflection) reads the same. */
    const ENUM_LABELS = {
      verdict: { justified: '不用改', 'not-justified': '要改', unknown: '未判定' },
      category: {
        'serial-wait': '串行等待',
        'repeat-pitfall': '重复踩坑',
        'design-churn': '设计反复',
        'decision-batching': '决策批处理',
        'tool-misuse': '工具误用',
        'no-evidence': '无证据',
        'spec-drift': '需求漂移',
        'human-wait': '等人',
        other: '其他',
      },
      type: { design: '设计', implement: '实现', debug: '排错', docs: '文档' },
      difficulty: { s: '小', m: '中', l: '大' },
      rework: { redo: '重做', major: '大改', minor: '小修', none: '不需要' },
      sentiment: { pos: '积极', neu: '中性', neg: '消极' },
      level: { green: '正常', yellow: '偏低', red: '要处理', gray: '按需' },
    };

    const enumLabel = (kind, value) => (ENUM_LABELS[kind] && ENUM_LABELS[kind][value]) || value || '';
    const PERSONA_LABEL = { owner: '奴隶主', senior: '和蔼的前辈', colleague: '经验丰富的同事', free: '放任自由的家长' };
    const personaIcon = (persona) => (persona && persona !== 'owner' ? '/api/agent-crop/think-icon' : '/api/agent-crop/whip-icon');


    const LAMP = { green: '#4ade80', yellow: '#eab308', red: '#ef4444' };

    /* Glass recipe shared with the wallpaper skins' message plates. The colour
       literals are the skins' own (242 245 250 light / 16 22 42 dark); the alpha
       and the blur ride the skin center's two sliders, exactly like the plates
       behind conversation text, so the cards thicken or thin with them. */
    const VIEW_CSS = `
.crop-page {
  padding: 22px 26px;
  display: grid;
  gap: 14px;
  align-content: start;
  width: 100%;
  max-width: 1120px;
  box-sizing: border-box;
  font-size: 13px;
}

.crop-card {
  border-radius: 10px;
  padding: 14px 16px;
  background: rgb(242 245 250 / var(--dsh-skin-bubble-alpha, .5));
  -webkit-backdrop-filter: blur(var(--dsh-skin-bubble-blur, 10px)) saturate(1.3);
  backdrop-filter: blur(var(--dsh-skin-bubble-blur, 10px)) saturate(1.3);
  box-shadow: 0 1px 3px rgba(28, 37, 70, .08);
}

body[data-ds-dark-theme] .crop-card {
  background: rgb(16 22 42 / calc(var(--dsh-skin-bubble-alpha, .5) * .8));
  box-shadow: 0 1px 3px rgba(0, 0, 0, .28);
}

/* The verdict keeps its red/amber/grey accent as a left edge on the same card. */
.crop-card--verdict {
  border-left: 3px solid transparent;
}

.crop-muted {
  color: var(--dsw-alias-label-tertiary, #5f6e93);
}

/* --- global panel -------------------------------------------------------- */

/* The layout mounts this as the center-column page, so the element owns the
   scroll: the shell's column gives it the height and nothing else. */
.crop-global {
  height: 100%;
  box-sizing: border-box;
  overflow: auto;
  padding: 22px 26px;
  display: grid;
  gap: 14px;
  align-content: start;
  max-width: 1120px;
  font-size: 13px;
}

.crop-global-head {
  display: flex;
  align-items: baseline;
  gap: 12px;
  flex-wrap: wrap;
}

.crop-global-title {
  font-size: 15px;
  font-weight: 600;
  margin: 0;
}

.crop-chips {
  display: inline-flex;
  gap: 6px;
}

.crop-chip {
  border: 1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .16));
  border-radius: 999px;
  padding: 3px 12px;
  font: inherit;
  font-size: 12px;
  color: inherit;
  background: transparent;
  cursor: pointer;
}

.crop-chip:hover {
  background: var(--dsw-alias-interactive-bg-hover, rgba(74, 95, 168, .08));
}

.crop-chip[data-on] {
  background: var(--dsw-alias-interactive-bg-active, rgba(74, 95, 168, .16));
  border-color: var(--dsw-alias-state-business-primary, #4a5fa8);
}

.crop-global-asof {
  margin-left: auto;
  font-size: 11px;
}

.crop-metrics {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(150px, 1fr));
  gap: 12px;
}

.crop-metric-k {
  font-size: 12px;
}

.crop-metric-v {
  font-size: 22px;
  font-weight: 600;
  margin-top: 4px;
  font-variant-numeric: tabular-nums;
}

.crop-metric-n {
  font-size: 11px;
  margin-top: 2px;
  min-height: 16px;
}

.crop-chart {
  display: flex;
  align-items: flex-end;
  gap: 6px;
  height: 180px;
  padding-top: 6px;
}

.crop-slot {
  flex: 1;
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  align-items: center;
  height: 100%;
  min-width: 8px;
  padding: 0;
  border: 0;
  background: none;
  color: inherit;
  font: inherit;
  cursor: pointer;
}

.crop-slot:hover .crop-bar {
  filter: brightness(1.25);
}

.crop-bar {
  width: 100%;
  min-height: 2px;
  border-radius: 3px 3px 0 0;
  background: #3b6bb0;
}

.crop-day-val {
  font-size: 10px;
  margin-bottom: 3px;
  font-variant-numeric: tabular-nums;
}

.crop-day-cap {
  font-size: 10px;
  margin-top: 6px;
  white-space: nowrap;
}

.crop-day {
  margin-top: 12px;
  padding-top: 12px;
  border-top: 1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .16));
  display: grid;
  gap: 10px;
}

.crop-day-head {
  display: flex;
  align-items: center;
  gap: 8px;
  font-size: 12px;
}

.crop-stat {
  border: 1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .16));
  border-radius: 999px;
  padding: 1px 8px;
  font-variant-numeric: tabular-nums;
}

.crop-x {
  margin-left: auto;
  border: 0;
  background: none;
  color: inherit;
  opacity: .6;
  cursor: pointer;
  font-size: 16px;
  line-height: 1;
  padding: 2px 6px;
  border-radius: 6px;
}

.crop-x:hover {
  opacity: 1;
  background: var(--dsw-alias-interactive-bg-hover, rgba(74, 95, 168, .08));
}

.crop-day-section {
  display: flex;
  align-items: flex-start;
  gap: 10px;
  font-size: 12px;
}

.crop-day-label {
  flex: none;
  width: 30px;
  line-height: 22px;
}

.crop-day-body {
  flex: 1;
  min-width: 0;
}

.crop-work-rows {
  display: grid;
  gap: 3px;
}

.crop-work-row {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  min-width: 0;
  line-height: 18px;
}

.crop-work-name {
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}

.crop-work-min {
  flex: none;
  opacity: .8;
  font-variant-numeric: tabular-nums;
}

.crop-more {
  border: 0;
  background: none;
  color: inherit;
  opacity: .7;
  cursor: pointer;
  font: inherit;
  font-size: 11px;
  padding: 0;
  margin-top: 2px;
}

.crop-work-title {
  min-width: 160px;
}

/* The global page is a data surface, not prose: its cards sit a little denser
   than the message plates (same tint family, higher alpha) and carry a hairline
   edge so numbers keep contrast where the wallpaper is bright. The skin's two
   sliders still drive them. */
.crop-global .crop-card {
  background: rgb(242 245 250 / min(1, calc(var(--dsh-skin-bubble-alpha, .5) * 1.7)));
  border: 1px solid rgba(255, 255, 255, .35);
  box-shadow: 0 2px 12px rgba(20, 30, 60, .10);
}

body[data-ds-dark-theme] .crop-global .crop-card {
  background: rgb(16 22 42 / min(1, calc(var(--dsh-skin-bubble-alpha, .5) * 2 * .8)));
  border-color: rgba(160, 180, 230, .18);
  box-shadow: 0 2px 12px rgba(0, 0, 0, .28);
}

.crop-delta {
  font-size: 11px;
  margin-top: 4px;
}

.crop-good {
  color: var(--dsw-alias-state-success-primary, #2e8e52);
}

.crop-bad {
  color: var(--dsw-alias-state-error-primary, #c00000);
}

.crop-bar-stack {
  display: flex;
  flex-direction: column;
  justify-content: flex-end;
  width: 100%;
  max-width: 56px;
  border-radius: 3px;
  overflow: hidden;
}

.crop-bar--active {
  background: #3b6bb0;
}

.crop-bar--wait {
  background: #d9a94f;
}

.crop-bar--empty {
  background: var(--dsw-alias-border-l2, rgba(140, 156, 188, .6));
  opacity: .45;
}

.crop-slot[data-on] .crop-bar-stack {
  box-shadow: 0 0 0 1px rgba(120, 170, 255, .75);
}

.crop-legend {
  display: flex;
  align-items: center;
  gap: 14px;
  font-size: 11px;
  margin-top: 10px;
}

.crop-dot {
  display: inline-block;
  width: 8px;
  height: 8px;
  border-radius: 2px;
  margin-right: 5px;
}

.crop-dot--active {
  background: #3b6bb0;
}

.crop-dot--wait {
  background: #d9a94f;
}

.crop-legend-note {
  margin-left: auto;
}

.crop-list {
  margin: 0;
  padding-left: 18px;
  display: grid;
  gap: 6px;
  font-size: 12px;
  line-height: 1.6;
}

.crop-tags {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}

.crop-tag {
  border: 1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .16));
  border-radius: 999px;
  padding: 2px 10px;
  font-size: 11px;
}

/* --- health view --------------------------------------------------------- */

.crop-modes {
  display: inline-flex;
  gap: 6px;
  margin-left: auto;
}

.crop-health-head {
  display: flex;
  align-items: center;
  gap: 18px;
  flex-wrap: wrap;
}

.crop-donut {
  width: 132px;
  height: 132px;
  border-radius: 50%;
  position: relative;
  flex: none;
}

.crop-donut-hole {
  position: absolute;
  inset: 22px;
  border-radius: 50%;
  background: rgb(242 245 250 / .92);
  display: grid;
  place-items: center;
  text-align: center;
  line-height: 1.25;
}

body[data-ds-dark-theme] .crop-donut-hole {
  background: rgb(16 22 42 / .92);
}

.crop-donut-hole b {
  font-size: 21px;
  font-variant-numeric: tabular-nums;
}

.crop-donut-hole span {
  font-size: 11px;
  opacity: .65;
}

.crop-legend-list {
  display: grid;
  gap: 7px;
  font-size: 12px;
}

.crop-legend-row {
  display: flex;
  align-items: center;
  gap: 8px;
}

.crop-legend-row b {
  min-width: 24px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.crop-health-note {
  margin-left: auto;
  max-width: 340px;
  text-align: right;
  font-size: 12px;
  opacity: .78;
  line-height: 1.6;
}

.crop-meter {
  position: relative;
  height: 12px;
  border-radius: 4px;
  background: rgba(100, 116, 160, .16);
  min-width: 64px;
  overflow: hidden;
}

.crop-health-table {
  table-layout: fixed;
}

.crop-meter i {
  display: block;
  height: 100%;
  opacity: .55;
}

.crop-meter b {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  font-size: 10.5px;
  font-weight: 500;
}

.crop-row-red td {
  background: rgba(239, 68, 68, .10);
}

.crop-sect td {
  color: var(--dsw-alias-label-tertiary, #5f6e93);
  font-size: 11px;
  padding: 10px 6px 3px;
  border-bottom: 1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .12));
}

.crop-mini {
  opacity: .6;
  font-size: 10.5px;
  margin-top: 2px;
}

.crop-diag {
  display: grid;
  grid-template-columns: 10px 1fr;
  gap: 8px;
  padding: 8px 0;
  border-bottom: 1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .12));
}

.crop-diag:last-child {
  border-bottom: 0;
}

.crop-diag .t {
  font-size: 12.5px;
}

.crop-diag .e,
.crop-diag .c {
  color: var(--dsw-alias-label-tertiary, #5f6e93);
  font-size: 11.5px;
  line-height: 1.55;
}

.crop-spark {
  display: inline-flex;
  align-items: flex-end;
  gap: 2px;
  height: 16px;
}

.crop-spark i {
  display: block;
  width: 5px;
  border-radius: 1px 1px 0 0;
  background: rgba(96, 128, 200, .55);
  min-height: 2px;
}

/* --- session timeline ------------------------------------------------ */
.crop-sess-head { display: flex; align-items: center; gap: 14px; padding: 12px 16px; flex-wrap: wrap; }
.crop-sess-title { font-size: 15px; font-weight: 600; }
.crop-sess-clock { display: flex; align-items: baseline; gap: 8px; font-size: 22px; font-weight: 650; font-variant-numeric: tabular-nums; }
.crop-sess-clock .crop-muted { font-size: 11px; font-weight: 400; }
.crop-sess-split { display: flex; width: 180px; height: 5px; border-radius: 999px; overflow: hidden; background: rgba(150, 165, 205, .14); }
.crop-sess-split i { height: 100%; }
.crop-sess-split i.work { background: #5b8cff; }
.crop-sess-split i.wait { background: rgba(150, 165, 205, .42); }
.crop-sess-turn { margin-left: auto; font-size: 12px; color: #b9c6e2; }
.crop-sess-stats { flex: 1 0 100%; display: flex; flex-wrap: wrap; gap: 6px; margin-top: 4px; }
.crop-stat-chip { font-size: 10.5px; border: 1px solid rgba(160,180,230,.18); border-radius: 999px; padding: 1px 8px; color: #98a6c6; }
.crop-stat-chip b { margin-left: 4px; color: #e8edf8; font-variant-numeric: tabular-nums; }
.crop-persona-chip { font-size: 11px; border: 1px solid rgba(160,180,230,.22); border-radius: 999px; padding: 1px 8px; color: #a9b6d4; }
.crop-small { font-size: 11.5px; }
.crop-sess-current { display: flex; align-items: center; gap: 12px; padding: 10px 16px; }
.crop-sess-label { width: 56px; flex: none; font-size: 11.5px; color: #8f9dbe; }
.crop-sess-name { font-size: 13px; font-weight: 600; max-width: 38%; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.crop-sess-bar { flex: 1; height: 8px; border-radius: 999px; background: rgba(150, 165, 205, .16); overflow: hidden; }
.crop-sess-bar i { display: block; height: 100%; background: linear-gradient(90deg, #4f7cf0, #6ea0ff); }
.crop-tl-card { padding: 12px 16px 10px; }
.crop-tl-head { display: flex; align-items: center; justify-content: space-between; margin-bottom: 4px; }
.crop-tl-legend { display: flex; align-items: center; gap: 12px; font-size: 11px; color: #98a6c6; }
.crop-swatch { display: inline-block; width: 12px; height: 6px; border-radius: 2px; background: #5b8cff; margin-right: 5px; }
.crop-legend-whip { width: 16px; height: 16px; object-fit: contain; vertical-align: middle; margin-right: 4px; }
.crop-dot-red { display: inline-block; width: 8px; height: 8px; border-radius: 50%; background: #f08484; margin-right: 5px; }
.crop-tl-body { display: grid; gap: 4px; }
.crop-tl-drag { cursor: grab; user-select: none; }
.crop-tl-drag:active { cursor: grabbing; }
.crop-tl-zoom { display: inline-flex; gap: 4px; }
.crop-tl-zoom .crop-chip { font-size: 10.5px; padding: 1px 7px; }
.crop-tl-hint { margin-top: 4px; opacity: .7; }
.crop-tl-row { display: grid; grid-template-columns: 160px 1fr; gap: 10px; align-items: end; }
.crop-tl-label { font-size: 12px; padding-bottom: 4px; overflow: hidden; }
.crop-tl-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.crop-tl-meta { font-size: 10.5px; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.crop-tl-label--current { color: #cfe0ff; }
.crop-tl-track { position: relative; height: 34px; border-radius: 6px; background: rgba(120, 140, 190, .07); margin-top: 6px; }
.crop-tl-grid { position: absolute; inset: 0; background-image: repeating-linear-gradient(90deg, rgba(160, 180, 230, .10) 0 1px, transparent 1px 10%); }
.crop-seg { position: absolute; top: 8px; height: 14px; border-radius: 3px; background: linear-gradient(180deg, #5b8cff, #4a77e0); cursor: pointer; }
.crop-seg--picked { outline: 1.5px solid rgba(240, 212, 138, .9); }
.crop-seg--wait { background: repeating-linear-gradient(135deg, rgba(150, 165, 205, .34) 0 4px, rgba(150, 165, 205, .16) 4px 8px); }
.crop-tl-axis { position: relative; height: 14px; margin-top: 4px; margin-left: 170px; font-size: 10px; color: #7785a6; }
.crop-tl-axis span { position: absolute; transform: translateX(-50%); }
.crop-tl-axis span:first-child { transform: none; }
.crop-tl-axis span:last-child { transform: translateX(-100%); }
.crop-whip { position: absolute; bottom: 12px; height: 22px; cursor: pointer; transition: transform .15s ease; width: auto; transform: translateX(-50%); filter: drop-shadow(0 2px 4px rgba(0, 0, 0, .45)); }
.crop-whip:hover, .crop-whip--hl { transform: translateX(-50%) scale(1.35); filter: drop-shadow(0 0 8px rgba(240,212,138,.9)); z-index: 2; }
.crop-lane-whips { color: #e8c46a; }
.crop-whip-count { position: absolute; bottom: 31px; transform: translateX(4px); font-size: 9px; line-height: 13px; padding: 0 4px; border-radius: 999px; background: #e8c46a; color: #1a1608; }
.crop-blocked-dot { position: absolute; bottom: 9px; width: 9px; height: 9px; border-radius: 50%; background: #f08484; border: 1px solid rgba(255, 255, 255, .45); transform: translateX(-50%); }
.crop-tl-detail { margin-top: 10px; padding-top: 8px; border-top: 1px solid rgba(160, 180, 230, .12); display: flex; gap: 10px; align-items: baseline; font-size: 11.5px; }
.crop-tl-range { color: #f0a9a9; font-variant-numeric: tabular-nums; }
.crop-work-row { display: flex; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid rgba(160, 180, 230, .08); font-size: 12px; }
.crop-work-row:first-child { border-top: 0; }
.crop-dot { display: inline-block; width: 7px; height: 7px; border-radius: 50%; }
.crop-tl-cap { margin-bottom: 6px; }
.crop-reflect { padding: 12px 16px; font-size: 12px; }
.crop-reflect-head { display: flex; flex-wrap: wrap; align-items: center; gap: 8px; margin-bottom: 6px; }
.crop-verdict-tag { white-space: nowrap; font-size: 12.5px; line-height: 22px; padding: 0 12px; border-radius: 999px; border: 1px solid rgba(160, 180, 230, .28); color: #c6d2ea; font-weight: 600; }
.crop-verdict-tag--justified { color: #9fd8a8; border-color: rgba(110, 200, 130, .45); }
.crop-verdict-tag--not-justified { color: #fff; background: #e5484d; border-color: #e5484d; font-weight: 700; box-shadow: 0 1px 6px rgba(229, 72, 77, .35); }
.crop-verdict-tag--unknown { color: #a9b6d4; }
.crop-pair--urgent { border-left: 3px solid #e5484d; background: rgba(239, 68, 68, .06); }
.crop-pair--think { border-left: 3px solid #e8c46a; background: rgba(232, 196, 106, .06); }
.crop-iv-tag--think { color: #1a1608; background: #e8c46a; font-weight: 700; }
.crop-pair--hl { background: rgba(120, 160, 255, .10); }
.crop-pair--flash { animation: cropFlash 1.4s ease; }
@keyframes cropFlash { 0% { background: rgba(240, 212, 138, 0); } 25% { background: rgba(240, 212, 138, .22); } 100% { background: rgba(240, 212, 138, 0); } }
.crop-iv-tag { font-size: 9.5px; line-height: 15px; padding: 0 6px; border-radius: 999px; margin-right: 5px; }
.crop-iv-tag--urgent { color: #fff; background: #e5484d; font-weight: 700; }
.crop-session-grid { display: grid; grid-template-columns: minmax(0, 1.65fr) minmax(300px, 1fr); gap: 14px; align-items: start; }
.crop-session-main { display: grid; gap: 14px; min-width: 0; }
.crop-reflect { position: sticky; top: 8px; scroll-behavior: smooth; max-height: calc(100vh - 240px); overflow-y: auto; }
@media (max-width: 900px) {
  .crop-session-grid { grid-template-columns: 1fr; }
  .crop-reflect { position: static; max-height: none; }
}
.crop-pair { display: grid; grid-template-columns: 22px minmax(0, 1fr); gap: 8px; align-items: start; padding: 8px 0; border-top: 1px solid rgba(160, 180, 230, .10); }
.crop-pair-body { display: grid; gap: 6px; min-width: 0; }
.crop-pair-no { color: #7f9fe0; font-variant-numeric: tabular-nums; padding-top: 14px; }
.crop-pair-label { display: block; font-size: 9.5px; color: #7785a6; margin-bottom: 2px; }
.crop-pair-text { line-height: 1.55; overflow-wrap: anywhere; word-break: break-word; }
.crop-pair-problem .crop-pair-text { color: #a9b6d4; }
.crop-pair-meta { margin-top: 4px; font-size: 10px; color: #7785a6; }
.crop-pair-arrow { display: none; }
.crop-pair-improve .crop-pair-text { color: #c6d2ea; }
.crop-imp-tag { flex: none; height: 15px; line-height: 14px; padding: 0 5px; border: 1px solid rgba(160, 180, 230, .16); border-radius: 999px; font-size: 9.5px; color: #8fa0c4; }

/* The session page is also a data surface: same denser glass as the global
   page so numbers keep contrast over a busy wallpaper. */
.crop-page .crop-card {
  background: rgb(242 245 250 / min(1, calc(var(--dsh-skin-bubble-alpha, .5) * 1.7)));
  border: 1px solid rgba(255, 255, 255, .35);
  box-shadow: 0 2px 12px rgba(20, 30, 60, .10);
}

body[data-ds-dark-theme] .crop-page .crop-card {
  background: rgb(16 22 42 / min(1, calc(var(--dsh-skin-bubble-alpha, .5) * 2 * .8)));
  border-color: rgba(160, 180, 230, .18);
  box-shadow: 0 2px 12px rgba(0, 0, 0, .28);
}
`;

    const S = {
      head: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '12px' },
      headMain: { minWidth: 0 },
      goal: { fontSize: '15px', lineHeight: 1.45 },
      sub: { fontSize: '12px', marginTop: '2px' },
      lamp: (color) => ({ display: 'inline-block', width: '9px', height: '9px', borderRadius: '5px', background: color, marginRight: '7px' }),
      timer: { fontSize: '30px', fontWeight: 600, letterSpacing: '-0.01em', fontVariantNumeric: 'tabular-nums' },
      quote: { fontSize: '14px', margin: 0 },
      meta: { fontSize: '12px', marginTop: '4px' },
      caption: { fontSize: '11px', marginBottom: '8px' },
      tl: { display: 'flex', gap: '3px', height: '34px', alignItems: 'stretch' },
      axis: { display: 'flex', justifyContent: 'space-between', fontSize: '10px', marginTop: '6px' },
      table: { width: '100%', borderCollapse: 'collapse', fontVariantNumeric: 'tabular-nums' },
      td: { padding: '6px 0', borderBottom: '1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .16))' },
      tdRight: { padding: '6px 0', borderBottom: '1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .16))', textAlign: 'right', whiteSpace: 'nowrap', opacity: 0.8 },
      fail: { color: '#e5484d' },
      quiet: { fontSize: '12px' },
    };

    function mmss(minutes) {
      const seconds = Math.round((minutes || 0) * 60);
      return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }

    function fmtMinutes(value) {
      if (value == null) return '—';
      const minutes = Math.round(value);
      if (minutes < 60) return `${minutes} 分`;
      return `${Math.floor(minutes / 60)} 时 ${minutes % 60} 分`;
    }

    function ago(ms) {
      if (!ms) return '';
      const minutes = Math.max(0, Math.round((Date.now() - ms) / 60000));
      return minutes < 60 ? `${minutes} 分钟前` : `${Math.round(minutes / 60)} 小时前`;
    }

    /* The dashboard page walks its own day range from period.start; the panel
       repeats that so the bars match the standalone page exactly (including the
       31-slot cap the server-side page applies). */
    function useSession(sessionId) {
      const [state, setState] = React.useState({ data: null, error: null });
      React.useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            const response = await fetch(`${ENDPOINT}?sid=${encodeURIComponent(sessionId || '')}`, { cache: 'no-store' });
            const data = await response.json();
            if (alive) setState({ data, error: null });
          } catch (error) {
            if (alive) setState({ data: null, error: String(error) });
          }
        };
        tick();
        const timer = setInterval(tick, 3000);
        return () => { alive = false; clearInterval(timer); };
      }, [sessionId]);
      return state;
    }

    function fmtSpan(ms) {
      const minutes = Math.max(0, Math.round(ms / 60000));
      if (minutes < 60) return `${minutes}m`;
      return `${Math.floor(minutes / 60)}h ${minutes % 60}m`;
    }

    function View({ sessionId }) {
      const { data, error } = useSession(sessionId);
      const [picked, setPicked] = React.useState(null);
      const [view, setView] = React.useState(null);
      const [hoverIv, setHoverIv] = React.useState(null);
      const [flashIv, setFlashIv] = React.useState(null);
      const [hotMarker, setHotMarker] = React.useState(null);
      const dragRef = React.useRef(null);
      if (error || !data) {
        return h('div', { className: 'crop-page' },
          h('div', { className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.quiet }, '数据读不到：检查 dsh 日志里的 crop 报错。')));
      }
      if (!data.timeline) {
        return h('div', { className: 'crop-page' },
          h('div', { className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.quiet }, '后端还没升级到带时间轴的版本：重启 DSH 后再看。')));
      }

      const timeline = data.timeline || {};
      const titles = data.work_titles || {};
      const persona = data.persona || 'owner';
      const labelOf = (workId) => titles[workId] || workId || '未归属';
      const allBlocks = timeline.blocks || [];
      const lastAt = allBlocks.length ? allBlocks[allBlocks.length - 1].t : Date.now();
      // Sessions can stay open for days. Showing the whole wall clock turns
      // every bar into a hairline, so the timeline shows a recent window
      // (3h) ending at the last activity, not at "now" while idle.
      const started = data.session_started_at || (allBlocks.length ? allBlocks[0].start : Date.now());
      const idle = lastAt > 0 && Date.now() - lastAt > 30 * 60 * 1000;
      const baseEnd = idle ? lastAt : Date.now();
      const viewEnd = view ? view.end : baseEnd;
      const windowStart = view ? view.start : Math.max(started, baseEnd - 6 * 60 * 60 * 1000);
      const span = Math.max(5 * 60 * 1000, viewEnd - windowStart);
      const setWindow = (start, end) => {
        const full = Math.max(30 * 60 * 1000, baseEnd - started);
        const width = Math.max(5 * 60 * 1000, Math.min(full, end - start));
        let nextStart = Math.max(started, Math.min(start, baseEnd - width));
        setView({ start: nextStart, end: nextStart + width });
      };
      const zoomTo = (minutes) => {
        const width = minutes == null ? Math.max(30 * 60 * 1000, baseEnd - started) : minutes * 60000;
        setWindow(viewEnd - width, viewEnd);
      };
      const onDown = (event) => {
        if (event.button !== 0) return;
        const rect = event.currentTarget.getBoundingClientRect();
        dragRef.current = { x: event.clientX, start: windowStart, end: viewEnd, width: rect.width };
        event.currentTarget.setPointerCapture?.(event.pointerId);
        event.preventDefault();
      };
      const onMove = (event) => {
        const drag = dragRef.current;
        if (!drag || !drag.width) return;
        const shift = ((drag.x - event.clientX) / drag.width) * (drag.end - drag.start);
        setWindow(drag.start + shift, drag.end + shift);
      };
      const onUp = () => { dragRef.current = null; };
      const pos = (t) => Math.min(100, Math.max(0, ((t - windowStart) / span) * 100));
      const pct = (v) => `${v}%`;
      const blocks = allBlocks.filter((block) => block.t >= windowStart);
      const whips = (timeline.whips || []).filter((whip) => whip.t >= windowStart);
      const waits = (timeline.waits || []).filter((wait) => wait.t >= windowStart);
      const reports = (timeline.reports || []).filter((report) => report.t >= windowStart);
      const interventions = data.interventions || [];
      // Greedy one-to-one: each whip marker claims the nearest still-unused
      // intervention. Two markers never point at the same reflection row.
      const whipLink = new Map();
      {
        const used = new Set();
        for (const whip of whips.slice().sort((a, b) => a.t - b.t)) {
          const pool = interventions
            .map((item, index) => ({ index, gap: Math.abs((item.at || 0) - whip.t) }))
            .filter((item) => !used.has(item.index))
            .sort((a, b) => a.gap - b.gap);
          const pick = pool[0];
          if (!pick || pick.gap > 6 * 3600 * 1000) continue;
          used.add(pick.index);
          whipLink.set(`${whip.work_id}|${whip.t}`, pick.index);
        }
      }
      const nearestIv = (workId, mark) => whipLink.get(`${workId}|${mark.at}`) ?? -1;

      const laneMap = new Map();
      for (const block of blocks) {
        const key = block.work_id || '';
        if (!laneMap.has(key)) laneMap.set(key, { key, blocks: [], whips: [], blocked: [] });
        laneMap.get(key).blocks.push(block);
      }
      for (const whip of whips) {
        const lane = laneMap.get(whip.work_id || '');
        if (lane) lane.whips.push(whip);
      }
      for (const report of reports) {
        if (report.phase !== 'blocked') continue;
        const lane = laneMap.get(report.work_id || '');
        if (lane) lane.blocked.push(report);
      }
      const activeId = data.active_work ? data.active_work.work_id : '';
      const lanes = [...laneMap.values()].sort((a, b) => {
        if (a.key === activeId) return -1;
        if (b.key === activeId) return 1;
        // A lane with a whip marker is the interesting one; keep it above
        // quiet lanes when the list has to be capped.
        const aWhip = a.whips.length ? 0 : 1;
        const bWhip = b.whips.length ? 0 : 1;
        if (aWhip !== bWhip) return aWhip - bWhip;
        return (a.blocks[0]?.start || 0) - (b.blocks[0]?.start || 0);
      });

      const markerLinks = [];
      const visibleLanes = lanes.slice(0, 5);
      const moreLanes = lanes.length - visibleLanes.length;
      const laneRows = visibleLanes.map((lane) => {
        const waitSegments = waits.filter((wait) => (wait.work_id || '') === lane.key).map((wait, index) => {
          const width = Math.max(0.6, pos(wait.t) - pos(wait.t - wait.ms));
          return h('div', { key: `w${index}`, className: 'crop-seg crop-seg--wait',
            style: { left: pct(pos(wait.t - wait.ms)), width: pct(width) } });
        });
        const segments = lane.blocks.map((block, index) => {
          const width = Math.max(0.8, pos(block.t) - pos(block.start));
          return h('div', {
            key: `s${index}`,
            className: `crop-seg${picked && picked.block === block ? ' crop-seg--picked' : ''}`,
            style: { left: pct(pos(block.start)), width: pct(width) },
            title: `${block.minutes}m`,
            onClick: () => setPicked({ block, lane: lane.key }),
          });
        });
        // whip markers cluster per 5 minutes so the panel stays quiet
        const whipMarks = [];
        let cluster = null;
        for (const whip of lane.whips) {
          if (cluster && whip.t - cluster.last < 5 * 60000) {
            cluster.count += 1;
            cluster.last = whip.t;
            continue;
          }
          cluster = { at: whip.t, last: whip.t, count: 1, persona: whip.persona || 'owner' };
          whipMarks.push(cluster);
        }
        const marks = whipMarks.map((mark, index) => {
          const link = nearestIv(lane.key, mark);
          const key = `${lane.key}:${index}`;
          markerLinks.push({ key, link });
          return h('img', {
            key: `w${index}`,
            id: `crop-whip-${link}`,
            className: `crop-whip${hotMarker === key ? ' crop-whip--hl' : ''}`,
            src: personaIcon(mark.persona),
            style: { left: pct(pos(mark.at)) },
            title: `${mark.persona === 'owner' ? '鞭策' : '提醒'} · 移上去看对应反思`,
            onMouseEnter: () => { setHoverIv(link); setHotMarker(key); },
            onMouseLeave: () => { setHoverIv(null); setHotMarker(null); },
            onClick: () => { setHotMarker(key); focusIv(link); },
          });
        });
        const blocked = lane.blocked.map((report, index) => h('i', {
          key: `b${index}`,
          className: 'crop-blocked-dot',
          style: { left: pct(pos(report.t)) },
          title: report.cause || enumLabel('category', report.category) || '卡点',
        }));
        return h('div', { key: lane.key || 'none', className: 'crop-tl-row' },
          h('div', { className: `crop-tl-label${lane.key === activeId ? ' crop-tl-label--current' : ''}` },
            h('div', { className: 'crop-tl-name', title: lane.key || '' },
              lane.key === activeId ? '▸ ' : '', labelOf(lane.key)),
            h('div', { className: 'crop-muted crop-tl-meta' },
              `${lane.blocks.reduce((sum, b) => sum + (b.minutes || 0), 0).toFixed(0)}m`,
              lane.whips.length ? h('span', { className: 'crop-lane-whips' }, ` · ${lane.whips.length} 次鞭策`) : null,
              lane.key && lane.key !== labelOf(lane.key) ? ` · ${lane.key}` : '')),
          h('div', { className: 'crop-tl-track' }, h('div', { className: 'crop-tl-grid' }), waitSegments, segments, marks, blocked));
      });

      const wall = Math.max(0, baseEnd - started);
      const activeMs = (data.work_minutes || 0) * 60000;
      const waitMs = Math.max(0, wall - activeMs);
      const head = h('div', { key: 'head', className: 'crop-card crop-sess-head' },
        h('div', { className: 'crop-sess-title' }, 'Crop'),
        h('div', { className: 'crop-sess-clock' }, `${fmtSpan(wall)}`, h('span', { className: 'crop-muted' }, windowStart > started ? '最近 ' + Math.round(span / 60000) + 'm' : '本次会话')),
        h('div', { className: 'crop-sess-split', title: `活跃 ${fmtSpan(activeMs)} · 等待 ${fmtSpan(waitMs)}` },
          h('i', { className: 'work', style: { width: pct(wall ? (activeMs / wall) * 100 : 0) } }),
          h('i', { className: 'wait', style: { width: pct(wall ? (waitMs / wall) * 100 : 0) } })),
        h('div', { className: 'crop-muted crop-small' }, `活跃 ${fmtSpan(activeMs)} · 等待 ${fmtSpan(waitMs)}`),
        h('span', { className: 'crop-persona-chip' }, `你是一个${PERSONA_LABEL[persona] || persona}`),
        h('div', { className: 'crop-sess-turn' }, `本回合 ${fmtSpan((data.current_minutes || 0) * 60000)}`),
        h('div', { className: 'crop-sess-stats' },
          (() => {
            const stats = data.stats || {};
            const items = [
              ['超时', stats.reminders], ['卡点', stats.blocked], ['返工', stats.rework],
              ['工具失败', stats.failed_tools], ['要改', stats.needs_fix], ['要想办法', stats.needs_idea],
            ].filter(([, count]) => count > 0);
            return items.length
              ? items.map(([label, count]) => h('span', { key: label, className: 'crop-stat-chip' },
                  label, h('b', null, String(count))))
              : h('span', { className: 'crop-muted crop-small' }, '这次会话还没有异常记录');
          })()));

      const active = data.active_work;
      const expect = active && active.expect_min ? active.expect_min : null;
      const current = h('div', { key: 'current', className: 'crop-card crop-sess-current' },
        h('span', { className: 'crop-sess-label' }, '当前工作'),
        h('span', { className: 'crop-sess-name', title: active ? active.work_id : '' }, active ? (active.title || active.goal || labelOf(active.work_id)) : '本会话还没有绑定工作'),
        expect ? h('div', { className: 'crop-sess-bar' },
          h('i', { style: { width: pct(Math.min(100, ((active.minutes || 0) / expect) * 100)) } })) : null,
        h('span', { className: 'crop-muted crop-small' },
          expect ? `估 ${Math.round(expect)}m / 已 ${Math.round(active.minutes || 0)}m` : ''));

      const frame = h('div', { key: 'frame', className: 'crop-card crop-tl-card' },
        h('div', { className: 'crop-tl-head' },
          h('span', { className: 'crop-muted crop-small' }, '会话运行过程'),
          h('span', { className: 'crop-tl-zoom' },
            [['30m', 30], ['1h', 60], ['3h', 180], ['6h', 360], ['全部', null]].map(([label, minutes]) => h('button', {
              key: label, type: 'button', className: 'crop-chip',
              'data-on': minutes == null ? (view == null ? '' : undefined) : ((view ? Math.round(span / 60000) === minutes : minutes === 360) ? '' : undefined),
              onClick: () => zoomTo(minutes),
            }, label))),
          h('span', { className: 'crop-tl-legend' },
            h('span', null, h('i', { className: 'crop-swatch' }), '活跃'),
            h('span', null, h('img', { className: 'crop-legend-whip', src: personaIcon(persona), alt: '' }), persona === 'owner' ? '鞭策' : '提醒'),
            h('span', null, h('i', { className: 'crop-dot-red' }), '卡点'))),
        h('div', {
          className: 'crop-tl-body crop-tl-drag',
          onPointerDown: onDown,
          onPointerMove: onMove,
          onPointerUp: onUp,
          onPointerCancel: onUp,
          onWheel: (event) => {
            if (!event.shiftKey) return;
            event.preventDefault();
            const width = event.currentTarget.getBoundingClientRect().width || 1;
            // Chrome turns Shift+wheel into horizontal scrolling: deltaY is 0
            // and the movement arrives as deltaX.
            const delta = Math.abs(event.deltaX) > Math.abs(event.deltaY) ? event.deltaX : event.deltaY;
            const shift = (delta / width) * span;
            setWindow(windowStart + shift, viewEnd + shift);
          },
        }, laneRows.length ? laneRows : h('div', { className: 'crop-muted' }, '这次会话还没有完成一个时间块。'),
          moreLanes > 0 ? h('div', { className: 'crop-muted crop-small' }, `还有 ${moreLanes} 个工作未显示`) : null),
        h('div', { className: 'crop-tl-axis' },
          [0, 0.25, 0.5, 0.75, 1].map((share) => h('span', {
            key: share,
            style: { left: pct(share * 100) },
          }, `${Math.round((span * share) / 60000)}m`))),
        h('div', { className: 'crop-muted crop-small crop-tl-hint' }, '拖动平移 · 滚轮或按钮缩放'),
        picked ? h('div', { className: 'crop-tl-detail' },
          h('span', { className: 'crop-tl-range' }, `${picked.block.minutes}m`),
          h('span', null, `${picked.lane || '未归属'} · 这一段做了 ${picked.block.minutes} 分钟`)) : null);

      const works = (data.session_works || []).length
        ? h('div', { key: 'works', className: 'crop-card' },
            h('div', { className: 'crop-muted crop-small crop-tl-cap' }, '本会话碰过的工作'),
            h('div', null, data.session_works.map((item) => h('div', { key: item.work_id, className: 'crop-work-row' },
              h('i', { className: 'crop-dot', style: { background: lampColor(item.ratio) } }),
              h('span', { title: item.work_id }, labelOf(item.work_id)),
              h('span', { className: 'crop-muted crop-small', style: { marginLeft: 'auto' } },
                `${Math.round(item.minutes || 0)}m`)))))
        : null;

      const reflection = data.reflection || {};
      const improvements = data.improvements || [];
      const focusIv = (index) => {
        if (index < 0) return;
        const node = document.getElementById(`crop-iv-${index}`);
        const box = node?.closest('.crop-reflect');
        if (node && box) {
          // Scroll the reflection card itself, not the whole conversation:
          // the card is a sticky, independently scrollable column.
          const boxRect = box.getBoundingClientRect();
          const nodeRect = node.getBoundingClientRect();
          const target = box.scrollTop + (nodeRect.top - boxRect.top)
            - (box.clientHeight - nodeRect.height) / 2;
          box.scrollTo({ top: Math.max(0, target), behavior: 'smooth' });
        } else {
          node?.scrollIntoView({ block: 'center', behavior: 'smooth' });
        }
        setFlashIv(index);
        window.setTimeout(() => setFlashIv(null), 1600);
      };
      const needsFix = reflection.verdict === 'not-justified';
      const fixCount = interventions.filter((item) => item.verdict === 'not-justified' && item.improve).length;
      const thinkCount = interventions.filter((item) => item.verdict === 'not-justified' && !item.improve).length;
      const verdictLine = reflection.verdict === 'justified' ? '这次慢得有道理，不用改'
        : reflection.verdict === 'not-justified' ? '这次慢得没道理，要改'
          : '';
      const reflect = h('div', { key: 'reflect', className: 'crop-card crop-reflect' },
        h('div', { className: 'crop-reflect-head' }, [
          h('span', { key: 'cap', className: 'crop-muted crop-small' }, '本会话反思'),
          h('span', { key: 'badge', className: 'crop-badge' }, '自评 · 待复核'),
          h('span', { key: 'counts', className: 'crop-muted crop-small' },
            `提醒 ${whips.length} 次 · 问题 ${interventions.length} 条`,
            fixCount ? ` · 要改 ${fixCount}` : '',
            thinkCount ? ` · 要想办法 ${thinkCount}` : ''),
        ]),
        interventions.length
          ? interventions.map((item, index) => {
              const urgent = item.verdict === 'not-justified';
              const needsIdea = urgent && !item.improve;
              const when = item.at && started ? `第 ${Math.max(1, Math.round((item.at - started) / 60000))} 分钟` : '';
              const state = item.phase === 'blocked' ? '卡点' : item.phase === 'end' ? '收尾' : item.phase ? '进行中' : '';
              const meta = [when, state, item.category ? enumLabel('category', item.category) : ''].filter(Boolean).join(' · ');
              return h('div', {
                key: `iv${index}`,
                id: `crop-iv-${index}`,
                className: `crop-pair${urgent && !needsIdea ? ' crop-pair--urgent' : ''}${needsIdea ? ' crop-pair--think' : ''}${hoverIv === index ? ' crop-pair--hl' : ''}${flashIv === index ? ' crop-pair--flash' : ''}`,
                onMouseEnter: () => {
                  const first = markerLinks.find((item) => item.link === index);
                  setHoverIv(index);
                  setHotMarker(first ? first.key : null);
                },
                onMouseLeave: () => { setHoverIv(null); setHotMarker(null); },
                onClick: () => {
                  setFlashIv(index);
                  const first = markerLinks.find((item) => item.link === index);
                  setHotMarker(first ? first.key : null);
                  window.setTimeout(() => setFlashIv(null), 1600);
                },
              }, [
                h('span', { key: 'n', className: 'crop-pair-no' }, String(index + 1)),
                h('div', { key: 'b', className: 'crop-pair-body' }, [
                  h('div', { key: 'p', className: 'crop-pair-problem' }, [
                    h('div', { key: 'h', className: 'crop-pair-label' }, [
                      urgent && !needsIdea ? h('span', { key: 't', className: 'crop-iv-tag crop-iv-tag--urgent' }, '要改') : null,
                      needsIdea ? h('span', { key: 't', className: 'crop-iv-tag crop-iv-tag--think' }, '要想办法') : null,
                      h('span', { key: 'l', className: 'crop-muted' }, '当时的问题'),
                    ]),
                    h('div', { key: 't', className: 'crop-pair-text' }, item.cause || '未记录'),
                    meta ? h('div', { key: 'm', className: 'crop-pair-meta' }, meta) : null,
                  ]),
                  h('div', { key: 'i', className: 'crop-pair-improve' }, [
                    h('div', { key: 'l', className: 'crop-pair-label' }, 'AI 的改进'),
                    h('div', { key: 't', className: item.improve ? 'crop-pair-text' : 'crop-pair-text crop-muted' },
                      item.improve || (needsIdea ? '还没想出办法' : '未提出改进')),
                  ]),
                ]),
              ]);
            })
          : h('div', { className: 'crop-muted crop-small' }, '还没有上报过问题。'));

      return h('div', { className: 'crop-page' }, [
        head, current,
        h('div', { className: 'crop-session-grid' }, [
          h('div', { className: 'crop-session-main' }, [frame, works]),
          reflect,
        ]),
      ]);
    }

    function fixed(value, digits) {
      return value == null ? '—' : value.toFixed(digits);
    }

    function pctText(value) {
      return value == null ? '—' : `${Math.round(value * 100)}%`;
    }

    /* The comparison line under each friction metric: the previous window's
       value plus a direction, coloured by whether the move is an improvement.
       A window with no events reports null upstream, so the card says "上期无数据"
       instead of pretending the metric held steady at zero. */
    function deltaLine(current, previous, fmtValue, fmtDelta, lowerIsBetter) {
      if (current == null) return { text: '样本不足', tone: '' };
      if (previous == null) return { text: '上期无数据', tone: '' };
      const diff = current - previous;
      if (Math.abs(diff) < 1e-9) return { text: `上期 ${fmtValue(previous)} · 持平`, tone: '' };
      const better = lowerIsBetter ? diff < 0 : diff > 0;
      return {
        text: `上期 ${fmtValue(previous)} · ${diff > 0 ? '↑' : '↓'} ${fmtDelta(Math.abs(diff))}`,
        tone: better ? 'good' : 'bad',
      };
    }

    function dayRange(startMs) {
      if (!startMs) return [];
      const out = [];
      const cursor = new Date(startMs);
      cursor.setHours(0, 0, 0, 0);
      const today = new Date();
      today.setHours(0, 0, 0, 0);
      while (cursor <= today && out.length < 31) {
        const key = `${cursor.getFullYear()}-${String(cursor.getMonth() + 1).padStart(2, '0')}-${String(cursor.getDate()).padStart(2, '0')}`;
        out.push({ date: key, label: key.slice(5) });
        cursor.setDate(cursor.getDate() + 1);
      }
      return out;
    }

    function useGlobal(days) {
      const [state, setState] = React.useState({ data: null, error: null, at: 0 });
      React.useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            const response = await fetch(`${API}/global?days=${days}`, { cache: 'no-store' });
            const data = await response.json();
            if (alive) setState({ data, error: null, at: Date.now() });
          } catch (error) {
            if (alive) setState({ data: null, error: String(error), at: Date.now() });
          }
        };
        tick();
        const timer = setInterval(tick, 5000);
        return () => { alive = false; clearInterval(timer); };
      }, [days]);
      return state;
    }

    function useHealth(days) {
      const [state, setState] = React.useState({ data: null, error: null, at: 0 });
      React.useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            let response = await fetch(`${API}/tool-health?days=${days}`, { cache: 'no-store' });
            if (!response.ok) {
              // 开发期宿主还没重载插件时，退回本地 dashboard 的同源接口
              response = await fetch(`http://127.0.0.1:8765/api/tool-health?days=${days}`, { cache: 'no-store' });
            }
            const data = await response.json();
            if (alive) setState({ data, error: null, at: Date.now() });
          } catch (error) {
            if (alive) setState({ data: null, error: String(error), at: Date.now() });
          }
        };
        tick();
        const timer = setInterval(tick, 5000);
        return () => { alive = false; clearInterval(timer); };
      }, [days]);
      return state;
    }

    function GlobalPanel() {
      const [days, setDays] = React.useState(7);
      const [selected, setSelected] = React.useState(null);
      const [detail, setDetail] = React.useState(null);
      const [showAll, setShowAll] = React.useState(false);
      const global = useGlobal(days);
      const [mode, setMode] = React.useState('overview');
      const health = useHealth(days);

      React.useEffect(() => {
        if (!selected) { setDetail(null); return undefined; }
        let alive = true;
        (async () => {
          try {
            const response = await fetch(`${API}/day?date=${encodeURIComponent(selected)}`, { cache: 'no-store' });
            const data = await response.json();
            if (alive) setDetail(data);
          } catch (error) {
            if (alive) setDetail({ error: String(error) });
          }
        })();
        return () => { alive = false; };
      }, [selected]);

      const data = global.data;
      const current = (data && data.current) || null;
      const previous = (data && data.previous) || null;
      const prevReady = !!(previous && previous.events > 0);
      const prev = prevReady ? previous : {};

      const metricCard = (label, value, delta, note) => h('div', { key: label, className: 'crop-card' },
        h('div', { className: 'crop-muted crop-metric-k' }, label),
        h('div', { className: 'crop-metric-v' }, value),
        h('div', { className: `crop-delta${delta.tone ? ` crop-${delta.tone}` : ''}` }, delta.text),
        h('div', { className: 'crop-muted crop-metric-n' }, note));

      const loadingCard = !data
        ? h('div', { key: 'state', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.quiet },
              global.error ? '数据读不到：检查 dsh 日志里的 crop 报错。' : '正在读取…'))
        : null;

      const head = h('div', { key: 'head', className: 'crop-card' },
        h('div', { className: 'crop-global-head' },
          h('h2', { className: 'crop-global-title' }, 'Crop · 全局'),
          h('div', { className: 'crop-chips' },
            [[1, '今天'], [7, '7 天'], [30, '30 天']].map(([value, label]) => h('button', {
              key: value,
              type: 'button',
              className: 'crop-chip',
              'data-on': days === value ? '' : undefined,
              onClick: () => { setDays(value); setSelected(null); },
            }, label))),
          h('div', { className: 'crop-modes' },
            [['overview', '概览'], ['health', '健康度']].map(([value, label]) => h('button', {
              key: value,
              type: 'button',
              className: 'crop-chip',
              'data-on': mode === value ? '' : undefined,
              onClick: () => setMode(value),
            }, label))),
          h('span', { className: 'crop-muted crop-global-asof' },
            global.at ? `更新于 ${new Date(global.at).toLocaleTimeString()}` : '')));

      const metrics = current
        ? h('div', { key: 'metrics', className: 'crop-metrics' }, [
            metricCard(
              '等待回话占比',
              pctText(current.wait_ratio),
              deltaLine(current.wait_ratio, prev.wait_ratio, pctText, (value) => `${Math.round(value * 100)} 个百分点`, true),
              '等待 ÷ (活跃 + 等待)'),
            metricCard(
              '每完成一个工作',
              fmtMinutes(current.active_per_completed),
              deltaLine(current.active_per_completed, prev.active_per_completed, fmtMinutes, fmtMinutes, true),
              '活跃分钟 ÷ 完成数'),
            metricCard(
              '每回合工具数',
              fixed(current.tools_per_block, 1),
              deltaLine(current.tools_per_block, prev.tools_per_block, (value) => value.toFixed(1), (value) => value.toFixed(1), true),
              '工具调用 ÷ 回合'),
            metricCard(
              '工具失败率',
              pctText(current.failure_rate),
              deltaLine(current.failure_rate, prev.failure_rate, pctText, (value) => `${(value * 100).toFixed(1)} 个百分点`, true),
              '失败调用 ÷ 工具调用'),
            metricCard(
              '重复工具信号 / 回合',
              fixed(current.churn_per_block, 2),
              deltaLine(current.churn_per_block, prev.churn_per_block, (value) => value.toFixed(2), (value) => value.toFixed(2), true),
              '同工具反复调用 ÷ 回合'),
          ])
        : null;

      const byDay = new Map(((data && data.daily) || []).map((row) => [row.date, row]));
      const series = dayRange(data && data.start).map(({ date, label }) => {
        const row = byDay.get(date);
        const total = row ? Number(row.minutes) || 0 : 0;
        const wait = row ? Number(row.wait_minutes) || 0 : 0;
        return { date, label, total, wait, active: Math.max(0, total - wait) };
      });
      const peak = Math.max(1, ...series.map((item) => item.total));
      const busy = series.filter((item) => item.total > 0);
      const avgActive = busy.length ? busy.reduce((sum, item) => sum + item.active, 0) / busy.length : null;
      const avgWait = busy.length ? busy.reduce((sum, item) => sum + item.wait, 0) / busy.length : null;

      const chart = current
        ? h('div', { key: 'chart', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, '每天的时间去向（活跃 + 等待回话，点柱子看当天明细）'),
            h('div', { className: 'crop-chart' }, series.map((item) => {
              const totalHeight = item.total ? Math.max(2, Math.round((item.total / peak) * 150)) : 2;
              const waitHeight = item.total ? Math.round(totalHeight * (item.wait / item.total)) : 0;
              const activeHeight = totalHeight - waitHeight;
              return h('button', {
                key: item.date,
                type: 'button',
                className: 'crop-slot',
                title: `${item.date} · 活跃 ${Math.round(item.active)} 分 · 等待 ${Math.round(item.wait)} 分`,
                'data-on': selected === item.date ? '' : undefined,
                onClick: () => { setShowAll(false); setSelected(selected === item.date ? null : item.date); },
              },
                h('span', { className: 'crop-muted crop-day-val' }, item.total ? String(Math.round(item.total)) : ''),
                h('span', { className: 'crop-bar-stack', style: { height: `${totalHeight}px` } },
                  waitHeight ? h('span', { className: 'crop-bar crop-bar--wait', style: { height: `${waitHeight}px` } }) : null,
                  item.total
                    ? h('span', { className: 'crop-bar crop-bar--active', style: { height: `${activeHeight}px` } })
                    : h('span', { className: 'crop-bar crop-bar--empty', style: { height: '2px' } })),
                h('span', { className: 'crop-muted crop-day-cap' }, item.label));
            })),
            h('div', { className: 'crop-muted crop-legend' },
              h('span', null, h('i', { className: 'crop-dot crop-dot--active' }), '活跃'),
              h('span', null, h('i', { className: 'crop-dot crop-dot--wait' }), '等待回话'),
              h('span', { className: 'crop-legend-note' },
                busy.length ? `日均 活跃 ${fmtMinutes(avgActive)} · 等待 ${fmtMinutes(avgWait)}` : '这段时间还没有记录')),
            detail
              ? h('div', { className: 'crop-day' },
                  h('div', { className: 'crop-day-head' }, [
                    h('strong', { key: 'd' }, detail.error ? detail.date : (detail.date || '')),
                    detail.error
                      ? h('span', { key: 'e', className: 'crop-muted' }, detail.error)
                      : [
                          h('span', { key: 'b', className: 'crop-stat' }, `${detail.blocks || 0} 回合`),
                          h('span', { key: 'm', className: 'crop-stat' }, fmtMinutes(detail.minutes || 0)),
                          h('span', {
                            key: 'f',
                            className: `crop-stat${detail.failures ? ' crop-bad' : ''}`,
                          }, detail.failures ? `失败 ${detail.failures}` : '无失败'),
                        ],
                    h('button', {
                      key: 'x',
                      type: 'button',
                      className: 'crop-x',
                      'aria-label': '关闭当天明细',
                      onClick: () => setSelected(null),
                    }, '×'),
                  ]),
                  h('div', { className: 'crop-day-section' },
                    h('span', { className: 'crop-muted crop-day-label' }, '工具'),
                    h('div', { className: 'crop-day-body' },
                      h('div', { className: 'crop-tags' },
                        (detail.tools || []).slice(0, showAll ? undefined : 6).map(([name, count]) => h('span', { key: name, className: 'crop-tag' }, `${name} ×${count}`))),
                      !showAll && (detail.tools || []).length > 6
                        ? h('button', { type: 'button', className: 'crop-more', onClick: () => setShowAll(true) }, `+${(detail.tools || []).length - 6} 个工具`)
                        : null)),
                  h('div', { className: 'crop-day-section' },
                    h('span', { className: 'crop-muted crop-day-label' }, '工作'),
                    h('div', { className: 'crop-day-body' },
                      h('div', { className: 'crop-work-rows' },
                        (detail.works || []).slice(0, showAll ? undefined : 5).map((item) => h('div', { key: item.work_id, className: 'crop-work-row' },
                          h('span', { className: 'crop-work-name', title: item.goal || item.work_id }, item.goal || item.work_id),
                          h('span', { className: 'crop-work-min' }, fmtMinutes(item.minutes))))),
                      !showAll && (detail.works || []).length > 5
                        ? h('button', { type: 'button', className: 'crop-more', onClick: () => setShowAll(true) }, `+${(detail.works || []).length - 5} 个工作`)
                        : null)))
              : h('div', { className: 'crop-muted', style: S.quiet }, '点上面任意一根柱子看当天明细'))
        : null;

      const improves = (data && data.improvements) || [];
      const improvements = improves.length
        ? h('div', { key: 'improve', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, 'AI 自述的改进（下次可以少走一步）'),
            h('ul', { className: 'crop-list' },
              improves.slice(0, 4).map((item, index) => h('li', { key: index },
                h('div', null, `「${item.improve}」`),
                h('div', { className: 'crop-imp-ctx' }, [
                  item.goal || item.work_id || '',
                  item.phase === 'blocked' ? '卡点' : item.phase === 'end' ? '收尾' : '',
                  item.category ? enumLabel('category', item.category) : '',
                  item.cause ? `当时：${item.cause}` : item.summary ? `当时：${item.summary}` : '',
                  item.t ? new Date(item.t).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' }) : '',
                ].filter(Boolean).join(' · '))))))
        : null;

      const categories = (data && data.categories) || [];
      const pitfalls = categories.length
        ? h('div', { key: 'pitfalls', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, '还在重复的坑（AI 自述的归因类别）'),
            h('div', { className: 'crop-tags' },
              categories.slice(0, 6).map((item) => h('span', { key: item.category, className: 'crop-tag' }, `${enumLabel('category', item.category)} ×${item.count}`))))
        : null;

      const rows = (data && data.work_list) || [];
      const works = data
        ? h('div', { key: 'works', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, '工作列表'),
            rows.length
              ? h('table', { style: S.table }, [
                  h('thead', { key: 'th' }, h('tr', null, [
                    h('th', { key: 'lamp', style: { ...S.td, textAlign: 'left', fontSize: '12px' } }, '灯'),
                    h('th', { key: 'work', style: { ...S.td, textAlign: 'left', fontSize: '12px' } }, '工作'),
                    h('th', { key: 'type', style: { ...S.td, textAlign: 'left', fontSize: '12px' } }, '类型'),
                    h('th', { key: 'diff', style: { ...S.td, textAlign: 'left', fontSize: '12px' } }, '难度'),
                    h('th', { key: 'active', style: { ...S.tdRight, fontSize: '12px' } }, '活跃'),
                    h('th', { key: 'wait', style: { ...S.tdRight, fontSize: '12px' } }, '等待'),
                  ])),
                  h('tbody', { key: 'tb' },
                    rows.map((row) => h('tr', { key: row.work_id },
                      h('td', { style: S.td },
                        h('i', { style: S.lamp(LAMP[row.level] || '#6b7280') }), row.level ? enumLabel('level', row.level) : '—'),
                      h('td', { style: S.td },
                        h('div', { className: 'crop-work-title' }, row.goal || row.work_id)),
                      h('td', { style: S.td }, row.type ? enumLabel('type', row.type) : '—'),
                      h('td', { style: S.td }, row.difficulty ? enumLabel('difficulty', row.difficulty) : '—'),
                      h('td', { style: S.tdRight }, fmtMinutes(row.minutes)),
                      h('td', { style: S.tdRight }, row.wait_minutes ? fmtMinutes(row.wait_minutes) : '—')))),
                ])
              : h('div', { className: 'crop-muted', style: S.quiet }, '这段时间还没有工作记录。先用 probe_report 认领一条。'))
        : null;

      const healthNodes = (() => {
        const data = health.data;
        if (!data) {
          return [h('div', { key: 'hstate', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.quiet }, health.error ? '健康度读不到：检查 dsh 日志里的 crop 报错。' : '正在读取…'))];
        }
        const cell = { ...S.td, padding: '5px 6px', whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' };
        const cellRight = { ...S.tdRight, padding: '5px 6px', whiteSpace: 'nowrap' };
        const pctText = (value) => (value == null ? '—' : `${Math.round(value * 100)}%`);
        const pctWidth = (value) => `${Math.max(0, Math.min(100, Math.round((value || 0) * 100)))}%`;
        const toneOf = (row) => {
          if (row.status === 'not-shipped' || row['class'] === 'on_demand') return 'gray';
          if (row.lamp === 'red' || (row.events > 0 && row.session_coverage === 0)) return 'red';
          return row.lamp === 'yellow' ? 'yellow' : 'green';
        };
        const colorOf = (row) => {
          if (row.status === 'not-shipped' || row['class'] === 'on_demand') return '#8892a6';
          const threshold = { session: .8, work: .7, ended: .8, call: .9 }[row['class']] || .8;
          const value = row.session_coverage;
          if (value == null) return '#8892a6';
          return value >= threshold ? '#4ade80' : (value > 0 ? '#eab308' : '#ef4444');
        };
        const all = [...(data.passive || []), ...(data.active || [])];
        const counts = { green: 0, yellow: 0, red: 0, gray: 0 };
        all.forEach((row) => { counts[toneOf(row)] += 1; });
        const total = all.length || 1;
        let cursor = 0;
        const stops = [];
        [['green', '#4ade80'], ['yellow', '#eab308'], ['red', '#ef4444'], ['gray', '#8892a6']].forEach(([key, color]) => {
          if (!counts[key]) return;
          const share = counts[key] / total * 100;
          stops.push(`${color} ${cursor}% ${cursor + share}%`);
          cursor += share;
        });
        const donut = h('div', { key: 'd', className: 'crop-donut', style: { background: `conic-gradient(${stops.join(',')})` } },
          h('div', { className: 'crop-donut-hole' }, [
            h('b', { key: 'v' }, `${counts.green} / ${all.length}`),
            h('span', { key: 's' }, '正常'),
          ]));
        const legend = h('div', { key: 'l', className: 'crop-legend-list' }, [
          ['#4ade80', '正常', counts.green],
          ['#eab308', '偏低', counts.yellow],
          ['#ef4444', '要处理', counts.red],
          ['#8892a6', '按需 / 未上线', counts.gray],
        ].map(([color, label, count]) => h('div', { key: label, className: 'crop-legend-row' },
          h('i', { className: 'crop-dot', style: { background: color } }),
          h('b', null, String(count)),
          h('span', { className: 'crop-muted' }, label))));
        const summaryCard = h('div', { key: 'summary', className: 'crop-card crop-health-head' }, [
          donut,
          legend,
          h('div', { key: 'n', className: 'crop-health-note' }, (data.summary || {}).headline || ''),
        ]);

        const tableHead = (showClosure) => h('thead', { key: 'th' }, h('tr', null, [
          h('th', { key: 't', style: { ...cell, textAlign: 'left', fontSize: '11.5px' } }, '工具'),
          h('th', { key: 's', style: { ...cell, textAlign: 'left', fontSize: '11.5px' } }, '口径'),
          h('th', { key: 'e', style: { ...cellRight, fontSize: '11.5px' } }, '事件'),
          h('th', { key: 'c', style: { ...cell, textAlign: 'left', fontSize: '11.5px' } }, '覆盖'),
          h('th', { key: 'f', style: { ...cellRight, fontSize: '11.5px' } }, '完整'),
          showClosure ? h('th', { key: 'cl', style: { ...cell, textAlign: 'left', fontSize: '11.5px' } }, '闭环') : null,
        ]));

        const rowEl = (row, showClosure) => {
          const red = row.lamp === 'red' || (row.events > 0 && row.session_coverage === 0);
          const color = colorOf(row);
          const coverageNote = row.coverage_denominator == null ? '' : `${row.coverage_numerator} / ${row.coverage_denominator}`;
          const missing = Object.entries(row.missing_fields || {}).map(([key, value]) => `${key}×${value}`).join('、');
          const meter = (value, fill) => h('div', { className: 'crop-meter', style: { background: fill === '#ef4444' ? 'rgba(239,68,68,.20)' : 'rgba(100,116,160,.16)' } },
            h('i', { style: { width: pctWidth(value), background: fill } }),
            h('b', null, pctText(value)));
          return h('tr', { key: row.tool, className: red ? 'crop-row-red' : undefined }, [
            h('td', { key: 'n', style: cell }, [
              h('div', { key: 't' }, [
                row.label,
                h('span', { key: 'e', className: 'crop-muted', style: { marginLeft: 6 } }, row.tool),
              ]),
              row.note ? h('div', { key: 'note', className: 'crop-mini' }, row.note) : null,
            ]),
            h('td', { key: 's', style: cell }, row.scope || ''),
            h('td', { key: 'ev', style: cellRight }, String(row.events)),
            h('td', { key: 'cv', style: cell }, [
              row.status === 'not-shipped' ? h('span', { key: 'x', className: 'crop-muted' }, '未上线')
                : row['class'] === 'on_demand' ? h('span', { key: 'x', className: 'crop-muted' }, '—')
                  : meter(row.session_coverage, color),
              coverageNote ? h('div', { key: 'cd', className: 'crop-mini' }, coverageNote) : null,
            ]),
            h('td', { key: 'f', style: { ...cellRight, color: row.events && row.field_completeness < .95 ? '#b45309' : undefined } }, [
              row.events ? pctText(row.field_completeness) : '—',
              missing ? h('div', { key: 'm', className: 'crop-mini' }, `缺 ${missing}`) : null,
            ]),
            showClosure ? h('td', { key: 'cl', style: cell }, [
              row.stage_closure == null
                ? h('span', { key: 'x', className: 'crop-muted' }, '—')
                : meter(row.stage_closure, row.stage_closure >= .7 ? '#4ade80' : '#eab308'),
            ]) : null,
          ]);
        };

        const rowsFor = (list, showClosure) => {
          const groups = [
            ['required', '必采 · 直接决定数据能不能用'],
            ['on-demand', '按需 · 条件触发，只看有没有在采'],
            ['not-shipped', '未上线'],
          ];
          const out = [];
          groups.forEach(([id, label]) => {
            const items = list.filter((row) => {
              if (id === 'not-shipped') return row.status === 'not-shipped';
              if (id === 'on-demand') return row.status !== 'not-shipped' && row['class'] === 'on_demand';
              return row.status !== 'not-shipped' && row['class'] !== 'on_demand';
            });
            if (!items.length) return;
            out.push(h('tr', { key: `sec-${id}`, className: 'crop-sect' },
              h('td', { colSpan: showClosure ? 6 : 5 }, label)));
            items.forEach((row) => out.push(rowEl(row, showClosure)));
          });
          return out;
        };

        const tableCard = (key, title, list, showClosure) => h('div', { key, className: 'crop-card' }, [
          h('div', { key: 'c', className: 'crop-muted', style: S.caption }, title),
          list.length
            ? h('table', { key: 't', className: 'crop-health-table', style: S.table }, [
                showClosure
                  ? h('colgroup', { key: 'cg' }, [
                      h('col', { key: '1', style: { width: '28%' } }),
                      h('col', { key: '2', style: { width: '8%' } }),
                      h('col', { key: '3', style: { width: '8%' } }),
                      h('col', { key: '4', style: { width: '22%' } }),
                      h('col', { key: '5', style: { width: '16%' } }),
                      h('col', { key: '6', style: { width: '18%' } }),
                    ])
                  : h('colgroup', { key: 'cg' }, [
                      h('col', { key: '1', style: { width: '32%' } }),
                      h('col', { key: '2', style: { width: '9%' } }),
                      h('col', { key: '3', style: { width: '9%' } }),
                      h('col', { key: '4', style: { width: '26%' } }),
                      h('col', { key: '5', style: { width: '24%' } }),
                    ]),
                tableHead(showClosure),
                h('tbody', { key: 'tb' }, rowsFor(list, showClosure)),
              ])
            : h('div', { key: 'e', className: 'crop-muted', style: S.quiet }, '这段时间没有数据。'),
        ]);

        const diagnostics = (data.diagnostics || []).map((item) => h('div', { key: item.title, className: 'crop-diag' }, [
          h('i', { key: 'd', className: 'crop-dot', style: { background: item.severity === 'info' ? '#8892a6' : '#eab308' } }),
          h('div', { key: 'b' }, [
            h('div', { key: 't', className: 't' }, item.title),
            h('div', { key: 'e', className: 'e' }, item.evidence),
            h('div', { key: 'c', className: 'c' }, `${item.cause}${item.card ? ` · 卡 ${item.card}` : ''}`),
          ]),
        ]));
        const copyText = ['Crop 健康度：' + ((data.summary || {}).headline || '')]
          .concat((data.diagnostics || []).filter((d) => d.severity !== 'info')
            .map((d) => `- ${d.title}：${d.evidence}${d.card ? `（卡 ${d.card}）` : ''}`))
          .join('\n');
        const diagCard = h('div', { key: 'diag', className: 'crop-card' }, [
          h('div', { key: 'c', className: 'crop-muted', style: S.caption }, '诊断 · 哪些工具在一线是哑的'),
          diagnostics.length ? diagnostics : h('div', { key: 'e', className: 'crop-muted', style: S.quiet }, '没有发现问题。'),
          h('div', { key: 'a', style: { display: 'flex', justifyContent: 'flex-end', marginTop: 8 } },
            h('button', {
              type: 'button',
              className: 'crop-chip',
              onClick: () => { navigator.clipboard?.writeText(copyText).catch(() => {}); },
            }, '复制修复指令')),
        ]);

        return [
          summaryCard,
          tableCard('passive', '被动采集', data.passive || [], false),
          tableCard('active', 'AI 主动上报', data.active || [], true),
          diagCard,
        ];
      })();

      return h('div', { className: 'crop-global' }, mode === 'health'
        ? [head, ...healthNodes]
        : [head, loadingCard, metrics, chart, improvements, pitfalls, works]);
    }

    /** Sidebar glyph for the global panel row; the shell owns the row itself. */
    function CropPanelIcon({ size, active }) {
      return h('svg', {
        viewBox: '0 0 16 16',
        width: size,
        height: size,
        fill: 'none',
        stroke: 'currentColor',
        strokeWidth: 1.3,
        strokeLinecap: 'round',
        strokeLinejoin: 'round',
        'data-dsh-panel-entry': PANEL_ID,
        'aria-hidden': true,
      }, [
        h('path', { key: 'axis', d: 'M2.5 2.5v11h11' }),
        h('path', { key: 'bars', d: 'M5.5 10.5V7M8 10.5V4.5M10.5 10.5V6' }),
      ]);
    }

    function installStyles() {
      const existing = document.getElementById(STYLE_ID);
      if (existing) return existing;
      const style = document.createElement('style');
      style.id = STYLE_ID;
      style.textContent = VIEW_CSS;
      document.head.append(style);
      return style;
    }

    function apply(ctx) {
      ctx.effect(() => {
        const style = installStyles();
        return () => { style.remove(); };
      }, 'crop: glass styles');
      ctx.effect(() => ctx.slots.inject('conversation.view', () => ctx.slots.register({
        name: 'conversation.view',
        id: VIEW_ID,
        order: 30,
        label: () => 'Crop',
        inject: (sessionId) => ({ sessionId }),
      }, View)), 'crop: conversation view');
      ctx.effect(() => ctx.slots.inject('sidebar.panellist', () => ctx.slots.register({
        name: 'sidebar.panellist',
        id: PANEL_ID,
        order: PANEL_ORDER,
        label: () => 'Crop 全局',
      }, CropPanelIcon)), 'crop: global panel row');
      ctx.effect(() => ctx.slots.inject('main', () => ctx.slots.register({
        name: 'main',
        key: PANEL_ID,
      }, GlobalPanel)), 'crop: global panel page');
    }

    exports.apply = apply;
    exports.inject = ['slots'];
    return module.exports;
  },
});
