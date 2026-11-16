# Crop Trace · 事件、存储与消费

> 这份文档回答三个问题：**采什么事件**、**怎么存**、**谁在用**。
> 也是 README 里「传统 Trace vs Crop Trace」对比的底稿。

## 0. 背景：为什么要把 Trace 的思路搬到 AI 干活上

传统后端做性能优化，靠的是 Trace（OpenTelemetry 那套）：一次请求打一条 trace，
每个中间件、DB 调用、下游 RPC 是一个 span，带上耗时和状态。于是你能回答
「这次请求 800ms，其中 500ms 花在 DB、200ms 在序列化」。

AI 干活这件事现在**没有这样的基础设施**：你知道它跑了 40 分钟，但不知道
40 分钟里多少是模型在推理、多少在等工具、多少在等你回话、多少在原地打转。
没有 trace，就没有归因；没有归因，性能优化只能靠感觉。

Crop 要补的就是这一层：**给一次「工作」打一条 trace**。

## 1. 事件清单

### 1.1 已有（v0，代码里已在采）

| 事件 | 何时产生 | 关键字段 | 用途 |
|---|---|---|---|
| `session_start` | 宿主会话开始 | `sid` `workspace` `host` | 会话边界 |
| `prompt` | 用户发一条消息 | `sid` `workspace` `snippet`(≤160) | 任务块起点 |
| `tool` | 每次工具调用结束 | `sid` `name` `is_error` | 工具分布、失败率 |
| `stop` | 回合结束 | `sid` `block_start` `minutes` `work_id` | 任务块终点；`work_id` = 结束时的认领 |
| `reminder` | 超红线提醒注入 | `sid` `block_start` `minutes` | 提醒效果 |
| `probe_report` | AI 主动上报 | `phase` `task_id` `summary` `cause` `next` `category` `minutes` `turn` `step` `sediment`(收尾落点) | 归因与任务状态 |
| `task_link` | 报告/块归属到工作 | `task_id` `block_start` | 把工作挂到时间线上；同一 `block_start` 以最后一条为准 |
| `session_end` | 会话结束 | `sid` | 收尾 |

所有事件公共字段：`ev`（类型）、`t`（毫秒时间戳）、`host`（dsh / claude-code / …）、`sid`。
`user_wait` / `idle_gap` 自 v1 起带 `work_id`（等待归属到它前面那个 block 的工作）。

> **活跃时长口径（Issue #18 起）**：`stop.minutes` 与 `blocks/*.jsonl` 的 `minutes`
> 是**观测到的活跃时长**，不是墙钟。相邻事件间隔超过 `IDLE_MIN`（默认 15 分钟）的
> 静默段不计；`user_wait` / `idle_gap` / `human_wait` 覆盖的时段也会扣掉。
> `wall_minutes` 字段保留原始墙钟供诊断。没有 `turn_end` 收尾的 block 会在
> `session_start` / `session_end` 时关闭，事件带 `abandoned: true`，按最后一条
> 实际事件截断，不会把重启后的整段墙钟接着算。

### 1.2 新增（v1，按 grillme 决策）

| 事件 | 何时产生 | 关键字段 | 对应决策 |
|---|---|---|---|
| `work_open` | 开始一条工作 | `work_id` `goal` `type` `difficulty_est` `workspace` | D1 / D7 / D10 |
| `work_update` | 工作推进/挂起/恢复 | `work_id` `state` `summary` | D8 |
| `work_close` | 判定完成 | `work_id` `difficulty_actual` `summary` | D1 / D7 / D8 |
| `work_sediment` | 收尾时登记这条经验的落点 | `work_id` `sediment`(`agents:`/`skill:`/`tool:`/`hook:`/`docs:`/`none:`) | 复盘沉淀；二次收尾补记时只发这个事件 |
| `work_archive` | 挂起超期归档 | `work_id` `archived_at` | D8 / D9 |
| `turn_end` | 回合结束（替代/补充 `stop`） | `sid` `block_start` `minutes` | D2 |
| `user_wait` | 从回合结束到用户回话 | `sid` `waiting_ms` `within_work_hours` `active_work_id` | D2 |
| `idle_gap` | 用户回话间隔超阈值 | `sid` `gap_ms` `reason`(idle/off-hours) | D2 |
| `side_issue` | 发现主线外的枝节 | `work_id` `note` `suggest`(new_session/later) | D4 / D10 |
| `adjustment` | 采纳了一个改进 | `work_id` `before` `after` `evidence` | D13 |
| `tool_churn` | 工具层的重复/失败（事实信号，**不是返工**） | `kind`(repeat-tool) `tool` `count` `failures` | D3 |
| `self_review` | AI 判断的返工档次（redo/major/minor/none） | `work_id` `rework` `sentiment` `reason` | D3 / D5 |

`tool` 事件的 `name` 来自 `tool/call`（插件先记 callId→name，`tool/result` 再回填）；
早期数据里出现过 callId 当工具名的情况，属于 v1 之前的问题，不回溯修。
| `work_summary` | 工作结束时趁记忆热写的复盘 | `work_id` `what` `cost` `next_time` | D1 / D13 |

