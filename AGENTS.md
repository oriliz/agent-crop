# Agent Crop · 给 agent 的工作指引

这个仓库是给 AI 编程助手用的本地探针（DSH 插件 + Python core）。
**开工前先读 [docs/WORKFLOW.md](docs/WORKFLOW.md)**，下面是硬规则，和它冲突时以 WORKFLOW 为准。

## 改东西之前

- 画图 / 做界面前，先用 8–10 行文字把「谁在什么时候做什么」写出来给人过一遍；改文字比改 SVG 便宜一个数量级。
- 有口味分歧（对齐、形状、颜色、密度）一次给 2–3 个变体让人挑，不要改一版问一版。

## 推之前

- 跑 `bash scripts/check.sh`：Python unittest + Node `node --test` + probe smoke。CI 只是第二道闸。
- `main` 是保护分支：走 PR，一个 PR 一件事；文档 PR 只带文档提交。

## 并行

- 仓库可能同时有别的会话在改：先 `git worktree add ../agent-crop-<task> -b <task>`，不要在共享工作树上切分支。

## 交付

- 一个会话一个交付物；估时按「未知数」报（例：「3 轮内出图，第 4 轮没定就停下来对齐」），别报死分钟数。
- 超预估 2 倍，或同一处返工 ≥2 次：**主动**写一行「哪里空转 + 现在改什么」，不等用户问。

## 事实来源

- 数据口径唯一真源：`core/probe_core.py`；面板与查询只读它。
- DSH 集成：`dsh/lib/index.js`（宿主半侧）+ `dsh/lib/client.js`（浏览器半侧）。
- 设计：`docs/DESIGN*.md`；事件口径：`docs/EVENTS.md`；工作方式：`docs/WORKFLOW.md`。
