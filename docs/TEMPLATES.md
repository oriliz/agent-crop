# 自定义注入模板

Agent Crop 有 5 个「对 AI 说话」的注入点，默认文案写在代码里；你可以在
`~/.agentcrop/templates/` 下放 Markdown 文件覆盖它们。没放文件（或文件为空）就用默认。

## 目录

```text
~/.agentcrop/templates/
├── protocol.md            # 常驻协议（每会话注入；改完需重启宿主）
├── start-hint.md          # 首轮候选注入
├── binding-hint.md        # 绑定提示
├── end-hint.md            # 收尾沉淀提示
└── voice/
    ├── warm.md            # 提醒话术六档；每档多条候选用单独一行 --- 分隔
    ├── yellow.md
    ├── red.md
    ├── overdue.md
    ├── miss.md
    └── miss_streak.md
```

- 目录可用环境变量 `KIT_TEMPLATES_DIR` 覆盖（测试/多环境用）。
- **前缀自动加**：正文不用写 `[crop] `，程序会加；写了也不会重复。
- **关闭某个注入**：文件内容只写 `none`（`no`/`None` 都行）。
- **空文件** = 没设置，回退默认。

## 可用变量

| 模板 | 变量 |
|---|---|
| `protocol.md` | `{{persona}}` `{{user_name}}` |
| `start-hint.md` | `{{mine}}` `{{others}}` `{{candidates}}` `{{example_task_id}}` `{{limit}}` |
| `binding-hint.md` | `{{last_work}}` `{{last_phase}}` `{{example_task_id}}` |
| `end-hint.md` | `{{work_id}}` |
| `voice/*.md` | `{m}` `{e}` `{over}` `{left}` `{streak}` `{changes}` `{basis}` |

未知变量会被替换成空串，并在日志里写一条警告。变量表是白名单，不能新增。
（`voice/*.md` 例外：它走 Python 的 `format_map`，占位符是**单花括号**；写错会
让这条自定义文案回退成内置默认，不会静默替换。）

## 生效范围

- `start-hint.md` / `binding-hint.md` / `end-hint.md` / `voice/*.md`：**即时生效**；
- `protocol.md`：**重启宿主后生效**（它注册在 system prompt 区段里）。

## 示例

`~/.agentcrop/templates/binding-hint.md`：

```markdown
你还没绑定工作。开工前先调 crop_update(phase="start", task_id="<短名>", summary="<一句话>", expect_min=预估分钟)。
如果上一件已经 end（{{last_phase}}），交付和汇报不用再绑。
```

`~/.agentcrop/templates/voice/yellow.md`（只对「奴隶主」风格生效）：

```markdown
已经跑了 {m} 分钟，超出你报的 {e}。
---
{m} 分钟了，报的 {e} 已经用完。
```

## 限制

- 单模板正文上限 4000 字符，超出截断并写警告；
- 模板是纯文本 + 白名单变量，没有条件语法；
- 模板写坏（编码错、读失败）会回退默认，不会让会话挂掉。

## 触发规则（鞭策策略）

当前只有「超时」一种策略，以 AI 自报的 `expect_min` 为时钟：

| 档位 | 触发点 | 语气 |
|---|---|---|
| `warm` | 0.8 × expect_min | 预告，鞭子还没落下 |
| `yellow` | 1.0 × | 黄线 |
| `red` | 1.5 × | 红线 |
| `overdue` | 2.5 × | 超时 |

没报预期时按 `remind_min`（默认 15 分钟）兜底。`miss` / `miss_streak` 是「上一轮没登记」的提醒，不按分钟。

## 开放与透明：怎么查

每次鞭策实际注入的内容都会记一条 `nudge` 事件：策略、档位、来源（`template` / `builtin`）、全文（前 500 字符）、分钟数、预期值。

```bash
crop nudges            # 最近注入的鞭策，含全文
crop nudges --limit 30
crop nudges --json     # 给脚本 / AI 用
crop preview           # 用示例变量渲染当前生效的 5 个注入点
```

`crop preview` 在没有模板文件时会标注「内置默认，未覆盖」，方便改模板前后对比。
