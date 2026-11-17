# Changelog

Agent Crop（npm 包 `agent-crop-dsh`）的版本历史。结构参考 Keep a Changelog，
版本号见仓库根目录 `package.json` 的 `version`。最新在上。

条目是对真实提交的中文归纳，不是功能规划。

> **v0.4.0 及更早：未整理。** 那段时间是边写边改，提交里混着设计草稿与试验，
> 没有逐条回溯。要看那段历史，直接读 `git log`。

## [0.8.1] — 2026-11-17

### Added（新增）

- 改进台账：`crop_adjust`（record / ledger）、`crop ledger`、`/api/agent-crop/ledger`
  与面板「改进台账」卡；指标从事件日志按周派生，AI 只交声明与主观判断，不再自报数字（#29 / #30）
- 行为测试基线与实机记录：`tests/behavior/AI-BEHAVIOR-2026-11-17.md`，
  覆盖 0.8.0 之后的注入拆分、收尾、切工作、台账四个行为点（#34）

### Changed（变更）

- 收尾进入 120 秒冷静期：`end` 之后交付 / 汇报 / 截图不再触发「未绑定」提示，
  同一回合的收尾动作也不再计入 miss；没有绑定时不再催「上一轮没登记」（#28）
- 绑定提示改由 core 按「上一轮是否 end」生成，给出 side-issue 与新工作两条出路（#28）
- 提醒冷却跨档位共享，同一分钟不再同时出 yellow 和 overdue；改口后给一个
  `remind_min` 冷静窗口，改小预期不会立刻算超时（#28）
- 切工作时写 `work_switch` 事件，并在工具结果里警告旧工作还没收尾（#28）
- 首轮注入拆出「本会话 N 条 / 其他会话 M 条」，动作放在候选列表之前（#28）
- 重写常驻协议与工具描述：任务粒度、progress 节奏、改口语义、end 后动作（#28）
- 台账判定只用完整周：不满一周统一「观察中 / 暂无完整周」，避免部分周的一两天数据带偏结论（#31）

### Fixed（修复）

- dashboard 路由补上 Connection 的 Host / Origin 准入：handler 入口先过
  `ctx.connection.requestRejection`，401 / 403 直接拒绝且不读数据（#33）
- 开源文档去掉本地环境痕迹：worktree 约定改成条件式、dev-restart 泛化并注明 macOS、
  脱敏个人路径与内部代号（#32）

## [0.8.0] — 2026-11-17

### Added（新增）

- 会话页 Crop 改为时间轴面板：会话时钟（活跃/等待）、按工作分 lane 的运行条、
  拖拽平移 / Shift+滚轮 / 30m–全部缩放、窗口刻度；当前工作显示预估进度。
- 鞭策标记：奴隶主用鲸鱼娘被抽图标，其他风格用站姿思考的鲸鱼娘；lane 上显示该工作累计鞭策次数。
- 标记与反思双向联动：悬停互亮、点击滚动到对应反思行并闪烁；新数据用 `reply_to`
  精确配对，旧数据才退回最近匹配。
- 反思四列：序号 / 来源（被鞭策、主动提出、主动总结）/ 当时的问题 / AI 的改进；
  自驱上报挂鲸鱼娘奖章，状态分「要改 / 要想办法」。
- 鞭策风格四档：奴隶主 / 和蔼的前辈 / 经验丰富的同事 / 放任自由的家长；设置里可切换，不用重启。
- 全局面板：AI 自述的改进改成带圆点、胶囊标签、时间的列表；日详情工作行加圆点、占比条、时长；
  工作名悬停显示完整文本。
- 枚举值统一中文：verdict / category / type / difficulty / rework / sentiment / level。
- `session_summary` 增加 `timeline` / `stats` / `interventions` / `work_titles` / `reflection` / `improvements`。

### Changed（变更）

- 反思判定从「合理/不合理」改成「不用改 / 要改 / 要想办法」，并给「要改」加红色紧迫样式。
- 时间窗口过滤补齐上下界；右侧反思列表跟随时间轴窗口。
- 工作名默认截到 20–24 字，完整名走 tooltip。

### Fixed（修复）

- `tool` 事件带 `work_id`，`tool_churn` 同步（Issue #15）。
- `phase=end` 强制 `what`（Issue #16）；健康度「收尾小结」必需字段收窄为 `what`。
- 会话页 `max-width` 卡在 720px 导致右侧大片空白；顶部卡片收进左栏、反思独占右栏。
- 改进列表 CSS 未插入导致样式丢失；改进文本不再溢出卡片。
- 全局面板性能：单次汇总 2.4s → 0.21s（事件解析缓存 + bisect 对齐）。

## [0.7.0] — 2026-11-16

### Added（新增）

- 全局面板新增「健康度」：按工具看**采集器本身有没有在干活**。事件分四类口径
  （会话 / 工作 / 调用 / 按需），覆盖按各自锚点算（会话数、工作数、收尾工作数），
  按需事件只报计数不亮灯；环形图看健康构成，两张表按必采/按需/未上线分组，
  覆盖与闭环是彩色条（红条行加红底），趋势为每日条数小柱图；底部诊断每条带看板卡，
  可「复制修复指令」。`core.health_summary()` 计算，`/api/tool-health` 暴露。
- `crop health` 命令（`--days` / `--host` / `--json`），以及 DSH / MCP 的 `crop_health` 工具。
- 报告新增 `artifact` 字段（这次产出了什么），随 `work_summary` 与 reports 落盘。
- 提醒文案带用户可见例句（warm / yellow / red / overdue），开工提示附一段「说话方式」。
- 仓库工程化：`scripts/check.sh` 推前检查、`AGENTS.md`、`docs/WORKFLOW.md`。

### Changed（变更）

- 工具命名统一为 `crop_update`（MCP 原 `probe_report`、DSH 原 `crop_report`），
  旧名保留为调用别名；MCP 服务名 `probe` → `crop`。
- 报告 schema 对齐：DSH 侧补 `what` / `cost` / `next_time` / `artifact`；
  MCP 侧补 `expect_min` / `verdict` / `improve` / `goal` / `type` / `difficulty` /
  `rework` / `sentiment` / `what` / `cost` / `next_time` / `artifact`。
- `session_end` 改为按宿主口径统计：只有 claude-code 有 SessionEnd 钩子，
  DSH 会话不再被算进分母、也不会把这一行拖红。

### Fixed（修复）

- `work_claim` 现在在 `crop_update(phase="start")` 时写入：之前只有 `crop switch` 会记。
- 时间归属只认本会话的绑定，不再串到别的会话的唯一 active 工作（卡 747c57d4）。
- 台账工具（`crop_work` / `crop_review` / `crop_works` / `crop_health` / …）不再被当成
  「干活」，不会误触发「你还没有绑定任何工作」（卡 815ba0e4）。
- MCP 报告 schema 与 DSH 漂移导致 `expect_min` 等字段被拒收（Issue #11）。

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
