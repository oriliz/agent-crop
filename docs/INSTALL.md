# 安装

Crop 的宿主分两类：DSH 是主力（原生插件 + 系统提示 + 面板），
Claude Code 走 hook 接线。

装之前先拿到仓库：

```bash
git clone https://github.com/oriliz/agent-crop.git ~/agent-crop
```

## DeepSeek Harness（推荐）

```bash
dsh plugin --profile <profile> add ~/agent-crop
# 重启 dsh
```

装完即生效：

- 原生工具 `crop_report`（`probe_report` 仍作兼容别名）
- 系统提示里常驻 `crop:protocol` 段落（不用改 AGENTS.md）
- 每回合首步注入一行工作状态；超红线注入提醒
- 右栏新增 **Crop** 标签页（实时数字）

验证：

```bash
crop status                     # 仓库里的 bin/crop；最后一行打印真实数据目录
tail -1 ~/.agentcrop/data/events.jsonl   # 应看到 "host":"dsh"
```

> 数据目录历史名 `~/.ai-coding-efficiency`，新版名 `~/.agentcrop`。没有后者时以 `crop status` 末行为准，或 `ln -s ~/.ai-coding-efficiency ~/.agentcrop`，也可用 `KIT_SHARED_HOME` 覆盖。

## Claude Code

```bash
claude plugin marketplace add ~/agent-crop
claude plugin install agent-crop@agent-crop -s user
claude plugin list
```

Claude 侧通过 `.mcp.json` 暴露 `probe_report`，hooks 负责注入未完成任务与红线提醒。
装完需要新开会话。

## 卸载

```bash
dsh plugin --profile <profile> remove agent-crop-dsh
# Claude: claude plugin uninstall agent-crop@agent-crop
```

卸载只移除插件与协议段落，**不动数据目录**。要清数据：

```bash
rm -rf ~/.agentcrop
```

## 把 crop 放到 PATH

`crop` 是仓库里的一个可执行脚本，装一下即可：

```bash
ln -sf ~/agent-crop/bin/crop /usr/local/bin/crop   # 或放进你自己的 bin 目录
```

## 数据与配置

| 项 | 默认 |
|---|---|
| 数据目录 | `~/.agentcrop/data`（0700） |
| 事件库 | `data/events.jsonl` |
| 工作列表 | `data/tasks/open.json`、`tasks/archive/` |
| 黄线（工作级提醒） | 10 分钟（`KIT_TASK_YELLOW_MIN`） |
| 红线（单轮） | 30 分钟（`KIT_TASK_RED_MIN`） |
| 工作时段 | 10:00–18:00（`KIT_WORK_START` / `KIT_WORK_END`） |
| 离开阈值 | 15 分钟（`KIT_IDLE_MIN`） |
| 面板端口 | 8765（`KIT_DASHBOARD=0` 可禁用自动启动） |
