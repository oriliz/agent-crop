# 设计：注入模板自定义（DESIGN-CUSTOM-TEMPLATES）

> 状态：设计稿，待评审。2026-11-17。
> 目标：把「影响 AI 行为的注入文本」从写死代码里拿出来，交给用户用模板文件定义；
> crop 只负责注入工程（加载 → 渲染 → 合并 → 限长 → 预览）。

## 1. 背景与目标

当前所有向 Agent 注入的文案都写死在代码里：

| 注入点 | 位置 | 用户能否改 |
|---|---|---|
| 常驻协议（身份/规则/说话方式） | `dsh/lib/index.js` 的 `PROTOCOL_TEXT` | 否 |
| 首轮候选注入 | `core/probe_core.py` 的 `format_candidates` / `format_open_tasks` | 否 |
| 绑定提示 | `core/probe_core.py` 的 `binding_hint` | 否 |
| 提醒 / 鞭策话术（6 档） | `core/crop_voice.py` 的文案池 | 否（只能选四档 persona） |
| 收尾沉淀提示 | `dsh/lib/index.js` 的 `END_SEDIMENT_NUDGE` | 否 |
| 工具描述（后续批次） | 各 tool 定义 | 否 |
| `crop-retro` skill 文本（后续批次） | `dsh/lib/index.js` 的 `RETRO_SKILL` | 否 |

目标：用户能编辑上述文本（第一批 5 个注入点），保存后生效；crop 保证渲染可控、
失败可回退、超长有上限、改动可预览。

非目标：用户自定义*指标口径*（本设计不含，指标仍由 core 派生）；新增模板变量；
条件语法（if/else）；多工作区覆盖（后续增量）。

## 2. 总体架构（方案 1：core 统一模板引擎）

```text
~/.agentcrop/templates/           用户模板（不存在则全部用内置默认）
        │
        ▼
core/crop_templates.py            唯一加载/渲染入口（带 mtime 缓存、白名单、限长）
        │
        ├── core 侧：start_hint / binding_hint / reminder / miss 直接调用
        └── node 侧：通过 core CLI（callCore）取渲染结果
                      ├─ 常驻协议 PROTOCOL_TEXT
                      └─ 收尾沉淀提示 END_SEDIMENT_NUDGE
```

理由：CLI、MCP、DSH 三个入口共用一套加载与渲染，行为不会漂；测试只写一套。
node 侧不重复实现模板解析。

## 3. 目录结构与模板约定

```text
~/.agentcrop/templates/
├── protocol.md            # 常驻协议（身份段也在里面）
├── start-hint.md          # 首轮候选注入
├── binding-hint.md        # 绑定提示
├── end-hint.md            # 收尾沉淀提示
└── voice/
    ├── warm.md            # 每档多条候选，用单独一行 --- 分隔
    ├── yellow.md
    ├── red.md
    ├── overdue.md
    ├── miss.md
    └── miss_streak.md
```

- 目录可用 `KIT_TEMPLATES_DIR` 覆盖（测试用）。
- **前缀由程序统一加**：模板只写正文，`[crop] ` 由渲染器补；模板里写了也不会重复加。
- **文件缺失** → 用内置默认；**文件为空** → 视为未设置（回退默认）；
  **正文恰好是 `none`** → 关闭该注入（返回空串，不注入）。
- voice 模板中 `---` 单独成行即分隔候选，至少 1 条；解析失败回退内置池。

## 4. 变量白名单

渲染只替换白名单变量，格式 `{{name}}`；未知变量替换为空串并记一条警告日志（不打断）。

| 模板 | 可用变量 |
|---|---|
| `protocol.md` | `{{persona}}` `{{user_name}}` |
| `start-hint.md` | `{{mine}}` `{{others}}` `{{candidates}}`（整段候选列表）`{{example_task_id}}` `{{limit}}` |
| `binding-hint.md` | `{{last_work}}` `{{last_phase}}` `{{example_task_id}}` |
| `end-hint.md` | `{{work_id}}` |
| `voice/*.md` | `{{minutes}}` `{{expect}}` `{{yellow}}` `{{red}}` `{{work}}` `{{streak}}` `{{changes}}` |

