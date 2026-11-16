# Changelog

Agent Crop（npm 包 `agent-crop-dsh`）的版本历史。结构参考 Keep a Changelog，
版本号见仓库根目录 `package.json` 的 `version`。最新在上。

条目是对真实提交的中文归纳，不是功能规划。

> **v0.4.0 及更早：未整理。** 那段时间是边写边改，提交里混着设计草稿与试验，
> 没有逐条回溯。要看那段历史，直接读 `git log`。

## [Unreleased]

### Changed（变更）

- MCP 服务名从 `probe` 改为 `crop`。多个 MCP 同时挂载时，`probe` 和产品名对不上，
  看不出它属于 Agent Crop。MCP `serverInfo.name` 同步改为 `crop`。
- MCP 工具名从 `probe_report` 改为通用名 `crop`：这个工具后面还会承载工作生命周期、
  改进记录等内容，叫 `*_report` 会越用越不对。旧名 `probe_report` 保留为**调用别名**
  （`tools/list` 只暴露 `crop`），旧会话/旧配置仍能调用。
  Claude Code 侧工具名变为 `mcp__plugin_agent-crop_crop__crop`。
  **迁移**：升级后重载 Claude Code 插件（重启会话生效）；如果有按旧服务名或旧工具名
  写的权限白名单（`mcp__plugin_agent-crop_probe*`），需要同步改名。

## [0.6.0] — 2026-11-15

> 发版口径：**不打碎版本**。0.5.x 之后不再逐个打点，这条线一直累积到项目真正可用，
> 那时才发 `1.0.0`。本条是第一个发布到 npm 的版本，之前的改动都算 0.6.0。

### Added（新增）

- 发布到 npm：`dsh plugin --profile <profile> add agent-crop-dsh` 直接装预构建产物，
  不再需要先 `git clone`，也不需要 pnpm 的 `allowBuilds` 放行
- 提醒改按「合同」抽鞭子：`crop_report` 新增 `expect_min` / `expect_why`，AI 开工先报
  预估工时，提醒以它为时钟（0.8× 预告、1.0× 抽、1.5× 翻账本、2.5× 焦躁），在它自己
  报的范围内完全不打扰；没报预期仍按 10 / 30 分钟兜底，并催它报数字
- 文案池 `core/crop_voice.py`：六个档位（warm / yellow / red / overdue / miss /
  miss_streak）各 4–6 条「牛马庄园」口味句，洗牌袋轮换、短期不重复；动作句固定，
  随机只发生在口味上。缺报提醒从 DSH 插件迁进池子（新 core op `miss_nudge`）
- 改报价允许且记账：带新 `expect_min` + 理由即改口，落 `estimate_revised`，
  第 2 次起文案点名；不阻断、不设上限
- `docs/VOICE.md`：档位表、报预期/改报价流程、文案池占位符与轮换机制

### Changed（变更）

- 打开源仓库基础设施：CI（python 3.10/3.12/3.13、node 22/24、冒烟、npm 打包清单断言）、
  tag 触发的发版流水线、issue/PR 模板、社区文档（CONTRIBUTING / SECURITY / CODE_OF_CONDUCT）
- 打包元数据补齐 `bin`：在此之前 `npm pack` 漏掉 `bin/crop`，按 npm 装法拿不到 `crop` 命令
- 发版流水线加人工审批门：`npm publish` 与 GitHub Release 都必须经维护者批准后才执行
- 兼容声明改用 DSH 现行规则：新增 `engines.dsh: ">=0.2.1 <0.3.0"`，移除已不被读取的
  `dsh.compatibility`；安装与 profile 启动会按这个范围拒绝不兼容的 DSH
- 安装文档改为 npm 优先（README / docs/INSTALL.md），源码安装降级为开发路径

### Fixed（修复）

- npm 安装后 `crop` 命令直接崩：`bin/crop` 用 `abspath(__file__)` 推包根，经
  `node_modules/.bin/crop` 符号链接执行时解析成 `node_modules/`，`import probe_core` 失败。
  改用 `realpath` 后按真实包路径找 `core/`
- 面板 `/api/*` 的 `Access-Control-Allow-Origin: *` 改为只对回环 Origin 回显：此前
  用户浏览器里任何网页都能跨源读到工作时间线（工作短名、自述、耗时），一个 GET 就够。
  非回环绑定时另打印警告
- Python 测试改用 `unittest discover`，新增面板 CORS 回归用例（5 条，覆盖回环/外来 Origin、
  无 Origin 头、三个端点的行为一致性）
- 冒烟测试的断言还停在改名前的 `[探针]` / `probe_report`，脚本实际一直是红的；
  改回 `[crop]` / `crop_report` 并跑通

### Removed（移除）

- `tests/behavior/TRACE-2026-11-15.md`：一次被污染的真实会话追踪，含真实提示与本地路径，
  不适合放在公开仓库

