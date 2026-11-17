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
| `voice/*.md` | `{{minutes}}` `{{expect}}` `{{yellow}}` `{{red}}` `{{work}}` `{{streak}}` `{{changes}}` |

未知变量会被替换成空串，并在日志里写一条警告。变量表是白名单，不能新增。

## 生效范围

- `start-hint.md` / `binding-hint.md` / `end-hint.md` / `voice/*.md`：**即时生效**；
- `protocol.md`：**重启宿主后生效**（它注册在 system prompt 区段里）。

## 示例

`~/.agentcrop/templates/binding-hint.md`：

```markdown
你还没绑定工作。开工前先调 crop_update(phase="start", task_id="<短名>", summary="<一句话>", expect_min=预估分钟)。
如果上一件已经 end（{{last_phase}}），交付和汇报不用再绑。
```

`~/.agentcrop/templates/voice/yellow.md`：

```markdown
已经跑了 {{minutes}} 分钟，超出你报的 {{expect}}。
---
{{minutes}} 分钟了，报的 {{expect}} 已经用完。
```

## 限制

- 单模板正文上限 4000 字符，超出截断并写警告；
- 模板是纯文本 + 白名单变量，没有条件语法；
- 模板写坏（编码错、读失败）会回退默认，不会让会话挂掉。
