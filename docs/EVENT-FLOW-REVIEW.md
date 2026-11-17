# 事件流评审：流程对比 · 逐事件拆解 · 会话绑定

> 按时间顺序写。先看两条流程差在哪，再逐个事件拆“设计要什么 / 实际拿到什么 / 落盘什么 / AI 侧发生什么”。

## 1. 两条流程

### 1.1 设计流程（来自设计文档与访谈决策）

```text
会话开始
   │
   ├─ work_open                    ← 显式事件：一条工作开始
   │     goal / type / difficulty
   │
   ├─ 工作中
   │     ├─ 块（采样点，不单独成事件）
   │     ├─ 提醒（三职责：反思 / 情绪 / 范围）
   │     └─ 枝节 → side_issue
   │
   ├─ work_close                   ← 显式事件：一条工作结束
   └─ work_summary                 ← 趁热写三句
```

### 1.2 实现流程

```text
session_start
   │
   ├─ 首轮注入：候选工作列表（最多 3 条）
   │     └─ AI 认领：probe_report(start) 或 crop switch
   │           → 落 probe_report + task_link + work_claim
   │
   ├─ 每轮
   │     ├─ prompt ──► (user_wait / idle_gap) ──► tool × N ──► stop
   │     ├─ 每轮首步注入状态行（已认领 → 工作名；未认领 → 候选）
   │     └─ 黄线 10 分钟 / 红线单轮 30 分钟 → reminder
   │           └─ AI 写 cause + verdict → probe_report
   │
   ├─ 收尾 probe_report(end) → work_summary
   └─ work_claim / suspend / resume / archive
```

### 1.3 差在哪（流程级）

| # | 设计 | 实现 | 性质 |
|---|---|---|---|
| F1 | `work_open` / `work_close` 是**独立事件** | 用 `probe_report` 的 phase 承担 | 事件粒度不同：一个工具干了整个生命周期 |
| F2 | 设计里没有“认领” | 实现新增 `work_claim` + `last_sid` + 有效期 | 实现比设计多一层，因为多会话需要它 |
| F3 | 块是采样点，不单独成事件 | `stop` + `blocks/*.jsonl` 是一等公民 | 实现更细，但块不带 work_id |
| F4 | 提醒三职责 | 两档（块级/工作级），情绪句只在块级 | 部分落地 |
| F5 | 归因三层 | 事实 + 自述，人校正无入口 | 少一层 |

**核心结论**：设计的“工作”是流程上的节点，实现的“工作”是 `probe_report` 的一个 phase；
这是流程级出入，不是字段级。

---

## 2. 逐事件（时间顺序）

### E1 · `session_start`

- **设计**：会话开始，记录宿主、工作区
- **采集方式**：hook —— 宿主会话创建 → 插件 `agent/pre-step` 首次触发
- **真实可拿到**：`sid`、`workspace`、`host`
- **真实落盘**：

```json
{"ev":"session_start","sid":"session-fef5…","workspace":"/home/dev/example-project","host":"dsh"}
```

- **AI 侧**：不参与
- **差异**：无

### E2 · 首轮注入（不是事件，但决定后面一切）

- **设计**：不存在这一步
- **实际注入**（原样）：

```text
[探针] 开工前先绑定工作（一句话说给用户，不用解释规则）：
- 续接下面某条：crop switch <work_id>
- 新开一条：probe_report(phase="start", task_id="<kebab-slug>", summary="<一句话>")
没绑定之前不要开始干活。
[探针] 未完成任务（可续接）：
- a — 做A（最后活跃 …；下一步：x）
```

- **AI 侧**：模型在第一个 step 看到这段，它要回显目标并选一条工作
- **差异**：设计里没有“开工前绑定”这一步；**尾部还重复了一遍同样的指令**（候选列表后又说一次“续接请沿用…”），是文案冗余

### E3 · `prompt`（用户发消息）

- **设计**：块起点
- **采集方式**：hook —— `user/message` → 插件 → Python
- **真实可拿到**：`sid`、`workspace`、消息文本（截 160 字）
- **真实落盘**：

```json
{"ev":"prompt","sid":"session-fef5…","workspace":"/home/dev/example-project","snippet":"只回复 OK","host":"dsh"}
```

- **AI 侧**：不参与；但它是后续所有注入的锚点
- **差异**：**不带 `work_id`** —— 这个块属于哪个工作，此刻没有记录

### E4 · `user_wait` / `idle_gap`（在 prompt 之前写入）

