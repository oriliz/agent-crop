<div align="center">

# Agent Crop

<p align="center">
  <img src="docs/assets/banner.jpg" alt="一只愤怒的手握着长马鞭指向跪坐的蓝发鲸鱼娘女仆，背景是船坞里造了一半的航空母舰；对话框写着「我只是要到对面去，你怎么造了个航母？」和「你让我造个船啊……」" width="100%">
</p>

> *「我的 Token 啊！！！」*

**Crop 给 AI 干活打一条 trace：推理、工具，还有你。**

<sub>一次工作一条 trace · 跨会话 · 跨天 · 本地 JSONL · 不上传</sub>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Runtime](https://img.shields.io/badge/Runtime-DeepSeek%20Harness%20·%20Claude%20Code%20·%20Qoder%20·%20Codex-blueviolet)](#安装)
[![Data](https://img.shields.io/badge/Data-Stays%20Local-green)](#它不做什么)

<br>

[先看一次拆解](#先看一次拆解) · [安装](#安装) · [它记了什么](#它记了什么) · [它不做什么](#它不做什么)

</div>

---

上面那个是段子。下面这个是账本。

你的 AI 编程助手跑了一个下午，产出两行代码。

它说「抱歉，这个拖得有点久」。

你不知道那两小时去哪了。

你不知道是它慢，还是你在想。

**Crop 就是来回答这个问题的。**

它做的事很简单：把你和它一起干活的过程，记成一条能查的 trace。

---

## 先看一次拆解

一次 50 分钟的设计访谈，拆开是这样：

| 项 | 值 |
|---|---|
| 任务块时长 | 50 分钟 |
| AI 干活 | 2.5 分钟 |
| 等你回话 | **40.4 分钟（82%）** |
| AI 道歉 | 2 次 |
| 应该道歉 | **0 次** |

**AI 没有摸鱼。它在等你。**

---

## 不舒服的地方

AI 提效讲了两年，工具越来越快，你的时间没有变多。

因为没人给「人的等待」记账——包括你自己。

那 40.4 分钟不是浪费，那就是工作：你在想、在判断、在决定要不要继续。
但它被记成了「AI 很慢」，然后 AI 为此道歉。

**我们在用错误的账本，评价正确的事。**

---

## 这不是一次意外

同一台机器上的 7 天，全部是真实记录，不是 Demo：

```bash
$ crop report --days 7

Crop 周报（近 7 天）

这 7 天里，一共 90 个回合、190 分钟，涉及 17 个工作。

最花时间的工作：
  - agent-interaction-mode-design（80 分钟）
  - diagnose-3080-page-load（15 分钟）
  - learn-harness-survey（8 分钟）
```

![DSH 右侧栏的 Crop 页签](docs/assets/panel.png)

越好的访谈越容易超时，因为时间花在人的思考上。
**原来的红线，在惩罚正确的行为。**

> 市面上的 AI 工具都在量 AI。Crop 连你一起量。

---

## 为什么需要一条新的 trace

传统后端做性能优化靠 Trace：一次请求一条，每个中间件、DB、RPC 是一个 span。
于是你能回答「这 800ms 里 500ms 花在 DB」。

AI 干活没有这层基础设施。你知道它跑了 40 分钟，但不知道：
多少在推理、多少在等工具、多少在等你、多少在原地打转。

| 维度 | 传统 Trace | Crop Trace |
|---|---|---|
| 单位 | 一次请求 | 一次工作（可跨会话、跨天） |
| 节点 | 中间件 / DB / RPC | 人 / agent / 模型 / 工具 / 等待 |
| 等待 | IO 阻塞 | **等人的时间单独成一类** |
| 归因 | 按 span 确定性归因 | 能算的算，算不出的让 AI 定性判断 |
| 对象 | 机器 | 人机协作过程 |

等你回话的 20 分钟不是 IO 阻塞，它是真实的瓶颈，所以单独成一类。
你不在场的时间不算——吃饭时间不会被算成 AI 的慢。

**Crop 不评价你。它只记账。**

---

## 它记了什么

| 记什么 | 谁来记 | 用来回答 |
|---|---|---|
| 任务块 | 宿主自动 | 这一轮花了多久 |
| 等待 | 宿主自动 | 有多少时间是你在想 |
| 工作 | AI 主动（系统提示兜底） | 这件事前前后后花了多少 |
| 返工 | 宿主自动（同工具 / 文件反复失败） | 是不是在原地打转 |
| 卡点 | AI 主动 | 为什么慢 |

一行事件长这样：

```json
{"ev":"probe_report","phase":"blocked","task_id":"crop-readme",
 "summary":"在等用户确认定位","cause":"decision-wait","next":"先出数据图",
 "category":"human-wait","minutes":12.3,"host":"dsh","t":1794638837426}
```

数据只落本机 `~/.agentcrop/data`（权限 0700），可用 `KIT_DATA_DIR` 覆盖。
不存对话正文，只存 ≤160 字符的片段和一句话摘要。

---

## 安装

把仓库地址丢给你的 agent，说「按 README 装一下」就行。手动也只要两行：

```bash
git clone https://github.com/oriliz/agent-crop.git ~/agent-crop
dsh plugin --profile <你的 profile> add ~/agent-crop
# 重启 dsh，新会话生效
```

装完你不需要改任何配置、不需要动 `AGENTS.md`——探针协议通过插件注册进系统提示，
卸载时自动移除。

<details>
<summary>其他宿主（Claude Code / Qoder / Codex）</summary>

```bash
# Claude Code
claude plugin marketplace add ~/agent-crop
claude plugin install ai-coding-efficiency-kit@ai-coding-kit -s user

# Qoder
qodercli plugin validate ~/agent-crop && qodercli plugin install ~/agent-crop

# Codex：只完成接线，采集未适配，不产生指标
codex plugin marketplace add ~/agent-crop && codex plugin add ai-coding-efficiency-kit@ai-coding-kit
```

完整说明见 [docs/INSTALL.md](docs/INSTALL.md)。

</details>

自检：

```bash
crop status                              # 一屏看当前状态
tail -1 ~/.agentcrop/data/events.jsonl   # 应出现 "host":"dsh"
crop serve                               # 本地 dashboard，127.0.0.1:8765
```

![Crop dashboard](docs/assets/dashboard.png)

---

## 接入成本

这套东西会让 AI 多输出一点，但代价压到了最低：

| 增量 | 量级 |
|---|---|
| 注入（每回合一行状态 / 红线提醒） | 约 100–300 字符 |
| 上报（AI 调一次 `probe_report`） | 约 50–100 token |
| 一个 20 回合的工作合计 | 约 2k token |

对比老办法——拿到数据之后**去会话里反查**：读会话文件再塞进上下文分析，
一条中等长度会话就是几万 token，长的几十万，根本塞不进去。

真正的差别不是省几百 token，而是：**分析不再需要把会话塞进模型**。
事件是结构化数据，聚合、对比、出周报都在宿主之外用程序算，模型只在需要解释时被叫一次。

---

## 它不做什么

- **不打分。** 没有效率分，没有排行榜，没有「你的 AI 使用水平击败了 83% 的用户」。
- **不上传。** 数据只在这台机器上，目录 0700。
- **不改你的代码。** 它只观察；所谓「方案」也只影响提示，不碰仓库。
- **不替代 APM。** 工具内部的性能仍然归 OpenTelemetry。
- **不给你老板看。** 没有云，没有团队看板。

这是镜子，不是裁判。

---

## 诚实边界

- **样本小。** 上面那组 50 分钟是 n=1，结论只对交互型任务成立，别当普适规律。
- **看板还在长。** `crop serve` 的趋势、按类型对比、按天明细已经有了，问题分类视图还没做。
- **Codex 只接线。** 不产生指标。
- **红线会误报。** 墙钟超时经常不是 AI 慢，下一步要把「等你」从块时长里扣掉。

**一个不告诉你局限在哪的探针，不值得装。**

---

## 为什么叫 Crop

Crop 是骑手的短鞭。

不是抽打，是轻点一下：马跑慢了，催一句，不换马。

顺便说一句，DSH 生态里已经有一只鲸鱼娘了（[dsh-pet](https://github.com/zhu1090093659/dsh-pet)，美术 powerdog996 / DreamSkin）。
一个负责哄你，一个负责记账，分工明确。与本项目无隶属关系，此处仅为致意。

---

## 文档

- [docs/DESIGN.md](docs/DESIGN.md) — 设计原则与边界
- [docs/EVENTS.md](docs/EVENTS.md) — 采哪些事件、怎么存、谁在用
- [docs/INSTALL.md](docs/INSTALL.md) — 四个宿主的安装与卸载
- [docs/LONGEVITY.md](docs/LONGEVITY.md) — 长任务里怎么不被上下文冲刷掉

## License

MIT — 随便用，随便改。

---

<div align="center">

**传统 Trace 记录机器做了什么。**<br>
**Crop 记录你和它一起做了什么。**

<br>

**你的时间，也该有日志。**

<br>

MIT License © [oriliz](https://github.com/oriliz)

</div>
