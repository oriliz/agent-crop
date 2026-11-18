import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

process.env.KIT_DASHBOARD = '0';

import { apply, PROTOCOL_TEXT } from '../lib/index.js';

function fakeCtx() {
  const handlers = new Map();
  const registered = [];
  const skillDefinitions = [];
  const sections = [];
  return {
    handlers,
    registered,
    skillDefinitions,
    sections,
    systemPrompt: { section(entry) { sections.push(entry); } },
    on(event, handler) { handlers.set(event, handler); },
    effect(fn) { fn(); },
    // 宿主侧 ctx.inject 是能力接缝：测试里直接以「没有 webServer」回调，
    // dashboard 路由那段会按设计跳过，headless 行为与真实 TUI 一致。
    inject(_specs, fn) { fn({ webServer: undefined }); },
    tools: {
      register(definition) { registered.push(definition); return () => {}; },
    },
    skills: {
      register(definition) { skillDefinitions.push(definition); return () => {}; },
    },
    logger: { warn() {} },
  };
}

function fakeSession(id = 's1') {
  return {
    id,
    header: { cwd: '/tmp/probe-dsh' },
    snapshotEvents: () => [
      { type: 'step/start', data: { turn: 2, step: 3 } },
    ],
  };
}

test('protocol pins the tone: colleague, not customer support', async () => {
  assert.ok(PROTOCOL_TEXT.includes('说话方式'), 'tone block must exist');
  assert.ok(PROTOCOL_TEXT.includes('同事吐槽'), 'must say colleague, not customer support');
  assert.ok(PROTOCOL_TEXT.includes('自嘲'), 'must carry the self-deprecating tone');
  assert.ok(PROTOCOL_TEXT.includes('梗是包装'), 'jokes must not replace facts');
  assert.ok(PROTOCOL_TEXT.includes('expect_min'), 'estimate contract stays in the protocol');
});

test('registers probe_report with the documented schema', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });

  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  assert.ok(tool, 'crop_update should be registered');
  assert.ok(ctx.registered.some((item) => item.name === 'probe_report'), 'probe_report alias stays for old prompts');
  assert.ok(ctx.registered.some((item) => item.name === 'crop_report'), 'crop_report alias stays for older prompts');
  assert.ok(ctx.registered.some((item) => item.name === 'crop_health'), 'crop_health is registered');
  assert.ok(ctx.registered.some((item) => item.name === 'crop_works'));
  assert.ok(ctx.registered.some((item) => item.name === 'crop_review'));
  assert.ok(ctx.registered.some((item) => item.name === 'crop_work'));
  assert.ok(ctx.registered.some((item) => item.name === 'crop_adjust'));
  assert.deepEqual(tool.parameters.required, ['phase', 'task_id', 'summary']);
  assert.ok(tool.parameters.properties.phase.enum.includes('blocked'));
  for (const field of ['what', 'cost', 'next_time', 'artifact', 'expect_min', 'verdict', 'sediment']) {
    assert.ok(tool.parameters.properties[field], `${field} should be in the schema`);
  }
  assert.equal(typeof tool.execute, 'function');
});

test('tool execute records a report through the Python core', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  const session = fakeSession('s-report');
  const result = await tool.execute(
    { phase: 'blocked', task_id: 'Design Doc!', summary: '查资料中', cause: '资料太散', next: '先定目录', category: 'tool-misuse' },
    { agent: { session } },
  );
  assert.equal(result.ok, true);
  assert.equal(result.task_id, 'design-doc');
  assert.equal(result.turn, 2);
  assert.equal(result.step, 3);

  const now = new Date();
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const report = await readFile(join(directory, 'reports', `${day}.jsonl`), 'utf8');
  assert.match(report, /"host": "dsh"/);
  assert.match(report, /"phase": "blocked"/);
});

test('protocol forbids starting work before claiming and forces side issues aside', async () => {
  const source = await readFile(new URL('../lib/index.js', import.meta.url), 'utf8');
  assert.match(source, /phase="progress"/);
  assert.match(source, /先绑定，再动手/);
  assert.match(source, /category="side-issue"/);
  assert.match(source, /禁止新建任务/);
});

