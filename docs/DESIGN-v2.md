# 事件与数据模型设计 v2（2026-11-15）

> 来源：飞书文档上 17 条评论 + 确认过的类图。本文替换 v1 的事件口径。
> 原则：`work_id` 是贯穿全流程的主键；块显式归属工作；绑定是多对多映射。

## 1. 实体与关系

```text
Session (sid) ────< Binding >──── Work (work_id)
                    sid
                    work_id
                    claimed_at
                    expires_at
                    state: active | released

Work (work_id)
 ├─ goal / type / difficulty（AI 开工时给，结束时校正）
 ├─ state: open | suspended | archived
 ├─ created_at / last_active_at / closed_at
 └─ summary: {what, cost, next_time}（人确认完成后写）

Block (block_id, sid, work_id, start, end, minutes)
 ├─ work_id 直接落盘，不再靠 task_link 反推
 └─< ToolCall (name, is_error)
       └─< Report (phase, cause, category, verdict, rework)
```

- **会话与工作是多对多**：一个会话可以先后绑多个工作；一个工作可以被多个会话认领（跨会话完成）
- **目标状态是一会话一工作**：串了不禁止，但要在数据里能看出来，作为分析点提示用户拆
- **块显式带 work_id**：不再依赖时间戳匹配

## 2. 字段与写入方

| 实体 | 字段 | 谁写 | 说明 |
|---|---|---|---|
| Session | `sid` | 宿主 | 会话主键 |
| Binding | `sid` `work_id` | AI（认领工具） | 一条认领 = 一条 binding |
| Binding | `claimed_at` `expires_at` | 系统 | 默认 30 分钟有效期，活跃即续期 |
| Work | `goal` `type` `difficulty` | AI | 开工写，收尾校正 `difficulty_actual` |
| Work | `state` | AI / 人 | open / suspended / archived |
| Work | `summary` | AI | 人确认完成后写 |
| Block | `work_id` | 系统 | 开工时从 binding 取 |
| Report | `phase` `cause` `category` `verdict` | AI | 主动汇报 |
| Report | `rework` `rework_reason` | AI | 主观判断“是否在返工” |

## 3. 事件流（时间顺序）

### 3.1 会话开始

```text
session_start(sid, workspace)
   │
   └─ 首轮注入（条件化，见 §4.1）
```

- 此时**还没有绑定**：注入文本要说明“本会话尚未绑定工作”，要求 AI 调用认领工具
- AI 认领后落 `binding` 事件

### 3.2 每轮

```text
prompt(sid, work_id, snippet)      ← 带 work_id
  ├─ human_wait / idle_gap          ← 见 §3.4
  ├─ tool(work_id, name, ok) × N
  ├─ 每轮首步注入：当前工作一行（已绑定时）
  └─ stop(sid, work_id, minutes, 反思检查)
```

### 3.3 工作生命周期（独立事件，不再用 report 的 phase 承担）

```text
work_open(work_id, goal, type, difficulty)
crop_report(work_id, state, summary)
work_close(work_id, difficulty_actual)
work_archive(work_id, reason)
```

### 3.4 人机等待（human-in-the-loop）

两种“停住”要分开记：

| 场景 | 事件 | 判定 |
|---|---|---|
| AI 回合结束，等人回话 | `human_wait` | 距下次 prompt 的间隔 < 阈值 |
| AI 调 ask-human 类工具主动停下 | `human_wait(reason="ask-human")` | 工具返回后立即记 |
| 人离开（超过阈值/非工作时段） | `idle_gap` | 同现有规则 |

### 3.5 提醒（分阶段动作）

| 阶段 | 触发 | 注入要求 |
|---|---|---|
| 任务进行中 | 工作累计 ≥ 10 分钟（黄线） | ① 判断偏慢是否合理（`verdict`）② 对用户说一句人话（情绪价值） |
| 单轮过长 | 单轮 ≥ 30 分钟（红线） | 同上，且必须写 `cause` |
| 收尾前 | `stop` 且本轮超时 | 检查有没有反思/上报，没有就要求补 |

### 3.6 返工（AI 主观判断，不是工具维度）

- 触发：每 N 轮或收尾时，注入一句「这次有没有返工？判断标准：你是否理解错要求 / 交付是否被否」
- 字段：`rework: true|false` + `rework_reason`
- 工具连续失败属于**工具可用性**，单独统计，不算返工

### 3.7 收尾

```text
AI 认为完成 → 向用户确认 → 用户确认后
   └─ work_close + work_summary(what/cost/next_time)
```

未确认不得自动关闭（当前实现是 AI 自己 end 就算完成，v2 要加确认门）。

## 4. 注入规则

### 4.1 首轮（条件化）

| 绑定状态 | 注入 |
|---|---|
| 未绑定 | 「本会话（sid=…）尚未绑定工作。先判断：这次是续接已有工作，还是新开一条？续接用 `work_claim(work_id)`；新开用 `crop_report(phase="start", goal=…)`。」+ 候选列表（最多 3 条，含目标与最后活跃） |
| 已绑定 | 「当前工作：<goal>（work_id）；上次更新 …」——**不再念绑定规则** |

> 评论原话：首轮注入很关键；但已绑定时还念绑定指令会给 AI 错误指令。

### 4.2 每轮

- 已绑定：一行状态（工作名 + 已用时长 + 灯）
- 未绑定：一行「尚未绑定工作」+ 提示绑定
- 缺报检测：上轮 ≥2 分钟且无上报 → 提醒补记

### 4.3 提醒文案分阶段

见 §3.5；情绪价值句固定保留（README 的「即刻鞭策」）。

## 5. 会话与工作绑定

### 5.1 数据结构

```json
{
  "bindings": [
    {"sid": "session-…", "work_id": "panel-design", "claimed_at": 1794…, "expires_at": 1794…, "state": "active"}
  ]
}
```

### 5.2 规则

1. 一个会话同一时刻只有一条 `active` binding（切工作 = 旧的置 released，新的 active）
2. 一个工作可有多条 active（多会话并行），聚合时按 work 汇总
3. 有效期 30 分钟，有活动即续期；过期后会话重启时可“继承”唯一活跃工作，多条活跃则列候选
4. **串换检测**：同一会话在 1 小时内换了 ≥3 个工作 → 记录为分析点，提示用户“尽量一会话一工作”

## 6. 与 v1 的差异

| 项 | v1 | v2 |
|---|---|---|
| 工作生命周期 | `crop_report` 的 phase | 独立 `work_open/close` 事件 |
| 块归属 | 靠 `task_link` 反推 | `Block.work_id` 直接落盘 |
| 中间事件 | `task_link` | 删除，report 带 `block_id` |
| 绑定 | `last_sid` 单值 | `bindings` 多对多表 |
| 返工 | 工具连续失败 | AI 主观判断 |
| 完成 | AI 自己 end | 人确认后 close |
| 提醒 | 两种形状 | 统一分阶段文案 |

## 7. 实施顺序

1. 数据模型：`Block.work_id`、`bindings` 表、`work_open/close` 事件（影响最大，先做）
2. 注入规则：首轮条件化、每轮状态行
3. 返工自评 + 提醒分阶段文案
4. 完成确认门
5. 测试：事件流用例扩到覆盖绑定、多会话、返工

## 8. 待决

- 认领工具是复用 `crop_report` 还是新开 `work_claim`（现在两者都有，需要收敛成一个）
- 绑定有效期 30 分钟是否合适
- 串换检测的阈值（1 小时内 ≥3 次）
