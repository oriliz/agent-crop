---
name: fast-loop
description: Use at the start of any non-trivial task (改代码、新功能、修 bug、跨仓变更、部署验证、调研出结论) to enforce the 30-minute red line — output a decision table + trap list + time budget BEFORE writing code, split any task estimated over 30 minutes into parallelizable subtasks, front-load release pipelines, and run a mandatory retro when the tempo hook flags an over-redline block; triggers on 任务拆分, 并行, 超时, 太慢, 提效, 30分钟, or whenever a multi-step implementation or release task begins.
version: 0.2.4
---

# fast-loop：10 分钟黄线，30 分钟红线

## Overview

慢的根因从来不是敲码慢，是三件事：**设计反复**（边写边拍板，写了又改）、**串行等待**（发包/部署排队时干等）、**老坑重踩**（已知陷阱没有前置排掉）。本 skill 用开工三件套 + 强制拆分并行 + 超时复盘消灭它们。

> 路径约定：下文以 Qoder 的 `~/.qoder` 为例。其他 IDE 用 `install.sh --ide <x>` 接线后，把前缀换成对应目录（claude→`~/.claude`、cursor→`~/.cursor`、codex→`~/.codex`）。

红线：**任何任务块超过 30 分钟 = 有大问题**。hook 自动计时（`~/.qoder/task-metrics/`），超线会在下一条消息注入告警；告警不是提示，是必须执行的复盘指令。

黄线：**单任务块 ≥10 分钟自动记账**（`harness_scan.py` 的 Stop hook），不告警、不打断，只进日报。日报把时长拆成「模型生成 / 工具执行 / 慢调用 / 工具族（build、test、deploy、search…）」——模型占比高是设计反复，工具占比高是串行等待或命令本身慢。每日 17:00 复盘（统计窗口＝昨日 17:00 → 今日 17:00，17 点后工作归次日）用 `harness_scan.py daily --date $(date +%F) --since <昨日>T17:00` 作为「时间去哪了」的主数据源。

观测面：看趋势用 `python3 ~/.qoder/task-metrics/bin/harness_dashboard.py`（单文件 HTML；默认 14 天，`--month YYYY-MM` 出月度视图=本月累计/日均/超线，有上月数据时自动给环比；`--pull` 先拉对端，`--days N` 改窗口）。图表：每日模型/工具堆叠+7日均线、超线任务数、过程指标、工具族、改进前后对比（提效计划节点竖虚线 + 启动前/后对比表，原始日历明细折叠）、数据健康。要一屏文字面板用 `python3 ~/.qoder/task-metrics/bin/harness_scan.py status`，它一屏看完——钩子是否注册且脚本存在、近 24h 各模式运行次数与最近时间、今日黄线任务与工具族、规则状态、最近两条路线调整、近 7 日趋势（任务数 / 黄红数 / 模型 / 工具 / 均思考 / 均重开 / 均计划 / 阻塞）。`--oneline` 给状态栏用。判读：趋势里均思考与均计划下降 = 设计反复在收敛；工具时长占比高 = 串行等待待治。时长口径是 active（墙上时间减去 >15′ 的空档），跨天恢复的会话不会污染统计。每日 17:00 复盘任务会自动生成月度 + 近 14 天两份看板并附路径；看板按自然日聚合，与 17:00 窗口口径不同，引用时要说明。

多机：`harness_scan.py pull` 拉对端指标进 `data/peers/<对端>/`，daily/status 自动合并并按 host 标注（`--deploy` 顺带推脚本、`--rescan` 在对端先补扫）。对端没装 hook 时只显示 tempo 粗粒度行（仅时长）。历史补扫由各机在 Qoder 进程内每日自动跑一次；macOS 的 sshd 读不了 Qoder 转录，人工补扫必须在对端的 Qoder 会话里执行。

## 开工三件套（写第一行代码前必须输出）

1. **决策表**：本次所有待拍板项一张表——问题 / 选项 / 我的推荐 / 默认值。用户一次勾完。禁止逐个问答式推进设计。**拍板结果落盘** `~/.qoder/task-metrics/data/decisions/<date>.md`（格式 `|时间|决策|理由|状态|`）——此后已拍板项不重辩，翻案须写回文件；session-start hook 会自动重注入当日基线。
2. **陷阱清单 + 依赖预检**：从记忆和 skill 检索本任务已知坑，逐条写「本次怎么避」。跨仓/发布类变更至少核对三条：发布顺序（上游制品先发）、契约/声明面只读、测试工具自身是否依赖被改的东西。**部署/发布/回归类任务开工先跑依赖预检三问**：①产物可达（目标 registry/仓库里有没有）②目标环境存活 ③上游依赖与审批就绪——答案一律**活查询**（CLI/接口现查），不从记忆或存档取。5 分钟预检换 40 分钟排障。各项目的具体检查清单与命令模板属项目实践层，放项目记忆/项目 skill 维护，不写进本方法论。
3. **时间预算 + 拆分**：预估总时长。**预估 >30 分钟必须拆**，拆到每个子任务 ≤30 分钟且可独立验证，标注哪些可并行。