test('agent/pre-step injects the start hint and a reminder', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-pre');
  const preStep = ctx.handlers.get('agent/pre-step');
  const event = ctx.handlers.get('session/event');
  event(session, {
    type: 'user/message',
    data: { source: { kind: 'user' }, content: [{ type: 'text', text: '做设计文档' }] },
  });

  const decision = await preStep(
    { agent: { session }, turn: 1, step: 1 },
    async () => ({ kind: 'enter', messages: [] }),
  );
  assert.equal(decision.kind, 'enter');
  assert.ok(decision.messages.some((message) => message.content[0].text.includes('[crop]')));
  assert.equal(decision.messages[0].source.kind, 'agent-crop');
});

test('registers the crop protocol as a system prompt section', () => {
  const sections = [];
  const ctx = fakeCtx();
  ctx.systemPrompt = { section(section) { sections.push(section); return () => {}; } };
  apply(ctx, { python: 'python3' });

  assert.equal(sections.length, 1);
  assert.equal(sections[0].name, 'crop:protocol');
  assert.equal(typeof sections[0].order, 'number');
  assert.ok(sections[0].text.includes('crop_update'));
});

test('injects the work status line at turn start', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-turn');
  const event = ctx.handlers.get('session/event');
  const preStep = ctx.handlers.get('agent/pre-step');
  event(session, {
    type: 'user/message',
    data: { source: { kind: 'user' }, content: [{ type: 'text', text: '开始' }] },
  });

  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  await tool.execute(
    { phase: 'start', task_id: 'open-crop', summary: '把探针开源', goal: '把探针开源', type: 'design', difficulty: 'm' },
    { agent: { session } },
  );

  await preStep({ agent: { session }, turn: 1, step: 1 }, async () => ({ kind: 'enter', messages: [] }));
  const decision = await preStep({ agent: { session }, turn: 2, step: 1 }, async () => ({ kind: 'enter', messages: [] }));
  assert.ok(decision.messages.some((message) => message.content[0].text.includes('[crop]')));
});

test('tool results record the tool name captured from tool/call', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-tools');
  const event = ctx.handlers.get('session/event');
  event(session, {
    type: 'tool/call',
    data: { callId: 'call-1', name: 'Bash', turn: 1, step: 1, arguments: '{}' },
  });
  event(session, {
    type: 'tool/result',
    data: { message: { toolCallId: 'call-1' }, turn: 1, step: 1 },
  });

  const now = new Date();
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const text = await readFile(join(directory, 'events.jsonl'), 'utf8');
  const tools = text.trim().split('\n').map((line) => JSON.parse(line)).filter((row) => row.ev === 'tool');
  assert.equal(tools.at(-1).name, 'Bash');
});

test('asks for a report after a substantial turn without one', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3', missMin: 0 });
  const session = fakeSession('s-miss');
  const event = ctx.handlers.get('session/event');
  const preStep = ctx.handlers.get('agent/pre-step');

  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  // 先绑定一件活，再构造一轮“用了工具但没上报”的长回合
  event(session, {
    type: 'user/message',
    data: { source: { kind: 'user' }, content: [{ type: 'text', text: '干活' }] },
  });
  await tool.execute({ phase: 'start', task_id: 'miss-work', summary: '干活' }, { agent: { session } });
  event(session, { type: 'turn/end', data: {} });

  event(session, { type: 'tool/call', data: { callId: 'c1', name: 'bash' } });
  event(session, { type: 'tool/result', data: { callId: 'c1' } });
  event(session, { type: 'turn/end', data: {} });

  const decision = await preStep({ agent: { session }, turn: 3, step: 1 }, async () => ({ kind: 'enter', messages: [] }));
  assert.ok(decision.messages.some((message) => message.content[0].text.includes('在做什么 / 卡在哪 / 下一步')));
});

test('pure chat turns never count as missed reports', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-second');
  const preStep = ctx.handlers.get('agent/pre-step');

  const decision = await preStep({ agent: { session }, turn: 2, step: 1 }, async () => ({ kind: 'enter', messages: [] }));
  assert.ok(!decision.messages.some((message) => message.content[0].text.includes('在做什么 / 卡在哪 / 下一步')));
});

