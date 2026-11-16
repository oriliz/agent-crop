# 参与贡献

Agent Crop 是给 AI 编程助手用的本地探针：只观察、只记在你这台机器上、不改别人的代码。
贡献也守同一条边界——小步、可验证、不偷偷扩大采集面。

> English: one PR, one thing. Run the three test commands below before you push.

---

## 30 秒跑起来

```bash
git clone https://github.com/oriliz/agent-crop.git ~/agent-crop
cd ~/agent-crop
python3 -m unittest discover -s tests -t .
node --test dsh/test/index.test.mjs
bash tests/probe-smoke.sh
python3 bin/crop status      # 把 bin/crop 放进 PATH 之后就是 crop status
```

预期结果：

| 命令 | 应该看到 |
|---|---|
| `python3 -m unittest discover -s tests -t .` | `Ran 25 tests` … `OK` |
| `node --test dsh/test/index.test.mjs` | `pass 7` / `fail 0` |
| `bash tests/probe-smoke.sh` | `PASS: probe smoke test` |
| `python3 bin/crop status` | 当前块、今日事件、进行中的工作；退出码 0 |

环境要求：

- **Python >= 3.10** —— 源码用了 PEP604 联合类型（`X | None`，见 `core/probe_core.py`），3.9 直接语法错
- **Node >= 22.19** —— 见 `package.json` 的 `engines`
- **不需要装依赖** —— 仓库当前零依赖：Python 只用标准库，Node 只用内置模块，`node --test` 直接能跑。不要为了跑测试去 `npm install` 或 `pip install`

把 `crop` 放进 PATH（可选，方便直接敲 `crop status`）：

```bash
ln -sf ~/agent-crop/bin/crop /usr/local/bin/crop
```

---

## 分支与 PR

`main` 是受保护分支：不接受直接 push，所有改动走 PR。

```bash
git switch main && git pull
git switch -c fix/status-line-claim
# 改代码 → 跑那三条命令 → 提交
git push -u origin fix/status-line-claim
```

- **一个 PR 一件事。** 顺手重构请另开一个 PR，评审时好判断
- **PR 必须 CI 绿。** `.github/workflows/ci.yml` 跑的就是你本地那三条：Python 3.10 / 3.12 / 3.13 的 `unittest`、Node 22 / 24 的 `node --test`，再加 smoke 与 `npm pack` 打包清单断言。本地绿了 CI 一般不会挂
- **行为变了就补测试。** Python 侧进 `tests/test_crop_cli.py`，Node 侧进 `dsh/test/index.test.mjs`
- **PR 描述写清两件事：** 为什么改（问题是什么）、怎么验证的（跑了什么、看到什么）。不用贴长日志
- 文档改动同样走 PR，但只需要 `docs` 前缀，不必凑测试

---

## 提交信息

沿用仓库既有风格：`<前缀>(<范围>): <中文一句话>`。前缀用小写英文，正文用中文。

真实例子（都来自本仓库历史）：

- `feat(core): 认领带有效期，重启/fork 后单条活跃工作可直接继承`
- `fix(core): 暂停的工作不再出现在进行中列表`
- `docs(readme): 卸载命令改用新包名 agent-crop-dsh`
- `test(core): 锁住「认领后不再提示认领」的行为`
- `chore: release v0.5.1（状态行候选+认领、任务排序、跨零点测试修复）`

常用前缀：

| 前缀 | 什么时候用 |
|---|---|
| `feat` | 新增能力：工具、CLI 子命令、面板、注入行为 |
| `fix` | 修 bug |
| `docs` | 只动文档 |
| `test` | 只动测试 |
| `refactor` | 不改行为的结构调整 |
| `chore` | 发版、构建、杂务 |

括号里的范围可以省，常见的是模块名：`core`、`cli`、`dsh`、`panel`、`readme`。
纯英文提交也出现过（比如一次插件改名的 refactor），但正文仍以中文为准。

---

## 什么改动算「影响面大」

下面四类会牵动**别人机器上的行为**。动之前先在 issue 里把方案对齐，对不齐就先别写。

### 1. 事件数据格式

指的是 `events.jsonl` 里的字段与取值：schema、枚举、公共字段。代码在 `core/probe_core.py`，
文档在 `docs/EVENTS.md`（v0 已采事件 + v1 新增事件）。

- 已有事件不改语义，只加字段；加字段要能容忍历史数据里没这个字段
- 枚举（如 `category` 九类 + `side-issue`）锁死，不是自由文本
- 改字段要同时想清楚：老数据怎么办、`crop status` 和面板怎么读

### 2. 注入给 AI 的协议文本

