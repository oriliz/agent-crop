# Claude Code / DSH 双宿主探针

## 为什么不是文本暗号

旧版让 AI 在回复里写「超时点/根因/下次/兑现」四行。问题有两个：

1. 上下文压缩后，AI 可能已经忘了规则，长任务里反而不写。
2. 文本要解析、要清洗，而且侵入对话。

v0.4 改成：

- **探针确定性触发**：每次工具调用后检查时长，超线注入一行提醒。不依赖 AI 记忆。
- **AI 调用工具上报**：`probe_report` 直接记录结构化字段。
- **用户可见文本只负责体验**：一句“不好意思，这个做得有点久”，分析数据走工具。

## 两层时间

| 层 | 是什么 | 谁判定 |
|---|---|---|
| block | 一次 prompt → stop 的工程任务块（20–30 分钟） | 探针机械计时 |
| task | 一个真实任务，可能跨多次对话（如 8 小时的设计文档） | AI 通过 `phase=start/end` 判定 |

block 通过 `task_link` 事件关联到 task；task 记录累计 block 数和最后活跃时间。

## 数据模型

默认目录：`~/.ai-coding-efficiency/data`（0700）。

```
events.jsonl                 # 所有事件，带 host
tasks/open.json              # 未完成任务
tasks/closed/<date>.jsonl    # 已完成任务
reports/<date>.jsonl         # probe_report 原始记录
blocks/<date>.jsonl          # 机械任务块
runtime/current.json         # 当前会话/block（Claude MCP 用）
config.json                  # red_min / remind_min / user_facing
```

事件示例：

```json
{"t":1760000000000,"ev":"probe_report","host":"dsh","sid":"...","task_id":"design-doc","phase":"blocked","summary":"正在查资料","cause":"资料太散","next":"先定目录","category":"tool-misuse","turn":2,"step":8}
```

配置环境变量：

| 变量 | 默认 | 说明 |
|---|---|---|
| `KIT_DATA_DIR` | `$KIT_SHARED_HOME/data` | 最高优先级 |
| `KIT_SHARED_HOME` | `~/.ai-coding-efficiency` | 共享根 |
| `KIT_TASK_RED_MIN` | `30` | 红线分钟 |
| `KIT_TASK_REMIND_MIN` | `15` | 提醒间隔 |
| `KIT_USER_FACING` | `1` | 是否让 AI 说一句人话 |

## Claude Code 映射

| Claude 事件 | 探针动作 |
|---|---|
| `SessionStart` | 记录 session；注入 open tasks |
| `UserPromptSubmit` | 记录 prompt；首条消息注入“新任务 start / 续接已有 task_id”；用户说“好了/可以了”时注入 end 提醒 |
| `PostToolUse` | 记录工具；超线时输出 `hookSpecificOutput.additionalContext` 提醒 |
| `Stop` | 关闭 block，写 blocks |
| `SessionEnd` | 记录 session 结束 |

工具通过 `.mcp.json` 暴露；模型侧名字形如
`mcp__plugin_ai-coding-efficiency-kit_probe__probe_report`。

## DSH 映射

| DSH 事件 | 探针动作 |
|---|---|
| `session/event user/message`（`source.kind === "user"`） | 记录 prompt；开始计时；完成语注入 end 提醒 |
| `session/event tool/result` | 记录工具结果 |
| `session/event turn/end` | 关闭 block |
| `agent/pre-step` | 首次注入 open tasks；队列里的提醒/完成提示追加到当前 step |
| 原生工具 `probe_report` | 写入 reports/events/tasks，返回确认文本 |

DSH 插件零运行时依赖：工具定义和消息对象都是本地构造，核心逻辑通过 `spawnSync` 调
`core/probe_core_cli.py`。

## 安装

### Claude Code

```bash
# 隔离测试：只加载这个插件目录，不改全局 settings
KIT_DATA_DIR=/tmp/probe-e2e claude --plugin-dir /Users/lizhi/ai-coding-efficiency-kit

# 正式安装（已执行，user scope）
claude plugin marketplace add /Users/lizhi/ai-coding-efficiency-kit
claude plugin install ai-coding-efficiency-kit@ai-coding-kit -s user

# 核对
claude plugin list   # 应显示 ai-coding-efficiency-kit@ai-coding-kit，enabled
```

安装后需要重启 Claude Code（或新开会话）才会加载插件；当前已运行的会话不会热加载。
验证：上下文出现 `[探针]`，调用 `probe_report` 后 `~/.ai-coding-efficiency/data/events.jsonl` 有 `host:"claude-code"`。

卸载：

```bash
claude plugin uninstall ai-coding-efficiency-kit@ai-coding-kit
claude plugin marketplace remove ai-coding-kit
```

### DSH

```bash
# 独立 profile，避免影响线上 web / 小橘
dsh --profile coding --from-default-profile web --dump-config
dsh plugin --profile coding add /Users/lizhi/ai-coding-efficiency-kit
dsh --profile coding --port 3081 --no-open
```

验证：`~/.ai-coding-efficiency/data/events.jsonl` 出现 `host:"dsh"`；模型能调用 `probe_report`。

卸载：

```bash
dsh plugin --profile coding remove ai-coding-efficiency-kit-dsh
rm -rf ~/.dsh/profiles/coding     # 如果整个 profile 都不要了
# 数据目录不动，手工删除 ~/.ai-coding-efficiency
```

## 已验证

- Claude Code 插件：`claude plugin validate` 通过；`--plugin-dir` 端到端能注入 `[探针]`、调用 `probe_report`、写入 events/reports/blocks。
- DSH：`coding` profile 启动无错误；headless 端到端能注入 `[探针]`、调用原生 `probe_report`、写入 `turn/step`。
- 跨宿主：同一 `KIT_DATA_DIR` 下同时出现 `host:"claude-code"` 与 `host:"dsh"`；DSH 能看到 Claude 写入的 open task。

## 已知边界

- Claude 的 MCP server 用 `runtime/current.json` 取当前会话；多个 Claude 会话同时跑时以最后一次 prompt 为准。
- DSH 的 `headless` profile 默认关闭 credentials 服务；测试时用 `DEEPSEEK_API_KEY` 环境变量。`coding`（web 模板）没有这个问题。
- 第一版只做探针 + 提醒 + 工具上报；DSH 转录归一化、日报/看板留到下一版。
- 数据只落本机，不上传；`summary` 可能含任务内容，目录保持 0700。
