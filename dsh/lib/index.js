/**
 * DSH host adapter for the AI coding efficiency probe.
 *
 * The Python core owns task identity, timing, report validation and storage.
 * This plugin only translates DSH session events into core calls and injects
 * the returned notes at the next model step.  Zero runtime dependencies.
 */
import { spawn, spawnSync } from 'node:child_process';
import z from '@deepseek-ai/schemastery';
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
  'Crop 协议：',
  '- 首轮要么绑定工作，要么明确声明跳过（只限纯问答、不调用任何工具）。一旦你打算调用工具，先绑定再动手；绑定只用这个工具，不要一边干活一边补登。',
  '- 接着做：crop_report(phase="start", task_id=已有 id)；新开：crop_report(phase="start", task_id="<2-3 个词的短名>")。',
  '- 途中状态变化用 phase="progress"；卡住用 phase="blocked" 并写 cause 与 verdict。',
  '- 黄线提醒：先安抚用户一句，再反思方法对不对，然后上报。红线提醒：先判断是否还走在正确路上，能阶段性交付就先交付。',
  '- 与当前工作无关的问题：一句话回答后记为 category="side-issue"，禁止新建任务、禁止现场排查，除非用户明确要求。',
  '- 用户表达不满、否定或要求重做时，调 crop_review 记一档（redo=推翻重做 / major=大改 / minor=小修）；普通确认不用调。',
  '- 用户确认完成后再 crop_report(phase="end")，写三句小结：做了什么 / 时间花在哪 / 下次怎么更好。',
  '- 看到“待清退”的工作：先问用户要继续 / 挂起 / 清退，再用 crop_work 执行，不要自己决定。',
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

// Settings shown in 设置 → 插件 → Agent Crop (volatile = applied without restart).
export const Config = z.object({
  python: z.string().default('python3'),
  yellowMin: z.number().min(1).max(240).default(10).volatile()
    .description('工作级黄线：一个工作累计活跃多少分钟后提醒 AI 判断'),
  redMin: z.number().min(1).max(480).default(30).volatile()
    .description('单轮红线：一轮超过多少分钟提醒'),
  remindMin: z.number().min(1).max(240).default(15).volatile()
    .description('提醒冷却：同一条提醒多少分钟内不重复'),
  missMin: z.number().min(0).max(60).default(2).volatile()
    .description('缺报检测：一轮超过多少分钟且没有上报，下一轮提醒补记'),
  askHumanTools: z.string().default('ask_user_question,ask_human,askHuman')
    .description('哪些工具名算“在等用户回答”，逗号分隔；不同宿主名字不同，在这里加'),
});

function setting(value, fallback) {
  if (value && typeof value.get === 'function') return value.get();
  return value === undefined || value === null ? fallback : value;
}