这是直接进 AI 上下文的字，改一句话就改变所有用户机器上 AI 的行为，还要算 token。

- DSH 侧：常驻系统提示段 `crop:protocol`，在 `dsh/lib/index.js`；每回合首步一行工作状态、超红线一行提醒
- Claude 侧：hooks 注入（`hooks/claude-hooks.json` → `core/probe_hook.py`）与 MCP 工具 `crop`（`.mcp.json` → `core/probe_mcp_server.py`）

### 3. 面板客户端

`dsh/lib/client.js` 是 DSH 客户端半侧（右栏 Crop 标签页），和布局、状态存档绑在一起。
改它请配截图说明改前改后。

### 4. 安装方式

`package.json` 的 `files` / `dsh.bundle` / `dsh.client` 字段、`dsh/cordis.patch.yml`、`.mcp.json`、
`.claude-plugin/plugin.json`、`hooks/claude-hooks.json`。装不上的事故比功能 bug 贵得多，
所以 CI 里有一条 `npm pack` 清单断言兜着。

### 不在讨论范围的东西

产品边界是冻结的：**只本地观察、数据不出本机（`~/.agentcrop`）、不自动改代码、不评估人、不替代测试**。
任何引入上传、打分排名、自动改写代码的 PR 都不合，不用先开 issue 讨论。

---

## 新增依赖

现在这套东西是**零依赖**的，这不是省事，是设计：探针要挂在每一轮对话的路径上，
它不能在别人的机器上因为装包失败而不工作。

想加依赖，PR 里必须写清：

- 为什么不加就做不了（标准库 / Node 内置模块到底差在哪）
- 装不上的后果：hook 路径必须 **fail-open** —— 探针坏掉不能挡住用户的一次提问或一轮对话
- 体积、许可证、是否联网、是否要编译
- 谁会真的走到这段代码

`core/probe_hook.py` 这条每回合都跑的路径不接受第三方依赖。

---

## 不要提交的数据

`.gitignore` 已经拦掉了常见的，但截图和粘贴管不住：

- **`~/.agentcrop/data/` 下的真实数据**（`events.jsonl`、`reports/`）—— 那是你的工作记录，不是样例
- **截图与日志里的隐私**：带用户名的家目录路径、内网域名、仓库名、token、会话原文
- **`config/kit.env`**（本机覆盖配置；要举例用仓库里的 `config/kit.env.example`）

需要举例时用虚构的短名，比如 `task_id="refactor-auth"`，别贴真实内容。

---

## 在哪问什么

| 你想干的事 | 去哪 |
| --- | --- |
| 装不上、跑不起来 | [开 install_help issue](https://github.com/oriliz/agent-crop/issues/new?template=install_help.yml)（表单会引导你贴 `crop status`） |
| 行为不对、报 bug | [开 bug issue](https://github.com/oriliz/agent-crop/issues/new?template=bug_report.yml) |
| 这东西怎么用 / 我的场景适配吗 | [Discussions · Q&A](https://github.com/oriliz/agent-crop/discussions/new?category=q-a) |
| 有个还没想清楚的方向 | [Discussions · Ideas](https://github.com/oriliz/agent-crop/discussions/new?category=ideas) |
| 安全漏洞 | [私密 Security Advisory](https://github.com/oriliz/agent-crop/security/advisories/new)，**不要开 public issue** |

维护者的响应节奏：装不上类优先复现；enhancement 不承诺实现，但会给「收进 backlog / 不做，原因是 X」的答复。

## 开 issue 之前

- **装不上**：给 `dsh --version`、`node --version`、`python3 --version`，别贴整段日志
- **报 bug**：最小复现——哪条命令、哪一步、期望什么、实际什么
- **提问**：直接用 `question`；安装相关用 `install`；宿主相关用 `host:dsh` / `host:claude` / `host:codex`
- **想找活干**：看 `good first issue`
- **安全漏洞**：**不要开 public issue**，走 [SECURITY.md](SECURITY.md) 里的私密渠道

---

## 相关文档

- [SECURITY.md](SECURITY.md) —— 漏洞怎么私密上报
- [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) —— 社区行为准则
- [CHANGELOG.md](CHANGELOG.md) —— 版本历史
- [docs/DESIGN.md](docs/DESIGN.md) —— 设计原则与边界
- [docs/EVENTS.md](docs/EVENTS.md) —— 采什么事件、怎么存、谁在用
- [docs/SESSION-WORK.md](docs/SESSION-WORK.md) —— 会话与工作怎么绑定、失效了怎么办
- [docs/INSTALL.md](docs/INSTALL.md) —— 各宿主的安装与卸载
- [docs/dual-host.md](docs/dual-host.md) —— 双宿主并存
