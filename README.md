# AI Coding 提效套件

本地探针 + 待验证的提效方案。探针只做一件事：把「AI 写代码的时间去哪了」变成可观测的数据；
方案是对问题的干预，**全部处于未验证状态，不该默认强加给使用者**。

不绑定业务、仓库、机器；数据只落本机，不上传。

## 一、两部分，边界要清楚

| | 探针 | 方案 |
|---|---|---|
| 做什么 | 采集 + 出数 | 改变任务过程中的行为 |
| 内容 | 计时、任务块切分、黄线记账、看板、耗时归因 | 即时提示、节奏规则、交付流程 |
| 对使用者的影响 | 只观察，不打断 | 会插话、会影响动作 |
| 默认 | 启用 | 不启用（逐条选择） |
| 验证状态 | — | 见「三、方案清单」，全部未验证或试用中 |

本版（0.3.0）：探针随插件启用；方案开关在本机数据目录的 `solutions.json`，**默认空**——
没启用任何方案时，探针只采集、不输出任何提示。用 `solutions/cli.py` 逐条开启，立即生效，不需要重装。

## 0. Claude Code / DSH 双宿主探针（v0.4）

这一层把探针从「解析文本暗号」改成「确定性触发 + 工具上报」：

- 每次工具调用后检查当前任务块时长；超红线（默认 30 分钟）注入一行短提醒。
- AI 调用 `probe_report` 记录 `phase / task_id / summary / cause / next / category`。
- 对用户只说一句简短的人话，不把分析模板塞进对话。
- 探针维护 open tasks；跨会话继续同一个真实任务，靠 `task_id` 关联。
- 数据默认写到共享目录 `~/.ai-coding-efficiency/data`，Claude 与 DSH 用 `host` 字段区分。

**Claude Code**

```bash
# 先隔离测试，不装全局
claude --plugin-dir /Users/lizhi/ai-coding-efficiency-kit

# 正式安装（已执行，user scope）
claude plugin marketplace add /Users/lizhi/ai-coding-efficiency-kit
claude plugin install ai-coding-efficiency-kit@ai-coding-kit -s user
claude plugin list          # 核对 enabled
```

安装后需要重启 Claude Code 或新开会话才会加载。Claude 侧通过 `.mcp.json` 暴露 `probe_report`
（工具名带 `mcp__plugin_...__` 前缀），hooks 负责注入 open tasks 和红线提醒。

**DSH**

```bash
# 从 web 模板建一个独立的 coding profile，不碰现有 web / 小橘
dsh --profile coding --from-default-profile web --dump-config

# 安装本地插件
dsh plugin --profile coding add /Users/lizhi/ai-coding-efficiency-kit

# 启动（避开 live web 的 3080）
dsh --profile coding --port 3081 --no-open
```

DSH 侧注册的是原生工具 `probe_report`；`agent/pre-step` 注入 open tasks / 提醒，`session/event` 采集 prompt、tool、turn 结束。

**工具字段**

| 字段 | 必填 | 说明 |
|---|---|---|
| `phase` | 是 | `start` / `progress` / `blocked` / `end` |
| `task_id` | 是 | kebab-case，跨会话复用 |
| `summary` | 是 | 现在在做什么，一句话 |
| `cause` | blocked 必填 | 为什么慢/卡住 |
| `next` | 否 | 下一步动作 |
| `category` | blocked 必填 | serial-wait / repeat-pitfall / design-churn / decision-batching / tool-misuse / no-evidence / spec-drift / human-wait / other |

数据只落本机，不上传；`summary` 可能含任务内容，目录权限 0700。

## 二、安装：用 IDE 原生的插件命令

不需要安装脚本。两个平台都用各自 CLI 装解压后的本目录。

### Qoder

```bash
cd ai-coding-efficiency-kit        # 解压后的目录
qodercli plugin validate "$PWD"    # 装前自检
qodercli plugin install  "$PWD"
```

装载内容：`skills/` 两个 skill + `hooks/qoder-hooks.json` 的 8 条 hook。

### Codex

