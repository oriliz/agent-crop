# 探针开源与优化 · 技术方案（2026-11-15 周末冲刺）

> 目标：把「AI Coding 提效套件」从只在自己机器上跑的工具，变成陌生人 30 分钟内
> 能装起来、看得懂设计、看得见数据、愿意 star 的开源项目。
> 三条达标线：**设计逻辑清楚** / **能够使用** / **有可视化面板**。

## 1. 先对齐：这套设计到底在做什么

读源代码后的理解，五条设计初衷：

1. **只观察，不打断。** 探针默认启用且只采集；「方案」是干预，逐条开关、默认全关、
   全部标"未验证"。没开任何方案时，探针一条提示都不输出。
2. **数据不出本机。** 落 `~/.ai-coding-efficiency/data`（0700），不采对话正文、代码、凭证；
   跨人比对靠手工导出 jsonl，不做自动上传。
3. **一个 core，多个宿主。** 宿主只做事件翻译（Claude hooks / DSH plugin / Qoder / Codex），
   任务身份、计时、校验、存储全在 Python core。换宿主不换数据模型。
4. **以「任务块」为原子。** prompt 进入开始计时，turn 结束闭合；红线在块内触发，
   时长拆成 agent 执行 / 块内等你 / 块结束后你离开，避免把等人算成工具耗时。
5. **跨会话靠 open tasks 续接。** `task_id` 是主键，`phase=end` 才关闭；
   下次开工先注入"未完成任务"，让 AI 从上次离开处继续。

它现在解决的是：长任务失焦、串行等待、同类问题反复出现。
它**不**解决：设计反复、规格漂移这类"事后不可判定"的问题——那类只能靠开工前的
对齐门（alignment-gate）造证据，探针本身不猜原因。

这条边界必须写进 README，否则开源后会被当成"又一个 AI 监控工具"。

## 2. 现状体检

| 模块 | 规模 | 状态 | 上 GitHub 的问题 |
|---|---|---|---|
| `core/probe_core.py` | 579 行 | 稳定 | 数据模型没有 schema 文档 |
| 宿主适配 | DSH 255 行 + Claude MCP/hook + Qoder/Codex hooks | 4 宿主可接 | 四套安装方式，`$QKIT` 缓存路径靠 hack 定位 |
| 分析脚本 | 15 个 / 7150 行 | 能用 | 几乎零测试；`harness_scan.py` 1768 行、`daily_case.py` 1135 行 |
| 方案 `solutions/` | 6 个 | 试用中 / 未验证 | 状态散在各 `solution.json`，没有汇总视图 |
| 可视化 | 静态 14 天 HTML，零 JS | 能用 | 只读历史，没有"现在正在跑什么" |
| 工程基建 | — | **缺失** | 不是 git repo、无 LICENSE、无 CI、无 .gitignore |
| 文档 | README 277 行 | 自用视角 | 硬编码 `/Users/lizhi/...`；四个宿主安装混在正文；缺定位与截图 |

结论：**核心不弱，外壳不够。** 周末的工作量 80% 在"把已有的东西讲清楚 + 补外壳"，
不需要重写 core。

## 3. 三条达标线（验收清单）

### A. 设计逻辑梳理清楚

- [ ] `docs/DESIGN.md`：问题与边界（探针 vs 方案）、五条原则、一次 turn 的完整时序图
- [ ] `docs/DATA-MODEL.md`：`events.jsonl` / `tasks/open.json` / `reports/` / `blocks/` /
      `runtime/current.json` 的字段表 + 一条真实样例（脱敏）
- [ ] README 首屏：一句话定位 + 三张图（架构分层、一次 turn、面板截图）
- [ ] 单列一节「它不做什么」：不自动改代码、不上传、不评估人、不替代测试

### B. 能够使用（陌生人视角）

- [ ] 四个宿主各 5 行安装（DSH / Claude Code / Qoder / Codex），装完一条自检命令
- [ ] 首次运行 30 秒内出第一条数据，`status` 能自证装好了
- [ ] `tests/` 覆盖 core 的校验 / 计时 / 任务开关；CI 在 Python 3.10–3.13 跑绿
- [ ] fail-open 实测：core 抛异常时宿主 prompt/回合不被阻塞
- [ ] 升级不丢数据（数据目录在插件缓存之外，已满足，需写进文档）

### C. 可视化面板

- [ ] `probe serve` 一条命令起本地面板（127.0.0.1，零第三方依赖）
- [ ] 「现在」视图：当前块已跑多久 / 距红线 / 今日事件流 / open tasks
- [ ] 「趋势」视图：14 天任务块、工具族、失败率、黄线以上任务（复用 `harness_scan` 指标）
- [ ] 「问题类」视图：`problem_class` 的 finding + 干预前后同口径对比
- [ ] README 放截图 + 30 秒 GIF

## 4. 周末排期

假设：周五晚 2h、周六 8h、周日 8h，共 18h。

