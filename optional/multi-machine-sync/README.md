# 多机协同（可选模块）

在两台（或更多）自有机器之间同步会话交接与记忆摘要，解决「换台机器就丢上下文」。

**默认不安装**。没有第二台机器时，本模块没有价值，装上也只会多一堆空目录。

## 适用与不适用

- 适用：一人两台以上机器（台式 + 笔记本），需要接手对方正在做的事。
- 不适用：单机开发；多人协作（那应该用代码仓库和知识库，不是本模块）。

## 安装

```bash
./install.sh --with-multi-machine-sync
```

安装后编辑 `~/.agent-sync/config.env`：

```bash
MACHINE=laptop          # 本机名
ROLE=orchestrator       # orchestrator 负责汇总与推送；follower 只接收
PEER_NAME=desktop       # 对端名
PEER_SSH=user@10.0.0.2  # 对端 ssh 目标
#KIT_PEER_TB_PREFIX=   # 可选：直连网段前缀
```

然后在 IDE 的 `settings.json` 里按需注册两个 hook（安装脚本不自动注册，避免多机角色配错）：

```json
{
  "SessionStart": [{"matcher": "startup|resume|compact", "hooks": [
    {"type": "command", "command": "bash $HOME/.agent-sync/bin/hook_session_start.sh", "timeout": 8}
  ]}],
  "SessionEnd": [{"matcher": "*", "hooks": [
    {"type": "command", "command": "bash $HOME/.agent-sync/bin/hook_session_end.sh", "timeout": 12}
  ]}]
}
```

## 数据边界

- 全部状态在 `~/.agent-sync/`（`state/`、`shared/`、`logs/`、`conflicts/`），不进仓库、不上云。
- 同步走 ssh + rsync，仅在两台自有机器之间；不经过任何第三方服务。
- 冲突文件说明：`conflicts/` 下的条目是两边同时改过的记忆条目，需人工合并。

## 与计时引擎的关系

计时引擎的看板支持合并对端数据：设置 `KIT_PEER_NAME` / `KIT_PEER_SSH` 后，`harness_scan.py pull` 会把对端指标拉到本机 `data/peers/<对端>/`，看板自动合并并按机器标注。未配置对端时该能力自动关闭。
