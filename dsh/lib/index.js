/**
 * DSH host adapter for the AI coding efficiency probe.
 *
 * The Python core owns task identity, timing, report validation and storage.
 * This plugin only translates DSH session events into core calls and injects
 * the returned notes at the next model step.  Zero runtime dependencies.
 */
import { spawn, spawnSync } from 'node:child_process';
import { randomUUID } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'agent-crop-dsh';
export const inject = ['tools'];

const HERE = dirname(fileURLToPath(import.meta.url));
const CORE_CLI = join(HERE, '..', '..', 'core', 'probe_core_cli.py');
const DASHBOARD = join(HERE, '..', '..', 'core', 'crop_serve.py');

const PHASES = ['start', 'progress', 'blocked', 'end'];
const CATEGORIES = [
  'serial-wait',
  'repeat-pitfall',
  'design-churn',
  'decision-batching',
  'tool-misuse',
  'no-evidence',
  'spec-drift',
  'human-wait',
  'other',
];

const PROTOCOL_TEXT = [
  'Crop 探针协议：',
  '- 开工前用一句话回显这次工作的目标，并调用 probe_report(phase="start", task_id="<kebab>", summary="<一句话>") 登记。',
  '- 收到 [crop] 提醒时，先对用户说一句人话，再调用 probe_report(phase="blocked" 或 "progress", ...)，写清卡在哪 / 为什么 / 证据。',
  '- 用户表示满意或明确结束时调用 probe_report(phase="end", ...)，顺手写三句小结：做了什么 / 时间花在哪 / 下次怎么更好。',
].join('\n');

function textOf(message) {
  const content = message?.content;
  if (typeof content === 'string') return content;
  if (Array.isArray(content)) {
    return content
      .filter((block) => block && block.type === 'text')
      .map((block) => block.text ?? '')
      .join('');
  }
  return '';
}

function createNotice(text) {
  return {
    id: randomUUID(),
    role: 'user',
    content: [{ type: 'text', text }],
    source: {
      kind: 'agent-crop',
      form: 'notice',
      summary: text.slice(0, 120),
    },
  };
}

