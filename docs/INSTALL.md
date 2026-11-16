# 安装

Crop 的宿主分两类：DSH 是主力（原生插件 + 系统提示 + 面板），
Claude Code 走 hook 接线。

DSH 走 npm，不需要克隆仓库。只有改源码或给 Claude Code 装 hook 时，才需要仓库：

```bash
git clone https://github.com/oriliz/agent-crop.git ~/agent-crop
```

## DeepSeek Harness（推荐）

```bash
dsh plugin --profile <profile> add agent-crop-dsh
# 重启 dsh
```

npm 包 `agent-crop-dsh` 里是预构建好的 `dsh/lib/`，安装时不会执行构建脚本：

- 不需要 pnpm 的 `allowBuilds` 放行
- 不需要本机装 Node 构建工具链（Node 22.19+ 仍要，用来跑插件）
- 插件声明了 `engines.dsh: ">=0.2.1 <0.3.0"`，DSH 版本不在范围内会被安装检查拒绝

改源码时的安装方式（`dsh plugin add` 对本地目录建 link）：

```bash
dsh plugin --profile <profile> add ~/agent-crop
```

装完即生效：

- 原生工具 `crop_update`（`crop_report` / `probe_report` 仍作兼容别名）
- 系统提示里常驻 `crop:protocol` 段落（不用改 AGENTS.md）
- 每回合首步注入一行工作状态；超红线注入提醒
- 右栏新增 **Crop** 标签页（实时数字）

验证：

```bash
# npm 装法下 crop 不在 PATH，二选一：
npm i -g agent-crop-dsh && crop status
npx --yes --package=agent-crop-dsh -- crop status

tail -1 ~/.agentcrop/data/events.jsonl   # 应看到 "host":"dsh"
```

> 数据目录历史名 `~/.ai-coding-efficiency`，新版名 `~/.agentcrop`。没有后者时以 `crop status` 末行为准，或 `ln -s ~/.ai-coding-efficiency ~/.agentcrop`，也可用 `KIT_SHARED_HOME` 覆盖。

## Claude Code

Claude Code 走 marketplace，需要先有仓库：

```bash
claude plugin marketplace add ~/agent-crop
claude plugin install agent-crop@agent-crop -s user
claude plugin list
```

Claude 侧通过 `.mcp.json` 暴露 `crop` 工具，hooks 负责注入未完成任务与红线提醒。
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

`crop` 是 npm 包里的 `bin`，两种放法：

```bash
npm i -g agent-crop-dsh          # 全局装，crop 直接可用
# 或者不全局装：
npx --yes --package=agent-crop-dsh -- crop status
```

源码装法才用符号链接：

```bash
ln -sf ~/agent-crop/bin/crop /usr/local/bin/crop
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
