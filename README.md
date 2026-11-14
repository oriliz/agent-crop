# Agent Crop

> 给 AI 编程助手一条短马鞭——它跑超时的时候轻点一下，跑完之后告诉你时间去哪儿了。

Agent Crop 是一套**本地探针**：记录每个任务块花了多久、卡在哪、哪类问题在反复，
并让 AI 在超时的时候自己说一句"我在干什么、为什么慢、下一步做什么"。

它**只观察，不打断**。会不会插话，由你逐条开启的「方案」决定——默认全部关闭。
数据只落本机，不上传。

> A local probe for coding agents: task-block timing, red-line nudges and
> structured self-reports. Everything stays on your machine.

**状态**：v0.4.x，开发中，接口未冻结。

---

## 1. 它到底做了什么

三件事，没有第四件：

| 动作 | 说明 |
|---|---|
| **计时** | 你提问 → 回合结束，算一个「任务块」；超过红线（默认 30 分钟）就注入一行提醒 |
| **自报** | AI 被提醒时调用 `probe_report`，记录 `现在做什么 / 为什么慢 / 下一步 / 问题类别` |
| **留痕** | 任务块、工具调用、失败、报告都落 `events.jsonl`；没做完的任务下次开工自动提示续接 |

```text
你提问 ──► 开始计时 ──► AI 干活（工具调用…） ──► 回合结束 ──► 归档任务块
              │                                        │
              └── 超过红线（默认 30 分钟）──► 注入一行提醒
                                       └── AI 调 probe_report 记录原因与下一步
```

提醒是注入给 **AI** 的，长这样：

```text
[探针] 当前任务块已 32 分钟（红线 30）。请做两件事：
1. 对用户说一句简短的人话，例如"这个做得有点久，我还在处理"。
2. 调用 probe_report：phase="blocked"，task_id="..."，summary="..."，
   cause="为什么慢"，next="下一步"，category="serial-wait|design-churn|..."
```

---

## 2. 装上（30 秒）

| 宿主 | 状态 | 装法 |
|---|---|---|
| **DeepSeek Harness** | ✅ 主力 | `dsh plugin --profile <profile> add <仓库路径>` |
| **Claude Code** | ✅ 可用 | `claude plugin marketplace add <仓库路径>` |
| Qoder | ⏳ 实验 | 见 §2.4 |
| Codex | ⏳ 实验 | 只接线，采集未适配，见 §2.4 |

先把仓库放到本地（路径随你，下面用 `~/agent-crop`）：

```bash
git clone https://github.com/oriliz/agent-crop.git ~/agent-crop
```

### 2.1 DeepSeek Harness

```bash
dsh plugin --profile <你的profile> add ~/agent-crop
# 重启 dsh（新会话生效）
```

插件会注册原生工具 `probe_report`，并通过 `agent/pre-step` 注入提醒。

### 2.2 Claude Code

```bash
claude plugin marketplace add ~/agent-crop
claude plugin install ai-coding-efficiency-kit@ai-coding-kit -s user
claude plugin list          # 确认 enabled
```

Claude 侧通过 `.mcp.json` 暴露 `probe_report`（工具名带 `mcp__plugin_..._` 前缀），
hooks 负责注入未完成任务和红线提醒。装完需要新开会话。

### 2.3 装完自检

1. 新会话里说一句「开工」，模型应该看到 `[探针] 开工前先认一下任务…`
2. 让模型调用一次 `probe_report`
3. 检查数据：

```bash
tail -1 ~/.ai-coding-efficiency/data/events.jsonl
# 应出现 "host":"dsh"（Claude 侧是 "host":"claude-code"）
```

### 2.4 Qoder / Codex（实验）

<details>
<summary>展开</summary>

```bash
# Qoder
qodercli plugin validate "$PWD" && qodercli plugin install "$PWD"

# Codex
codex plugin marketplace add "$PWD"
codex plugin add ai-coding-efficiency-kit@ai-coding-kit
codex plugin list          # 确认 installed, enabled
```

Codex 的会话转录格式与 Qoder 不同，**当前只完成接线，不产生指标**。

</details>

---

## 3. 数据在哪、长什么样

数据目录：`~/.ai-coding-efficiency/data`（权限 0700），可用 `KIT_DATA_DIR` 覆盖。

`events.jsonl` 一行一个事件：

