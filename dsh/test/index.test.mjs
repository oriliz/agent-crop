import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

process.env.KIT_DASHBOARD = '0';

import { apply } from '../lib/index.js';

function fakeCtx() {
  const handlers = new Map();
  const registered = [];
  return {
    handlers,
    registered,
    on(event, handler) { handlers.set(event, handler); },
    effect(fn) { fn(); },
    tools: {
      register(definition) { registered.push(definition); return () => {}; },
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
  assert.deepEqual(tool.parameters.required, ['phase', 'task_id', 'summary']);
  assert.ok(tool.parameters.properties.phase.enum.includes('blocked'));
  for (const field of ['what', 'cost', 'next_time', 'artifact', 'expect_min', 'verdict']) {
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
  assert.match(source, /绑定只用这个工具/);
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

  event(session, {
    type: 'user/message',
    data: { source: { kind: 'user' }, content: [{ type: 'text', text: '干活' }] },
  });
  event(session, { type: 'tool/call', data: { callId: 'c1', name: 'bash' } });
  event(session, { type: 'tool/result', data: { callId: 'c1' } });
  event(session, { type: 'turn/end', data: {} });

  const decision = await preStep({ agent: { session }, turn: 2, step: 1 }, async () => ({ kind: 'enter', messages: [] }));
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
  assert.ok(!text.includes('哪个属于本会话'), text);
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
  assert.ok(!text.includes('哪个属于本会话'), text);
});
