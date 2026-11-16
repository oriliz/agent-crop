# Agent Crop · 给 agent 的工作指引

这是**仓库级**约定，只覆盖「在这个仓库里干活」这一种场景。
怎么画图、怎么估时、怎么复盘这类方法，不在这个文件里——见 [docs/WORKFLOW.md](docs/WORKFLOW.md)。

## 硬门禁

- 推之前跑 `bash scripts/check.sh`：Python unittest + Node `node --test` + probe smoke。CI 只是第二道闸。
- `main` 受保护：走 PR，一个 PR 一件事；文档 PR 只带文档提交。
- 仓库可能同时有别的会话在改：先 `git worktree add ../agent-crop-<task> -b <task>`，不要在共享工作树上切分支。

## 记录约定（Crop 台账）

- 同一功能的一次迭代只用一个 `task_id`；截图、验证、合并算 `progress`，不单开工作。
- `expect_min` 只在范围真的变了时改，并写进 `expect_why`；不要为了催自己把预期改小。
- `end` 之后交付、汇报、截图不用重新绑定；只是收尾追问就记 `category="side-issue"`。

## 事实来源

- 数据口径唯一真源：`core/probe_core.py`；面板与查询只读它。
- DSH 集成：`dsh/lib/index.js`（宿主半侧）+ `dsh/lib/client.js`（浏览器半侧）。
- 设计：`docs/DESIGN*.md`；事件口径：`docs/EVENTS.md`；方法：`docs/WORKFLOW.md`。