test('nags an unbound session that already used tools', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-unbound');
  const event = ctx.handlers.get('session/event');
  const preStep = ctx.handlers.get('agent/pre-step');

  event(session, { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: '干活' }] } });
  event(session, { type: 'tool/call', data: { callId: 'c1', name: 'bash' } });
  event(session, { type: 'tool/result', data: { callId: 'c1' } });

  const decision = await preStep({ agent: { session }, turn: 1, step: 2 }, async () => ({ kind: 'enter', messages: [] }));
  assert.ok(decision.messages.some((message) => message.content[0].text.includes('还没有绑定')));
});

test('ledger tools do not count as work for the binding nudge', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-ledger');
  const event = ctx.handlers.get('session/event');
  const preStep = ctx.handlers.get('agent/pre-step');

  event(session, { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: '看看健康度' }] } });
  event(session, { type: 'tool/call', data: { callId: 'c1', name: 'crop_health' } });
  event(session, { type: 'tool/result', data: { message: { toolCallId: 'c1' } } });

  const decision = await preStep({ agent: { session }, turn: 1, step: 2 }, async () => ({ kind: 'enter', messages: [] }));
  assert.ok(!decision.messages.some((message) => message.content[0].text.includes('还没有绑定')));
});

test('stays quiet when the session is really bound', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-bound');
  const event = ctx.handlers.get('session/event');
  const tool = ctx.registered.find((item) => item.name === 'crop_update');

  event(session, { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: '干活' }] } });
  await tool.execute(
    { phase: 'start', task_id: 'bound-work', summary: '干活', goal: '干活' },
    { agent: { session } },
  );
  event(session, { type: 'tool/call', data: { callId: 'c2', name: 'bash' } });
  event(session, { type: 'tool/result', data: { callId: 'c2' } });

  const decision = await ctx.handlers.get('agent/pre-step')({ agent: { session }, turn: 1, step: 2 }, async () => ({ kind: 'enter', messages: [] }));
  assert.ok(!decision.messages.some((message) => message.content[0].text.includes('还没有绑定')));
});

test('first turn honors an existing binding instead of listing candidates', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-first');
  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  await tool.execute(
    { phase: 'start', task_id: 'first-work', summary: '干活', goal: '把活干完' },
    { agent: { session } },
  );

  // a fresh process reads the same store; the first step must show the bound work
  const second = fakeCtx();
  apply(second, { python: 'python3' });
  const sameSession = fakeSession('s-first');
  const decision = await second.handlers.get('agent/pre-step')(
    { agent: { session: sameSession }, turn: 1, step: 1 },
    async () => ({ kind: 'enter', messages: [] }),
  );
  const text = decision.messages.map((m) => m.content[0].text).join('\n');
  assert.ok(text.includes('当前工作'), text);
  assert.ok(!text.includes('先选一条'), text);
});

test('first turn shows the bound work even after it fell out of the candidates', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  await tool.execute(
    { phase: 'start', task_id: 'mine', summary: '主线', goal: '甲目标' },
    { agent: { session: fakeSession('s-mine') } },
  );
  for (let index = 1; index <= 3; index += 1) {
    await tool.execute(
      { phase: 'start', task_id: `t${index}`, summary: `别的${index}`, goal: `目标${index}` },
      { agent: { session: fakeSession(`s-other-${index}`) } },
    );
  }

  // Another session touched the bound work, and three newer works exist, so
  // the bound work is no longer among the three candidates. The binding is
  // still live; the first turn must go by the binding, not the candidate list.
  const openPath = join(directory, 'tasks', 'open.json');
  const open = JSON.parse(await readFile(openPath, 'utf8'));
  const now = Date.now();
  open.tasks.mine.last_sid = 's-other-9';
  open.tasks.mine.last_seen_at = now - 20 * 60000;
  for (let index = 1; index <= 3; index += 1) {
    open.tasks[`t${index}`].last_seen_at = now - index * 60000;
  }
  await writeFile(openPath, JSON.stringify(open));

  // A fresh plugin process (new state, same store and session id).
  const second = fakeCtx();
  apply(second, { python: 'python3' });
  const decision = await second.handlers.get('agent/pre-step')(
    { agent: { session: fakeSession('s-mine') }, turn: 1, step: 1 },
    async () => ({ kind: 'enter', messages: [] }),
  );
  const text = decision.messages.map((message) => message.content[0].text).join('\n');
  assert.ok(text.includes('当前工作'), text);
  assert.ok(text.includes('甲目标'), text);
  assert.ok(!text.includes('先选一条'), text);
});