```bash
cd ai-coding-efficiency-kit        # 解压后的目录
codex plugin marketplace add "$PWD"
codex plugin add ai-coding-efficiency-kit@ai-coding-kit
codex plugin list        # 确认 installed, enabled
```

Codex 的插件根是 `.codex-plugin/plugin.json`，hook 声明在 `hooks/codex-hooks.json`；
marketplace 清单在 `.agents/plugins/marketplace.json`。两者与 Qoder 共用同一套脚本，只是路径变量和事件名不同。

### 更新 / 卸载

```bash
# 更新：替换为新版本目录后重跑一次安装命令，覆盖插件缓存里的版本
qodercli plugin install "$PWD"
codex plugin marketplace upgrade && codex plugin add ai-coding-efficiency-kit@ai-coding-kit

# 卸载
qodercli plugin uninstall ai-coding-efficiency-kit
codex plugin remove ai-coding-efficiency-kit@ai-coding-kit
```

卸载不动数据目录；`data/` 在插件缓存之外，需手工删除。

## 三、方案清单

| 方案 ID | 解决什么 | 状态 | 现有证据 |
|---|---|---|---|
| `tempo-rules` | 长任务失控、压力下走捷径、决策反复 | 试用中 | 红线告警带「执行 / 等你 / 你离开」时间账，四行复盘落 `data/retros/`；无对照实验 |
| `inline-reminders` | 串行等待、读多定位少、长回合无人管 | 试用中 | RULE-005 跟进 77.8%、A/B 样本不足；RULE-004 跟进 0/17 且 11-03 导航上升（疑似反作用） |
| `lsp-autoconfig` | 查引用靠逐文件翻 | 未验证 | 只有产物与单次实测 |
| `fast-loop` | 设计反复、串行等待 | 未验证 | 无对照实验；Skill 调用命中见 `assets.py report` |
| `alignment-gate` | 照资料/协议实现，与业务要求不一致 | 试用中 | 问题类标记为事后不可判定，靠门记录造证据 |
| `detour-signal` | 卡住后反复试同一路径，失败不记录 | 试用中 | 声明落 `data/detours/`，看恢复率；2026-11-05 上线 |

部署等待器不再是独立方案：前台部署的提示由 `inline-reminders` 的 RULE-005 负责，等待本身用 `bin/deploy_watch.py`。交付类 skill（`html-report-loop`）和多机协同模块（`optional/multi-machine-sync`）也不列在这里：前者是报告交付方式，后者是跨机同步基础设施，都不是提效干预。

新装默认启用 `tempo-rules`、`inline-reminders`、`detour-signal`；已有 `solutions.json` 时安装器不覆盖，避免把手工关掉的重新打开。

状态四档：`未验证`（无对照）→ `试用中`（有触发读数、样本不足）→ `已验证`（过 A/B 门槛且结果指标同向）→ `已停用`。
状态写在每个方案目录的 `solution.json` 里，`cli.py list` 直接读它。

```bash
python3 solutions/cli.py list                     # 方案、状态、证据、开没开
python3 solutions/cli.py review                   # 近 7 天触发量 + 状态建议（每周复查）
python3 solutions/cli.py review 14                # 拉长窗口
python3 solutions/cli.py enable inline-reminders   # 开启一条，立即生效
python3 solutions/cli.py disable inline-reminders
```

开关落在 `$IDE_HOME/task-metrics/data/solutions.json`，不动宿主配置、不用重装。
内置方案的判定逻辑仍在探针脚本里，走同一个开关；外部方案在 `solutions/<id>/hook.py` 里实现，
由探针的固定入口调用 `solutions/dispatch.py` 分发（玩法见 [solutions/README.md](solutions/README.md)）。
插件安装（代码在缓存里）时，把 `solutions/cli.py` 换成 `${QKIT}solutions/cli.py`，QKIT 见「五、怎么看数据」。

## 四、探针采什么、不采什么

- 采：任务块墙钟与时间账（agent 执行 / 块内等你 / 块结束后你离开）、模型、工具名与工具族、调用次数、失败/重开、决策记录。
- 不采：对话正文、代码内容、凭证。
- 落点：Qoder `~/.qoder/task-metrics/data/`；Codex `~/.codex/task-metrics/data/`。插件缓存升级会覆盖代码目录，数据不在缓存里。