- **设计**：区分“等你回话”和“你离开”
- **采集方式**：推算 —— 上次 `stop` 到这次 `prompt` 的间隔，对照工作时段与 15 分钟阈值
- **真实可拿到**：间隔毫秒、是否在工作时段
- **真实落盘**：

```json
{"ev":"user_wait","sid":"…","waiting_ms":176745,"within_work_hours":true}
{"ev":"idle_gap","sid":"…","gap_ms":76931,"reason":"off-hours"}
```

- **AI 侧**：不参与
- **差异**：设计说“阈值近似”，实现没标注；面板却把百分比当事实展示

### E5 · `tool`（工具返回）

- **设计**：工具分布、失败率
- **采集方式**：hook —— `tool/call` 拿名字，`tool/result` 回填
- **真实可拿到**：工具名、是否失败
- **真实落盘**：

```json
{"ev":"tool","sid":"…","name":"Bash","is_error":false,"host":"dsh"}
```

- **AI 侧**：不参与
- **差异**：不带 `work_id`；工具参数（文件路径）没采，所以“同一文件改三次”这类返工判不出来

### E6 · `rework_signal`（同一工具连续 5 次）

- **设计**：返工识别
- **采集方式**：规则 —— `record_tool` 时回看最近 5 条
- **真实落盘**：

```json
{"ev":"rework_signal","sid":"…","work_id":"","kind":"repeat-tool","tool":"bash","count":5,"failures":0}
```

- **AI 侧**：不参与
- **差异**：`work_id` 是空的（还没绑定）；只认工具维度，设计返工（改两版方案）完全看不见

### E7 · `reminder`（两种形状）

- **设计**：三职责 —— 反思 / 情绪价值 / 守范围
- **采集方式**：规则 —— 块级 30 分钟，或工作级 10 分钟（黄线）
- **真实注入（块级）**：

```text
[探针] 当前任务块已 32 分钟（红线 30）。
1. 对用户说一句简短的人话，例如：不好意思，这个做得有点久…
2. 调用 probe_report：phase="blocked" 或 "progress"，… cause="为什么慢"，category="…"
```

- **真实注入（工作级）**：

```text
[crop] 这个工作已经 12 分钟（基线 30，0.40×）。
请判断这次偏慢是不是合理，并调用 probe_report 写一行：
- cause="为什么这么久"
- verdict="justified"（任务本身难）或 "not-justified"（自己走错路）
```

- **真实落盘**：块级 `{sid, block_start, minutes}`；工作级 `{sid, work_id, minutes, kind:"work"}`
- **AI 侧**：这是唯一会主动找 AI 要判断的通道。**期望**它写一句人话 + 归因；**实际**取决于模型配合度
- **差异**：情绪句只在块级；两种形状不统一

### E8 · `probe_report`（AI 主动）

- **设计**：`work_open` / `work_update` / `work_close` 三个事件
- **采集方式**：AI 调工具（不是 hook）
- **真实可拿到**：`phase`、`task_id`、`summary`、`cause`、`next`、`category`、`verdict`、`goal`、`type`、`difficulty`、`what`、`cost`、`next_time`、`minutes`、`turn`、`step`
- **真实落盘**：

```json
{"ev":"probe_report","sid":"…","workspace":"…","task_id":"dsh-upgrade-022",
 "phase":"start","summary":"把全局 dsh 升级…","goal":"","type":"","difficulty":"",
 "cause":"","next":"","category":"","verdict":"","minutes":0.0,"turn":1,"step":1}
```

- **AI 侧（重点）**：
  - **注入**：系统提示里的 `crop:protocol` 三条 + 每轮状态行 + 提醒
  - **怎么调**：模型自己决定 `phase`，在 `blocked` 时必填 `cause`+`category`
  - **可能写什么**：`cause` 是自由文本（“连续 3 次 bash 失败，卡在端口占用”）；`verdict` 是二值判断
  - **期望**：一行事实 + 一行归因，不写小作文
- **差异**：设计是三个事件，实现是一个工具 + phase；**字段随阶段变义**（`what` 只在 end 有意义）

### E9 · `task_link`

- **设计**：不存在（设计里块直接用 work_id）
- **采集方式**：core 在每次上报后写
- **真实落盘**：`{"ev":"task_link","task_id":"…","block_start":1794628001047,"sid":"…"}`
- **差异**：它是“上报↔块”的关系，被写成事件；同一块多次上报会产生多条 → 时长重复计数（已用去重兜住，但根因还在）

