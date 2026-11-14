import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
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

  assert.equal(ctx.registered.length, 1);
  const tool = ctx.registered[0];
  assert.equal(tool.name, 'probe_report');
  assert.deepEqual(tool.parameters.required, ['phase', 'task_id', 'summary']);
  assert.ok(tool.parameters.properties.phase.enum.includes('blocked'));
  assert.equal(typeof tool.execute, 'function');
});

test('tool execute records a report through the Python core', async (t) => {
  const directory = await mkdtemp(join(tmpdir(), 'probe-dsh-'));
  t.after(() => rm(directory, { recursive: true, force: true }));
  process.env.KIT_DATA_DIR = directory;

  const ctx = fakeCtx();
  apply(ctx, { python: 'python3' });
  const tool = ctx.registered[0];
  const session = fakeSession('s-report');
  const result = await tool.execute(
    { phase: 'blocked', task_id: 'Design Doc!', summary: '查资料中', cause: '资料太散', next: '先定目录', category: 'tool-misuse' },
    { agent: { session } },
  );
  assert.equal(result.ok, true);
  assert.equal(result.task_id, 'design-doc');
  assert.equal(result.turn, 2);
  assert.equal(result.step, 3);

  const day = new Date().toISOString().slice(0, 10);
  const report = await readFile(join(directory, 'reports', `${day}.jsonl`), 'utf8');
  assert.match(report, /"host": "dsh"/);
  assert.match(report, /"phase": "blocked"/);
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
  assert.ok(decision.messages.some((message) => message.content[0].text.includes('[探针]')));
  assert.equal(decision.messages[0].source.kind, 'ai-coding-efficiency-kit');
});

test('registers the crop protocol as a system prompt section', () => {
  const sections = [];
  const ctx = fakeCtx();
  ctx.systemPrompt = { section(section) { sections.push(section); return () => {}; } };
  apply(ctx, { python: 'python3' });

  assert.equal(sections.length, 1);
  assert.equal(sections[0].name, 'crop:protocol');
  assert.equal(typeof sections[0].order, 'number');
  assert.ok(sections[0].text.includes('probe_report'));
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

  const tool = ctx.registered[0];
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

  const day = new Date().toISOString().slice(0, 10);
  const text = await readFile(join(directory, 'events.jsonl'), 'utf8');
  const tools = text.trim().split('\n').map((line) => JSON.parse(line)).filter((row) => row.ev === 'tool');
  assert.equal(tools.at(-1).name, 'Bash');
});
