# 隔离 headless 验证 · 首轮注入尊重已有绑定（2026-11-15）

## 要验证什么

会话先绑定工作，**重启进程**后首轮仍显示 `当前工作：…`，而不是
「未完成的工作有 N 条……哪个属于本会话？」候选列表。

## 隔离方式

- `KIT_DATA_DIR=$(mktemp -d)`：crop 数据（工作/绑定/事件）全在临时目录，不碰 `~/.agentcrop/data`。
- 工作区 `$(mktemp -d)`：会话落在独立 workspace，结束后删除。
- `dsh --profile headless`：该 profile 的 `agent-crop-dsh` 直链仓库
  `/Users/lizhi/AgentProjects/agent-crop`，跑的就是当前 working tree。
- 进程 2 用 `--session-id` 续接同一会话，`state.started` 是**新进程**的，
  所以首步会重新走 `start_hint` 注入路径。

## 步骤与现场

```bash
CROP_DATA=$(mktemp -d); WS=$(mktemp -d); cd "$WS"

# 进程 1：新会话（未绑定）
KIT_DATA_DIR="$CROP_DATA" dsh --profile headless --json "调用 crop_report … 然后只回复 OK"
# -> session-8f4b9f59-732e-476b-a692-ecdc39e6e5a5

# 绑定：走 crop_report 同一 core 路径（record_report phase=start）
KIT_DATA_DIR="$CROP_DATA" python3 -c 'probe_core.record_report({...task_id="headless-verify"...}, {"sid": SID, "workspace": WS})'
# -> binding: headless-verify

# 进程 2：重启，续接同一 session
KIT_DATA_DIR="$CROP_DATA" dsh --profile headless --session-id "$SID" --json "只回复 OK"
```

## 证据（session.v4.jsonl.zstd）

进程 1（turn 1，未绑定）：

```
seq 14  [crop] 本机当前没有进行中的工作。新开一条：crop_report(phase="start", ...)
```

进程 2（turn 2, step 1，新进程）：

```
seq 26  step/start {"turn":2,"step":1}
seq 28  [crop] 当前工作：确认首轮显示当前工作（headless-verify）。已进行 0 分钟，
        未超时（黄线 10 分钟）；上次更新 0 分钟前。留意用户是否把话题岔到另一件事…
```

进程 2 没有再出现候选列表，也没有出现「哪个属于本会话」。

> 注：headless profile 没有配置 `DEEPSEEK_API_KEY`，两次运行都在模型请求处
> `MISSING_CREDENTIAL` 失败。注入发生在模型请求之前且已落盘（上面的 seq 14/28），
> 因此不影响本次验证；也意味着这次验证没有消耗模型额度。

## 单测

```
python3 -m unittest discover -s tests -p 'test_*.py'   # 54 tests, OK
node --test dsh/test/index.test.mjs                    # 13 pass, 0 fail
```

新增/覆盖：

- `test_start_hint_honors_an_existing_binding`（已有）：绑定后首轮是状态行。
- `test_start_hint_honors_a_binding_outside_the_candidates`（新增）：
  绑定的工作被别的会话更新、被更新更近的 3 条挤出候选列表后，首轮仍是状态行。
- `first turn honors an existing binding instead of listing candidates`（已有）：
  插件 fresh process + 同一 store/session。
- `first turn shows the bound work even after it fell out of the candidates`（新增）：
  插件路径上的同一回归（直接改 `open.json` 的 `last_seen_at/last_sid` 构造场景）。

## 落地改动

- `core/probe_core.py`
  - `format_open_tasks`：`resolve_binding(sid)` 为真就返回 `work_status_line`，
    不再要求绑定的工作出现在 `tasks` 候选里。
  - `work_status_line`：绑定存在时直接从 `open.json` 取绑定的工作，不受 top-3 限制。
  - 顺手删掉了一段被后续定义覆盖的重复 `ago/format_open_tasks/format_start_hint/format_reminder`
    （同一次坏 patch 留下的死代码，两处逻辑不一致时容易改错地方）。
