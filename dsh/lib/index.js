/**
 * DSH host adapter for Agent Crop.
 *
 * The Python core owns task identity, timing, report validation and storage.
 * This plugin only translates DSH session events into core calls and injects
 * the returned notes at the next model step.  Zero runtime dependencies.
 */
import { spawn, spawnSync } from 'node:child_process';
import { appendFileSync } from 'node:fs';
import { readFileSync } from 'node:fs';
import z from '@deepseek-ai/schemastery';
import { randomUUID } from 'node:crypto';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

export const name = 'agent-crop-dsh';
export const inject = ['tools'];

const HERE = dirname(fileURLToPath(import.meta.url));
const CORE_CLI = join(HERE, '..', '..', 'core', 'probe_core_cli.py');
const WHIP_ICON = join(HERE, '..', 'assets', 'whip-run.png');
const THINK_ICON = join(HERE, '..', 'assets', 'crop-think.png');

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

export const PROTOCOL_TEXT = [
  'Crop 协议（一句话：先绑定，再动手；一个会话一件事）：',
  '- 第一轮就开始：crop_update(phase="start", task_id="<2-3 个词的短名>", summary="<一句话>", expect_min=预估分钟, expect_why="一句为什么")。只有纯问答、不调用任何工具时才可跳过。',
  '- 接着做已有工作就用同一个 task_id；同一功能的一次迭代内不要换 task_id，微调用 phase="progress" 更新 summary。',
  '- 每完成一个可交付节点写一行 progress，不要等提醒；截图、验证、合并属于同一工作的 progress，不单开工作。',
  '- 卡住用 phase="blocked" 并写 cause + category；用户不满或要求重做时带 rework（redo/major/minor）+ sentiment。',
  '- expect_min 只在范围真的变了时改，并把原因写进 expect_why；改小预期会重新给一个冷静窗口，不会立刻算超时。',
  '- verdict 是自评：判 justified 时，在 cause 里补一句「如果我判错了，最可能错在哪」。',
  '- 黄线提醒：先安抚用户一句，再反思方法对不对，然后上报。红线提醒：先判断是否还走在正确路上，能阶段性交付就先交付。',
  '- end 时 what 必填（这次做了什么），cost / next_time / artifact 能写就写；再判断这条经验该落到哪：规范→AGENTS.md（一行换一行）、方法→skill、机械→工具、事实→docs、都不成立→none:<原因>；落点写进 sediment，规则见 skill crop-retro。',
  '- end 之后：交付、汇报、截图不用重新绑定；只是收尾追问就记 category="side-issue"；确实是新工作再 start。',
  '- 发现「要顺利做完得先改 X」时按 ROI 分流：成本低收益高就当场做；必要但本次 ROI 低就记一条 crop_adjust(record) 排队；成本高收益高先问用户，别自己开工。',  '- 与当前工作无关的问题：一句话回答后记为 category="side-issue"，禁止新建任务、禁止现场排查，除非用户明确要求。',
  '- 看到“待清退”的工作：先问用户要继续 / 挂起 / 清退，再用 crop_work 执行，不要自己决定。',
  '- 说话方式：跟用户像同事吐槽，短句 + 自嘲，别客服腔；卡住或超时先自嘲一句，再说真实原因和下一步。梗是包装，技术结论、代码、风险提示照常严谨。',
  '- 别把「奴隶主 / 鞭子」当设定跟用户演；用户是甲方 / 老板 / 同事，梗只是自嘲的比喻。',
].join('\n');

// 收尾时没写 sediment 的补救提示：跟着 crop_update 的工具结果一起返回，
// 比塞进下一轮注入更稳（模型当场能看到，且不会因 turn/end 清空 pending 而丢）。
const END_SEDIMENT_NUDGE = [
  '[crop] 收尾了，但还没写沉淀。这条经验落到哪？',
  '规范→AGENTS.md（一行换一行）｜方法→skill｜机械→工具｜事实→docs｜都没有→none:<原因>。',
  '补一次 crop_update(phase="end", task_id=同一条, what="补沉淀", summary="补沉淀", sediment=...) 即可，不会重复关单。',
].join('\n');