### 1.3 派生而非新增采集

以下不单独记事件，由上面的原始事件算出来：

- **活跃时长**：`turn_end.minutes` 之和，且只取工作时段内、间隔 < 15 分钟（D2）
- **等人的时长**：`user_wait.waiting_ms` 归一化到活跃段
- **返工档次**：`self_review.rework`（AI 判断），工具重复单独看 `tool_churn`
- **估准率**：`difficulty_est` vs `difficulty_actual`
- **情绪痕迹**：纠正次数、重复提问次数——从 `prompt` 序列里数，不读语义

### 1.4 建议补上：给 `tool` 事件加 span 结构

现在 `tool` 是扁平列表，只能统计分布，算不出「关键路径」。补三个字段成本极低：

| 字段 | 含义 |
|---|---|
| `turn` / `step` | 所属回合与步骤（已经在 `probe_report` 里有） |
| `span_id` | 本次调用自己的 id |
| `parent` | 上一级（通常是 step 或 turn） |

有了它，`crop` 就能回答传统 trace 里最有价值的问题：**这一轮里最长的链是哪条**。
这是当前设计与传统 Trace 最大的差距，补起来却最便宜。

## 2. 存储布局

```
~/.agentcrop/data/
├── events.jsonl              # 唯一追加日志（当前按月一个文件，读取走尾部）
├── tasks/
│   ├── open.json             # 进行中的工作（含 goal/type/state）
│   ├── closed/YYYY-MM-DD.jsonl
│   └── archive/              # 挂起超期归档（不删除）
├── reports/
│   └── YYYY-MM-DD.jsonl      # AI 上报的原始记录
├── blocks/
│   └── YYYY-MM-DD.jsonl      # 任务块归档
└── runtime/
    └── current.json          # 当前会话/块/活动工作
```

规则：

- **只追加，不改写历史**；状态变化用新事件表达
- 读写分离：写只写当天，读只读尾部（`crop` 负责，不让宿主直接解析全文件）
- 目录 0700；不存对话正文，只存 `snippet`（≤160 字符）与 `summary`

## 3. 谁在用、怎么用

| 消费方 | 读什么 | 输出 |
|---|---|---|
| **DSH 右侧栏面板** | 最近事件 + `runtime/current.json` + `tasks/open.json` | 纯数字：当前工作、活跃时长、等你多久、今日事件数、最贵三个工作 |
| `crop status` | 同上 | 一屏文字 |
| `crop report --week` | 一周事件 + `reports/` | 人话周报：一段叙述 + 高亮「最具价值的调整」或「问题 + A/B/C 建议」 |
| `crop report --month` | 一个月聚合 | 趋势、估准率、human-wait 占比、下月想改的一点 |
| 分析（离线） | 全部 | 同类工作 × 难度分层对比、返工率 |
| 宿主注入 | `tasks/open.json` + `runtime` | 开工提示、红线提醒、枝节建议 |

## 4. 与传统 Trace 的对照（评审 + README 素材）

| 维度 | 传统 Trace（OpenTelemetry） | Crop Trace |
|---|---|---|
| 单位 | 一次请求（trace） | 一次工作（work，可跨会话跨天） |
| 节点 | 中间件 / DB / RPC | 人 / agent / 模型 / 工具 / 等待 |
| 分析粒度 | 到代码级 span | **到「AI 干活」这一层**，工具自身性能不归这里 |
| 时钟 | 单调时钟，同进程 | 墙钟 + 人在环，需要活跃段切分 |
| 等待 | 只算 IO 阻塞 | **等人的时间单独成一类**，还分工作时段/非工作时段 |
| 归因 | 按 span 耗时确定性归因 | 确定性部分按耗时；说不清的交给 AI 定性判断 |
| 采样 | 可按比例采样降开销 | 几乎全量采（事件便宜），成本在解析 |
| 因果 | 调用链天然因果 | 返工/枝节靠模式识别，不是调用链 |
| 对象 | 机器 | 人机协作过程，包含人的注意力 |

### 评审判读

**不是差距的：**

- **父子 span**：我们的分析粒度只到「AI 干活」这一层。工具内部的性能该用传统 APM 做，
  不归这套东西。之前说的「关键路径」收回——那是工具级的问题，不是 AI 行为级的问题。
- **时钟**：把人的动作、耗时、责任算进来，是这套设计的**特色**，不是缺陷。
- **归因**：AI 是概率系统，能用数据算的算（耗时、失败次数、返工），算不出的才让 AI 定性判断。

**真正的差距（要补）：**

1. **跨会话 / 跨进程的上下文**：同一个工作可能横跨多个会话，也可能被 subagent 拆到别的会话里干。
   现在只有 `sid`，没有「这条工作属于哪个 work」的贯穿标识——事件流会断。
   补法：`work_id` 当 trace 上下文，所有事件都带上它；subagent 继承父工作的 `work_id`。
