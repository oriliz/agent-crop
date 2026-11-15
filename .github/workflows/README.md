# CI / Release workflow 说明

本目录两个 workflow，全部只使用官方 GitHub Actions，没有第三方 action，也不做任何依赖安装（本仓库是零依赖：Python 侧只用标准库，Node 侧只用内置模块）。

| 文件 | 名字 | 触发 | 做什么 |
| --- | --- | --- | --- |
| `ci.yml` | CI | `pull_request`、`push` 到 `main` | 跑三条测试 + 断言 npm 打包清单 |
| `release.yml` | Release | 仅 `push` tag `v*` | 先重跑三条测试，再 `npm publish --provenance`，最后建 GitHub Release |

## ci.yml 的四个 job

| job | 内容 | 矩阵 |
| --- | --- | --- |
| `test-python` | `python3 -m unittest tests.test_crop_cli -v` | python 3.10 / 3.12 / 3.13 |
| `test-node` | `node --test dsh/test/index.test.mjs` | node 22 / 24 |
| `smoke` | `bash tests/probe-smoke.sh`（needs 前两个 job） | — |
| `package` | `npm pack --dry-run --json` + 清单断言 | — |

python 最低 3.10 是因为源码用 PEP604 联合类型（`X | None`）；node 矩阵与 `package.json` 的 `engines.node >= 22.19` 对齐。

`package` job 的断言（失败即 job 红）：

- 必须包含 `bin/crop`、`dsh/lib/index.js`、`dsh/lib/client.js`、`core/probe_core.py`
- 不得出现 `__pycache__` 或 `.pyc`

其余 job 都是单点命令，矩阵只扩版本、不改内容；同一 ref 的新推送会取消进行中的旧跑（`concurrency.cancel-in-progress: true`），顶层权限只有 `contents: read`。

## release.yml

1. `verify`：原样重跑上面三条测试命令，任一失败则后续 job 不会启动，等于不发版。
2. `publish`：`setup-node` 配 `registry-url: https://registry.npmjs.org`；先 `npm pack --dry-run` 自检，再 `npm publish --provenance --access public`。
3. 发布成功后 `gh release create <tag> --title <tag> --generate-notes`，Release 正文用 GitHub 自动 changelog，不手写内容。

权限：`contents: write`（建 Release）+ `id-token: write`（provenance 签名）。只在 tag 上触发，PR 与分支 push 都不会进这条流水线。

## 人工审批门（发布必须人批准）

`publish` job 绑定 GitHub Environment `npm-publish`，该 environment 配了 **Required reviewers**
（仓库 owner）。效果：

- tag 推上来后 `verify` 先跑，跑绿也**不会自动发布**
- `publish` 停在 "Waiting for approval"，直到维护者在 Actions 页面点 Approve
- 拒绝或超时 = 不发布；`npm publish` 与 GitHub Release 在同一个 job 里，因此一次批准才同时生效

配置入口：仓库 Settings → Environments → `npm-publish` → Required reviewers。
审批记录留在 Actions 的运行日志里，可追溯。

## 需要配置的 secret

| secret | 谁提供 | 用在哪 | 说明 |
| --- | --- | --- | --- |
| `NPM_TOKEN` | 仓库自己配 | release.yml `publish` | npm **Automation** token（具备 publish 权限）。配置位置：Settings → Secrets and variables → Actions → New repository secret |
| `GITHUB_TOKEN` | Actions 自动注入 | release.yml `publish` | 不需要手工创建；workflow 已声明 `contents: write` 供 `gh release create` 使用 |

`NPM_TOKEN` 缺失时的行为是**显式失败**：`publish` job 第一步会检查变量是否为空，为空则打印可读原因并 `exit 1`，不会静默跳过、也不会让发布悄悄不发生。这一步只打印 token 长度，不打印内容。

## 本地等价复现

在仓库根目录（Python >= 3.10、Node >= 22.19）：

```bash
# 1) Python 单测（20 tests）
python3 -m unittest tests.test_crop_cli -v

# 2) Node 测试（7 pass，零 npm 依赖）
node --test dsh/test/index.test.mjs

# 3) 探针冒烟（用临时目录，不碰真实数据）
bash tests/probe-smoke.sh

# 4) 打包清单断言（与 package job 完全一致的两条命令）
npm pack --dry-run --json > pack.json
node -e 'const files = JSON.parse(require("fs").readFileSync("pack.json","utf8"))[0].files.map((f)=>f.path); console.log(files.length, files.includes("bin/crop"), files.filter((p)=>/__pycache__|\.pyc$/.test(p)));'
rm -f pack.json
```

注意：本地跑第 4 步会在仓库根目录留下 `pack.json`，跑完删掉即可；CI 里它是 job 内临时文件，不会入库。

发布流程的本地等价（不要真的发版时跳过前置）：

```bash
npm pack --dry-run          # publish 前的自检
npm whoami                  # 确认 registry 登录状态（CI 里用 NPM_TOKEN）
```

不要本地 `npm publish` 验证：真正的发布只在 tag 触发的 `release.yml` 里发生。

## 未在 CI 里做的事

- 不做 `npm install` / `pip install`：本仓库零依赖，装了反而掩盖"偷偷引入依赖"的问题。
- 不跑第三方 action（如缓存、lint 聚合器）：需要时另开 PR 讨论。
- 发布版本号与 tag 的一致性由发版流程人工保证，本流水线不做强校验。

## 产品边界

Agent Crop 只本地观察，数据不出本机（`~/.agentcrop`），不自动改代码，不评估人，不替代测试。CI 同样只跑测试与打包自检，不上传任何本地数据。
