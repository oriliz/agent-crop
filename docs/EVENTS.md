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
| `stop` | 回合结束 | `sid` `block_start` `minutes` | 任务块终点 |
| `reminder` | 超红线提醒注入 | `sid` `block_start` `minutes` | 提醒效果 |
| `probe_report` | AI 主动上报 | `phase` `task_id` `summary` `cause` `next` `category` `minutes` `turn` `step` | 归因与任务状态 |
| `task_link` | 报告关联到块 | `task_id` `block_start` | 把报告挂到时间线上 |
| `session_end` | 会话结束 | `sid` | 收尾 |

所有事件公共字段：`ev`（类型）、`t`（毫秒时间戳）、`host`（dsh / claude-code / …）、`sid`。

### 1.2 新增（v1，按 grillme 决策）

| 事件 | 何时产生 | 关键字段 | 对应决策 |
|---|---|---|---|
| `work_open` | 开始一条工作 | `work_id` `goal` `type` `difficulty_est` `workspace` | D1 / D7 / D10 |
| `work_update` | 工作推进/挂起/恢复 | `work_id` `state` `summary` | D8 |
| `work_close` | 判定完成 | `work_id` `difficulty_actual` `summary` | D1 / D7 / D8 |
| `work_archive` | 挂起超期归档 | `work_id` `archived_at` | D8 / D9 |
| `turn_end` | 回合结束（替代/补充 `stop`） | `sid` `block_start` `minutes` | D2 |
| `user_wait` | 从回合结束到用户回话 | `sid` `waiting_ms` `within_work_hours` `active_work_id` | D2 |
| `idle_gap` | 用户回话间隔超阈值 | `sid` `gap_ms` `reason`(idle/off-hours) | D2 |
| `side_issue` | 发现主线外的枝节 | `work_id` `note` `suggest`(new_session/later) | D4 / D10 |
| `adjustment` | 采纳了一个改进 | `work_id` `before` `after` `evidence` | D13 |
| `rework_signal` | 检测到返工痕迹 | `work_id` `kind`(repeat-tool/repeat-file/test-red/redo) `count` | D3 / D5 |

### 1.3 派生而非新增采集

以下不单独记事件，由上面的原始事件算出来：

- **活跃时长**：`turn_end.minutes` 之和，且只取工作时段内、间隔 < 15 分钟（D2）
- **等人的时长**：`user_wait.waiting_ms` 归一化到活跃段
- **返工次数**：同 `work_id` 内 `rework_signal` 计数
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

## 4. 与传统 Trace 的对照（设计评审 + README 素材）

| 维度 | 传统 Trace（OpenTelemetry） | Crop Trace |
|---|---|---|
| 单位 | 一次请求（trace） | 一次工作（work，可跨会话跨天） |
| 节点 | 中间件 / DB / RPC | 人 / agent / 模型 / 工具 / 等待 |
| 层级 | 显式父子 span | turn / step / tool，层级是隐含的 |
| 时钟 | 单调时钟，同进程 | 墙钟 + 人在环，需要活跃段切分 |
| 等待 | 只算 IO 阻塞 | **等人的时间单独成一类**，还分工作时段/非工作时段 |
| 归因 | 按 span 耗时确定性归因 | 证据 + AI 自述 + 人校正，概率性归因 |
| 采样 | 可按比例采样降开销 | 几乎全量采（事件便宜），成本在解析 |
| 因果 | 调用链天然因果 | 返工/枝节需要模式识别，不是调用链 |
| 关键路径 | 有，算得出来 | **还没有**——缺显式 span 层级 |
| 对象 | 机器 | 人机协作过程，包含人的注意力 |

### 评审判读：我们比传统 Trace 缺什么

1. **没有显式 span 层级**——无法算「关键路径」，也就无法回答「这 40 分钟里最长的那条链是什么」
2. **没有跨进程 trace 上下文**——agent → 工具 → 子 agent 之间不传 trace_id，串联靠 `sid` + 时间
3. **墙钟不单调**——需要活跃段切分来去掉人的离场（D2）
4. **归因是概率的**——AI 自述不可靠，只能靠证据兜底（D3）
5. **基数不可控**——工具名可枚举，但「AI 干了什么」不可枚举，需要靠 work/type/difficulty 降维

这五条是设计里最该被质疑的地方，也是后续版本要补的。

## 5. 待定

- `waiting_ms` 的阈值（15 分钟）是否需要按工作类型区分
- `rework_signal` 的判定规则（连续失败 N 次？同一文件改 M 次？）
- 周报的「同类工作」聚类维度：`type` 够不够，要不要加项目/仓库维度
