# 注入模板自定义 Implementation Plan

**Goal:** 让用户用 `~/.agentcrop/templates/` 里的 md 文件覆盖 5 个注入点的文本，crop 负责加载、渲染、限长与回退。

**Architecture:** core 新增 `crop_templates.py` 统一加载/渲染（mtime 缓存、白名单、4000 字符上限、失败回退）；`probe_core.py` 的 5 个注入函数改为「先取模板，未配置走现有字符串」；CLI 暴露模板操作；node 通过 CLI 取协议与收尾提示。

**Tech Stack:** Python 3.10+（core/CLI）、Node 22（dsh 插件）、unittest + node:test。

**Spec:** `docs/DESIGN-CUSTOM-TEMPLATES.md`

## Global Constraints

- 默认（无模板目录）下，所有注入文本必须与现状**逐字一致**——现有测试不改断言也要全绿。
- 模板渲染只做白名单变量替换，不引入求值/条件语法。
- 单模板正文上限 4000 字符，超长截断并写警告日志。
- `[crop] ` 前缀由程序统一加，模板正文里不重复加。
- 文件缺失或为空 → 回退内置默认；正文 `none` → 关闭该注入（返回空串）。
- 全部改动在 worktree 里，最后按仓库规矩提一个代码 PR（设计稿、计划、实现同 PR）。

## Review Focus

1. 用户模板写坏（非 UTF-8 / 语法乱）→ 会话不能挂，回退默认。
2. 未知变量 `{{foo}}` → 空串替换，不把 `{{foo}}` 原样注入给模型。
3. voice 模板每条为空/只有 `---` → 不能注入空行，回退内置池。
4. 协议模板改完未重启 → 设置页必须提示「重启生效」，其余四类即时生效。
5. 模板目录不存在时，不能自动创建或生成空文件导致注入被关闭。

---

### Task 1: core 模板引擎

**Files:**
- Create: `core/crop_templates.py`
- Create: `tests/test_templates.py`

**Interfaces:**
- `templates_dir() -> Path`（`KIT_TEMPLATES_DIR` 优先，默认 `paths()["templates"]`）
- `load(name: str) -> str | None`（None=用默认；""=关闭）
- `render(name: str, variables: dict[str, str]) -> str | None`
- `voice_lines(tier: str) -> list[str] | None`

**Steps:**
- [ ] 写 `tests/test_templates.py`：缺失 → None；空文件 → None；`none` → ""；变量替换；未知变量空串；4001 字符截断；voice `---` 分隔；mtime 改动后缓存失效。
- [ ] 跑 `python3 -m unittest tests.test_templates -v`，确认 FAIL（模块不存在）。
- [ ] 实现 `core/crop_templates.py`（读取 + 缓存 + 渲染 + 限长 + 警告日志）。
- [ ] 再跑测试，确认 PASS。
- [ ] `git add core/crop_templates.py tests/test_templates.py && git commit -m "feat(templates): 模板加载与渲染引擎"`。

### Task 2: 接入 5 个注入点

**Files:**
- Modify: `core/probe_core.py`（`format_candidates` / `format_open_tasks` / `binding_hint` / `miss_nudge` / `reminder` / `miss`）
- Test: `tests/test_event_flow.py`、`tests/test_crop_cli.py`（只加用例，不改既有断言）

**Steps:**
- [ ] 加用例：`KIT_TEMPLATES_DIR` 指向临时目录，写 `binding-hint.md` 正文 `NONE` 变量替换后断言输出；无模板时断言与现状一致。
- [ ] 跑新用例确认 FAIL。
- [ ] 每个函数：先 `crop_templates.render(...)`；`None` 走原字符串；`""` 返回 `""`；正文拼 `[crop] ` 前缀（若原函数已含则不再加）。
- [ ] `voice` 相关：`voice_lines(tier)` 非空则用模板候选，空则用 `crop_voice.pick`。
- [ ] 跑 `python3 -m unittest discover -s tests -t . -q` 全绿。
- [ ] Commit。

### Task 3: CLI 与 node 接入

**Files:**
- Modify: `core/probe_core_cli.py`（新增 `template_get` / `template_render` / `template_defaults`）
- Modify: `dsh/lib/index.js`（`PROTOCOL_TEXT`、`END_SEDIMENT_NUDGE` 运行时读取）
- Test: `dsh/test/index.test.mjs`（CLI 失败时回退内置）

**Steps:**
- [ ] 加 node 用例：`callCore` 返回空时 `PROTOCOL_TEXT` 回退内置文本（导出常量保留）。
- [ ] 实现 CLI 三个 op；`template_defaults` 把内置默认写入模板目录。
- [ ] index.js：apply 时取 `template_render protocol`，成功则注册该文本，失败回退；end 时取 `end-hint`。
- [ ] `bash scripts/check.sh` 全绿。
- [ ] Commit。

### Task 4: 设置页与文档

**Files:**
- Modify: `dsh/lib/client.js`（设置卡：路径 / 打开目录 / 生成默认 / 预览 / 重启提示）
- Create: `docs/TEMPLATES.md`（用户文档：目录结构、变量表、示例、none 关闭）
- Modify: `docs/INSTALL.md`（一句指向 TEMPLATES）

**Steps:**
- [ ] 设置卡加按钮与预览弹层；协议预览旁标注「改动需重启宿主」。
- [ ] 写 `docs/TEMPLATES.md`。
- [ ] 本机实机验证：写一个 binding-hint 模板，确认注入变化；删除恢复。
- [ ] `bash scripts/check.sh` 全绿。
- [ ] 提代码 PR（含本计划、设计稿、实现）。