## 拆分与并行

- 拆分粒度 = **可独立验证**（编译过 / 单测绿 / 端点可调），不是按文件切。
- 并行手段：长命令（构建 / 发包 / 部署 / 全量测试）一律后台跑，等待时间做别的；独立调研、独立仓改动派并行子代理。
- 串行只留给真依赖。发布链第一步：**先把流水线跑起来**，排队时间拿来写代码。

## 部署等待：禁止裸 sleep

- 部署/发布一律用 `python3 <IDE_HOME>/task-metrics/bin/deploy_watch.py --env <env-id> --apre <apre-id> [--trigger]`，`run_in_background` 起，不占当前轮。
- 三路判据：部署单状态、RUNNING 实例数、环境 HTTP 活性；状态变化才打一行，终态退出：0 成功、2 判死、3 超时、4 观测失败。
- 判死快返：部署 FAILED / 连续 3 次采样无 RUNNING 实例 / SUCCESS 后 HTTP≥500 持续 180s（疑似启动崩溃循环）。状态落 `~/.qoder/task-metrics/data/deploy/<apre>.json`，日志同名 `.log`。
- 起完命令继续做不依赖部署结果的事；没别的事就结束本轮，靠后台完成通知回来。禁止 `sleep 90/120/240` 盲等。

## 进度播报（消灭「怎么样了」）

- 预估 >10 分钟的任务：每 ~10 分钟或每完成一个里程碑，输出一行「已完成 X / 正在 Y / 还差 Z」。沉默 = 用户默认你卡住，活干了也等于没干。
- 钩子兜底：PostToolUse 会按「距上一条人类消息」计时，≥10′ 自动注入一条提醒（同会话 6′ 冷却，事件记 `reminder`）。收到提醒当轮必须先补播报，再继续干活。（自建钩子注意：PostToolUse 裸 stdout 不进上下文，必须输出 `hookSpecificOutput.additionalContext`。）
- 停机强制：≥30′ 的块在 Stop 时会被钩子拒绝收工（decision=block，每轮只拦一次），必须当场给出三行复盘才能结束；≥10′ 在轮内与停机各推一条 macOS 桌面通知。用户不说话也能看到超线结果。
- 带 30 分钟限时的任务：**T-5 必须主动给**「能交付什么 / 不能交付什么 + 已完成清单」，不等用户来催。
- 长命令后台跑时不算沉默，但要在启动时说一句「后台跑 X，预计 Y 分钟，期间做 Z」。

## 超时即复盘（hook 告警后必做，三行不许跳过）

1. **超时点**：时间花在哪个环节（设计反复 / 等流水线 / 排障 / 返工）。
2. **根因**：属于三类根因里的哪一类，具体错在哪一步。
3. **下次同类任务怎么拆分并行**。
复盘结论若可固化（新坑、新拆法、新红线），当轮写进对应 skill 或记忆，不留口头。

**复盘 ≠ 收工**：告警到达时正因为工作没结束，给出三行复盘后必须当轮继续做下一件具体的事，不允许以「已交付 / 无阻断项」为由停下——那是拿未完成换指标好看。判断能否交还只有一个标准：可自主动作全部做完、剩余项确需用户拍板；即使交还也要附上你准备好的方案或默认值。

反面案例（2026-10-29 实测）：818′ 超时告警后只输出了复盘和交付清单就停，用户直接指出「看起来是让效率数据变好看了，但实际上事情没做完」。

## 限时诚实协议

剩余时间不够时，直接报「到 deadline 能交付什么 / 不能交付什么」并给选项。不硬赶、不假装来得及、不拿半成品冒充完成。

**压力时刻条款**：「快」「只剩 X 分钟」「快快快」这类信号触发的是**取舍明示**——报「全做要超时，建议砍 A 保 B」让用户拍板，而不是加速硬冲。禁止为赶时间触碰任何已声明红线（如契约不打快照进制品、缺数据 fail-closed 不造值；项目红线以项目层声明为准）：捷径省 5 分钟，被拦返工赔 1 小时。

## 收尾报时

任务完成时附一行：本任务约 X 分钟（预算 Y），超了说原因。X 从 `python3 ~/.qoder/task-metrics/bin/task_retro.py --last 3` 读，不凭感觉报。

## 取证工具

```sh
python3 ~/.qoder/task-metrics/bin/task_retro.py                # 最近 10 个任务耗时
python3 ~/.qoder/task-metrics/bin/task_retro.py --last 20      # 更多
python3 ~/.qoder/task-metrics/bin/task_retro.py --session <sid>  # 单会话
```