export function apply(ctx, config = {}) {
  const python = setting(config?.python, 'python3');
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
      state = { started: false, blockStart: null, pending: [], injected: new Set(), calls: new Map(), reported: false, missed: 0, everReported: false, toolsUsed: false, bindNudged: false };
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
        env: {
          ...process.env,
          KIT_HOST: 'dsh',
          KIT_TASK_YELLOW_MIN: String(setting(config.yellowMin, 10)),
          KIT_TASK_RED_MIN: String(setting(config.redMin, 30)),
          KIT_TASK_REMIND_MIN: String(setting(config.remindMin, 15)),
        },
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
    name: 'crop_report',
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
        verdict: {
          type: 'string',
          enum: ['justified', 'not-justified', 'unknown'],
          description: '你是否认为这次变慢/超时是合理的：任务本身难 = justified；自己走错路 = not-justified',
        },
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
      if (!result) throw new Error('crop_report could not be recorded');
      state.reported = true;
      state.everReported = true;
      const retire = result.phase === 'end' ? (callCore({ op: 'pending_retire' }) || '') : '';
      const recorded = `recorded ${result.phase} ${result.task_id} (${result.minutes ?? 0} min in this block)`;
      return {
        ok: true,
        phase: result.phase,
        task_id: result.task_id,
        session_id: sid,
        turn,
        step,
        recorded: retire ? recorded + '\n' + retire : recorded,
      };
    },
  };

  // Deprecated alias: older prompts and sessions still call probe_report.
  const legacyTool = { ...tool, name: 'probe_report',
    description: 'Deprecated, use crop_report. ' + tool.description };

  const workTool = {
    name: 'crop_work',
    description:
      '挂起 / 恢复 / 清退一条工作。用户说“这条先放着”用 suspend；说“继续做”用 resume；'
      + '确认不再做了用 archive。清退前先问用户一句，不要自己决定。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: ['suspend', 'resume', 'archive'] },
        work_id: { type: 'string' },
      },
      required: ['action', 'work_id'],
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: { ok: { type: 'boolean' }, text: { type: 'string' } },
        required: ['ok', 'text'],
      },
      render: (_args, value) => [{ type: 'text', text: value.text }],
    },
    async execute(args, exec) {
      const session = exec.agent?.session;
      const sid = session ? String(session.id) : '';
      const workspace = session?.header?.cwd || process.cwd();
      const map = { suspend: 'work_suspend', resume: 'work_resume', archive: 'work_archive' };
      const op = map[String(args?.action)];
      if (!op) throw new Error('action must be suspend, resume or archive');
      const ok = callCore({ op, context: { sid, workspace }, work_id: args.work_id });
      return { ok: Boolean(ok), text: ok ? `已${args.action}：${args.work_id}` : `没有找到工作：${args.work_id}` };
    },
  };

  const reviewTool = {
    name: 'crop_review',
    description:
      '记录用户对上一轮产出的反馈档次。只在用户表达不满、否定、或要求重做时调用；'
      + '普通的确认、追问、补充信息都不用调。档次：redo=完全不可用、要推翻重做；'
      + 'major=方向对但需要大改；minor=只需小修；none=其实没问题（一般不用调）。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        rework: { type: 'string', enum: ['redo', 'major', 'minor', 'none'] },
        sentiment: { type: 'string', enum: ['pos', 'neu', 'neg'], description: '用户这句话的语气' },
        reason: { type: 'string', description: '一句话说明为什么是这一档' },
        work_id: { type: 'string', description: '可选，默认取本会话最近的工作' },
      },
      required: ['rework'],
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: { ok: { type: 'boolean' }, text: { type: 'string' } },
        required: ['ok', 'text'],
      },
      render: (_args, value) => [{ type: 'text', text: value.text }],
    },
    async execute(args, exec) {
      const session = exec.agent?.session;
      const sid = session ? String(session.id) : '';
      const workspace = session?.header?.cwd || process.cwd();
      const result = callCore({ op: 'record_review', args, context: { sid, workspace } });
      if (!result) throw new Error('crop_review could not be recorded');
      return { ok: true, text: `已记录：${result.rework}${result.sentiment ? ' / ' + result.sentiment : ''}` };
    },
  };

  const worksTool = {
    name: 'crop_works',
    description: 'List works in progress on this machine (newest first). Use it when you are not sure which work this session should continue.',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        query: { type: 'string', description: 'Optional substring to match against work id or goal' },
        limit: { type: 'integer', description: 'Max rows, default 10' },
      },
      required: [],
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: { ok: { type: 'boolean' }, text: { type: 'string' } },
        required: ['ok', 'text'],
      },
      render: (_args, value) => [{ type: 'text', text: value.text }],
    },
    async execute(args, exec) {
      const session = exec.agent?.session;
      const sid = session ? String(session.id) : '';
      const workspace = session?.header?.cwd || process.cwd();
      const tasks = callCore({ op: 'works', context: { sid, workspace }, limit: args?.limit ?? 10 }) || [];
      const query = String(args?.query || '').toLowerCase();
      const rows = tasks.filter((task) => !query
        || String(task.task_id).toLowerCase().includes(query)
        || String(task.goal || task.title || '').toLowerCase().includes(query));
      const text = rows.length
        ? rows.map((task) => `${task.task_id} — ${task.goal || task.title || ''}`).join('\n')
        : '没有匹配的进行中工作';
      return { ok: true, text };
    },
  };

  ctx.effect(() => ctx.tools.register(tool), 'agent-crop.crop_report');
  ctx.effect(() => ctx.tools.register(legacyTool), 'agent-crop.probe_report (alias)');
  ctx.effect(() => ctx.tools.register(worksTool), 'agent-crop.crop_works');
  ctx.effect(() => ctx.tools.register(reviewTool), 'agent-crop.crop_review');
  ctx.effect(() => ctx.tools.register(workTool), 'agent-crop.crop_work');

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
        state.reported = false;
        state.toolsUsed = false;
        state.bindNudged = false;
        state.pending = [];
        callCore({ op: 'record_prompt', context: { sid, workspace }, text });
        return;
      }

      if (event.type === 'tool/call') {
        const callId = event.data?.callId;
        const name = event.data?.name;
        if (callId && name) state.calls.set(String(callId), String(name));
        const askTools = String(setting(config.askHumanTools, '')).split(',').map((s) => s.trim()).filter(Boolean);
        if (name && askTools.includes(String(name))) {
          state.askStarted = Date.now();
          state.askTool = String(name);
        }
        return;
      }

      if (event.type === 'tool/result') {
        state.toolsUsed = true;
        if (state.askStarted) {
          callCore({
            op: 'record_human_wait',
            context: { sid, workspace },
            reason: state.askTool,
            started_at: state.askStarted,
            ended_at: Date.now(),
          });
          state.askStarted = null;
          state.askTool = null;
        }
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
        const minutes = state.blockStart ? (Date.now() - state.blockStart) / 60000 : 0;
        if (minutes >= Number(setting(config.missMin, 2)) && !state.reported) state.missed += 1;
        callCore({ op: 'record_stop', context: { sid, workspace } });
        state.blockStart = null;
        state.reported = false;
        state.toolsUsed = false;
        state.bindNudged = false;
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

      if (state.toolsUsed && !state.everReported && !state.bindNudged) {
        state.bindNudged = true;
        notes.push('[crop] 你已经在调用工具，但还没有绑定任何工作。先调 crop_report(phase="start", task_id="<2-3 个词的短名>", summary="<一句话>") 再继续，这样这段时间才有归属。');
      }

      if (payload.turn >= 2 && payload.step === 1 && !state.everReported) {
        notes.push('[crop] 这已经是第二轮对话，仍未绑定工作。“一句话答完”的豁免只适用于纯问答；你如果要调工具，现在先调 crop_report(phase="start", task_id="<2-3 个词的短名>", summary="<一句话>")，再继续。');
      }

      if (state.missed > 0) {
        const streak = state.missed;
        state.missed = 0;
        notes.push(streak === 1
          ? '[crop] 上一轮跑了挺久但没有登记。如果这个工作有进展或卡点，调 probe_report 写一行。'
          : `[crop] 已经连续 ${streak} 轮没有登记工作状态。请现在调 probe_report 写一行：在做什么 / 卡在哪 / 下一步。`);
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