### E10 · `work_summary`

- **设计**：收尾三句
- **采集方式**：AI（`phase=end` 且带 `what/cost/next_time`）
- **真实落盘**：`{"ev":"work_summary","work_id":"…","verdict":"…","what":"…","cost":"…","next_time":"…"}`
- **差异**：无（这条设计和实现一致）

### E11 · `work_claim` / `work_suspend` / `work_resume` / `work_archive`

- **设计**：只有 suspend/resume/archive（状态机）
- **采集方式**：`crop switch` / `crop suspend` / `crop archive`
- **真实落盘**：

```json
{"ev":"work_claim","work_id":"crop-panel-functional-design","sid":"session-df2c…"}
{"ev":"work_suspend","work_id":"crop-readme-attractive"}
```

- **差异**：`host` 是 `unknown`（CLI 路径没带 `KIT_HOST`）；`work_claim` 设计时没有，是实现补的

### E12 · `stop`

- **设计**：`turn_end`
- **采集方式**：hook —— `turn/end`
- **真实落盘**：`{"ev":"stop","sid":"…","block_start":…,"minutes":0.0}` + `blocks/<date>.jsonl`
- **差异**：块记录没有 `work_id`

### E13 · `session_end`

- **设计**：会话收尾
- **采集方式**：hook
- **真实落盘**：`{"ev":"session_end","sid":"…"}`
- **差异**：写了没人消费（死事件）

---

## 3. 会话与工作怎么绑定（你问的三个问题）

### Q1 工作流和会话 ID 怎么绑？

**现状**：靠两条记录

1. `tasks[work_id].last_sid = sid` —— 工作上的“当前认领会话”
2. `tasks[work_id].active_until = 上报时间 + 30 分钟` —— 认领有效期

AI 通过 `probe_report(task_id=…)` 隐式绑定（上报即认领），或显式 `crop switch <work_id>`。

### Q2 绑定后，后续事件怎么体现？

| 事件 | 有没有带绑定关系 |
|---|---|
| `probe_report` | ✅ 带 `task_id` |
| `task_link` | ✅ 带 `task_id` + `block_start` |
| `work_claim` | ✅ 带 `work_id` + `sid` |
| `prompt` / `tool` / `stop` | ❌ 只有 `sid` |
| `reminder`（工作级） | ✅ 带 `work_id` |
| `work_summary` / `work_archive` | ✅ 带 `work_id` |

**问题**：块级事件全都不带 `work_id`，所以“这个块算哪个工作”只能靠 `task_link` 反推，反推又依赖时间戳匹配（±5 秒容差）。

### Q3 多会话干同一个工作，怎么绑、怎么映射？

**现状：不支持。**

- `last_sid` 是单值，第二个会话一上报就覆盖第一个
- `active_until` 只解决“会话重启后继承”，不解决“两个会话同时在跑同一工作”
- 事件里 `sid` 是单值，没有“这个工作当前活跃在哪些会话”

**设计缺口**（这是设计问题，不是 bug）：

| 需要的东西 | 现在 | 建议 |
|---|---|---|
| 工作 ↔ 会话 多对多 | 一对多（last_sid 单值） | 加 `work_sessions: {work_id: [sid…]}`，带最近活跃时间 |
| 每会话当前工作指针 | 靠 last_sid 反查 | 显式 `session_work: {sid: work_id}` |
| 块归属 | 反推 | `prompt/tool/stop` 直接带 `work_id` |
| 并发写冲突 | 无 | 事件追加即可；工作表的写用 flock（已有） |

---

## 4. 汇总：流程级 5 处出入

| # | 出入 | 后果 | 建议 |
|---|---|---|---|
| F1 | 工作生命周期用 probe_report 承担 | 字段随阶段变义，统计要条件筛 | 拆事件，或至少加 `event: work_start/close` 标记 |
| F2 | 块不带 work_id | 归属靠时间戳反推，多会话更脆 | 块记录直接带 work_id |
| F3 | task_link 是关系却写成事件 | 重复计数（已兜底） | 并入 report 字段 |
| F4 | 提醒两形状 | 消费方要分支 | 统一 `{kind:"block"\|"work"}` |
| F5 | 多会话没有映射表 | 并发时归属错乱 | 加 `work_sessions` + `session_work` |

---

## 5. 待你拍板

1. 工作生命周期要不要拆成独立事件（F1）
2. 块要不要直接绑 `work_id`（F2）
3. 多会话映射表现在做还是先记债（F5）