export function apply(ctx, config = {}) {
  const python = config?.python || 'python3';
  const states = new Map();

  let dashboard = null;
  try {
    if (process.env.KIT_DASHBOARD === '0') throw new Error('dashboard disabled');
    dashboard = spawn(python, [DASHBOARD, '--host', '127.0.0.1', '--port', '8765'], {
      stdio: 'ignore', detached: true,
    });
    dashboard.unref();
    dashboard.on('error', () => { dashboard = null; });
    dashboard.on('exit', () => { dashboard = null; });
  } catch {
    dashboard = null;
  }
  ctx.effect(() => () => {
    try { dashboard?.kill(); } catch { /* already gone */ }
  }, 'crop: dashboard server');

  // Persistent protocol section: assembled into the system prompt on every step,
  // removed automatically when the plugin is disposed (clean uninstall).
  try {
    if (ctx.systemPrompt && typeof ctx.systemPrompt.section === 'function') {
      ctx.systemPrompt.section({ name: 'crop:protocol', order: 900, text: PROTOCOL_TEXT });
    }
  } catch (error) {
    ctx.logger?.warn?.('[crop] system prompt section failed: %o', error);
  }


  const stateFor = (sid) => {
    let state = states.get(sid);
    if (!state) {
      state = { started: false, blockStart: null, pending: [], injected: new Set(), calls: new Map() };
      states.set(sid, state);
    }
    return state;
  };

  const callCore = (payload) => {
    try {
      const result = spawnSync(python, [CORE_CLI], {
        input: JSON.stringify(payload),
        encoding: 'utf8',
        timeout: 2500,
        env: { ...process.env, KIT_HOST: 'dsh' },
      });
      if (result.error || result.status !== 0 || !result.stdout) return null;
      const parsed = JSON.parse(result.stdout);
      return parsed && parsed.ok ? parsed.result : null;
    } catch (error) {
      ctx.logger?.warn?.('[crop] probe core call failed: %o', error);
      return null;
    }
  };

  const turnBoundary = (session) => {
    let turn = 0;
    let step = 0;
    try {
      for (const event of session.snapshotEvents()) {
        if (event.type === 'step/start') {
          turn = event.data?.turn ?? turn;
          step = event.data?.step ?? step;
        }
      }
    } catch {
      // keep 0/0; the report is still useful
    }
    return { turn, step };
  };

  const tool = {
    name: 'probe_report',
    description:
      'Record what the current long task is doing, why it is slow or blocked, and what comes next. '
      + 'Call it when the probe asks, or when the user confirms the task is finished. Keep fields short.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        phase: { type: 'string', enum: PHASES },
        task_id: { type: 'string' },
        summary: { type: 'string' },
        cause: { type: 'string' },
        next: { type: 'string' },
        category: { type: 'string', enum: CATEGORIES },
        goal: { type: 'string', description: 'One-line goal of this piece of work' },
        type: { type: 'string', enum: ['design', 'implement', 'debug', 'docs'] },
        difficulty: { type: 'string', enum: ['s', 'm', 'l'] },
      },
      required: ['phase', 'task_id', 'summary'],
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          ok: { type: 'boolean' },
          phase: { type: 'string' },
          task_id: { type: 'string' },
          session_id: { type: 'string' },
          turn: { type: 'integer' },
          step: { type: 'integer' },
          recorded: { type: 'string' },
        },
        required: ['ok', 'phase', 'task_id', 'session_id', 'turn', 'step', 'recorded'],
      },
      render: (_args, value) => [{ type: 'text', text: value.recorded }],
      presentationMeta: (_args, value) => ({
        phase: value.phase,
        taskId: value.task_id,
        turn: value.turn,
        step: value.step,
      }),
    },
    async execute(args, exec) {
      const agent = exec.agent;
      if (!agent) throw new Error('probe_report requires an agent-bound call');
      const session = agent.session;
      const sid = String(session.id);
      const workspace = session.header?.cwd || process.cwd();
      const { turn, step } = turnBoundary(session);
      const state = stateFor(sid);
      const result = callCore({
        op: 'record_report',
        args,
        context: {
          sid,
          workspace,
          turn,
          step,
          block_start: state.blockStart,
        },
      });
      if (!result) throw new Error('probe_report could not be recorded');
      return {
        ok: true,
        phase: result.phase,
        task_id: result.task_id,
        session_id: sid,
        turn,
        step,
        recorded: `recorded ${result.phase} ${result.task_id} (${result.minutes ?? 0} min in this block)`,
      };
    },
  };

  ctx.effect(() => ctx.tools.register(tool), 'agent-crop.probe_report');

  ctx.on('session/event', (session, event) => {
    try {
      const sid = String(session.id);
      const workspace = session.header?.cwd || process.cwd();
      const state = stateFor(sid);

      if (event.type === 'user/message') {
        const source = event.data?.source;
        if (source?.kind !== 'user') return;
        const text = textOf(event.data);
        state.blockStart = Date.now();
        state.pending = [];
        callCore({ op: 'record_prompt', context: { sid, workspace }, text });
        return;
      }

      if (event.type === 'tool/call') {
        const callId = event.data?.callId;
        const name = event.data?.name;
        if (callId && name) state.calls.set(String(callId), String(name));
        return;
      }

      if (event.type === 'tool/result') {
        const isError = Boolean(event.data?.error || event.data?.message?.isError);
        const callId = event.data?.message?.toolCallId;
        const toolName = (callId && state.calls.get(String(callId))) || 'tool';
        if (callId) state.calls.delete(String(callId));
        callCore({ op: 'record_tool', context: { sid, workspace }, name: toolName, is_error: isError });
        return;
      }

      if (event.type === 'compaction/end') {
        state.forceInject = true;
        return;
      }

      if (event.type === 'turn/end') {
        callCore({ op: 'record_stop', context: { sid, workspace } });
        state.blockStart = null;
        state.pending = [];
      }
    } catch (error) {
      ctx.logger?.warn?.('[crop] session event failed: %o', error);
    }
  });

  ctx.on('agent/pre-step', async (payload, next) => {
    const decision = await next();
    try {
      if (!decision || decision.kind !== 'enter') return decision;
      const agent = payload.agent;
      const session = agent?.session;
      if (!session) return decision;
      const sid = String(session.id);
      const workspace = session.header?.cwd || process.cwd();
      const state = stateFor(sid);
      const notes = [];

      const startedAlready = state.started;
      if (!state.started) {
        state.started = true;
        callCore({ op: 'record_session_start', context: { sid, workspace } });
        const hint = callCore({ op: 'start_hint', workspace, limit: 3 });
        if (hint) notes.push(hint);
      }

      if (state.pending.length) {
        notes.push(...state.pending);
        state.pending = [];
      }

      // Turn-start (step 1) or post-compaction: one line of current work state.
      if (startedAlready && (payload.step === 1 || state.forceInject)) {
        state.forceInject = false;
        const status = callCore({ op: 'work_status', context: { sid, workspace } });
        if (status) notes.push(status);
      }

      const reminder = callCore({ op: 'maybe_reminder', context: { sid, workspace } });
      if (reminder) notes.push(reminder);

      if (!notes.length) return decision;
      const key = `${sid}:${payload.turn ?? 0}:${payload.step ?? 0}:${notes.join(' ').slice(0, 160)}`;
      if (state.injected.has(key)) return decision;
      state.injected.add(key);
      return {
        ...decision,
        messages: [...(decision.messages ?? []), ...notes.map(createNotice)],
      };
    } catch (error) {
      ctx.logger?.warn?.('[crop] pre-step injection failed: %o', error);
      return decision;
    }
  });
}
