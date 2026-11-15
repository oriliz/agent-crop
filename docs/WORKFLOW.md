# 工作模式

这份文档收的是「怎么干活」，不是「干什么」：专门记 review 里反复出现的摩擦，以及对应的改法。
它跟 [CONTRIBUTING](../CONTRIBUTING.md) 的关系是——CONTRIBUTING 管边界与流程，这里管节奏与协作。

## 1. 叙事先行，图后行

画图、做界面之前，先把「谁在什么时候做什么」写成 8–10 行文字，给人过一遍：

```
会话开始 → 注入：创建 / 绑定工作项
每步 → 记工具与失败
一轮结束 → 记停止时点
你发下一句 → 记回复时点 → 感知你的反馈
AI 提问 → 前时点 / 后时点（中间是等人）
超时 → 超时提醒 + 鞭策 → AI 反思与上报
收工 → 人类确认 → 总结与复盘
```

文字改一版 1 分钟；SVG 改一版 10 分钟，还要重新渲染核对。
本仓库的 README 时间线改了十几版，前六版都在用坐标猜语义——那段成本本来可以省掉。

## 2. 有分歧就给变体

「上下要不要对齐」「菱形还是竖线」「超时要不要实心」这类是口味，不是对错。
一次给 2–3 个变体让人挑；不要改一版、问一版、再改一版。

## 3. 推前跑 `scripts/check.sh`

```bash
bash scripts/check.sh
```

Python unittest + Node `node --test` + probe smoke，一条命令全跑；CI 只是第二道闸。
本仓库的 CI 曾两次挂在 node 任务上（缺 `npm ci`、测试桩没有 `ctx.inject`），本地一条命令都能提前看到。

## 4. 一个会话一个交付物

玻璃卡片、面板、dashboard 架构、README 塞在同一个会话里，估时就没法准，复盘也混在一起。
拆成多个会话，各自算账。

## 5. 并行时先开 worktree

仓库可能同时有别的会话在改：

```bash
git worktree add ../agent-crop-<task> -b <task>
```

不要在共享工作树上切分支（踩过：分支被切走，提交落到别人的分支上）。

## 6. PR 按主题拆

文档 PR 只带文档提交，行为改动单独 PR，评审时能只看一个主题。
（踩过：一个「改 README」的 PR 带上了本地 main 的五十多个提交。）

## 7. 估时按「未知数」报，不是按分钟

报「预计 3 轮内出图，第 4 轮还没定就停下来对齐」，比报「12 分钟」有用。
超过预估 2 倍，或同一处返工 ≥2 次：**主动**写一行「流程观察 + 现在改什么」，不等用户问。

## 8. 开发机重启用脚本

macOS 没有 util-linux 的 `setsid`，别手搓 `nohup setsid`——会静默失败。
用 `scripts/dev-restart.sh`：python 双 fork 脱离父进程，等端口真的起来，再打印带 token 的 URL。

```bash
scripts/dev-restart.sh 3082 dev
```

## 这套约定怎么传到别的会话

文档不会自动进上下文，真正起作用的是 agent 每次会话开头会读到的载体：

| 载体 | 什么时候生效 | 放什么 |
|---|---|---|
| `agent-crop/AGENTS.md`（本仓库根） | 会话 cwd 在仓库内：**首次请求**就注入；cwd 在父工作区时，**第一次 read/write/edit 到仓库文件**后注入（subdir on-touch） | 硬规则 + 指向本文件 |
| `~/AgentProjects/AGENTS.md`（工作区根） | 该工作区里每个会话的首次请求 | 一行指针：「碰 agent-crop 前先读仓库 AGENTS.md / docs/WORKFLOW.md」 |
| `CONTRIBUTING.md` / PR 模板 | 人读、agent 按需读 | 边界与提交流程 |

所以：**改仓库根 AGENTS.md** 是这件事的主载体；工作区那行是让「别的会话在动手前就知道有这个约定」的引子。
DSH 的注入包是 `@deepseek-ai/dsh-agent-instructions`：用户全局链（`$DSH_HOME/AGENTS.md`）+ 从项目根到 cwd 的每一层 AGENTS.md，默认每链 64 KiB 预算。

## 自查（每次交付前）

- [ ] 叙事文字过过一遍了？还是直接开始画/改？
- [ ] 有口味分歧的地方，给了 2–3 个变体吗？
- [ ] `bash scripts/check.sh` 绿了吗？
- [ ] 这个 PR 只讲一件事吗？
- [ ] 这次超预估了吗？超了的话，我主动说了「哪里空转、现在怎么改」吗？