现在的绑定规则、失效与自愈见 [SESSION-WORK.md](SESSION-WORK.md)。
2. **可靠性**：AI 主动上报会忘（见 §5），需要有兜底和缺口检测。

## 5. 触发时机、由谁触发、怎么保证不漏

### 5.1 一次工作的事件流

```text
用户发消息
   │  ← prompt            （宿主自动）
   ├─ session_start       （宿主自动，会话创建时）
   │
   ├─ work_open           （AI 主动，开工时上报）
   │
   ├─ tool × N            （宿主自动，每次工具返回）
   ├─ work_update         （AI 主动，有进展/卡住时）
   ├─ turn_end            （宿主自动，回合结束）
   │
   ├─ user_wait           （插件反推：下次用户回话时算出间隔）
   │     └─ 间隔 > 15 分钟 → idle_gap
   │
   ├─ reminder            （插件自动，超红线时注入）
   ├─ side_issue          （AI 主动：发现主线外的枝节）
   │
   ├─ work_close          （AI 主动，用户说满意/完成）
   └─ work_archive        （定时任务，挂起超期）
```

### 5.2 三类触发者

| 触发者 | 事件 | 可靠性 |
|---|---|---|
| **宿主 / 插件（自动）** | `session_start` `prompt` `tool` `turn_end` `reminder` `user_wait` `idle_gap` `rework_signal` `work_archive` | 100%，不依赖 AI |
| **AI 主动上报** | `work_open` `work_update` `work_close` `side_issue`（语义部分：`goal` `type` `difficulty` `summary` `cause`） | 依赖 AI，必须用注入兜底 |
| **人** | `adjustment`（采纳改进） | 由人或复盘时标 |

**原则：能自动采的绝不让 AI 记；只有语义判断（这活是什么、为什么慢）才交给 AI。**

### 5.3 怎么保证 AI 不漏报

不靠 AI 的记性，靠**推而不是拉**：

1. **每次回合开始，宿主主动注入一行**：当前工作、上次更新过去多久、如果没有更新就提醒一句。
   AI 不需要「想起来去看」，信息是被推到它面前的。
2. **缺口检测**：回合结束时若本轮没有任何 `probe_report`，下一轮注入「你上一轮没登记工作状态」。
3. **降级不丢时间**：即使 AI 一直不报，`prompt/tool/turn_end` 仍然齐全——活跃时长、工具分布、
   返工痕迹都算得出来；丢的只是「这条工作叫什么、为什么慢」这层语义。
4. **上报贵在一次一句话**：`crop` 把上报压缩成一行，AI 的负担足够低，才可能长期不偷懒。

### 5.4 运行时阶段：Crop 的「回合」和 DSH 的「turn/step」不是一回事

先对齐术语，这是之前没说清的地方：

| DSH 运行时阶段 | 含义 | Crop 的动作 |
|---|---|---|
| `user/message`（turn 开启） | 用户发一条消息，开启一个 turn | 记 `prompt`，算任务块起点 |
| `agent/pre-step`（每个 step 前） | **每次模型调用前**（包括工具调用后的续步） | 注入工作状态 / 红线提醒 |
| compaction（压缩） | 在 pre-step 内按已提交请求的 header 压力执行 | **不感知**——见下方风险 |
| `tool/result` | 每次工具返回 | 记 `tool` |
| `turn/end` | 回合结束 | 记 `turn_end`，算块时长 |

**关键修正**：我先前说的「回合开始时注入」不准确。`pre-step` 是**每一步模型调用前**都会跑的，
不是每个 turn 只跑一次。如果每步都注入，就是噪音；正确做法是——

- 只在 **turn 的第一个 step** 注入工作状态（`step === 1`）
- 或者工作状态**发生变化**时才注入（新建、挂起、恢复）
- 红线提醒单独判断，不受此限

**压缩的风险**：compaction 在 pre-step 内按已提交请求的 header 执行，和我们的注入发生在同一阶段，
先后取决于监听器顺序。后果是：**注入的内容可能在后续步骤被压缩掉**。
对策：注入尽量短（一行），且不依赖它长期存在——每轮需要时重新注入。

### 5.5 工作结束时的「趁热小结」

任务完成时，AI 的记忆最完整。这时让它写一段工作小结，比事后从日志反推准得多：

- 触发：`work_close` 的同一时刻（AI 本来就要调 `crop` 收尾）
- 内容三句：**做了什么 / 时间花在哪 / 下次怎么做更好**
- 落盘：`work_summary` 事件 + `tasks/closed` 里对应记录
- 用途：周报的素材、同类工作对比的定性解释

风险：AI 不调 `work_close` 就没有小结；兜底是归档时用自动事件拼一个「时间去哪了」的干版本。

## 6. 待定

- `waiting_ms` 的阈值（15 分钟）是否需要按工作类型区分
- `rework_signal` 的判定规则（连续失败 N 次？同一文件改 M 次？）
- 周报的「同类工作」聚类维度：`type` 够不够，要不要加项目/仓库维度
- `work_id` 由 subagent 继承父会话的工作（已确认方向；待验证 DSH 子会话是否暴露父会话标识）
