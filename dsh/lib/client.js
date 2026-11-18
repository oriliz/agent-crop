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
    const MEDAL_ICON = 'data:image/png;base64,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';

    const PERSONA_LABEL = { owner: '奴隶主', senior: '和蔼的前辈', colleague: '经验丰富的同事', free: '放任自由的家长' };
    const personaIcon = (persona) => (persona && persona !== 'owner' ? '/api/agent-crop/think-icon' : '/api/agent-crop/whip-icon');

    /* The four nudge voices, as the settings page offers them. `owner` is the
       whipped copy everyone knows; the other three are neutral reminders. */
    const PERSONA_CHOICES = [
      { value: 'owner', label: '奴隶主', hint: '默认。重时间和效率，超时催收尾、催交活。' },
      { value: 'senior', label: '和蔼的前辈', hint: '不抽鞭子；提醒你卡住可以问、先说说打算怎么试。' },
      { value: 'colleague', label: '经验丰富的同事', hint: '不抽鞭子；提醒你先拆最小可验证的一步。' },
      { value: 'free', label: '放任自由的家长', hint: '完全不提醒，自己安排。' },
    ];
    const personaLabelOf = (value) => (PERSONA_CHOICES.find(item => item.value === value) || PERSONA_CHOICES[0]).label;


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
  max-width: none;
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

