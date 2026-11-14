/**
 * DSH web client half for Agent Crop.
 *
 * Adds a "Crop" tab next to 对话 / 轨迹 in the conversation view. It is a
 * session-scoped reading surface: where the time went, what is off, and one
 * sentence about what to change. Numbers come from the local dashboard server.
 *
 * Hand-written in the client module system's lazy-CJS factory format, so the
 * package needs no build step.
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

    const ui = {
      page: { padding: '20px 24px', display: 'grid', gap: '14px', maxWidth: '760px' },
      row: { display: 'flex', alignItems: 'baseline', gap: '10px' },
      label: { fontSize: '12px', opacity: 0.55, letterSpacing: '0.02em' },
      big: { fontSize: '28px', fontWeight: 600, fontVariantNumeric: 'tabular-nums' },
      mid: { fontSize: '18px', fontWeight: 600, fontVariantNumeric: 'tabular-nums' },
      card: { border: '1px solid rgba(127,127,127,.22)', borderRadius: '10px', padding: '14px 16px' },
      bar: { display: 'flex', height: '10px', borderRadius: '5px', overflow: 'hidden', marginTop: '10px', background: 'rgba(127,127,127,.15)' },
      legend: { display: 'flex', gap: '16px', marginTop: '8px', fontSize: '12px', opacity: 0.7, flexWrap: 'wrap' },
      dot: (color) => ({ display: 'inline-block', width: '8px', height: '8px', borderRadius: '4px', background: color, marginRight: '6px' }),
      list: { margin: '8px 0 0', paddingLeft: '16px', fontSize: '13px' },
      empty: { fontSize: '13px', opacity: 0.45, marginTop: '8px' },
      hint: { fontSize: '12px', opacity: 0.45 },
    };

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

    function conclusion(data) {
      const share = data.share || {};
      const wait = Math.round((share.wait || 0) * 100);
      const anomalies = data.anomalies || [];
      const failure = anomalies.find((item) => item.kind === 'failure');
      const rework = anomalies.find((item) => item.kind === 'rework');
      if (wait >= 25) return `时间主要在等你回话（${wait}%）——这不是 AI 慢，是节奏断了。`;
      if (failure && failure.count >= 2) return `${failure.tool} 失败了 ${failure.count} 次，先看它，比继续跑更省时间。`;
      if (rework) return `工具在反复重试（${rework.tool || '同一类'}），大概在原地打转。`;
      if (data.failures > 0) return `有 ${data.failures} 次工具失败，没有集中在一处，属于正常损耗。`;
      return '这一轮没有明显卡点。';
    }

    function Section({ title, children }) {
      return h('div', { style: ui.card },
        h('div', { style: ui.label }, title),
        children);
    }

    function View({ sessionId }) {
      const { data, error } = useSessionSummary(sessionId);
      if (error || !data) {
        return h('div', { style: ui.page },
          h('div', { style: ui.label }, 'Crop'),
          h('div', { style: ui.hint }, '数据服务没起来：先运行 crop serve（或重启 dsh）'));
      }
      const share = data.share || {};
      const workPct = Math.round((share.work || 0) * 100);
      const waitPct = Math.round((share.wait || 0) * 100);
      const restPct = Math.max(0, 100 - workPct - waitPct);
      const anomalies = data.anomalies || [];
      const tools = data.tool_top || [];
      return h('div', { style: ui.page },
        // 1. what is happening now
        h('div', null,
          h('div', { style: ui.label }, data.latest_work ? `当前工作 · ${data.latest_work}` : '当前工作'),
          h('div', { style: { fontSize: '15px', marginTop: '4px' } },
            data.goal || '还没有登记目标（下次开工让 AI 回显一句）')),
        // 2. the three numbers that matter
        h('div', { style: { display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '12px' } },
          h('div', { style: ui.card },
            h('div', { style: ui.label }, '进行中'),
            h('div', { style: ui.big }, `${Math.round(data.current_minutes || 0)}m`),
            h('div', { style: ui.hint }, `红线 30m`)),
          h('div', { style: ui.card },
            h('div', { style: ui.label }, '本会话'),
            h('div', { style: ui.big }, `${Math.round(data.minutes || 0)}m`),
            h('div', { style: ui.hint }, `${data.blocks} 个回合 · ${data.tools_total} 次工具`)),
          h('div', { style: ui.card },
            h('div', { style: ui.label }, '等你回话'),
            h('div', { style: ui.big }, `${Math.round(data.wait_minutes || 0)}m`),
            h('div', { style: ui.hint }, `${waitPct}% 的时间`))),
        // 3. composition
        h(Section, { title: '时间花在哪' },
          h('div', { style: ui.bar },
            h('div', { style: { width: `${workPct}%`, background: '#3b82f6' } }),
            h('div', { style: { width: `${waitPct}%`, background: '#f59e0b' } }),
            h('div', { style: { width: `${restPct}%`, background: 'rgba(127,127,127,.25)' } })),
          h('div', { style: ui.legend },
            h('span', null, h('i', { style: ui.dot('#3b82f6') }), `推进 ${workPct}%`),
            h('span', null, h('i', { style: ui.dot('#f59e0b') }), `等你 ${waitPct}%`),
            h('span', null, h('i', { style: ui.dot('rgba(127,127,127,.4)') }), `未细分 ${restPct}%`))),
        // 4. what is off
        h(Section, { title: '异常' },
          anomalies.length
            ? h('ul', { style: ui.list }, anomalies.map((item, index) =>
                h('li', { key: index },
                  item.kind === 'failure'
                    ? `${item.tool} 失败 ${item.count} 次`
                    : `${item.tool || '同一工具'} 连续调用 ${item.count} 次`)))
            : h('div', { style: ui.empty }, '没有异常')),
        // 5. where the calls went
        h(Section, { title: '工具分布' },
          tools.length
            ? h('div', { style: { display: 'flex', gap: '14px', flexWrap: 'wrap', marginTop: '8px', fontSize: '13px' } },
                tools.map(([name, count]) => h('span', { key: name }, `${name} ×${count}`)))
            : h('div', { style: ui.empty }, '还没有工具调用')),
        // 6. one sentence
        h(Section, { title: '一句话' },
          h('div', { style: { fontSize: '14px', marginTop: '6px' } }, conclusion(data))));
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