---

## [Unreleased]

新改动先写在这里，发版时再折叠进对应版本。

---

## [0.5.8] - 2026-11-15

### Added（新增）

- 支线改走 `probe_report(category=side-issue)`：撞见别的问题记一笔就走，不再单独开口子
- 协议里明确禁止「顺手新建任务」与「现场排查」：别把当前工作晾在一边去修别的

### Changed（变更）

- 面板补齐工作健康度（基线 + 指示灯）、会话内工作清单与 AI 归因，按既定布局落地

### Tests（测试）

- 补真实 DSH 实例的行为测试报告（S1 部分通过 / S2 失败）

## [0.5.7] - 2026-11-15

### Changed（变更）

- 开工提示改为「跟最近活跃的那条；都对不上就新建」，不再按旧规则硬猜该挂到哪条工作

## [0.5.6] - 2026-11-15

### Added（新增）

- **开工绑定**：会话与工作怎么绑定、怎么解绑、失效了怎么自愈，写进 `docs/SESSION-WORK.md`
- **支线记录**：干活时撞见的别的问题单独记一条，不打断当前对话
- 真实 DSH 实例的行为测试报告

### Changed（变更）

- README 三张图统一改成 Markdown 相对路径，避开 HTML `img` 在部分预览器里显示成字符串
- README 删掉重复的 Trace 对比表，流程一节改名为「Crop 是如何工作的」

### Docs（文档）

- 新增会话 / 工作 / 事件 / 运行时类图

## [0.5.5] - 2026-11-15

### Added（新增）

- `crop switch <work_id>`：只把当前会话的焦点切到某条工作，不改它的状态

### Changed（变更）

- README 结尾图换成推磨杆版本，修掉脖子套绳的物理错误

## [0.5.4] - 2026-11-15

### Fixed（修复）

- 挂起的（暂停的）工作不再出现在「进行中」列表里

### Changed（变更）

- README 精简：合并为案例分析、安装前移，336 行砍到 266 行
- README 成本说明改成三条：少量 token、不重读对话、不拖时间
- README 去掉未完善的截图，改用 mermaid 流程图
- README 删掉诚实边界 / 常见问题 / 命名由来三节，结尾改为口号 + 拉磨图

## [0.5.3] - 2026-11-15

### Added（新增）

- 认领带有效期：重启或 fork 之后，如果只有一条活跃工作，可以直接继承，不用重新认领

## [0.5.2] - 2026-11-15

### Fixed（修复）

- 上报时同步 runtime 的会话 ID，修掉 fork 之后会话语义漂移的问题

## [0.5.1] - 2026-11-15

### Added（新增）

- 状态行改为**候选列表**：由 AI 认领当前工作，不再由程序直接指定

### Fixed（修复）

- 任务排序改成「会话 > 工作区 > 最近活动」，修掉状态行总显示最旧任务的问题
- DSH 测试改用本地日期而不是 UTC，修掉跨零点必挂的测试

### Changed（变更）

- DSH 插件改名为 `agent-crop-dsh`（包名、cordis id、客户端 bundle、notice 来源、MCP server 名一起改）
- README 文案：去掉 kebab-case 行话、AI 上报字段补齐枚举值、采集分层展开、
  免责声明改成人话、「30 秒」标明是单个动作的耗时

### Removed（移除）

- 移除仓库里的本地样本数据与含真实数据的截图 / 复盘，README 改为方法论优先

## [0.5.0] - 2026-11-14

### Added（新增）

- **面板大改**：Crop 视图改成 lap-board 设计（计时 + 分段 + 结论），会话小结里带最近的分段
- Crop 进入会话视图的标签条（挨着「轨迹」），改成会话级设计：时间切分、异常、一句话结论
- 右侧栏 Crop 标签页，数字实时刷新
- 本地 dashboard：区间选择（7 / 14 / 30 天）、按类型对比表、按天表格与下钻、返工信号
- 工作状态机、`work_summary`、周期报告
- 工作字段（`goal` / `type` / `difficulty`）、回合首行状态、上下文压缩后重注入
- `user_wait` / `idle_gap` 检测，按工作时段（work-hours）判断
- 注册 `crop:protocol` 系统提示段（effect scoped，卸载时干净移除）
- `crop status` CLI：读事件存储，打印当前块、今日事件、进行中的工作

### Changed（变更）

- 弃用关键词式「完成」检测（一个会话里误判 4 次），完成与否交给模型侧判断
- 提醒改走系统提示段，不再改用户的 `AGENTS.md`

### Docs（文档）

- 新增 README / `docs/INSTALL.md` / `docs/DESIGN.md` / `docs/WEEKLY.md` 与 MIT 许可证
- 补 dashboard 截图与 PATH 说明，双宿主路径去掉个人化痕迹
- 新增 Crop trace 事件模型文档（v0 / v1 事件、存储、消费者）