## 五、怎么看数据

插件安装后脚本在缓存目录，用一下方式定位（版本号会自动取最新）：

```bash
QKIT=$(ls -d ~/.qoder/plugins/cache/*/ai-coding-efficiency-kit/*/ | tail -1)
python3 "${QKIT}hooks/task-metrics/bin/harness_scan.py" status     # 一屏：钩子健康 / 今日任务 / 趋势
python3 "${QKIT}hooks/task-metrics/bin/harness_scan.py" daily      # 当日黄线以上任务
python3 "${QKIT}hooks/task-metrics/bin/harness_dashboard.py"       # 14 天 HTML 看板
python3 "${QKIT}hooks/task-metrics/bin/task_retro.py" --last 3     # 最近 3 个任务块实际耗时
python3 "${QKIT}hooks/task-metrics/bin/taskfacts.py" --report 7    # 任务事实：类型/难度/方法/失败率
python3 "${QKIT}hooks/task-metrics/bin/taskfacts.py" --compare 7   # 最近 7 天 vs 前 7 天
python3 "${QKIT}hooks/task-metrics/bin/assets.py" report           # 资产命中与 0 命中清单
```

任务事实由 SessionEnd 钩子增量落 `data/taskfacts/<日期>.jsonl`；老数据用
`taskfacts.py backfill --days 30` 回填。每个块记 `minutes`（墙钟）、`work_minutes`（agent 执行）、
`wait_minutes`（块内等你）、`away_minutes`（块结束后你离开到下次开工）；红线告警按这个时间账
直接点名，避免把等你/你离开的时间记成工具耗时。资产先 `assets.py seed` 写入起始项，之后
`assets.py auto --days 7` 从转录（Skill 调用、读记忆文件）和提醒 tag 回写命中。
弯路声明在会话开始时注入规则，Stop 时把「走了弯路：现象 → 改走什么」采到 `data/detours/`，
并记录尾部窗口失败数、自述恢复与后续证据。

### 每日一案（推荐先看这个）

趋势看板信息量大、解读成本高。日常复盘用「每日一案」：自动挑当天最耗时的任务，
把最贵的一段拆成证据，只给一条对策、一条验证线，最后请拍板。

```bash
python3 "${QKIT}hooks/task-metrics/bin/daily_case.py"                 # 今天最长任务
python3 "${QKIT}hooks/task-metrics/bin/daily_case.py" --list          # 当日候选
python3 "${QKIT}hooks/task-metrics/bin/daily_case.py" --date 2026-11-04 --task <session:turn>
python3 "${QKIT}hooks/task-metrics/bin/daily_case.py" --record adopt --case <报告.json>
```

报告落 `data/reports/<日期>/`，五块：问题全貌 / 本段事实与证据 / 解决对策 / 测试验证 / 采纳建议。

问题全貌以会话为单位：同一个会话=一个问题，按停顿超过 30 分钟切成连续工作段，先看总投入、哪段没做好，
再下钻到一轮问答。出现纠正或质疑时，纠正前的那段判为没做好。

Stop 时自动给当前连续工作段写一份事实摘要（活跃 ≥10 分钟才写），落
`data/summaries/<日期>/<会话>.md`：每次提问你说什么、它回了什么、动作构成、改动文件、失败与压缩点。
这是效能分析的主证据——看“为什么慢”读这份总结加会话原文即可，不需要日志/Trace；日志/Trace/复现只在
被分析任务本身是线上排障时使用。

「本段事实与证据」有三层：① 时间划分（模型/工具/等你回答/空档）；② 关键片段——每个可疑时段截取
「该段之前的用户要求 → 当时的思考原文（反复处高亮）→ 随后动作」，回答“这段时间为什么慢、在反复什么”；
③ 旁证——上下文压缩事件与任务窗口的先后关系。加 `--no-quotes` 可去掉会话原文。

不改行为前先出报告；采纳后对后续 3 个同类任务重跑，用同一指标回填，验证不成立就按「不采纳」处理。
报告含本机会话原文，只落本地，不要直接外发。

### 问题类扫描（长期用这个）