test('registers the retro-sediment skill for on-demand loading', () => {
  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });

  const skill = ctx.skillDefinitions.find((item) => item.name === 'crop-retro');
  assert.ok(skill, 'crop-retro should be registered as a runtime skill');
  assert.ok(skill.description.includes('复盘沉淀'), 'description must carry the trigger words');
  assert.ok(skill.content.includes('四件套'), 'skill body must state the four-part rule');
  assert.ok(skill.content.includes('sediment'), 'skill body must mention the ledger pointer');
  assert.ok(skill.content.includes('验收判据'), 'skill body must demand an acceptance check');
});

test('protocol points at sediment without inlining the decision tree', async () => {
  assert.ok(PROTOCOL_TEXT.includes('sediment'), 'protocol must mention the sediment field');
  assert.ok(PROTOCOL_TEXT.includes('crop-retro'), 'protocol must point at the skill');
  assert.ok(!PROTOCOL_TEXT.includes('## 落点决策'), 'decision tree stays out of the always-on protocol');
});

test('ending without sediment returns the nudge, and a sediment pointer is stored', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  const session = fakeSession('s-sed');

  const first = await tool.execute(
    { phase: 'end', task_id: 'sed-work', summary: '做完了', what: '做完了' },
    { agent: { session } },
  );
  assert.match(first.recorded, /还没写沉淀/, 'missing sediment must be called out in the tool result');

  const second = await tool.execute(
    { phase: 'end', task_id: 'sed-work', summary: '补沉淀', what: '补沉淀', sediment: 'docs:WORKFLOW.md#复盘沉淀' },
    { agent: { session } },
  );
  assert.doesNotMatch(second.recorded, /还没写沉淀/, 'a provided sediment must not nag');

  const now = new Date();
  const day = `${now.getFullYear()}-${String(now.getMonth() + 1).padStart(2, '0')}-${String(now.getDate()).padStart(2, '0')}`;
  const report = await readFile(join(directory, 'reports', `${day}.jsonl`), 'utf8');
  assert.match(report, /"sediment": "docs:WORKFLOW.md#复盘沉淀"/);
});


test('after an end, delivery tools do not trigger the binding nudge', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-done');
  const event = ctx.handlers.get('session/event');
  const preStep = ctx.handlers.get('agent/pre-step');
  const tool = ctx.registered.find((item) => item.name === 'crop_update');

  event(session, { type: 'user/message', data: { source: { kind: 'user' }, content: [{ type: 'text', text: '干活' }] } });
  await tool.execute({ phase: 'start', task_id: 'done-work', summary: '干活' }, { agent: { session } });
  await tool.execute({ phase: 'end', task_id: 'done-work', summary: '做完了', what: '做完了' }, { agent: { session } });

  // 收尾之后交付、截图、汇报都算收尾动作，不该被要求重新绑定
  event(session, { type: 'tool/call', data: { callId: 'c1', name: 'gh' } });
  event(session, { type: 'tool/result', data: { message: { toolCallId: 'c1' } } });
  const decision = await preStep({ agent: { session }, turn: 2, step: 2 }, async () => ({ kind: 'enter', messages: [] }));
  const text = decision.messages.map((message) => message.content[0].text).join('\n');
  assert.ok(!text.includes('还没有绑定'), text);
});

test('starting another work warns that the old one is still open', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-switch');
  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  await tool.execute({ phase: 'start', task_id: 'work-a', summary: '甲' }, { agent: { session } });
  const second = await tool.execute({ phase: 'start', task_id: 'work-b', summary: '乙' }, { agent: { session } });
  assert.match(second.recorded, /已从 work-a 切到 work-b/);
  assert.match(second.recorded, /旧工作/);
});

test('a revised estimate returns a calm note instead of an instant overdue', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = fakeSession('s-revise');
  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  await tool.execute({ phase: 'start', task_id: 'rev-work', summary: '做', expect_min: 40 }, { agent: { session } });
  const record = await tool.execute(
    { phase: 'progress', task_id: 'rev-work', summary: '改口', expect_min: 2, expect_why: '只剩收尾' },
    { agent: { session } },
  );
  assert.match(record.recorded, /改口：40 → 2 分钟/);
  assert.match(record.recorded, /不会立刻算超时/);
});