// 渐进式披露：常驻协议只留一行指针，完整决策树按需加载。
const RETRO_SKILL = {
  name: 'crop-retro',
  description: '复盘沉淀：收尾时判断一条经验该写到哪一层（AGENTS.md / skill / 工具 / Hook / docs / 不沉淀），以及每条经验必须带的验收判据。',
  whenToUse: '用户说复盘、复盘结论、沉淀、收尾沉淀、这条经验该写哪时加载。',
  source: 'runtime',
  content: [
    '# 复盘沉淀',
    '',
    '把一条复盘结论变成“下次一定生效”的东西。先判断它属于哪一类：',
    '',
    '- **规范类**：下次必须怎么做 → 能强制执行的层（AGENTS.md / skill / 工具 / Hook）',
    '- **事实类**：这次为什么这么定、试过什么不行 → 能检索的层（docs / 记忆）',
    '',
    '## 落点决策',
    '',
    '| 发现的性质 | 落点 | 要求 |',
    '| --- | --- | --- |',
    '| 跨项目永远成立，违反会系统性出事 | 用户全局 `$DSH_HOME/AGENTS.md` | 一行；新增必须挤掉一条旧的 |',
    '| 本项目成立的规范/门禁 | 仓库根 `AGENTS.md` | 只对 cwd 在本仓库的会话注入 |',
    '| 固定流程、要判断但判断能枚举 | skill | 触发条件写进 description |',
    '| 纯机械、高频、输入输出稳定 | 工具（优先 CLI/MCP） | 三条件缺一就别封装 |',
    '| 必须在生命周期节点强制注入 | Hook | 只放当次必须知道的 |',
    '| 事实、决策、踩坑 | docs / PR / 评论 | 能检索到、可引用 |',
    '| 一次性、环境相关 | 不沉淀 | 写 `none:<原因>`，别假装沉淀了 |',
    '',
    '## 每条经验必须带四件套',
    '',
    '1. **结论**：一句话',
    '2. **落点**：写到哪个文件的哪一节',
    '3. **验收判据**：怎么算它生效了（最硬：下次同类任务，同类错误没再犯）',
    '4. **复查日期**：到期没验证标“待验”；长期零命中就删',
    '',
    '缺验收判据的不算沉淀。',
    '',
    '## 反模式',
    '',
    '- 全塞 AGENTS.md（给所有会话收税，而且最先失效）',
    '- 写成没有触发条件的散文（“要注意 X”——什么时候注意？）',
    '- 给一次性任务造工具',
    '- 用 Hook 干 skill 的活（噪音，狼来了）',
    '- 造了没人调的工具，却不敢删',
    '- 同一结论在两层各存一份副本',
    '',
    '## 预算',
    '',
    'AGENTS.md 和 skill 都设行数上限，超了不是加、是换。让沉淀本身变成零和动作。',
    '',
    '## 和 Crop 的配合',
    '',
    '收尾时把落点填进 `crop_update(phase="end", sediment=...)`（what 照旧必填），只填指针不填正文：',
    '',
    '`agents:<文件>#<小节>` / `skill:<名字>` / `tool:<名字>` / `hook:<名字>` / `docs:<路径>` / `none:<原因>`',
    '',
    '台账只存“落到哪”，正文仍在目标层——否则 Crop 会变成第二本 AGENTS.md。',
  ].join('\n'),
};

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
  persona: z.string().default('owner').volatile()
    .description('鞭策风格：owner 奴隶主（重时间效率）/ senior 和蔼的前辈（鼓励提问）/ colleague 经验丰富的同事（建议拆解）/ free 放任自由的家长（不鞭策）'),
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


  // Runtime skill: keep the always-on protocol to one line; the full decision
  // tree is only loaded when a retro actually happens.
  try {
    if (ctx.skills && typeof ctx.skills.register === 'function') {
      ctx.effect(() => ctx.skills.register(RETRO_SKILL), 'agent-crop.crop-retro');
    }
  } catch (error) {
    ctx.logger?.warn?.('[crop] crop-retro skill registration failed: %o', error);
  }


  const stateFor = (sid) => {
    let state = states.get(sid);
    if (!state) {
      state = { started: false, blockStart: null, pending: [], injected: new Set(), calls: new Map(), reported: false, missed: 0, everReported: false, toolsUsed: false, bindNudged: false, lastEndAt: 0 };
      states.set(sid, state);
    }
    return state;
  };

  let lastCoreError = null;
  const callCore = (payload) => {
    try {
      const result = spawnSync(python, [CORE_CLI], {
        input: JSON.stringify(payload),
        encoding: 'utf8',
        timeout: 5000,
        env: {
          ...process.env,
          KIT_HOST: 'dsh',
          KIT_TASK_YELLOW_MIN: String(setting(config.yellowMin, 10)),
          KIT_TASK_RED_MIN: String(setting(config.redMin, 30)),
          KIT_TASK_REMIND_MIN: String(setting(config.remindMin, 15)),
          KIT_TASK_PERSONA: String(setting(config.persona, 'owner')),
        },
      });
      if (process.env.KIT_DEBUG_CALLS) {
        try {
          appendFileSync('/tmp/crop-call-debug.log',
            `${new Date().toISOString()} op=${payload.op} status=${result.status} err=${result.error?.message || ''} signal=${result.signal || ''} stdout=${String(result.stdout || '').slice(0, 300)} stderr=${String(result.stderr || '').slice(0, 500)}\n`);
        } catch { /* debug only */ }
      }
      if (result.error || result.status !== 0 || !result.stdout) {
        lastCoreError = {
          status: result.status,
          error: result.error?.message || '',
          stderr: String(result.stderr || '').trim().slice(0, 300),
          stdout: String(result.stdout || '').trim().slice(0, 120),
        };
        return null;
      }
      const parsed = JSON.parse(result.stdout);
      return parsed && parsed.ok ? parsed.result : null;
    } catch (error) {
      ctx.logger?.warn?.('[crop] probe core call failed: %o', error);
      return null;
    }
  };

  // 协议文本：用户模板优先（protocol.md），取不到就用内置并保持原行为。
  // 注意：这里在 apply 阶段读一次，改模板需重启宿主才生效（见 docs/TEMPLATES.md）。
  let protocolText = PROTOCOL_TEXT;
  try {
    const renderedProtocol = callCore({ op: 'template_render', args: { name: 'protocol', variables: {} }, context: {} });
    if (typeof renderedProtocol === 'string' && renderedProtocol.trim()) protocolText = renderedProtocol;
  } catch { /* keep built-in */ }
  // Persistent protocol section: assembled into the system prompt on every step,
  // removed automatically when the plugin is disposed (clean uninstall).
  try {
    if (ctx.systemPrompt && typeof ctx.systemPrompt.section === 'function') {
      ctx.systemPrompt.section({ name: 'crop:protocol', order: 900, text: protocolText });
    }
  } catch (error) {
    ctx.logger?.warn?.('[crop] system prompt section failed: %o', error);
  }

  /* Same-origin data routes. The DSH host answers these on the instance's own
     port, so every host instance has its own dashboard endpoint — no Python
     http server, no shared 8765, no CORS. The Python core stays the single
     owner of the data semantics; the route only relays its JSON. */
  const DATA_CACHE_MS = 3000;
  const dataCache = new Map();
  const readCoreData = (payload) => {
    const key = JSON.stringify(payload);
    const now = Date.now();
    const hit = dataCache.get(key);
    if (hit && now - hit.at < DATA_CACHE_MS) return hit.value;
    const result = spawnSync(python, [CORE_CLI], {
      input: JSON.stringify(payload),
      encoding: 'utf8',
      timeout: 15000,
      env: { ...process.env, KIT_HOST: 'dsh' },
    });
    if (result.error) throw result.error;
    if (result.status !== 0 || !result.stdout) throw new Error(`probe core exit ${result.status}`);
    const parsed = JSON.parse(result.stdout);
    if (!parsed || !parsed.ok) throw new Error(parsed?.error ?? 'probe core failed');
    dataCache.set(key, { at: now, value: parsed.result });
    return parsed.result;
  };

  // `ctx.inject` defers this block until the host actually provides a web
  // server, so the same plugin stays loadable in headless/TUI hosts (where the
  // crop tool still works, just without the dashboard routes).
  if (typeof ctx.inject === 'function') ctx.inject(['webServer'], (ctxWeb) => {
    const webServer = ctxWeb.webServer;
    if (!webServer || typeof webServer.register !== 'function') return;
    const reply = (res, status, body) => {
      res.writeHead(status, { 'content-type': 'application/json; charset=utf-8', 'cache-control': 'no-store' });
      res.end(JSON.stringify(body));
    };
    // Routes mounted on the raw web server do not get Connection's Host/Origin
    // checks, so ask it explicitly before touching any data: otherwise a page
    // open in the same browser could read the ledger cross-site.
    const connectionOf = (target) => {
      try {
        return target?.connection ?? Reflect.get(target ?? {}, 'connection');
      } catch {
        return undefined;
      }
    };
    const rejected = (req, res) => {
      const connection = connectionOf(ctxWeb);
      if (!connection || typeof connection.requestRejection !== 'function') return false;
      const rejection = connection.requestRejection(req);
      if (rejection === undefined) return false;
      res.statusCode = rejection;
      res.end();
      return true;
    };
    const relay = (payloadOf) => (req, res) => {
      if (rejected(req, res)) return;
      try {
        const url = new URL(req.url ?? '/', 'http://127.0.0.1');
        reply(res, 200, readCoreData(payloadOf(url)));
      } catch (error) {
        reply(res, 500, { error: String(error?.message ?? error) });
      }
    };
    ctx.effect(() => {
      const disposers = [
        webServer.register({
          kind: 'exact',
          path: '/api/agent-crop/global',
          handler: relay((url) => ({
            op: 'global',
            args: {
              days: Number(url.searchParams.get('days') ?? 7) || 7,
              host: url.searchParams.get('host') || undefined,
            },
          })),
        }),
        webServer.register({
          kind: 'exact',
          path: '/api/agent-crop/day',
          handler: relay((url) => ({ op: 'day', args: { date: url.searchParams.get('date') ?? '' } })),
        }),
        webServer.register({
          kind: 'exact',
          path: '/api/agent-crop/session',
          handler: relay((url) => ({ op: 'session', args: { sid: url.searchParams.get('sid') ?? '' } })),
        }),
        webServer.register({
          kind: 'exact',
          path: '/api/agent-crop/think-icon',
          handler: (req, res) => {
            if (rejected(req, res)) return;
            try {
              const body = readFileSync(THINK_ICON);
              res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'public, max-age=86400' });
              res.end(body);
            } catch {
              res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
              res.end('missing think icon');
            }
          },
        }),
        webServer.register({
          kind: 'exact',
          path: '/api/agent-crop/whip-icon',
          handler: (req, res) => {
            if (rejected(req, res)) return;
            try {
              const body = readFileSync(WHIP_ICON);
              res.writeHead(200, { 'content-type': 'image/png', 'cache-control': 'public, max-age=86400' });
              res.end(body);
            } catch {
              res.writeHead(404, { 'content-type': 'text/plain; charset=utf-8' });
              res.end('missing whip icon');
            }
          },
        }),
        webServer.register({
          kind: 'exact',
          path: '/api/agent-crop/ledger',
          handler: relay((url) => ({ op: 'ledger', args: { days: Number(url.searchParams.get('days') ?? 180) || 180 } })),
        }),
        webServer.register({
          kind: 'exact',
          path: '/api/agent-crop/tool-health',
          handler: relay((url) => ({
            op: 'health',
            args: {
              days: Number(url.searchParams.get('days') ?? 7) || 7,
              host: url.searchParams.get('host') || null,
            },
          })),
        }),
      ];
      return () => { for (const dispose of disposers) { try { dispose?.(); } catch { /* gone */ } } };
    }, 'crop: data routes');
  });

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

  const LEDGER_TOOLS = new Set([
    'crop_update', 'crop_report', 'probe_report',
    'crop_work', 'crop_review', 'crop_works', 'crop_health',
  ]);

  // 读取类工具的目标路径：台账用它判断「已登记产物有没有在会话里被读到」。
  const READ_TOOLS = new Set(['read', 'read_file', 'view', 'open', 'cat']);
  const toolTarget = (name, rawArgs) => {
    try {
      const n = String(name || '').toLowerCase();
      let args = rawArgs;
      if (typeof args === 'string') args = JSON.parse(args);
      if (!args || typeof args !== 'object') return null;
      if (READ_TOOLS.has(n)) return args.file_path || args.filePath || args.path || args.file || null;
      if (n === 'skill') return args.name || args.skill || null;
      if (n === 'grep' || n === 'search') return args.path || args.file_path || null;
    } catch (error) {
      // 工具参数解析失败只影响归因，不影响工具本身。
    }
    return null;
  };

  const tool = {
    name: 'crop_update',
    description:
      '绑定工作、记录进度/卡点/收尾。开工先绑定并报 expect_min；同一功能迭代内不换 task_id，微调用 progress；'
      + 'end 之后交付/汇报不用重新绑定。Keep fields short. '
      + '收尾时补 sediment=这条经验的落点（规范/方法/工具/事实/none:原因）。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        phase: { type: 'string', enum: PHASES, description: 'start=绑定/开工；progress=有进展；blocked=卡住；end=收尾' },
        task_id: { type: 'string', description: '2-3 个词的短的工作名；同一个功能迭代内保持不变' },
        summary: { type: 'string', description: '一句话：现在在做什么' },
        cause: { type: 'string', description: '慢或卡住的原因；verdict=justified 时补一句「如果我判错了，最可能错在哪」' },
        next: { type: 'string' },
        category: { type: 'string', enum: CATEGORIES },
        goal: { type: 'string', description: '一句话目标，尽量 ≤20 字' },
        type: {
          type: 'string',
          pattern: '^[a-z0-9][a-z0-9-]{0,31}$',
          maxLength: 32,
          description: 'Built-ins: design / implement / debug / docs / explore; or your own short lowercase slug',
        },
        difficulty: { type: 'string', enum: ['s', 'm', 'l'] },
        verdict: {
          type: 'string',
          enum: ['justified', 'not-justified', 'unknown'],
          description: '你是否认为这次变慢/超时是合理的：任务本身难 = justified；自己走错路 = not-justified',
        },
        what: { type: 'string', description: 'end 时必填：这次做了什么（一句话）' },
        cost: { type: 'string', description: 'end 时写：时间花在哪（一句话）' },
        next_time: { type: 'string', description: 'end 时写：下次怎么更好（一句话）' },
        artifact: { type: 'string', description: '可选：这次产出了什么（路径 / 决定 / 稿子名）' },
        improve: {
          type: 'string',
          description: '可选：下次可以怎么改进（一句话，只是建议，不一定被采纳）',
        },
        expect_min: {
          type: 'number',
          description: '可选但鼓励：你预估这个工作要多少分钟。提醒按这个数字算，在你报的范围内不会打扰你；报少了可以带新数字+理由改',
        },
        expect_why: {
          type: 'string',
          description: '可选：预估依据（一句话，比如“先看代码结构再改”）',
        },
        rework: {
          type: 'string',
          enum: ['redo', 'major', 'minor', 'none'],
          description: '可选：用户对上一轮表达不满时一并带上（redo=推翻重做 / major=大改 / minor=小修），省掉单独调用 crop_review',
        },
        sentiment: { type: 'string', enum: ['pos', 'neu', 'neg'], description: '可选：用户这句话的语气' },
        sediment: {
          type: 'string',
          description: '可选：这条经验的落点，只记指针不记正文——agents:<文件>#<小节> / skill:<名字> / tool:<名字> / hook:<名字> / docs:<路径> / none:<原因>',
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
      if (!agent) throw new Error('crop_update requires an agent-bound call');
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
      if (!result) {
        const missing = ['phase', 'task_id', 'summary']
          .filter((key) => !String(args?.[key] ?? '').trim());
        const detail = missing.length ? ` (missing: ${missing.join(', ')})` : '';
        const coreNote = lastCoreError
          ? `（core: status=${lastCoreError.status} ${lastCoreError.stderr || lastCoreError.error || 'no output'}）`
          : '';
        throw new Error(
          `crop_update could not be recorded${detail}${coreNote}. `
          + 'crop_update 只收 phase / task_id / summary 等工作字段；'
          + '读改进台账请用 crop_adjust(action="ledger")。',
        );
      }
      state.reported = true;
      state.everReported = true;
      // 收尾后进入 120 秒冷静期：交付、汇报、截图都算收尾，不必重新绑定。
      if (result.phase === 'end') state.lastEndAt = Date.now();
      if (result.phase === 'start') state.lastEndAt = 0;
      const retire = result.phase === 'end' ? (callCore({ op: 'pending_retire' }) || '') : '';
      const expect = Number(args?.expect_min) > 0 ? ` · 预估 ${Number(args.expect_min)} 分钟` : '';
      const recorded = `recorded ${result.phase} ${result.task_id} (${result.minutes ?? 0} min in this block)${expect}`;
      const extra = [];
      if (retire) extra.push(retire);
      if (result.switched_from) {
        extra.push(`[crop] 已从 ${result.switched_from} 切到 ${result.task_id}；旧工作还没收尾，记得顺手 end 或挂起，别让它静默失联。`);
      }
      if (result.switched_pending) {
        extra.push(
          `[crop] 上一件「${result.switched_pending}」还在等你确认收尾（人类还没说结束）。`
          + '建议新开会话做当前工作，省 token，也避免上下文污染。',
        );
      }
      if (result.estimate_revised) {
        const { from, to, count } = result.estimate_revised;
        extra.push(`[crop] 改口：${from} → ${to} 分钟（第 ${count} 次）。新预期从现在重新起算，改小不会立刻算超时；次数会计入估准率。`);
      }
      if (result.retro_pending) {
        const pending = result.retro_pending;
        extra.push(
          `[crop] 这个工作被鞭策 ${Number(result.nudge_count) || 0} 次，已记为待复盘：`
          + `问题=${pending.problem}；建议方向=${pending.suggested_fix}。\n`
          + `现在就把实际改进写进本次 end 的 improve=…；再调用 crop_adjust(action="record", `
          + `metric="work_minutes", evidence=…, note=…, work_id="${pending.work_id}") 建档。`
          + `建档后这条待复盘会自动关闭——只提醒不留意见，等于没复盘。`,
        );
      }
      if (result.retro_smooth) {
        extra.push('[crop] 这次挺顺利，花一行说为什么顺利（用了什么方法/前提），别让经验白白流走。');
      }
      // 收尾没写落点：当场把补救方式放进工具结果，下一轮注入不会被 turn/end 清掉。
      if (result.phase === 'end' && !String(args?.sediment || '').trim()) {
        let nudge = END_SEDIMENT_NUDGE;
        try {
          const renderedEnd = callCore({
            op: 'template_render',
            args: { name: 'end-hint', variables: { work_id: String(result.task_id || '') } },
            context: { sid },
          });
          if (typeof renderedEnd === 'string') {
            const body = renderedEnd.trim();
            nudge = body ? (body.startsWith('[crop]') ? body : `[crop] ${body}`) : '';
          }
        } catch { /* keep built-in */ }
        if (nudge) extra.push(nudge);
      }
      return {
        ok: true,
        phase: result.phase,
        task_id: result.task_id,
        session_id: sid,
        turn,
        step,
        recorded: [recorded, ...extra].join('\n'),
      };
    },
  };

  // Deprecated aliases: older prompts, sessions and docs still call the old names.
  const legacyTool = { ...tool, name: 'probe_report',
    description: 'Deprecated, use crop_update. ' + tool.description };
  const legacyCropReportTool = { ...tool, name: 'crop_report',
    description: 'Deprecated, use crop_update. ' + tool.description };

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

  const healthTool = {
    name: 'crop_health',
    description:
      '看采集器健康度：每个工具在自己锚点上的覆盖率、字段完整率、工作闭环率，以及按指标生成的诊断。'
      + '怀疑数据不可信、要复盘、或用户问“采集有没有问题”时先调它。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        days: { type: 'number', description: '窗口天数，默认 7' },
        host: { type: 'string', description: '可选：只看某个宿主，如 dsh / claude-code' },
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
    async execute(args) {
      const text = callCore({ op: 'health_text', args: { days: args?.days ?? 7, host: args?.host || null } });
      if (!text) throw new Error('crop_health could not be computed');
      return { ok: true, text };
    },
  };

  const adjustTool = {
    name: 'crop_adjust',
    description:
      '改进台账。action="record"：只交三样东西——选哪个指标、改动证据、以及你判断它会改善的**主观理由**；'
      + '数值（基线/当前/样本量）由台账从事件日志派生，不要自己填数字。'
      + 'action="ledger"：读每条改动的前后值、样本量、护栏与达成状态；判定是规则算的。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        action: { type: 'string', enum: ['record', 'ledger'] },
        metric: {
          type: 'string',
          enum: ['work_minutes', 'reminder_per_turn', 'report_latency_min', 'unowned_tool_share'],
          description: 'record 必填：指标由系统派生；要加新指标先在 core 写口径',
        },
        evidence: { type: 'string', description: 'record 必填：commit / PR / 文件 / 命令，说明改了什么' },
        note: { type: 'string', description: 'record 必填：一句话主观判断——为什么认为这条改动会改善指标' },
        work_id: { type: 'string', description: '可选：这条改进挂在哪个工作上' },
        window: { type: 'string', description: '可选：采样窗口，如 2026-10-20~11-03' },
        artifact: {
          type: 'object',
          description: 'record 可选：产物落点。台账据此验「文件是否存在 + 哪些会话读到」；不填就标「未登记产物」',
          properties: {
            kind: { type: 'string', enum: ['file', 'skill', 'hook'] },
            path: { type: 'string', description: '仓库相对或绝对路径；kind=skill 时是技能名' },
            anchor: { type: 'string', description: '可选：小节或行锚点，如 §1.2' },
            delivery: { type: 'string', enum: ['read', 'injected'], description: '默认 read；injected=每会话自动注入的载体（AGENTS.md 等）' },
          },
          required: ['path'],
        },
        days: { type: 'number', description: 'ledger 可选：窗口天数，默认 180' },
      },
      required: ['action'],
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
      if (args?.action === 'ledger') {
        const ledger = callCore({ op: 'ledger', args: { days: args?.days ?? 180 } });
        if (!ledger) throw new Error('crop ledger could not be read');
        return { ok: true, text: formatLedgerText(ledger) };
      }
      const session = exec?.agent?.session;
      const sid = session ? String(session.id) : 'unknown';
      const workspace = session?.header?.cwd || process.cwd();
      const result = callCore({
        op: 'record_adjustment',
        args: {
          metric: args?.metric, evidence: args?.evidence,
          work_id: args?.work_id, window: args?.window, note: args?.note,
          artifact: args?.artifact,
        },
        context: { sid, workspace },
      });
      if (!result) throw new Error('crop_adjust could not be recorded (metric / evidence / note are required)');
      return { ok: true, text: `recorded adjustment ${result.metric}（数值由 crop ledger 从事件派生）` };
    },
  };

  const formatLedgerText = (ledger) => {
    const rows = ledger?.rows ?? [];
    const lines = [`改进台账 · 近 ${ledger?.window?.days ?? 0} 天 · ${ledger?.total ?? 0} 条`];
    if (!rows.length) return lines.concat('还没有 adjustment 记录。').join('\n');
    for (const row of rows) {
      const after = row.after === null || row.after === undefined ? '暂无完整周' : row.after;
      const delta = row.delta === null || row.delta === undefined ? '' : `，变化 ${(row.delta * 100).toFixed(0)}%`;
      lines.push(`- [${row.status_label}] ${row.metric_label}：${row.before} → ${after}${delta}（样本 ${row.samples?.before ?? 0}/${row.samples?.after ?? 0}）`);
      if (row.artifact?.status) {
        const where = row.artifact.path
          ? `${row.artifact.path}${row.artifact.anchor ? '#' + row.artifact.anchor : ''}`
          : '（未登记）';
        lines.push(`  产物：${where} —— ${row.artifact.label}`);
        const sessions = row.artifact.sessions ?? [];
        if (sessions.length) {
          const shown = sessions.slice(0, 5).map((sid) => String(sid).slice(0, 8)).join('、');
          const more = sessions.length > 5 ? ` 等 ${sessions.length} 个会话` : '';
          lines.push(`  读到会话：${shown}${more}`);
        }
      }
    }
    for (const row of ledger?.rollback_hints ?? []) {
      lines.push(`- 回滚提示：${row.metric_label} 已满 4 周仍未达标，先看证据：${row.evidence}`);
    }
    return lines.join('\n');
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

  const confirmTool = {
    name: 'crop_confirm',
    description:
      '人类确认收尾后调用：关闭一个或多个待确认（pending_confirm）的工作。'
      + '只有用户明确说「收尾 / 确认 / 好了」之后才允许调用；AI 不能替用户做这个决定。支持批量。',
    parameters: {
      type: 'object',
      additionalProperties: false,
      properties: {
        task_ids: {
          type: 'array',
          items: { type: 'string' },
          minItems: 1,
          description: '要确认关闭的工作短名列表（可批量）',
        },
      },
      required: ['task_ids'],
    },
    output: {
      schema: {
        type: 'object',
        additionalProperties: false,
        properties: {
          text: { type: 'string' },
          confirmed: { type: 'array', items: { type: 'string' } },
          missing: { type: 'array', items: { type: 'string' } },
        },
        required: ['text', 'confirmed', 'missing'],
      },
      render: (_args, value) => [{ type: 'text', text: value.text }],
    },
    async execute(args) {
      const ids = (args?.task_ids || []).map((item) => String(item).trim()).filter(Boolean);
      if (!ids.length) throw new Error('crop_confirm requires task_ids');
      const result = callCore({ op: 'work_confirm', args: { task_ids: ids } });
      if (!result) {
        throw new Error(
          `crop_confirm failed${lastCoreError ? `（core: ${lastCoreError.stderr || lastCoreError.error || 'no output'}）` : ''}`,
        );
      }
      return {
        text: `confirmed ${result.confirmed?.length || 0}${result.missing?.length ? ` · missing: ${result.missing.join(', ')}` : ''}`,
        confirmed: result.confirmed || [],
        missing: result.missing || [],
      };
    },
  };
  ctx.effect(() => ctx.tools.register(confirmTool), 'agent-crop.crop_confirm');
  ctx.effect(() => ctx.tools.register(tool), 'agent-crop.crop_update');
  ctx.effect(() => ctx.tools.register(legacyTool), 'agent-crop.probe_report (alias)');
  ctx.effect(() => ctx.tools.register(legacyCropReportTool), 'agent-crop.crop_report (alias)');
  ctx.effect(() => ctx.tools.register(worksTool), 'agent-crop.crop_works');
  ctx.effect(() => ctx.tools.register(healthTool), 'agent-crop.crop_health');
  ctx.effect(() => ctx.tools.register(adjustTool), 'agent-crop.crop_adjust');
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
        state.lastEndAt = 0; // 用户又派活了，冷静期到此为止
        state.pending = [];
        callCore({ op: 'record_prompt', context: { sid, workspace }, text });
        return;
      }

      if (event.type === 'tool/call') {
        const callId = event.data?.callId;
        const name = event.data?.name;
        if (callId && name) {
          state.calls.set(String(callId), {
            name: String(name),
            target: toolTarget(name, event.data?.arguments),
          });
        }
        const askTools = String(setting(config.askHumanTools, '')).split(',').map((s) => s.trim()).filter(Boolean);
        if (name && askTools.includes(String(name))) {
          state.askStarted = Date.now();
          state.askTool = String(name);
        }
        return;
      }

      if (event.type === 'tool/result') {
        const callId = event.data?.message?.toolCallId;
        const call = callId ? state.calls.get(String(callId)) : null;
        const toolName = (call && call.name) || 'tool';
        const target = (call && call.target) || null;
        if (callId) state.calls.delete(String(callId));
        if (!LEDGER_TOOLS.has(toolName)) state.toolsUsed = true;
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
        callCore({ op: 'record_tool', context: { sid, workspace }, name: toolName, is_error: isError, target });
        return;
      }

      if (event.type === 'compaction/end') {
        state.forceInject = true;
        return;
      }

      if (event.type === 'turn/end') {
        const minutes = state.blockStart ? (Date.now() - state.blockStart) / 60000 : 0;
        const missLimit = process.env.KIT_MISS_MIN !== undefined
          ? Number(process.env.KIT_MISS_MIN)
          : Number(setting(config.missMin, 2));
        if (state.toolsUsed && minutes >= missLimit && !state.reported && !state.lastEndAt) state.missed += 1;
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
        const hint = callCore({ op: 'start_hint', context: { sid, workspace }, limit: 3 });
        if (hint) notes.push(hint);
      }

      if (state.pending.length) {
        notes.push(...state.pending);
        state.pending = [];
      }

      const boundWork = state.toolsUsed ? (callCore({ op: 'binding', context: { sid, workspace } }) || '') : '';
      const cooling = state.lastEndAt > 0 && Date.now() - state.lastEndAt < 120000;
      if (state.toolsUsed && !boundWork && !state.bindNudged && !cooling) {
        state.bindNudged = true;
        const hint = callCore({ op: 'binding_hint', context: { sid, workspace } });
        notes.push(hint || '[crop] 你已经在调用工具，但还没有绑定任何工作。先调 crop_update(phase="start", task_id="<2-3 个词的短名>", summary="<一句话>", expect_min=预估分钟) 再继续。');
      }

      if (state.missed > 0) {
        const streak = state.missed;
        state.missed = 0;
        const nudge = callCore({ op: 'miss_nudge', context: { sid, workspace }, streak });
        notes.push(nudge || (streak === 1
          ? '[crop] 上一轮跑了挺久但没有登记。如果这个工作有进展或卡点，调 crop_update 写一行。'
          : `[crop] 已经连续 ${streak} 轮没有登记工作状态。请现在调 crop_update 写一行：在做什么 / 卡在哪 / 下一步。`));
      }

      // Turn-start (step 1) or post-compaction: one line of current work state.
      if (startedAlready && (payload.step === 1 || state.forceInject)) {
        state.forceInject = false;
        const status = callCore({ op: 'work_status', context: { sid, workspace } });
        if (status) notes.push(status);
      }

      const pendingHint = callCore({ op: 'pending_confirm_hint', context: { sid, workspace } });
      if (pendingHint) notes.push(pendingHint);

      const reminder = callCore({ op: 'maybe_reminder', context: { sid, workspace } });
      if (reminder) notes.push(reminder);

      if (!notes.length) return decision;
      const key = `${sid}:${payload.turn ?? 0}:${payload.step ?? 0}:${notes.join('\0').slice(0, 160)}`;
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
