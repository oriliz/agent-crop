/**
 * DSH web client half for Agent Crop.
 *
 * Two surfaces, one data source (the local crop_serve on 127.0.0.1:8765):
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
    const BASE = 'http://127.0.0.1:8765';
    const ENDPOINT = `${BASE}/api/session`;
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
  max-width: 720px;
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

    function useSummary(days) {
      const [state, setState] = React.useState({ data: null, error: null, at: 0 });
      React.useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            const response = await fetch(`${BASE}/api/summary?days=${days}`, { cache: 'no-store' });
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

    function View({ sessionId }) {
      const { data, error } = useSession(sessionId);
      if (error || !data) {
        return h('div', { className: 'crop-page' },
          h('div', { className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.quiet }, '数据服务没起来：运行 crop serve，或重启 dsh。')));
      }

      const work = data.active_work || null;
      const blocks = data.recent_blocks || [];
      const tools = data.tool_rank || [];
      const verdict = VERDICT[data.latest_verdict] || VERDICT.unknown;
      const color = work ? lampColor(work.ratio) : 'hsl(140 72% 52%)';
      const total = blocks.reduce((sum, row) => sum + (row.minutes || 0), 0) || 1;

      const header = h('div', { key: 'head', className: 'crop-card' },
        h('div', { style: S.head },
          h('div', { style: S.headMain },
            h('div', { style: S.goal }, work ? work.goal || work.work_id : '本会话还没有工作'),
            h('div', { className: 'crop-muted', style: S.sub }, work ? `${work.work_id} · 进行中` : '')),
          h('div', { style: { textAlign: 'right', flex: 'none' } },
            h('div', { style: S.timer },
              work ? h('i', { style: { ...S.lamp(color), verticalAlign: 'middle', marginRight: '8px' } }) : null,
              work ? mmss(work.minutes) : '--:--'))));

      const verdictCard = h('div', {
        key: 'verdict',
        className: 'crop-card crop-card--verdict',
        style: { borderLeftColor: verdict.color },
      },
        h('div', null,
          h('span', { style: { color: verdict.color, fontWeight: 600 } }, verdict.label),
          h('span', { className: 'crop-muted', style: S.meta }, ` · AI 判断 · ${data.latest_cause ? ago(data.latest_at) : '还没有记录'}`)),
        h('q', { style: { ...S.quote, marginTop: '6px', display: 'block' } },
          data.latest_cause || '被红线提醒或卡住时，AI 会在这里写一句为什么'));

      const toolRows = tools.length
        ? h('div', { key: 'tools', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, '工具排行（按失败次数）'),
            h('table', { style: S.table }, h('tbody', null,
              tools.map((item) => h('tr', { key: item.name },
                h('td', { style: S.td }, item.name),
                h('td', { style: { ...S.tdRight, ...(item.failures ? S.fail : {}) } },
                  `${item.calls} 次 · 失败 ${item.failures}`))))))
        : null;

      const timeline = blocks.length
        ? h('div', { key: 'timeline', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, '最近几轮的形状（宽度 = 用时，红点 = 有失败）'),
            h('div', { style: S.tl }, blocks.slice().reverse().map((row, index) => h('div', {
              key: index,
              title: `${mmss(row.minutes)} · 工具 ${row.tools} · 失败 ${row.failed}`,
              style: {
                width: `${Math.max(3, (row.minutes / total) * 100)}%`,
                background: '#3b82f6',
                opacity: index === blocks.length - 1 ? 1 : 0.55,
                borderRadius: '3px',
                position: 'relative',
              },
            }, row.failed ? h('i', {
              style: { position: 'absolute', right: '2px', top: '2px', width: '6px', height: '6px', borderRadius: '3px', background: '#e5484d' },
            }) : null))),
            h('div', { className: 'crop-muted', style: S.axis },
              h('span', null, '早'),
              h('span', null, '现在')))
        : null;

      const works = (data.session_works || []).length
        ? h('div', { key: 'works', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, '本会话的工作'),
            h('table', { style: S.table }, h('tbody', null,
              data.session_works.map((item) => h('tr', { key: item.work_id },
                h('td', { style: S.td },
                  h('i', { style: S.lamp(lampColor(item.ratio)) }), item.work_id),
                h('td', { style: S.tdRight }, mmss(item.minutes)))))))
        : null;

      return h('div', { className: 'crop-page' }, [header, verdictCard, toolRows, timeline, works]);
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
            const response = await fetch(`${BASE}/api/global?days=${days}`, { cache: 'no-store' });
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

      React.useEffect(() => {
        if (!selected) { setDetail(null); return undefined; }
        let alive = true;
        (async () => {
          try {
            const response = await fetch(`${BASE}/api/day?date=${encodeURIComponent(selected)}`, { cache: 'no-store' });
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
              global.error ? '数据服务没起来：运行 crop serve，或重启 dsh。' : '正在读取…'))
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
              improves.slice(0, 4).map((item, index) => h('li', { key: index }, `「${item.improve}」`))))
        : null;

      const categories = (data && data.categories) || [];
      const pitfalls = categories.length
        ? h('div', { key: 'pitfalls', className: 'crop-card' },
            h('div', { className: 'crop-muted', style: S.caption }, '还在重复的坑（AI 自述的归因类别）'),
            h('div', { className: 'crop-tags' },
              categories.slice(0, 6).map((item) => h('span', { key: item.category, className: 'crop-tag' }, `${item.category} ×${item.count}`))))
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
                        h('i', { style: S.lamp(LAMP[row.level] || '#6b7280') }), row.level || '—'),
                      h('td', { style: S.td },
                        h('div', { className: 'crop-work-title' }, row.goal || row.work_id)),
                      h('td', { style: S.td }, row.type || '—'),
                      h('td', { style: S.td }, row.difficulty || '—'),
                      h('td', { style: S.tdRight }, fmtMinutes(row.minutes)),
                      h('td', { style: S.tdRight }, row.wait_minutes ? fmtMinutes(row.wait_minutes) : '—')))),
                ])
              : h('div', { className: 'crop-muted', style: S.quiet }, '这段时间还没有工作记录。先用 probe_report 认领一条。'))
        : null;

      return h('div', { className: 'crop-global' }, [head, loadingCard, metrics, chart, improvements, pitfalls, works]);
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