变量值由现有函数计算后传入，模板不能新增变量。

## 5. 加载与渲染规则

1. **缓存**：按 `(path, mtime, size)` 缓存渲染结果；文件改动后下一次调用即生效。
2. **限长**：单模板正文上限 4000 字符；渲染后超出则截断并写警告日志。
3. **失败回退**：读失败、编码错误、渲染异常 → 用内置默认 + 警告日志；不抛异常。
4. **热生效范围**：
   - start-hint / binding-hint / end-hint / voice：**即时生效**（每次注入时读取）。
   - protocol.md：宿主区段是静态注册的，**改动后需重启宿主**才生效；设置页要明确提示。
5. **默认值不外置**：内置默认仍写在代码里；模板目录不自动生成，避免用户看到一堆
   文件误以为必须编辑。设置页提供「生成默认模板」按钮（把当前生效默认写进目录）。

## 6. 接入点

- 新增 `core/crop_templates.py`
  - `templates_dir() -> Path`
  - `load(name, vars) -> str | None`（None = 用内置默认；`""` = 关闭注入）
  - `voice_lines(tier) -> list[str] | None`
- `core/probe_core.py`
  - `format_candidates` / `format_open_tasks`：先取模板，未配置则走现有字符串。
  - `binding_hint` / `miss_nudge`：同上。
  - `reminder` / `miss`：优先 `voice/<tier>.md` 候选项，否则用 `crop_voice.pick`。
- `core/probe_core_cli.py`：新增 op：
  - `template_get`：返回某个模板的原始文本与内置默认；
  - `template_render`：返回渲染后的文本（预览用）；
  - `template_defaults`：把内置默认写进模板目录（「生成默认模板」按钮用）。
- `dsh/lib/index.js`
  - `PROTOCOL_TEXT` 改为运行时读取（`callCore({op:'template_render', args:{name:'protocol'}})`），
    失败回退内置；section 注册仍保持一次，重启宿主后生效。
  - `END_SEDIMENT_NUDGE` 同上（end 时读取，热生效）。
- `dsh/lib/client.js`
  - 设置页增加：模板目录路径、「打开目录」（宿主 exec open）、「生成默认模板」、
    每个模板一个「预览」按钮（弹层显示 `template_render` 结果）；
  - protocol 预览旁标注「改动需重启宿主」。

## 7. 测试

- `tests/test_templates.py`（新增）
  - 文件缺失 → 回退默认；空文件 → 回退默认；`none` → 返回空串；
  - 变量渲染、未知变量空串、超长截断；
  - voice 文件按 `---` 分隔，多条候选都能被 `pick` 取到；
  - KIT_TEMPLATES_DIR 隔离，不碰真实用户目录。
- 更新 `tests/test_crop_cli.py` / `test_event_flow.py`：默认未配置模板时，注入文本与现在完全一致（回归保护）。
- `dsh/test/index.test.mjs`：node 取模板失败时回退内置 `PROTOCOL_TEXT`。
- `tests/probe-smoke.sh` 不受影响（默认无模板目录）。

## 8. 安全与失败模式

- 模板文本会进入 system prompt / 注入点，这是用户对自身 agent 的配置，允许；
  但必须限长（4000 字符）防止一次性撑爆上下文。
- 未知变量、坏模板、读失败都回退默认并写警告，不阻断会话。
- 不引入求值/条件语法，模板是纯文本 + 白名单替换。
- `[crop]` 前缀由程序加，保证面板与日志还能按前缀识别注入。

## 9. 后续（不在本设计范围）

- 第二批注入点：6 个工具描述、`crop-retro` skill 文本。
- 工作区级覆盖（`~/.agentcrop/templates/<workspace-hash>/`）。
- 面板内的可视化编辑器（先做目录 + 预览）。
