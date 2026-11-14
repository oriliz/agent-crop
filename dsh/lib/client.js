/**
 * DSH web client half for Agent Crop.
 *
 * A "lap board" for one work session: the running round, the recent splits,
 * and one sentence about what to change. It sits as a view tab next to 轨迹.
 * Hand-written in the client module system's lazy-CJS factory format.
 */
window.__ModuleLoader__.load({
  id: 'ai-coding-efficiency-kit-dsh',
  factory: (require) => {
    const module = { exports: {} };
    const exports = module.exports;
    const React = require('react');
    const h = React.createElement;

    const VIEW_ID = 'crop';
    const ENDPOINT = 'http://127.0.0.1:8765/api/session';

    const S = {
      page: { padding: '22px 26px 26px', display: 'grid', gap: '18px', maxWidth: '720px', fontSize: '13px' },
      head: { display: 'flex', alignItems: 'flex-end', justifyContent: 'space-between', gap: '16px' },
      timer: { fontSize: '34px', fontWeight: 600, lineHeight: 1, fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.01em' },
      caption: { fontSize: '12px', opacity: 0.55, marginTop: '6px' },
      redline: { fontSize: '12px', opacity: 0.55, fontVariantNumeric: 'tabular-nums' },
      track: { height: '3px', background: 'currentColor', opacity: 0.12, position: 'relative', marginTop: '10px' },
      fill: (percent, over) => ({ position: 'absolute', inset: '0 auto 0 0', width: `${Math.min(100, percent)}%`, background: over ? '#e5484d' : '#3b82f6', opacity: 1 }),
      verdict: { borderLeft: '3px solid #3b82f6', padding: '2px 0 2px 12px', fontSize: '14px', lineHeight: 1.5 },
      table: { width: '100%', borderCollapse: 'collapse', fontVariantNumeric: 'tabular-nums' },
      th: { textAlign: 'right', fontWeight: 500, fontSize: '11px', opacity: 0.5, padding: '0 0 6px', borderBottom: '1px solid currentColor' },
      thLeft: { textAlign: 'left', fontWeight: 500, fontSize: '11px', opacity: 0.5, padding: '0 0 6px', borderBottom: '1px solid currentColor' },
      td: { textAlign: 'right', padding: '6px 0', borderBottom: '1px solid rgba(127,127,127,.16)' },
      tdLeft: { textAlign: 'left', padding: '6px 0', borderBottom: '1px solid rgba(127,127,127,.16)' },
      mark: (live) => ({ display: 'inline-block', width: '6px', height: '6px', borderRadius: '3px', background: live ? '#3b82f6' : 'transparent', border: live ? 'none' : '1px solid rgba(127,127,127,.5)', marginRight: '8px', verticalAlign: 'middle' }),
      warn: { color: '#e5484d' },
      quiet: { fontSize: '12px', opacity: 0.5 },
      chips: { display: 'flex', gap: '8px', flexWrap: 'wrap', fontSize: '12px' },
      chip: { border: '1px solid rgba(127,127,127,.35)', padding: '2px 8px', borderRadius: '3px' },
    };

    function fmt(minutes) {
      const value = Math.round((minutes || 0) * 60);
      const m = Math.floor(value / 60);
      const s = value % 60;
      return `${m}:${String(s).padStart(2, '0')}`;
    }

    function useSessionSummary(sessionId) {
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

    function verdict(data) {
      const wait = Math.round(((data.share || {}).wait || 0) * 100);
      const failure = (data.anomalies || []).find((item) => item.kind === 'failure');
      if (wait >= 25) return `花在等你回话的时间占 ${wait}%，这次慢不在 AI 身上。`;
      if (failure && failure.count >= 2) return `${failure.tool} 失败 ${failure.count} 次，先看它比继续跑更省。`;
      if (data.failures > 0) return `有 ${data.failures} 次工具失败，分散在不同工具上，属于正常损耗。`;
      return '这一轮没有明显卡点，可以继续。';
    }

    function View({ sessionId }) {
      const { data, error } = useSessionSummary(sessionId);
      if (error || !data) {
        return h('div', { style: S.page },
          h('div', { style: S.timer }, '--:--'),
          h('div', { style: S.quiet }, '数据服务没起来。运行 crop serve，或重启 dsh 后再看。'));
      }
      const minutes = data.current_minutes || 0;
      const percent = Math.round((minutes / 30) * 100);
      const over = minutes >= 30;
      const splits = data.recent_blocks || [];
      return h('div', { style: S.page },
        h('div', null,
          h('div', { style: S.head },
            h('div', null,
              h('div', { style: S.timer, ...(over ? S.warn : {}) }, fmt(minutes)),
              h('div', { style: S.caption }, `这一轮 · ${data.latest_work || '未登记工作'}`)),
            h('div', { style: S.redline }, `红线 ${fmt(30)}`)),
          h('div', { style: S.track }, h('div', { style: S.fill(percent, over) }))),
        h('div', { style: S.verdict }, verdict(data)),
        h('div', null,
          h('table', { style: S.table },
            h('thead', null, h('tr', null,
              h('th', { style: S.thLeft }, '最近几轮'),
              h('th', { style: S.th }, '用时'),
              h('th', { style: S.th }, '工具'),
              h('th', { style: S.th }, '失败'))),
            h('tbody', null, splits.length
              ? splits.map((row, index) => h('tr', { key: index },
                  h('td', { style: S.tdLeft }, h('i', { style: S.mark(index === 0) }), index === 0 ? '本轮结束' : `上 ${index} 轮`),
                  h('td', { style: S.td }, fmt(row.minutes)),
                  h('td', { style: S.td }, row.tools),
                  h('td', { style: { ...S.td, ...(row.failed ? S.warn : {}) } }, row.failed)))
              : h('tr', null, h('td', { style: S.tdLeft, colSpan: 4 }, h('span', { style: S.quiet }, '还没有完成的轮次')))))),
        h('div', { style: S.chips },
          h('span', { style: S.chip }, `本会话 ${fmt(data.minutes)}`),
          h('span', { style: S.chip }, `${data.blocks} 轮`),
          h('span', { style: S.chip }, `工具 ${data.tools_total} 次`),
          h('span', { style: S.chip }, `失败 ${data.failures} 次`),
          h('span', { style: S.chip }, `等你 ${fmt(data.wait_minutes)}`)));
    }

    function apply(ctx) {
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
