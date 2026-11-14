# Agent Crop

> 给你的 AI 编程助手一条短马鞭：跑超时了轻点一下，跑完之后告诉你时间去哪儿了。

后端做性能优化靠 Trace——一次请求打一条 trace，每个中间件、DB、RPC 是一个 span，
于是你能回答「这 800ms 里 500ms 花在 DB」。

AI 干活这件事没有这层基础设施。你知道它跑了 40 分钟，但不知道：
多少是模型在推理、多少在等工具、多少在**等你回话**、多少在原地打转。
没有归因，性能优化只能靠感觉。

Agent Crop 补的就是这一层：**给一次工作打一条 crop trace**。

![Crop 面板](docs/assets/panel.png)

## 它做三件事

| 动作 | 说明 |
|---|---|
| **计时** | 你提问 → 回合结束算一个任务块；超红线（默认 30 分钟）注入一行提醒 |
| **自报** | AI 被提醒时调用 `probe_report`，记录「现在做什么 / 为什么慢 / 下一步」 |
| **留痕** | 任务块、工具调用、失败、报告都落本地事件库；没做完的工作下次开工自动提示续接 |

和传统 Trace 的差别在于：**把人的时间也算进去了**。等你回话的 20 分钟不是 IO 阻塞，
它是真实的瓶颈，所以单独成一类；你不在场的时间不算，不会把吃饭时间算成 AI 的慢。

## 5 分钟装上

```bash
git clone https://github.com/oriliz/agent-crop.git ~/agent-crop
dsh plugin --profile <你的 profile> add ~/agent-crop
# 重启 dsh，新会话生效
```

装完你不需要改任何配置、不需要动 `AGENTS.md`——探针协议通过插件注册进系统提示，
卸载时自动移除。

其他宿主（Claude Code / Qoder / Codex）见 [docs/INSTALL.md](docs/INSTALL.md)。

## 你会看到

**会话内右栏 tab（实时数字）**：当前工作在干什么、本回合多久、等你多久、今日事件、
进行中的工作、近 7 天最花时间的三个工作。纯数字，不做分析。

**`crop status`**——一屏看当前状态：

```bash
crop status
# Crop 状态 — dsh  2026-11-14 19:30
# 当前任务块  12 分钟 / 红线 30
# 今日事件  153 条：prompt 12、tool 120、stop 9、probe_report 6
# 进行中的工作：
#   - open-source-crop — 把探针整理成可公开的 v0.5（下一步：写 README）
```

**`crop report`**——周期复盘的事实层，交给 Agent 改写成一篇人话周报：

```bash
crop report --days 7        # 或 --json 给自动化任务用
crop serve                  # 本地 dashboard（127.0.0.1:8765）
```

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

## 它不做什么

- 不打分、不排行、不评判人——这是镜子，不是裁判
- 不上传数据、不存对话正文（只存 ≤160 字符的片段和一句话摘要）
- 不自动改代码，只观察和提醒
- 不替代传统 APM：工具内部性能还是用 Trace / Profiler 去查

## 文档

- [docs/DESIGN.md](docs/DESIGN.md) — 设计原则与边界
- [docs/EVENTS.md](docs/EVENTS.md) — 采哪些事件、怎么存、谁在用
- [docs/INSTALL.md](docs/INSTALL.md) — 四个宿主的安装与卸载
- [docs/LONGEVITY.md](docs/LONGEVITY.md) — 长任务里怎么不被上下文冲刷掉

## License

MIT
