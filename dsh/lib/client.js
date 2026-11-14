/**
 * DSH web client half for Agent Crop.
 *
 * Session view tab next to 轨迹: is this work going to finish smoothly, and if not,
 * why does the AI itself say it is slow. Numbers from the local dashboard server.
 * Hand-written in the client module system's lazy-CJS factory format.
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

    // 0 → green, 0.6 → yellow, 1.5 → red; clamped, hue interpolated.
    function lampColor(ratio) {
      const r = Math.max(0, Math.min(1.5, ratio || 0));
      const hue = r < 0.6 ? 140 - (140 - 48) * (r / 0.6) : 48 - 48 * ((r - 0.6) / 0.9);
      return `hsl(${hue.toFixed(0)} 72% 52%)`;
    }

    function lampLabel(level) {
      return level === 'red' ? '红' : level === 'yellow' ? '黄' : '绿';
    }

    const S = {
      page: { padding: '22px 26px', display: 'grid', gap: '20px', maxWidth: '720px', fontSize: '13px' },
      head: { display: 'flex', justifyContent: 'space-between', alignItems: 'baseline', gap: '12px' },
      goal: { fontSize: '15px' },
      id: { fontSize: '12px', opacity: 0.45, fontVariantNumeric: 'tabular-nums', marginTop: '2px' },
      timer: { fontSize: '34px', fontWeight: 600, lineHeight: 1, fontVariantNumeric: 'tabular-nums', letterSpacing: '-0.01em' },
      unit: { fontSize: '14px', opacity: 0.5, marginLeft: '4px' },
      badge: (color) => ({ display: 'inline-block', marginLeft: '10px', fontSize: '12px', padding: '1px 8px', borderRadius: '10px', background: color.replace('52%', '52% / .18'), color }),
      base: { fontSize: '12px', opacity: 0.5, marginTop: '8px' },
      track: { height: '4px', background: 'rgba(255,255,255,.08)', borderRadius: '2px', marginTop: '10px', overflow: 'hidden' },
      fill: (color, percent) => ({ height: '100%', width: `${Math.min(100, percent)}%`, background: color }),
      row: { display: 'flex', justifyContent: 'space-between', fontSize: '12px', opacity: 0.5, marginTop: '6px', fontVariantNumeric: 'tabular-nums' },
      split: { display: 'flex', height: '10px', borderRadius: '5px', overflow: 'hidden', background: 'rgba(255,255,255,.06)' },
      legend: { display: 'flex', gap: '18px', fontSize: '12px', opacity: 0.65, marginTop: '8px', flexWrap: 'wrap' },
      dot: (color) => ({ display: 'inline-block', width: '7px', height: '7px', borderRadius: '4px', marginRight: '6px', verticalAlign: 'middle', background: color }),
      cause: (color) => ({ borderLeft: `3px solid ${color}`, padding: '2px 0 2px 12px' }),
      quote: { fontSize: '14px', margin: 0 },
      meta: { fontSize: '12px', opacity: 0.45, marginTop: '4px' },
      caption: { fontSize: '11px', opacity: 0.45, marginBottom: '8px' },
      table: { width: '100%', borderCollapse: 'collapse', fontVariantNumeric: 'tabular-nums' },
      th: { textAlign: 'right', fontWeight: 500, fontSize: '11px', opacity: 0.45, padding: '0 0 6px', borderBottom: '1px solid rgba(255,255,255,.18)' },
      thLeft: { textAlign: 'left', fontWeight: 500, fontSize: '11px', opacity: 0.45, padding: '0 0 6px', borderBottom: '1px solid rgba(255,255,255,.18)' },
      td: { textAlign: 'right', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,.08)' },
      tdLeft: { textAlign: 'left', padding: '6px 0', borderBottom: '1px solid rgba(255,255,255,.08)' },
      quiet: { fontSize: '12px', opacity: 0.4 },
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
        return h('div', { style: S.page }, h('div', { style: S.quiet }, '数据服务没起来：运行 crop serve，或重启 dsh。'));
      }

      const work = data.active_work || null;
      const blocks = data.recent_blocks || [];
      const waitPct = Math.round(((data.share || {}).wait || 0) * 100);
      const workPct = Math.round(((data.share || {}).work || 0) * 100);
      const restPct = Math.max(0, 100 - workPct - waitPct);
      const color = work ? lampColor(work.ratio) : 'hsl(140 72% 52%)';

      const header = h('div', { style: S.head, key: 'head' },
        h('div', null,
          h('div', { style: S.goal }, work ? work.goal || work.work_id : '本会话还没有工作'),
          h('div', { style: S.id }, work ? `${work.work_id} · 进行中` : '')),
        h('div', { style: S.id }, 'agent-crop'));

      const timer = work ? h('div', { key: 'timer' },
        h('div', null,
          h('span', { style: S.timer }, mmss(work.minutes)),
          h('span', { style: S.unit }, '已用'),
          h('span', { style: S.badge(color) }, lampLabel(work.level))),
        h('div', { style: S.base },
          `基线 ${Math.round(work.baseline)} 分 · ${work.baseline_source === 'history' ? '同类历史中位' : '默认值（这类工作还没有样本）'}`),
        h('div', { style: S.track }, h('div', { style: S.fill(color, (work.ratio / 1.5) * 100) })),
        h('div', { style: S.row },
          h('span', null, `${work.ratio.toFixed(2)} × 基线`),
          h('span', null, '红线上限 1.5 ×'))) : null;

      const split = h('div', { key: 'split' },
        h('div', { style: S.caption }, '时间构成'),
        h('div', { style: S.split },
          h('div', { style: { width: `${workPct}%`, background: '#3b82f6' } }),
          h('div', { style: { width: `${waitPct}%`, background: '#f59e0b' } }),
          h('div', { style: { width: `${restPct}%`, background: 'rgba(255,255,255,.18)' } })),
        h('div', { style: S.legend },
          h('span', null, h('i', { style: S.dot('#3b82f6') }), `推进 ${workPct}%（${mmss(data.work_minutes)}）`),
          h('span', null, h('i', { style: S.dot('#f59e0b') }), `等你 ${waitPct}%（${mmss(data.wait_minutes)}）`),
          h('span', null, h('i', { style: S.dot('rgba(255,255,255,.3)') }), `未细分 ${restPct}%`)));

      const cause = h('div', { key: 'cause', style: S.cause(data.latest_cause ? color : 'rgba(255,255,255,.2)') },
        h('q', { style: S.quote }, data.latest_cause || 'AI 还没有上报归因'),
        h('div', { style: S.meta },
          data.latest_cause
            ? `AI 自述 · ${data.latest_category || '未分类'} · ${ago(data.latest_at)}`
            : '被红线提醒或卡住时，它会写一句'));

      const sessionWorks = (data.session_works || []).length
        ? h('div', { key: 'works' },
            h('div', { style: S.caption }, '本会话的工作'),
            h('table', { style: S.table }, h('tbody', null,
              data.session_works.map((item) => h('tr', { key: item.work_id },
                h('td', { style: S.tdLeft }, h('i', { style: S.dot(lampColor(item.ratio)) }), item.work_id),
                h('td', { style: S.td }, mmss(item.minutes)))))))
        : null;

      const rounds = h('div', { key: 'rounds' },
        h('div', { style: S.caption }, '最近几轮'),
        h('table', { style: S.table },
          h('thead', null, h('tr', null,
            h('th', { style: S.thLeft }, '轮次'),
            h('th', { style: S.th }, '用时'),
            h('th', { style: S.th }, '工具'),
            h('th', { style: S.th }, '失败'))),
          h('tbody', null, blocks.length
            ? blocks.map((row, index) => h('tr', { key: index },
                h('td', { style: S.tdLeft }, index === 0 ? '本轮' : `上 ${index} 轮`),
                h('td', { style: S.td }, mmss(row.minutes)),
                h('td', { style: S.td }, row.tools),
                h('td', { style: S.td }, row.failed)))
            : h('tr', null, h('td', { style: S.tdLeft, colSpan: 4 }, h('span', { style: S.quiet }, '还没有完成的轮次'))))));

      return h('div', { style: S.page }, [header, timer, split, cause, sessionWorks, rounds]);
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