每日一案回答「这一次哪里慢」；问题类扫描回答「哪一类问题在反复发生、值不值得治」。
按已登记的问题类扫全部会话，过阈值才出 finding，每条自带干预与验证口径。

```bash
python3 "${QKIT}hooks/task-metrics/bin/problem_class.py"                    # 最近 30 天
python3 "${QKIT}hooks/task-metrics/bin/problem_class.py" --days 90 --write  # 写 findings
python3 "${QKIT}hooks/task-metrics/bin/problem_class.py" --class cli-help-churn \
        --since 2026-10-19 --until 2026-11-04                               # 干预前后同口径对比
```

问题类分两种，不能混：

| 类型 | 例子 | 怎么处理 |
|---|---|---|
| 可检出 | 命令行靠 --help 逐层试；首轮无证据就下结论 | 检测器给基线与成本，干预后同口径复跑 |
| 事后不可判定 | 资料/协议与业务要求不一致 | 不猜原因，改为开工前设门；门记录才是证据 |

门方案 `alignment-gate` 默认关闭。启用后只在「资料/协议 + 落地动作」同时出现时触发，
要求先答负责人 / 对齐结论 / 验收标准，记录写进 `data/gates/`：

```bash
python3 "${QKIT}solutions/cli.py enable alignment-gate"
python3 "${QKIT}hooks/task-metrics/bin/problem_class.py" --gates
```

判读：门覆盖率低先改话术，别急着算效果；效果比较「有确认」与「无确认」任务的返工时长，
样本 ≥3 再下结论。检测器只读本机转录，不上传。

Codex 把 `~/.qoder/plugins/cache` 换成 `~/.codex/plugins/cache`。

跨人比对：数据只落本机，所以由数据主人导出 `data/metrics/*.jsonl` 给对方，对方的看板指向该目录即可；
不做自动上传。

## 六、平台支持

| | Qoder | Codex |
|---|---|---|
| 插件安装 | ✅ 已实测 | ✅ 已实测 |
| 采集出数 | ✅ | ⏳ 转录格式适配中，装上暂无数据 |
| 方案注入 | ✅ | ⏳ hook 已接，随方案拆分一起验证 |

Codex 的会话记录是 `~/.codex/sessions/<年>/<月>/<日>/rollout-*.jsonl`，schema 与 Qoder 不同；
在解析适配完成前，Codex 侧只完成接线，不产生指标。这是当前最优先的待办。

## 七、仓库结构

```
.qoder-plugin/plugin.json     Qoder 插件清单（hooks 指向 hooks/qoder-hooks.json）
.codex-plugin/plugin.json     Codex 插件清单（hooks 指向 hooks/codex-hooks.json）
.agents/plugins/marketplace.json   Codex 团队 marketplace 清单
hooks/
  qoder-hooks.json            Qoder 接线：SessionStart / UserPromptSubmit / PostToolUse / Stop
  codex-hooks.json            Codex 接线：同上，相对路径 + KIT_IDE_HOME
  task-metrics/bin/           计时、账本、看板、收尾、任务事实、资产台账、deploy_watch（仅 python3 标准库）
skills/                       fast-loop、html-report-loop
solutions/                    方案目录：每个 solution.json + 可选 hook.py；kit_solutions.py（开关）、dispatch.py（分发）、cli.py（开关/复查命令）
rules/task-tempo.md           节奏规则块（属方案，不随插件注入）
optional/                     multi-machine-sync（按需）
install.sh / uninstall.sh     兜底：没有插件命令的环境用，创建软链并合并配置
tests/install-smoke.sh        兜底安装的冒烟（临时 HOME，不碰真实配置）
```

## 八、配置与隐私

配置写到 `$IDE_HOME/task-metrics/kit.env`，全部可选：

| 变量 | 默认 | 说明 |
|---|---|---|
| `KIT_DATA_DIR` | `$IDE_HOME/task-metrics/data` | 数据落点 |
| `KIT_YELLOW_MIN` | `10` | 黄线分钟数 |
| `KIT_RED_MIN` | `30` | 红线分钟数 |

多机协同走 ssh + rsync，只在自有机器之间；不记录对话正文。