**周五 20:00–22:00**（3 项，必须当晚定）
1. 定名（§7），全局替换包名 / CLI / 数据目录别名（数据目录保留旧名做兼容）
2. `git init` + LICENSE(MIT) + .gitignore + 目录整理（core / hosts / analytics / web）
3. `gh repo create` 建**私有**仓库，首次 push（先不 public）

**周六**
4. 上午（3h）：`docs/DESIGN.md` + `docs/DATA-MODEL.md`（我起草、你审）
5. 下午（3h）：安装收敛成 `./install.sh --host dsh|claude|qoder|codex`，四宿主逐一实测
6. 傍晚（2h）：CI workflow + core 单测补齐

**周日**
7. 上午（4h）：面板 v1（`serve` + 三个视图）
8. 下午（2h）：README 重写 + 截图/GIF
9. 晚上（2h）：切 public、打 tag `v0.5.0`、写 release notes

**降级策略**（时间不够时按顺序砍）：
- 面板只做「现在」+「趋势」两屏，问题类留到 v0.6
- CI 先只跑冒烟，不做矩阵
- Qoder/Codex 安装文档写"实验性"，不阻塞发布

## 5. 可视化面板技术方案

- **零依赖**：Python 标准库 `http.server` + 单文件 HTML/JS（无构建、无 npm）
- **端口**：8765（避开 3080 / 3082）
- **接口**：`/api/summary`（当前块、今日计数、红线）、`/api/events?since=`、
  `/api/tasks`、`/api/trend?days=14`、`/api/findings`
- **刷新**：SSE 优先，退化到 2s 轮询
- **只读**：面板不提供方案开关（避免变成攻击面），开关仍在 CLI
- **复用**：把 `harness_scan` 的 metrics 计算抽成函数直接复用，不要再写一套统计
- **风险**：`harness_scan.py` 1768 行，先抽 200 行左右的 metrics 层再动手

## 6. 仓库结构与发布 checklist

```
<name>/
├── core/            # 零依赖 Python：数据模型、计时、校验（已存在）
├── hosts/           # dsh / claude / qoder / codex 四个适配器
├── analytics/       # 现 hooks/task-metrics/bin 的 15 个脚本
├── solutions/       # 6 个干预方案 + 开关
├── skills/          # 3 个 skill
├── web/             # 面板（新增）
├── docs/            # DESIGN / DATA-MODEL / 安装 / 隐私
├── tests/
├── .github/workflows/ci.yml
├── install.sh
├── LICENSE          # MIT
└── README.md
```

发布 checklist：LICENSE ✓ / README 首屏 ✓ / 四宿主安装实测 ✓ / CI 绿 ✓ /
隐私声明 ✓ / 版本 0.5.0 ✓ / release notes ✓ / topics 标签 ✓

## 7. 命名提案

判据：能表达"让 AI 看见自己哪里慢、哪里该进化"；短、能读、能做 CLI 命令；
包名在 npm / PyPI 可用。

**已查证**：`metacog`、`pacer`、`vitals`、`endoscope` 单词在 npm 与 PyPI 均已被占；
`metacog-probe`、`agent-metacog`、`coding-probe`、`selfprobe`、`harness-probe`
两边**都空着**。

| 提案 | 中文 | 含义 | 优点 | 风险 |
|---|---|---|---|---|
| **Metacog** | 元知 | 元认知：AI 对自己工作过程的认知 | 概念最准；`metacog-probe` 双平台可用 | 学术词，第一次读未必顺口 |
| **Pacer** | 配速 | 陪跑配速，管节奏不替跑 | 拟人、好记 | 单词被 TanStack 占，需换组合名 |
| **Vitals** | 体征 | 给 agent 做体检 | 看板/告警语义天然 | 偏医疗，单词被占 |
| **Endoscope** | 内窥 | 探针伸进运行过程中看 | 最贴"探针"的物理隐喻 | 偏长 |
| **Selfprobe** | 自探 | 自我探测 | 直白、双平台可用 | 略生硬 |

**推荐：Metacog / 元知。**
- repo：`<owner>/metacog`
- 发行包：`metacog-probe`（避免与占位包撞名）
- CLI：`metacog`（`metacog status` / `metacog serve` / `metacog enable <方案>`）
- 一句话：*Metacog 让 AI 对自己的工作过程有元认知——它知道自己跑了多久、
  在哪卡住、哪一类问题在反复，然后把这件事同时讲给 AI 和你听。*

备选：想更"产品化"选 **Pacer（配速）**；想更"诊断"选 **Vitals（体征）**。

## 8. 明确不做

- 不做云端、不做团队实时看板（跨人仍靠导出）
- 不做自动改代码（探针只观察；方案也只影响提示，不改用户仓库）
- 不在首版重写 core（只补文档、测试、外壳）
- 不把未验证方案设为默认

## 9. 需要拍板

1. 名字：Metacog / Pacer / Vitals / 其它
2. 仓库放个人号 `oriliz` 还是新建组织
3. LICENSE 用 MIT 还是 Apache-2.0
4. 面板先做「现在」还是先做「趋势」
5. 首版是否包含 Qoder / Codex（这两边采集还是半成品）
