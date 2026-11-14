# 安全政策

## 支持的版本

只对**最新版本**提供安全修复。当前版本见仓库根目录 `package.json` 的 `version` 字段。
更早的版本请先升级，再确认问题是否还在。

## 怎么报漏洞

**首选：GitHub Security Advisories（私密）**

<https://github.com/oriliz/agent-crop/security/advisories/new>

在那个表单里写清：版本、宿主（DSH / Claude Code）、复现步骤、你看到的影响。
它只对维护者可见。

**不要在 public issue 里贴漏洞细节。** 不要贴 PoC、不要贴能一键复现的完整命令、
不要贴本机路径或数据样本。想先确认是不是漏洞，就开一个**不含细节**的 issue 问一句
「这个能不能走私密渠道」，然后走上面的 Advisory。

如果细节已经贴出去了，请尽快补一条 Advisory 并说明哪条已公开，我们好判断影响面。

## 我们承诺

- **7 天内首次响应**：收到后 7 天内至少回一句「收到了」+ 我们判断的严重程度
- 修好后在 Advisory 里致谢，除非你要求匿名

本项目没有官方邮箱，也不通过私信处理漏洞 —— 走上面的 Advisory，渠道才有记录。

## 受影响的面

先说结论：Crop **不上传任何数据**。没有服务端、没有账号、没有遥测接口，
所以**不存在云端数据面**——能泄露数据的只有本机这一侧。

### 1. 本机数据目录 `~/.agentcrop`

- **存什么**：结构化事件（时间戳、工具名、成功/失败、耗时、工作短名、一句话自述），
  以及周报。**不存对话正文**；但 `prompt` 事件会存你消息的**前 160 字符片段**
  （`core/probe_core.py` 里 `prompt[:160]`）——不要把密钥、密码写进提示词。
- **权限**：数据目录按 `0700` 创建（`core/probe_core.py` 里 `os.makedirs(..., mode=0o700)`）。
- **风险**：同机其他用户若能读目录，就能读到你的工作节奏、任务短名与自述片段；
  备份 / 同步工具（iCloud、Dropbox 之类）把家目录同步走，数据就出了本机。
- **建议**：确认 `ls -ld ~/.agentcrop` 是 `drwx------`；不要把 `~/.agentcrop` 放进任何同步目录。
- 数据位置可用 `KIT_DATA_DIR` 改。

### 2. 注入进 AI 系统提示的文本（提示注入面）

Crop 会往 AI 的上下文里写字，这是它最主要的能力，也是它最主要的风险：

- **DSH**：常驻系统提示段 `crop:protocol`（`dsh/lib/index.js`）；每回合首步一行工作状态；
  超红线时一行提醒
- **Claude Code**：hooks 注入额外上下文（`hooks/claude-hooks.json` → `core/probe_hook.py`）；
  MCP 工具 `probe_report`（`.mcp.json` → `core/probe_mcp_server.py`）

这些文本来自本机事件存储，只做 `strip` 与 **600 字符截断**（`core/probe_core.py` 的 `MAX_TEXT`），
**不做转义、不做语义过滤**。因此：

- 别在 `summary` / `next` / `task_id` 里粘不可信来源的内容（网页正文、别人给的文件、
  不可信仓库里的说明文字）——它们会原样回到 AI 的上下文里
- 谁能改仓库里的注入文本，谁就改变了 AI 行为；评审这类改动请当代码评审看，
  不要当文案改（见 [CONTRIBUTING.md](CONTRIBUTING.md) 的「影响面大」一节）

### 3. 面板：只监听 127.0.0.1

- 本地 dashboard 默认监听 `127.0.0.1:8765`（`core/crop_serve.py`）；面板客户端
  `dsh/lib/client.js` 也只连 `http://127.0.0.1:8765/api/session`
- `/api/*` 的响应头带 `Access-Control-Allow-Origin: *`。**已知限制**：这意味着你浏览器里
  打开的任何网页，都可能读到这些 JSON（工作短名、自述、耗时）。
  别在日常浏览的浏览器里长期开着面板；看完就关
- 面板**没有鉴权**。`crop serve --host 0.0.0.0` 会把它暴露到局域网，任何能连上的人都能读——
  不要这么用
- 面板只读：它不执行命令、不写数据目录以外的文件

### 4. 跨宿主适配脚本

- `core/probe_hook.py`（Claude Code 的 hooks 入口）、`core/probe_mcp_server.py`（MCP stdio 服务）、
  `.mcp.json` 与 `hooks/claude-hooks.json`（接线配置）
- 它们**以你的用户权限运行**，用 `python3` 直接执行插件目录下的脚本
- 设计约定是 **fail-open**：探针出错只影响它自己，不阻塞一次提问、不改变工具结果
- **风险**：能改仓库或插件安装目录的人，可以借 hook 在你的权限下执行命令。
  装之前请看一眼 `hooks/claude-hooks.json` 会跑哪几条命令；装完不要随手把安装目录交给别人写

## 威胁模型之外

- 不防「别人已经拿到你的用户权限」——那种情况下 `~/.agentcrop` 本来就在他手里
- 不防宿主 / 模型供应商本身的日志与缓存：Crop 不上传，但你和宿主的对话仍然经过宿主
- 不防你自己在提示词里写密钥（见上文 `prompt` 片段）