.crop-row-warn td {
  background: rgba(234,179,8,.055);
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
.crop-day-work { display: grid; grid-template-columns: 8px minmax(0, 1fr) minmax(60px, 1.1fr) 48px; align-items: center; gap: 8px; padding: 6px 0; border-top: 1px solid rgba(160, 180, 230, .08); font-size: 12px; }
.crop-day-work:first-child { border-top: 0; }
.crop-day-work-dot { width: 7px; height: 7px; border-radius: 50%; background: #5b8cff; }
.crop-day-work-name { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
.crop-day-work-bar { height: 6px; border-radius: 999px; background: rgba(120, 140, 190, .14); overflow: hidden; }
.crop-day-work-bar i { display: block; height: 100%; border-radius: 999px; background: linear-gradient(90deg, #4f7cf0, #6ea0ff); }
.crop-day-work-min { text-align: right; color: #b9c6e2; font-variant-numeric: tabular-nums; }
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
.crop-session-grid { display: grid; grid-template-columns: minmax(0, 1fr) minmax(480px, 1.05fr); gap: 14px; align-items: start; }
.crop-session-main { display: grid; gap: 14px; min-width: 0; }
.crop-reflect { position: sticky; top: 8px; scroll-behavior: smooth; max-height: calc(100vh - 240px); overflow-y: auto; }
@media (max-width: 900px) {
  .crop-session-grid { grid-template-columns: 1fr; }
  .crop-reflect { position: static; max-height: none; }
}
.crop-pair { display: grid; grid-template-columns: 24px 92px minmax(0, 1fr) minmax(0, 1.2fr); gap: 8px; align-items: start; padding: 8px 0; border-top: 1px solid rgba(160, 180, 230, .10); }


.crop-pair-no { color: #7f9fe0; font-variant-numeric: tabular-nums; padding-top: 8px; }
.crop-origin { display: flex; flex-direction: column; align-items: center; gap: 4px; padding-top: 4px; }
.crop-origin-icon { width: 26px; height: 26px; object-fit: contain; }
.crop-origin-tag { font-size: 9.5px; line-height: 15px; padding: 0 6px; border-radius: 999px; border: 1px solid rgba(160,180,230,.22); color: #a9b6d4; white-space: nowrap; }
.crop-origin-tag--self { background: #f0d48a; color: #1a1608; border-color: transparent; font-weight: 700; }
.crop-pair-label { display: block; font-size: 9.5px; color: #7785a6; margin-bottom: 2px; }
.crop-pair-text { line-height: 1.55; overflow-wrap: anywhere; word-break: break-word; }
.crop-pair-problem .crop-pair-text { color: #a9b6d4; }
.crop-pair-meta { margin-top: 4px; font-size: 10px; color: #7785a6; }
.crop-pair-arrow { color: #5f6e93; text-align: center; padding-top: 14px; }
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
.crop-improve-list { display: grid; gap: 0; }
.crop-improve-row { display: grid; grid-template-columns: 10px minmax(0, 1fr); gap: 10px; padding: 10px 0; border-top: 1px solid rgba(160, 180, 230, .10); }
.crop-improve-row:first-child { border-top: 0; }
.crop-improve-dot { width: 8px; height: 8px; margin-top: 7px; border-radius: 50%; background: #e8c46a; }
.crop-improve-body { min-width: 0; }
.crop-improve-quote { font-size: 12.5px; line-height: 1.6; color: #e8edf8; }
.crop-improve-tags { display: flex; flex-wrap: wrap; align-items: center; gap: 6px; margin-top: 5px; }
.crop-chip-sm { font-size: 9.5px; line-height: 15px; padding: 0 7px; border-radius: 999px; border: 1px solid rgba(160, 180, 230, .20); color: #a9b6d4; }
.crop-improve-time { font-size: 10px; color: #7785a6; margin-left: auto; font-variant-numeric: tabular-nums; }
.crop-improve-why { margin-top: 4px; font-size: 11px; line-height: 1.55; color: #7785a6; overflow-wrap: anywhere; }

/* Plugins page → Agent Crop → row → configuration: the nudge voice. */
.crop-config { display: grid; gap: 10px; font-size: 13px; max-width: 560px; }
.crop-config-title { font-size: 13px; font-weight: 600; }
.crop-config-hint { font-size: 12px; line-height: 1.55; color: var(--dsw-alias-label-tertiary, #6b7899); }
.crop-persona-list { display: grid; gap: 8px; }
.crop-persona-option {
  display: flex; gap: 10px; align-items: flex-start;
  padding: 10px 12px; border-radius: 10px;
  border: 1px solid var(--dsw-alias-border-l2, rgba(150, 170, 220, .22));
  cursor: pointer;
}
.crop-persona-option[data-active] {
  border-color: rgba(110, 140, 230, .65);
  background: rgba(110, 140, 230, .08);
}
.crop-persona-option input { margin-top: 2px; accent-color: #5b7cfa; }
.crop-persona-body { display: grid; gap: 2px; }
.crop-config-note { font-size: 12px; color: var(--dsw-alias-label-secondary, #4a5568); }

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
      const fullTitles = data.work_titles_full || {};
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
      const blocks = allBlocks.filter((block) => block.t >= windowStart && block.t <= viewEnd);
      const whips = (timeline.whips || []).filter((whip) => whip.t >= windowStart && whip.t <= viewEnd);
      const waits = (timeline.waits || []).filter((wait) => wait.t >= windowStart && wait.t <= viewEnd);
      const reports = (timeline.reports || []).filter((report) => report.t >= windowStart && report.t <= viewEnd);
      const interventions = data.interventions || [];
      // The right column follows the timeline window: pan/zoom on the left
      // filters the reflection rows on the right.
      const windowInterventions = interventions.filter((item) => item.at >= windowStart && item.at <= viewEnd);
      // Greedy one-to-one: each whip marker claims the nearest still-unused
      // intervention. Two markers never point at the same reflection row.
      const exactLinks = new Map();
      windowInterventions.forEach((item, index) => {
        if (item.reply_to) exactLinks.set(item.reply_to, index);
      });
      const whipLink = new Map();
      {
        const used = new Set();
        for (const whip of whips.slice().sort((a, b) => a.t - b.t)) {
          const exact = exactLinks.get(whip.t);
          let pickIndex = exact != null && !used.has(exact) ? exact : null;
          if (pickIndex == null) {
            const pool = windowInterventions
              .map((item, index) => ({ index, gap: Math.abs((item.at || 0) - whip.t) }))
              .filter((item) => !used.has(item.index))
              .sort((a, b) => a.gap - b.gap);
            // Legacy rows have no reply_to: fall back to nearest-but-unused.
            pickIndex = pool[0] && pool[0].gap <= 6 * 3600 * 1000 ? pool[0].index : null;
          }
          if (pickIndex == null) continue;
          used.add(pickIndex);
          whipLink.set(`${whip.work_id}|${whip.t}`, pickIndex);
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
            h('div', { className: 'crop-tl-name', title: fullTitles[lane.key] || lane.key || '' },
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
      const pendingWork = data.pending_work || null;
      const ended = data.ended_work || null;
      const currentJob = active || pendingWork || ended || null;
      const expect = active && active.expect_min ? active.expect_min : null;
      const current = h('div', { key: 'current', className: 'crop-card crop-sess-current' },
        h('span', { className: 'crop-sess-label' },
          active ? '当前工作' : (pendingWork ? '待确认 · 等人类' : (ended ? '上一件' : '当前工作'))),
        h('span', { className: 'crop-sess-name', title: currentJob ? currentJob.work_id : '' },
          currentJob ? (currentJob.title || currentJob.goal || labelOf(currentJob.work_id)) : '本会话还没有绑定工作'),
        active && expect ? h('div', { className: 'crop-sess-bar' },
          h('i', { style: { width: pct(Math.min(100, ((active.minutes || 0) / expect) * 100)) } })) : null,
        h('span', { className: 'crop-muted crop-small' },
          active
            ? (expect ? `估 ${Math.round(expect)}m / 已 ${Math.round(active.minutes || 0)}m` : '')
            : pendingWork
              ? `已提交收尾 · ${Math.round(pendingWork.minutes || 0)}m · 等人类确认`
              : ended ? `已收尾 · ${Math.round(ended.minutes || 0)}m` : ''));

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
              h('span', { title: fullTitles[item.work_id] || item.work_id }, labelOf(item.work_id)),
              item.state === 'closed' ? h('span', { className: 'crop-chip-sm' }, '已收尾')
                : item.state === 'pending_confirm' ? h('span', { className: 'crop-chip-sm' }, '待确认') : null,
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
            `提醒 ${whips.length} 次 · 窗口内问题 ${windowInterventions.length} 条 · 自驱 ${data.stats ? (data.stats.self_driven || 0) : 0} 次`,
            fixCount ? ` · 要改 ${fixCount}` : '',
            thinkCount ? ` · 要想办法 ${thinkCount}` : ''),
        ]),
        windowInterventions.length
          ? windowInterventions.map((item, index) => {
              const urgent = item.verdict === 'not-justified';
              const needsIdea = urgent && !item.improve;
              const selfDriven = (item.origin || (item.reply_to ? 'reminder' : 'self')) === 'self';
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
                h('div', { key: 'o', className: 'crop-origin' }, [
                  selfDriven
                    ? h('img', { key: 'icon', className: 'crop-origin-icon', src: MEDAL_ICON, alt: '主动', title: '没人提醒，自己发现的问题' })
                    : h('img', { key: 'icon', className: 'crop-origin-icon', src: '/api/agent-crop/whip-icon', alt: '被鞭策', title: '被提醒之后写的' }),
                  h('span', { key: 'tag', className: `crop-origin-tag${selfDriven ? ' crop-origin-tag--self' : ''}` },
                    selfDriven ? (item.phase === 'end' ? '主动总结' : '主动提出') : '被鞭策'),
                ]),
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
              ]);
            })
          : h('div', { className: 'crop-muted crop-small' }, '当前时间窗口内没有问题记录，平移时间轴看看。'));

      // 收尾后这张页不该像没干过活：把上一件的收尾自述留在左栏。
      const endedCard = ended ? h('div', { key: 'ended', className: 'crop-card' },
        h('div', { className: 'crop-muted crop-small crop-tl-cap' },
          `上一件已收尾 · ${ended.title || ended.goal || labelOf(ended.work_id)} · ${Math.round(ended.minutes || 0)}m`),
        h('div', { className: 'crop-pair-text' }, `做了什么：${(ended.report && ended.report.what) || '未记录'}`),
        ended.report && ended.report.cost
          ? h('div', { className: 'crop-pair-meta' }, `时间花在哪：${ended.report.cost}`) : null,
        h('div', { className: 'crop-pair-text' }, `下次怎么更好：${(ended.report && ended.report.next_time) || '未提出改进'}`))
        : null;

      return h('div', { className: 'crop-page' },
        h('div', { className: 'crop-session-grid' }, [
          h('div', { className: 'crop-session-main' }, [head, current, frame, works, endedCard]),
          reflect,
        ]));
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

    function useGlobal(days, host) {
      const [state, setState] = React.useState({ data: null, error: null, at: 0 });
      React.useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            const query = `days=${days}${host ? `&host=${encodeURIComponent(host)}` : ''}`;
            const response = await fetch(`${API}/global?${query}`, { cache: 'no-store' });
            const data = await response.json();
            if (alive) setState({ data, error: null, at: Date.now() });
          } catch (error) {
            if (alive) setState({ data: null, error: String(error), at: Date.now() });
          }
        };
        tick();
        const timer = setInterval(tick, 5000);
        return () => { alive = false; clearInterval(timer); };
      }, [days, host]);
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

    function useLedger(days) {
      const [state, setState] = React.useState({ data: null, error: null, at: 0 });
      React.useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            const response = await fetch(`${API}/ledger?days=${days}`, { cache: 'no-store' });
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
      const [host, setHost] = React.useState('');
      const [selected, setSelected] = React.useState(null);
      const [detail, setDetail] = React.useState(null);
      const [showAll, setShowAll] = React.useState(false);
      const global = useGlobal(days, host);
      const [mode, setMode] = React.useState('overview');
      const health = useHealth(days);
      const ledger = useLedger(days);

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
            }, label)),
            h('select', {
              key: 'host',
              className: 'crop-chip',
              value: host,
              title: '按宿主应用过滤',
              onChange: (event) => { setHost(event.target.value); setSelected(null); },
            }, [
              h('option', { key: '', value: '' }, '全部宿主'),
              ...(((data && data.hosts) || []).map((item) =>
                h('option', { key: item, value: item }, item))),
            ])),
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
                        (() => {
                          const rows = detail.works || [];
                          const maxMinutes = Math.max(1, ...rows.map((row) => Number(row.minutes) || 0));
                          return rows.slice(0, showAll ? undefined : 5).map((item) => h('div', { key: item.work_id, className: 'crop-day-work' }, [
                            h('i', { key: 'd', className: 'crop-day-work-dot' }),
                            h('span', { key: 'n', className: 'crop-day-work-name', title: item.goal_full || item.goal || item.work_id },
                              item.goal || item.work_id),
                            h('div', { key: 'b', className: 'crop-day-work-bar' },
                              h('i', { style: { width: `${Math.max(3, Math.round(((Number(item.minutes) || 0) / maxMinutes) * 100))}%` } })),
                            h('span', { key: 'm', className: 'crop-day-work-min' }, fmtMinutes(item.minutes)),
                          ]));
                        })()),
                      !showAll && (detail.works || []).length > 5
                        ? h('button', { type: 'button', className: 'crop-more', onClick: () => setShowAll(true) }, `+${(detail.works || []).length - 5} 个工作`)
                        : null)))
              : h('div', { className: 'crop-muted', style: S.quiet }, '点上面任意一根柱子看当天明细'))
        : null;

      const improves = (data && data.improvements) || [];
      const improvements = improves.length
        ? h('div', { key: 'improve', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, 'AI 自述的改进（下次可以少走一步）'),
            h('div', { className: 'crop-improve-list' },
              improves.slice(0, 4).map((item, index) => h('div', { key: index, className: 'crop-improve-row' }, [
                h('i', { key: 'd', className: 'crop-improve-dot' }),
                h('div', { key: 'b', className: 'crop-improve-body' }, [
                  h('div', { key: 'q', className: 'crop-improve-quote' }, `「${item.improve}」`),
                  h('div', { key: 'm', className: 'crop-improve-tags' }, [
                    item.goal ? h('span', { key: 'w', className: 'crop-chip-sm', title: item.work_id || '' }, item.goal) : null,
                    item.phase === 'blocked' ? h('span', { key: 'p', className: 'crop-chip-sm' }, '卡点') : null,
                    item.phase === 'end' ? h('span', { key: 'p', className: 'crop-chip-sm' }, '收尾') : null,
                    item.category ? h('span', { key: 'c', className: 'crop-chip-sm' }, enumLabel('category', item.category)) : null,
                    item.t ? h('span', { key: 't', className: 'crop-improve-time' },
                      new Date(item.t).toLocaleString('zh-CN', { month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit' })) : null,
                  ]),
                  item.cause || item.summary ? h('div', { key: 'c', className: 'crop-improve-why' }, `当时：${item.cause || item.summary}`) : null,
                ]),
              ]))))
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
          h('th', { key: 'c', style: { ...cell, textAlign: 'left', fontSize: '11.5px' } }, '趋势（最新完整天）'),
          h('th', { key: 'f', style: { ...cellRight, fontSize: '11.5px' } }, '完整（全窗口）'),
          showClosure ? h('th', { key: 'cl', style: { ...cell, textAlign: 'left', fontSize: '11.5px' } }, '闭环') : null,
        ]));

        const localDay = () => {
          const now = new Date();
          const pad = (value) => String(value).padStart(2, '0');
          return `${now.getFullYear()}-${pad(now.getMonth() + 1)}-${pad(now.getDate())}`;
        };
        const trendColor = (value) => {
          if (value == null) return '#64748b';
          if (value >= 0.9) return '#4ade80';
          if (value >= 0.6) return '#eab308';
          return '#ef4444';
        };
        const trendValue = (day) => (day.coverage != null ? day.coverage : day.completeness);
        const latestComplete = (daily) => {
          const rows = (daily || []).filter((day) => day && day.events);
          if (!rows.length) return null;
          const today = localDay();
          const complete = rows.filter((day) => day.date !== today);
          return complete.length ? complete[complete.length - 1] : null;
        };
        const TrendSpark = ({ row }) => {
          const daily = (row.daily || []).filter((day) => day && day.events);
          if (!daily.length) {
            return h('svg', { width: 140, height: 24, viewBox: '0 0 140 24' },
              h('line', { x1: 4, y1: 12, x2: 136, y2: 12, stroke: '#64748b', strokeWidth: 1.4, strokeDasharray: '3 3' }));
          }
          const width = 140;
          const height = 24;
          const pad = 6;
          const today = localDay();
          const step = daily.length > 1 ? (width - pad * 2) / (daily.length - 1) : 0;
          const points = daily.map((day, index) => {
            const value = trendValue(day) || 0;
            return { x: pad + index * step, y: height - pad - value * (height - pad * 2), value, date: day.date };
          });
          const gradient = `crop-trend-${String(row.tool || '').replace(/[^a-zA-Z0-9-]/g, '')}`;
          const stops = points.map((point, index) => h('stop', {
            key: `s${index}`,
            offset: `${((point.x - pad) / (width - pad * 2)) * 100}%`,
            stopColor: trendColor(point.value),
          }));
          const parts = [
            // userSpaceOnUse：水平线段的 bbox 高度为 0，objectBoundingBox 渐变在 Chrome 下会被吞掉。
            h('defs', { key: 'defs' },
              h('linearGradient', { id: gradient, gradientUnits: 'userSpaceOnUse',
                x1: pad, y1: 0, x2: width - pad, y2: 0 }, stops)),
            h('polyline', {
              key: 'line',
              points: points.map((point) => `${point.x},${point.y}`).join(' '),
              fill: 'none',
              stroke: `url(#${gradient})`,
              strokeWidth: 1.8,
              strokeLinecap: 'round',
              strokeLinejoin: 'round',
            }),
          ];
          points.forEach((point, index) => {
            const outgoing = point.date === today;
            parts.push(h('circle', {
              key: `p${index}`, cx: point.x, cy: point.y,
              r: outgoing ? 2.2 : 2.6,
              fill: outgoing ? 'none' : trendColor(point.value),
              stroke: trendColor(point.value),
              strokeWidth: outgoing ? 1.2 : 0,
            }));
          });
          return h('svg', { width, height, viewBox: `0 0 ${width} ${height}` }, parts);
        };

        const rowEl = (row, showClosure) => {
          const red = row.lamp === 'red' || (row.events > 0 && row.session_coverage === 0);
          const color = colorOf(row);
          const coverageNote = row.coverage_denominator == null ? '' : `${row.coverage_numerator} / ${row.coverage_denominator}`;
          const missing = Object.entries(row.missing_fields || {}).map(([key, value]) => `${key}×${value}`).join('、');
          const meter = (value, fill) => h('div', { className: 'crop-meter', style: { background: fill === '#ef4444' ? 'rgba(239,68,68,.20)' : 'rgba(100,116,160,.16)' } },
            h('i', { style: { width: pctWidth(value), background: fill } }),
            h('b', null, pctText(value)));
          const latest = latestComplete(row.daily);
          const latestTone = latest ? trendColor(trendValue(latest)) : null;
          // 行底色只看「最新完整天」：历史坏、当前好的行不该整行红。
          const rowClass = latestTone === '#ef4444'
            ? 'crop-row-red'
            : (latestTone === '#eab308' || latestTone === '#fb923c'
              ? 'crop-row-warn'
              : (latest ? undefined : (red ? 'crop-row-red' : undefined)));
          return h('tr', { key: row.tool, className: rowClass }, [
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
                  : h('div', { key: 'tr' }, [
                      h(TrendSpark, { key: 's', row }),
                      h('div', { key: 'v', className: 'crop-mini',
                        style: { color: latestTone === '#ef4444' ? '#ef4444' : (latest ? '#c7d5ee' : '#7f90b0') } },
                        latest ? `昨天 ${pctText(trendValue(latest))}` : '—'),
                      coverageNote ? h('div', { key: 'w', className: 'crop-mini', style: { color: '#7f90b0' } },
                        `全窗口 ${coverageNote}`) : null,
                    ]),
            ]),
            h('td', { key: 'f', style: { ...cellRight, color: latestTone === '#ef4444' ? '#ef4444' : (row.events ? '#9aa8c4' : undefined) } }, [
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

      const ledgerCard = (() => {
        const rows = (ledger.data && ledger.data.rows) || [];
        if (!ledger.data && !ledger.error) return null;
        return h('div', { key: 'ledger', className: 'crop-card' }, [
          h('div', { key: 'c', className: 'crop-muted', style: S.caption }, '改进台账（前后值 + 证据；状态由数据算，不靠自评）'),
          ledger.error
            ? h('div', { key: 'e', className: 'crop-muted', style: S.quiet }, '台账读不到：检查 dsh 日志里的 crop 报错。')
            : rows.length
              ? h('div', { key: 'l', className: 'crop-improve-list' }, rows.slice(0, 6).map((row) => h('div', {
                  key: `${row.work_id || 'no-work'}-${row.metric}-${row.adopted_at}`,
                  className: 'crop-improve-row',
                }, [
                  h('i', { key: 'd', className: 'crop-improve-dot' }),
                  h('div', { key: 'b', className: 'crop-improve-body' }, [
                    h('div', { key: 'q', className: 'crop-improve-quote' },
                      `${row.metric_label}：${row.before} → ${row.after == null ? '暂无完整周' : row.after}`
                      + (row.delta == null ? '' : `（${Math.round(row.delta * 100)}%）`)),
                    h('div', { key: 'm', className: 'crop-improve-tags' }, [
                      h('span', { key: 's', className: 'crop-chip-sm' }, row.status_label),
                      h('span', { key: 'n', className: 'crop-chip-sm' }, `样本 ${row.samples ? row.samples.before : 0}/${row.samples ? row.samples.after : 0}`),
                      row.work_id ? h('span', { key: 'w', className: 'crop-chip-sm' }, row.work_id) : null,
                    ]),
                    row.evidence ? h('div', { key: 'e', className: 'crop-mini' }, row.evidence) : null,
                  ]),
                ])))
              : h('div', { key: 'e', className: 'crop-muted', style: S.quiet }, '还没有改进记录。采纳改进时记一条 adjustment。'),
        ]);
      })();

      return h('div', { className: 'crop-global' }, mode === 'health'
        ? [head, ...healthNodes]
        : [head, loadingCard, metrics, chart, improvements, ledgerCard, pitfalls, works]);
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

    /**
     * Plugins page configuration for this bundle's row: the nudge voice.
     * The Host owns the form (`form.state` / `form.mutate` over the volatile
     * `persona` field); this page only renders the four choices. `view ===
     * 'summary'` is the row's one-liner fallback, so it renders as text.
     */
    function PersonaSettings(props) {
      const form = props && props.form;
      const stored = form && form.state && form.state.value && form.state.value.persona;
      const [value, setValue] = React.useState(stored || 'owner');
      const [note, setNote] = React.useState('');
      if (props && props.view === 'summary') {
        return h('span', null, `鞭策风格：${personaLabelOf(stored || value)}`);
      }
      if (!form) {
        return h('div', { className: 'crop-config' },
          h('div', { className: 'crop-config-title' }, '鞭策风格'),
          h('div', { className: 'crop-config-hint' }, 'Host 没有为这个插件提供配置表单；暂时只能改 profile 里的 config.persona。'));
      }
      const choose = (next) => {
        if (next === value) return;
        setValue(next);
        setNote('保存中…');
        form.mutate([{ op: 'set', path: ['persona'], value: next }], form.state.revision)
          .then((ok) => setNote(ok ? '已保存，下一条提醒立即生效。' : 'Host 拒绝了这次修改。'))
          .catch((error) => setNote(`保存失败：${error && error.message ? error.message : error}`));
      };
      return h('div', { className: 'crop-config' },
        h('div', { className: 'crop-config-title' }, '鞭策风格'),
        h('div', { className: 'crop-config-hint' }, '决定超时提醒的语气。只有「奴隶主」会抽鞭子；改完立即生效，不用重启。'),
        h('div', { className: 'crop-persona-list' }, PERSONA_CHOICES.map((choice) => h('label', {
          key: choice.value,
          className: 'crop-persona-option',
          'data-active': choice.value === value ? '' : undefined,
        },
          h('input', {
            type: 'radio',
            name: 'crop-persona',
            value: choice.value,
            checked: choice.value === value,
            onChange: () => choose(choice.value),
          }),
          h('span', { className: 'crop-persona-body' },
            h('b', null, choice.label),
            h('span', { className: 'crop-config-hint' }, choice.hint))))),
        h('div', { className: 'crop-config-hint' },
          '提醒里的具体台词用 Markdown 覆盖 ~/.agentcrop/templates/voice/*.md，改完即时生效；常驻协议模板要重启宿主。'),
        note ? h('div', { className: 'crop-config-note' }, note) : null);
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
      // The bundle's own row is `agent-crop-dsh` in its patch; the Plugins
      // page keys a row page as `<bundle>#<rowId>` and supplies the form for
      // the profile entry whose id is the row id.
      ctx.effect(() => ctx.slots.inject('plugins.row.config', () => ctx.slots.register({
        name: 'plugins.row.config',
        key: 'agent-crop-dsh#agent-crop-dsh',
      }, PersonaSettings)), 'crop: nudge voice settings');
    }

    exports.apply = apply;
    exports.inject = ['slots'];
    return module.exports;
  },
});