test('crop_adjust records an improvement and reads the ledger', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const tool = ctx.registered.find((item) => item.name === 'crop_adjust');
  const recorded = await tool.execute({
    action: 'record', metric: 'work_minutes',
    evidence: 'PR #29', note: '少一轮提醒应该更快',
  });
  assert.match(recorded.text, /recorded adjustment/);
  const ledger = await tool.execute({ action: 'ledger' });
  assert.match(ledger.text, /改进台账/);
});


function fakeWebCtx(rejection) {
  const routes = [];
  const ctx = fakeCtx();
  ctx.routes = routes;
  ctx.inject = (_specs, fn) => fn({
    webServer: { register(route) { routes.push(route); return () => {}; } },
    connection: { requestRejection: () => rejection },
  });
  return ctx;
}

function fakeResponse() {
  const res = {
    statusCode: 200,
    headers: {},
    ended: false,
    body: '',
    setHeader(key, value) { res.headers[key] = value; },
    writeHead(status, headers) { res.statusCode = status; Object.assign(res.headers, headers || {}); },
    end(body) { res.ended = true; if (body !== undefined) res.body += body; },
  };
  return res;
}

test('dashboard routes ask Connection before serving any data', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-gate-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const denied = fakeWebCtx(403);
  apply(denied, { python: 'python3' });
  const deniedRoute = denied.routes.find((item) => item.path === '/api/agent-crop/global');
  assert.ok(deniedRoute, 'route registers once a web server is present');
  const blocked = fakeResponse();
  deniedRoute.handler({ url: '/api/agent-crop/global?days=7', headers: {} }, blocked);
  assert.equal(blocked.statusCode, 403, 'rejection status is passed through');
  assert.equal(blocked.ended, true);
  assert.equal(blocked.body, '', 'nothing is read or leaked before the gate');
  assert.equal(blocked.headers['content-type'], undefined, 'rejected before the data path');

  const allowed = fakeWebCtx(undefined);
  apply(allowed, { python: 'python3' });
  const openRoute = allowed.routes.find((item) => item.path === '/api/agent-crop/global');
  const served = fakeResponse();
  openRoute.handler({ url: '/api/agent-crop/global?days=7', headers: {} }, served);
  assert.equal(served.statusCode, 200, 'a trusted request still gets data');
  assert.match(served.headers['content-type'], /application\/json/);
});

test('protocol section falls back to the built-in text when no template exists', async () => {
  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const section = ctx.sections.find((entry) => entry.name === 'crop:protocol');
  assert.ok(section, 'protocol section must be registered');
  assert.equal(section.text, PROTOCOL_TEXT, 'default template path must not change the protocol');
});

test('a wrong tool call returns a contextual error instead of a bare failure', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const tool = ctx.registered.find((item) => item.name === 'crop_update');
  await assert.rejects(
    () => tool.execute({ action: 'ledger' }, { agent: { session: fakeSession() } }),
    (error) => /crop_adjust\(action="ledger"\)/.test(error.message)
      && /missing: phase, task_id, summary/.test(error.message),
  );
});

test('tool/call arguments attribute the artifact hit to the adjustment', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;
  const spec = join(directory, 'spec.md');
  await writeFile(spec, '# spec');

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const session = { id: 's-artifact', header: { cwd: directory }, snapshotEvents: () => [] };
  const adjust = ctx.registered.find((item) => item.name === 'crop_adjust');
  await adjust.execute({
    action: 'record', metric: 'work_minutes', evidence: 'PR #x', note: '更短的收尾路径',
    artifact: { kind: 'file', path: spec },
  }, { agent: { session } });

  const event = ctx.handlers.get('session/event');
  event(session, {
    type: 'tool/call',
    data: { callId: 'call-a', name: 'read', arguments: JSON.stringify({ file_path: spec }) },
  });
  event(session, {
    type: 'tool/result',
    data: { message: { toolCallId: 'call-a' } },
  });

  const text = await readFile(join(directory, 'events.jsonl'), 'utf8');
  const rows = text.trim().split('\n').map((line) => JSON.parse(line));
  const hit = rows.find((row) => row.ev === 'artifact_hit');
  assert.ok(hit, 'artifact_hit event should be recorded');
  assert.equal(hit.sid, 's-artifact');
  assert.equal(hit.target, spec);
  assert.match(hit.adjustment_id, /^adj-/);
});
