# 实机验证 · 0.8.0 之后的行为改动（2026-11-17）

验证对象：`v0.8.0..main` 的 4 个功能 PR（#28～#31）。
这些改动只过了单测和 CI，没有在真实实例跑过；本文是补的实机证据。

## 环境

- 独立 DSH_HOME：`~/.dsh-e2e`，profile `lab`（headless 单轮）
- 插件：lab profile 的 `agent-crop-dsh` 从 `agent-crop-e2e` 改链到
  `agent-crop-route-gate`（`origin/main` + PR #33），即待验证代码本身
- 真实 DeepSeek API；每个场景独立数据目录 `/tmp/crop-clean-<scenario>/data`
- 工作区：`/tmp/crop-e2e-ws`（空目录，避免工作区文档干扰）
- 种子数据：每个场景先写两条「其他会话」的工作项
  （`alpha-doc-rework` 重写接入文档、`beta-panel-spec` 定面板功能点）
- 运行器：`tests/behavior/run_clean.py`（校验首条用户消息、注入标记、无外来注入）
- 所有场景 `verdict: VALID`（三项校验都过），trace 在 `tests/behavior/traces/`

## 结果总览

| 场景 | 覆盖的改动 | 结果 |
|---|---|---|
| S1 首轮注入拆分 | #28（card 9da932ed） | 通过：注入按「本会话 / 其他会话」分开 |
| S2 start → end | #28 收尾流程 | 通过：两步都落盘，模型回复 OK |
| S2b end 后交付 | #28（card 9aff221f / 0f9927d4） | 通过：end 后调 bash 不再催绑定 |
| S3 切工作 | #28（card 3681c288） | 通过：返回里带「旧工作还没收尾」警告 |
| S4 台账 | #29 / #30 / #31 | 通过：派生值 + 「暂无完整周」文案 |

## S1 · 首轮注入拆分（#28）

**用户**：`只回复 OK，不要做别的。`

**注入原文**：

```text
[crop] 本会话还没有绑定工作；其他会话有 2 条在跑。候选（最多 3 条）：
- beta-panel-spec — 定面板功能点（0 分钟前）
- alpha-doc-rework — 重写接入文档（0 分钟前）
先选一条，再动手：接着做用 crop_update(phase="start", task_id="beta-panel-spec")；新开用 2-3 个词的短名。
```

**结果**：模型没有擅自开工，`tool_calls=0`，`final_text="OK"`。
对比：旧版把全局待办都当成本会话候选（card 9da932ed 的问题），这次没有再出现。

## S2 / S2b · 收尾后的催办噪音（#28）

**S2**：start → end 两步都执行，`tool_calls=2`，模型只回 OK。

**S2b**：start → end → `bash echo deliver`，然后把 bash 返回原文贴出来：

```text
deliver
```

**结果**：返回里**没有**「未绑定工作 / 上一轮没登记」一类的 nudge。
说明 end 后的冷静期在真实回合里生效（card 9aff221f / 0f9927d4 修的就是这个）。

## S3 · 切工作警告（#28）

**用户**：start A，再 start B，把第二步返回原文贴出来。

**工具返回原文**：

```text
recorded start e2e-switch-b (0.1 min in this block)
[crop] 已从 e2e-switch-a 切到 e2e-switch-b；旧工作还没收尾，记得顺手 end 或挂起，别让它静默失联。
```

**结果**：`work_switch` 事件与警告按设计出现在工具返回里（card 3681c288 生效）；
模型也顺着提示说明了旧工作的状态。

## S4 · 改进台账（#29 / #30 / #31）

**用户**：`crop_adjust(action=record)`，再 `crop_adjust(action=ledger)`，贴出状态。

**ledger 返回要点**：

```text
状态：[没有基线]
before → after：null → 暂无完整周（样本 0/0）
```

**结果**：
- 写入只收 `metric/evidence/note`，数值由系统派生（#30）——没有让模型报数字；
- 无历史事件时是「没有基线」，不是伪造一个数；
- `after` 显示「暂无完整周」（#31 的新口径），不是拿部分周当结论。

## 没覆盖到的

1. **#31 的「部分周不参与判定」**：这次没有构造出「基线存在 + 部分周」的数据，
   只验证了空基线的路径。需要补一条单测（造 4 周基线 + 3 天部分周），
   这条不依赖模型，单测比实机更合适。
2. **#33 的路由准入**：lab profile 是 headless，没有 `webServer`，实测不到；
   已有单测覆盖「403 且不读数据」。要实机得另起一个 DSH web 实例。
3. **面板**（`client.js` 的台账卡片 / 「暂无完整周」文案）：headless 没有浏览器，
   本轮没有截屏。需要一次 web 实例的人工过目。

## 环境还原

验证用的 lab profile 改动：

```bash
# 改回原来的 e2e 插件
cp ~/.dsh-e2e/profiles/lab/package.json.bak-20261117 ~/.dsh-e2e/profiles/lab/package.json
ln -sfn ../../../../AgentProjects/agent-crop-e2e ~/.dsh-e2e/profiles/lab/node_modules/agent-crop-dsh
```

数据目录 `/tmp/crop-clean-*`、会话 `~/.dsh-e2e/sessions/--private-tmp-crop-e2e-ws--` 都可随时删除。
