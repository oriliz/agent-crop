/**
 * DSH web client half for Agent Crop.
 *
 * Registers a right-sidebar tab that shows live numbers from the local
 * dashboard server (crop serve). Numbers only — the analysis stays with the human.
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

    const TAB_ID = 'ai-coding-efficiency-kit-dsh';
    const ENDPOINT = 'http://127.0.0.1:8765/api/summary';

    const styles = {
      wrap: { padding: '14px 16px', display: 'flex', flexDirection: 'column', gap: '12px' },
      card: { border: '1px solid rgba(127,127,127,.25)', borderRadius: '10px', padding: '10px 12px' },
      key: { fontSize: '12px', opacity: 0.6 },
      value: { fontSize: '20px', fontWeight: 600, marginTop: '2px' },
      row: { display: 'flex', gap: '10px' },
      cell: { flex: 1 },
      list: { margin: 0, paddingLeft: '16px', fontSize: '13px' },
      note: { fontSize: '12px', opacity: 0.55 },
      bar: { height: '6px', borderRadius: '3px', background: 'rgba(127,127,127,.25)', marginTop: '8px' },
      fill: (ratio, red) => ({
        height: '100%',
        width: `${Math.min(100, Math.round(ratio * 100))}%`,
        borderRadius: '3px',
        background: red ? '#ef4444' : '#3b82f6',
      }),
    };

    function useSummary() {
      const [state, setState] = React.useState({ data: null, error: null });
      React.useEffect(() => {
        let alive = true;
        const tick = async () => {
          try {
            const response = await fetch(ENDPOINT, { cache: 'no-store' });
            const data = await response.json();
            if (alive) setState({ data, error: null });
          } catch (error) {
            if (alive) setState({ data: null, error: String(error) });
          }
        };
        tick();
        const timer = setInterval(tick, 3000);
        return () => { alive = false; clearInterval(timer); };
      }, []);
      return state;
    }

    function Card({ label, value, children }) {
      return h('div', { style: styles.card },
        h('div', { style: styles.key }, label),
        value !== undefined ? h('div', { style: styles.value }, value) : null,
        children || null);
    }

    function Body() {
      const { data, error } = useSummary();
      if (error || !data) {
        return h('div', { style: styles.wrap },
          h('div', { style: styles.key }, 'Crop'),
          h('div', { style: styles.note }, '面板数据未启动：先运行 crop serve'));
      }
      const status = data.status || {};
      const period = data.period || {};
      const minutes = Number(status.current_block_minutes || 0);
      const red = Number(status.red_min || 30);
      const tasks = status.open_tasks || [];
      const top = period.top_works || [];
      return h('div', { style: styles.wrap },
        h(Card, { label: '当前任务块', value: `${minutes.toFixed(0)} 分 / 红线 ${red.toFixed(0)}` },
          h('div', { style: styles.bar },
            h('div', { style: styles.fill(minutes / red, minutes >= red) }))),
        h('div', { style: styles.row },
          h('div', { style: styles.cell }, h(Card, { label: '今日事件', value: status.today_events || 0 })),
          h('div', { style: styles.cell }, h(Card, { label: '进行中', value: tasks.length }))),
        h(Card, { label: '近 7 天', value: `${Math.round(period.total_minutes || 0)} 分 · ${period.blocks || 0} 个回合` }),
        h('div', { style: styles.card },
          h('div', { style: styles.key }, '最花时间的三个工作'),
          top.length
            ? h('ul', { style: styles.list }, top.map((item) =>
                h('li', { key: item.work_id },
                  `${item.goal || item.work_id} · ${Math.round(item.minutes)} 分`)))
            : h('div', { style: styles.note }, '暂无')),
        h('div', { style: styles.card },
          h('div', { style: styles.key }, '进行中的工作'),
          tasks.length
            ? h('ul', { style: styles.list }, tasks.map((task) =>
                h('li', { key: task.task_id }, `${task.task_id} — ${task.title || task.summary || ''}`)))
            : h('div', { style: styles.note }, '暂无')));
    }

    function apply(ctx) {
      ctx.effect(() => ctx.sidebarRightTabs.register({
        id: TAB_ID,
        kind: 'crop',
        title: () => 'Crop',
        guide: [{
          id: 'open',
          order: 70,
          title: () => 'Crop 实时数字',
          description: () => '当前工作、时长、等待与最贵的工作',
        }],
      }), 'crop: sidebar tab type');

      ctx.effect(() => ctx.slots.inject('sidebar.right.pane.tab', () => ctx.slots.register({
        name: 'sidebar.right.pane.tab',
        key: TAB_ID,
      }, Body)), 'crop: sidebar pane');
    }

    exports.apply = apply;
    exports.inject = ['slots', 'sidebarRightTabs'];
    return module.exports;
  },
});
