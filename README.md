<div align="center">

# Agent Crop

<p align="center">
  <img src="docs/assets/banner.jpg" alt="一只愤怒的手握着长马鞭指向跪坐的蓝发鲸鱼娘女仆，背景是船坞里造了一半的航空母舰；对话框写着「我只是要到对面去，你怎么造了个航母？」和「你让我造个船啊……」" width="100%">
</p>

> *「我的 Token 啊！！！」*

**Agent Crop 是给 AI 的一根马鞭：催它交活，逼它复盘，让它自己越用越顺。**

<sub>测量 → 归因 → 优化 → 验证 · 一次工作一条 trace · 本地 JSONL · 不上传</sub>

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Runtime](https://img.shields.io/badge/Runtime-DeepSeek%20Harness%20·%20Claude%20Code%20·%20Qoder%20·%20Codex-blueviolet)](#安装)
[![Data](https://img.shields.io/badge/Data-Stays%20Local-green)](#它不做什么)

<br>

[这套方法](#这套方法不是我们发明的) · [它治的几种浪费](#它治的几种浪费) · [安装](#安装) · [它不做什么](#它不做什么)

</div>

---

你的 AI 编程助手跑了一个下午，产出两行代码。

它说「抱歉，这个拖得有点久」。

你不知道那两小时去哪了。

你不知道是它慢，还是你在想。

**Agent Crop 就是来回答这个问题的。**

---

## 这套方法不是我们发明的

后端做性能优化靠 Trace：一次请求一条，找到最贵的那一段，改完再用同一个指标测一遍。
这套东西在大厂跑了二十年，撑过双十一，也撑过每一次线上事故复盘。

职场里也有同一套结构，叫**复盘**：做了什么、时间花在哪、下次怎么改。

Agent Crop 只是把对象换成了「人和 AI 一起干活」：

| 传统性能工程 | Agent Crop |
|---|---|
| 测量：这次请求花了多少 ms | 一次工作的时间去向 |
| 归因：哪个 span 最贵 | 卡在哪一类浪费 |
| 优化：改代码、加索引、加缓存 | 改交互模式、改 AI 的工作习惯 |
| 验证：同一指标再测一遍 | 同一口径再跑一遍 |

---

## 马鞭做什么

- **防跑偏、防摸鱼。** Agent 长时间没回话、没给出结论的时候，鞭策它想一想：是不是走错了路。
- **即刻反思。** 被鞭策之后，要求它在当下的环境里找一找，还有没有能挽回的时间和方法。
- **复盘，自驱优化。** 通过周报、月报做自我反思，自己沉淀方法论。

三件事凑齐才叫自驱，不然只是被催。

---

## 它治的几种浪费

AI 浪费时间，不止一种。产品里每种都有名字：

| 症状 | 分类 | 典型现场 |
|---|---|---|
| 没问清楚就开始干 | `spec-drift` / `decision-batching` | 你要一条过街的路，它造了半艘航母 |
| 反复查工具怎么用 | `tool-misuse` / `repeat-pitfall` | 同一个接口查了四十分钟，Skill 写得不好它也不说 |
| 做不到也不停手 | `no-evidence` | 缺背景知识，硬啃两个小时，最后交上来一个空的 |

还有 `serial-wait`（串行等待）、`design-churn`（设计反复）等六类，完整词表见 [docs/EVENTS.md](docs/EVENTS.md)。

**有了词表，才谈得上归因；有了归因，才谈得上改。**

不过最常被误判的，不是这三种。

---

## 我自己的一个样本

我们有个环境部署工具，子命令很多。AI 每次用都得先 `help` 一层层翻，再挨个试——对应的 Skill 写得不完整。

Agent Crop 把这件事记了下来：同一个命令反复翻 help、反复试错，归到 `tool-misuse` 这一类。

这条记录被推回给 AI，让它即刻反思——不是模型不会用，是这个 Skill 缺了最关键的一页。它据此补了一份常用 Skill。

同一个动作，下一次：**10 分钟，变成 30 秒**。

样本只有我一个，不是测评。重点是这条链子：**数据 → 即刻反思 → 自我修复 → 同一口径验证**。哪一步翻了 help、翻了几次，都有事件记录。

---

## 为什么需要一条新的 trace

AI 干活没有这层基础设施。你知道它跑了 40 分钟，但不知道：
多少在推理、多少在等工具、多少在等你、多少在原地打转。

传统 Trace 的 span 是中间件、DB、RPC；Agent Crop 的 span 是人、agent、模型、工具。

| 维度 | 传统 Trace | Crop Trace |
|---|---|---|
| 单位 | 一次请求 | 一次工作（可跨会话、跨天） |
| 节点 | 中间件 / DB / RPC | 人 / agent / 模型 / 工具 / 等待 |
| 等待 | IO 阻塞 | **等人的时间单独成一类** |
| 归因 | 按 span 确定性归因 | 能算的算，算不出的让 AI 定性判断 |
| 对象 | 机器 | 人机协作过程 |

等你回话的 20 分钟不是 IO 阻塞，它是真实的瓶颈，所以单独成一类。
你不在场的时间不算——吃饭时间不会被算成 AI 的慢。

**Agent Crop 不评价你。它只记账。**

---

## 四步：让 AI 自己把账算明白

| 步 | 做什么 | 现在 |
|---|---|---|
| **整理** | 把过程记成结构化事件 | ✅ |
| **反思** | 归因到九类之一，说清为什么慢 | ✅ |
| **总结** | 趁记忆热写三句：做了什么 / 时间花在哪 / 下次怎么做 | ✅ |
| **证明** | 改进前后用同一口径对比 | 🚧 在建 |

前三步是记录，第四步才是价值：**没有 before/after 的优化，就是玄学。**

前三步做扎实了，AI 的自我改进才有依据——不是它说自己变好了，是同一口径下数字变了。

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
{"ev":"probe_report","phase":"blocked","task_id":"refactor-auth",
 "summary":"在等接口契约确认","cause":"接口字段没对齐","next":"先确认契约",
 "category":"spec-drift","minutes":12.3,"host":"dsh","t":1794638837426}
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

`crop` 是仓库里的脚本，第一次用先放进 PATH：

```bash
ln -sf ~/agent-crop/bin/crop /usr/local/bin/crop
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

- **样本只有一个。** 五小时到两小时是我自己的体感，不是对照实验。
- **证明还没做。** 四步里的「证明」目前只有设计（`adjustment` 的 before / after / evidence），代码里没有。
- **看板还在长。** `crop serve` 的趋势、按类型对比、按天明细已经有了，问题分类视图还没做。
- **Codex 只接线。** 不产生指标。
- **红线会误报。** 墙钟超时经常不是 AI 慢，下一步要把「等你」从块时长里扣掉。

**一个不告诉你局限在哪的探针，不值得装。**

---

## 常见问题

**这和 LangSmith / Langfuse 有什么区别？**
它们量 agent 内部：token、工具调用、延迟。Agent Crop 量协作过程：包括你等人的时间，和 AI 的返工。前者回答「模型花了多少」，后者回答「这件事为什么这么久」。

**算监控员工吗？**
不算，也做不到。数据只在本机，没有云、没有团队看板、没有评分。它记的是时间去向，不是谁对谁错。

**为什么单位是「工作」而不是「任务」？**
一次工作可能跨几十轮对话、跨天、跨会话。任务块只是采样点，工作才是账本的单位。

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
**Agent Crop 记录你和它一起做了什么。**

<br>

**你的时间，也该有日志。**

<br>

MIT License © [oriliz](https://github.com/oriliz)

</div>
