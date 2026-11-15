/**
 * DSH web client half for Agent Crop.
 *
 * Session view tab next to 轨迹: is this work going to finish smoothly, what does
 * the AI itself say about the slowdown, and which tool is the bottleneck.
 * Hand-written in the client module system's lazy-CJS factory format.
 *
 * Every section is a frosted-glass card. The recipe is deliberately the same one
 * the wallpaper skins paint behind message text (rgb(242 245 250 / bubble-alpha)
 * in light, rgb(16 22 42 / bubble-alpha * .8) in dark, blurred by bubble-blur),
 * so this view reads as part of the conversation instead of a flat panel over
 * the illustration. Both knobs come from the skin center's Background card and
 * fall back to its defaults; on a skinless install the fallbacks still give a
 * readable card, and the tokens below follow the active theme either way.
 */
window.__ModuleLoader__.load({
  id: 'agent-crop-dsh',
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    const React = require('react');
    const h = React.createElement;

    const VIEW_ID = 'crop';
    const ENDPOINT = 'http://127.0.0.1:8765/api/session';
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
      tdRight: { padding: '6px 0', borderBottom: '1px solid var(--dsw-alias-border-l1, rgba(44, 58, 115, .16))', textAlign: 'right', opacity: 0.8 },
      fail: { color: '#e5484d' },
      quiet: { fontSize: '12px' },
    };

    function mmss(minutes) {
      const seconds = Math.round((minutes || 0) * 60);
      return `${Math.floor(seconds / 60)}:${String(seconds % 60).padStart(2, '0')}`;
    }

    function ago(ms) {
      if (!ms) return '';
      const minutes = Math.max(0, Math.round((Date.now() - ms) / 60000));
      return minutes < 60 ? `${minutes} 分钟前` : `${Math.round(minutes / 60)} 小时前`;
    }

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
      }, 'crop: view glass styles');
      ctx.effect(() => ctx.slots.inject('conversation.view', () => ctx.slots.register({
        name: 'conversation.view',
        id: VIEW_ID,
        order: 30,
        label: () => 'Crop',
        inject: (sessionId) => ({ sessionId }),
      }, View)), 'crop: conversation view');
    }

    exports.apply = apply;
    exports.inject = ['slots'];
    return module.exports;
  },
});