```json
{"ev":"probe_report","sid":"...","task_id":"probe-001","phase":"start",
 "summary":"给探针加看板","cause":"","next":"先出静态页","category":"","minutes":12.3,
 "host":"dsh","t":1794638837426}
```

常见事件：`session_start` / `prompt` / `tool` / `stop` / `reminder` / `probe_report`。

`probe_report` 的字段：

| 字段 | 必填 | 说明 |
|---|---|---|
| `phase` | 是 | `start` / `progress` / `blocked` / `end` |
| `task_id` | 是 | kebab-case，跨会话复用 |
| `summary` | 是 | 现在在做什么，一句话 |
| `cause` | blocked 必填 | 为什么慢 / 卡住 |
| `next` | 否 | 下一步动作 |
| `category` | blocked 必填 | `serial-wait` / `repeat-pitfall` / `design-churn` / `decision-batching` / `tool-misuse` / `no-evidence` / `spec-drift` / `human-wait` / `other` |

---

## 4. 看数据

```bash
export KIT_DATA_DIR=~/.ai-coding-efficiency/data   # 指向共享目录

python3 hooks/task-metrics/bin/harness_scan.py status      # 一屏状态
python3 hooks/task-metrics/bin/harness_dashboard.py        # 14 天 HTML 看板
python3 hooks/task-metrics/bin/daily_case.py               # 当天最贵的任务，拆成证据
python3 hooks/task-metrics/bin/problem_class.py            # 哪类问题在反复
```

> ⚠️ **现状**：分析/看板层还是围绕 Qoder 的转录数据写的，DSH 与 Claude 采到的
> `events.jsonl` 尚未接进看板——现在跑看板可能是空的。这是 v0.5 的头号任务，
> 见 [docs/PLAN-open-source-2026-11.md](docs/PLAN-open-source-2026-11.md)。

---

## 5. 边界：探针 vs 方案

| | 探针 | 方案（solutions/） |
|---|---|---|
| 做什么 | 采集 + 出数 | 改变任务过程中的行为 |
| 对使用者的影响 | 只观察，不打断 | 会插话、会影响动作 |
| 默认 | 启用 | **不启用**，逐条开启 |
| 验证状态 | — | 全部未验证或试用中 |

---

## 6. 方案清单（默认全关）

| 方案 | 解决什么 | 状态 |
|---|---|---|
| `tempo-rules` | 长任务失控、压力下走捷径 | 试用中 |
| `inline-reminders` | 串行等待、长回合无人管 | 试用中 |
| `alignment-gate` | 照资料实现，与业务要求不一致 | 试用中 |
| `detour-signal` | 卡住后反复试同一路径 | 试用中 |
| `fast-loop` | 设计反复、串行等待 | 未验证 |
| `lsp-autoconfig` | 查引用靠逐文件翻 | 未验证 |

```bash
python3 solutions/cli.py list          # 方案、状态、证据、开关
python3 solutions/cli.py enable inline-reminders
python3 solutions/cli.py disable inline-reminders
```

---

## 7. 仓库结构

```text
core/           纯 Python 核心：数据模型、计时、校验、存储（零依赖）
dsh/            DeepSeek Harness 插件适配
solutions/      干预方案 + 开关 + 分发
hooks/          各宿主 hook 接线 + 分析脚本
skills/         可复用的 skill
docs/           设计、数据模型、开源计划
tests/          冒烟与红线检查
```

---

## 8. 开发：稳定版与开发版

日常使用吃 tag 导出的副本，开发在工作树上，互不影响：

| | stable | dev |
|---|---|---|
| 代码 | `~/.ai-coding-efficiency/install/vX.Y.Z/` | 仓库工作树 |
| 谁用 | 日常会话 | 独立 profile / 单元测试 |
| 数据 | `~/.ai-coding-efficiency/data` | `data-dev`（`KIT_DATA_DIR`） |

```bash
node --test dsh/test/*.test.mjs    # 3/3 通过
```

---

## 9. 隐私

- 只采：任务块时长、模型与工具名、调用次数、失败/重开、你自己写的 `summary`
- 不采：对话正文、代码内容、凭证
- 不上传；跨人比对靠手工导出 `data/metrics/*.jsonl`
- `summary` 可能含任务内容，所以目录保持 0700

## 10. 许可

暂未选定，计划 MIT。
