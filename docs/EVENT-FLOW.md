# 事件流：盘点、风险、测试

> 实现口径（不是设想）。每条都对应 `core/probe_core.py` 或 `dsh/lib/index.js` 里的真实写入点。

## 1. 事件清单

| 事件 | 谁写 | 触发 | 关键字段 | 是否带 sid |
|---|---|---|---|---|
| `session_start` | core | 宿主首个 pre-step | sid, workspace | ✅ |
| `prompt` | core | 用户发消息 | sid, workspace, snippet | ✅ |
| `user_wait` / `idle_gap` | core | 下次 prompt 时反推间隔 | sid, waiting_ms / gap_ms | ✅ |
| `tool` | core | 工具返回 | sid, name, is_error | ✅ |
| `rework_signal` | core | 同一工具连续 5 次 | sid, kind, tool, count | ✅ |
| `stop` | core | 回合结束 | sid, block_start, minutes | ✅ |
| `probe_report` | core | AI 调工具 | 全字段 + phase/task_id/cause/verdict | ✅ |
| `task_link` | core | 每次上报 | task_id, block_start | ✅（今晚补） |
| `work_summary` | core | phase=end 且带 what/cost/next_time | work_id, verdict, 三句 | ✅（今晚补） |
| `side_issue` | core | category=side-issue | note, suggest | ✅（今晚补） |
| `work_claim` | core | `crop switch` | work_id, sid | ✅ |
| `work_suspend` / `work_resume` | core | `crop suspend/resume` | work_id | ✅（今晚补） |
| `work_archive` | core | `crop archive` / `gc` | work_id, reason | ✅（今晚补） |
| `reminder` | core | 黄线 / 红线 | 两种形状：block_start 或 work_id | ✅ |
| `session_end` | core | 会话结束 | sid | ✅ |

## 2. 一次工作的时序

```text
session_start
prompt ──► (可能先写 user_wait / idle_gap)
  │
  ├─ tool × N ──► (同工具连续 5 次 → rework_signal)
  ├─ probe_report(start) ──► task_link + 任务 upsert(goal/type/difficulty)
  ├─ probe_report(progress, side-issue) ──► side_issue（不动任务状态）
  ├─ reminder（黄线 10 分钟 / 红线 30 分钟单轮）
  ├─ stop ──► blocks/<date>.jsonl
  └─ probe_report(end, what/cost/next_time) ──► work_summary + 关任务
work_claim / suspend / resume / archive ──► 工作状态变化
```

## 3. 今晚盘出来的问题（已修）

| # | 问题 | 后果 | 修法 |
|---|---|---|---|
| 1 | 同一块被多次上报，`task_link` 重复 | 工作时长翻倍（25.2 实为 12.6） | 按 block_start 去重（±5 秒） |
| 2 | `task_link` / `work_summary` / `side_issue` / 状态事件不带 `sid` | 按会话查不到这些事件 | 写入时带上 sid |
| 3 | 块起点有两个时间源（JS `Date.now()` / Python 毫秒） | 工作与块关联失败，时长算成 0 | ±5 秒容差匹配 |
| 4 | 红线判的是单轮时长 | 一轮很少超 30 分钟 → 提醒几乎不发 | 增加工作级黄线（10 分钟） |
| 5 | 没有缺报检测 | AI 忘登记没人管 | 一轮 ≥2 分钟无上报 → 下轮点名 |

## 4. 还没修的风险

| # | 风险 | 影响 |
|---|---|---|
| R1 | `runtime/current.json` 单文件 | 多会话并发时归属错乱（别人在 `crop-runtime-fix` 修） |
| R2 | 事件无 schema 版本字段 | 改字段后历史数据要猜 |
| R3 | `session_end` 写了没人消费 | 无实际影响，但属于死事件 |
| R4 | 文档事件名与实现不一致 | 已在本文件用实现口径覆盖，`EVENTS.md` 待同步 |

## 5. 测试覆盖

| 文件 | 用例数 | 覆盖 |
|---|---|---|
| `tests/test_event_flow.py` | 10 | 回合时序、等待/离开、上报三种形状、重复块、黄线阈值、返工信号、认领/挂起/归档 |
| `tests/test_crop_cli.py` | 11 | 状态视图、周报聚合、工作时长、基线、状态机、缺报 |
| `dsh/test/index.test.mjs` | 8 | 工具 schema、execute、pre-step 注入、系统提示段、状态行、工具名映射、缺报提醒 |

跑法：

```bash
python3 -m unittest tests.test_event_flow tests.test_crop_cli
node --test dsh/test/index.test.mjs
```
