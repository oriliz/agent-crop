# 数据模型与存储设计

> 状态：已评审（决策记录见文末）
> 原则：语义层稳定（Work 是第一公民），存储层用 SQLite 落地；原始事实入库、结论可重建、配置/模板留文件。

## 1. 问题：现有存储撑不住

```text
~/.agentcrop/data/
├── events.jsonl      # 全部事件，单文件追加（无索引、无 schema 版本）
├── blocks/           # 回合时间块（按天分文件）
├── reports/          # AI 上报（按天分文件）
├── tasks/            # open Work（单 JSON 覆盖写）
├── closed/           # closed Work（按天分文件）
├── runtime/          # 当前会话/block（覆盖写）
└── bindings.json     # 会话→Work 绑定（覆盖写）
```

| 维度 | 问题 |
|---|---|
| 查询 | 单文件线性扫描；面板每次全量解析，派生指标现算 |
| 性能 | 数据量增长即劣化；面板轮询 + 每次 spawn python + 全量读 |
| 一致性 | `tasks/` 与 `closed/` 状态双份；覆盖写无事务 |
| 崩溃安全 | JSONL 半行、覆盖写损坏；无 WAL / 无自愈 |
| 并发 | 多进程写靠 flock 串行；无 busy timeout |
| 演进 | 无 schema 版本；字段靠 `task_id or work_id` 到处兼容 |

## 2. 决策记录

| 决策 | 结论 |
|---|---|
| 存储选型 | **SQLite（WAL）**，Python 3 内置，无外部进程 |
| 第一公民 | **Work**，唯一标识 `work_id`；`task_id` 降级为历史别名 |
| report 表 | **不建**；高频字段用 event 的 generated columns + `report` 视图 |
| 配置/模板 | **留文件**（`settings.json` / `templates/`） |
| 迁移 | **一次性切换** + 破坏性变更公告，可回滚 |
| 数据服务 | 继续 **spawn python**（本地应用不管理常驻进程） |
| event 字段 | `json_extract` 生成 STORED 列 + 索引，payload 唯一写入点 |
| block 归属 | **不物化归属**：只存会话时间边界，归属完全派生（M1 方案 b） |
| 生成列 | 用 **VIRTUAL**（不是 STORED）：查询同级、省存储/写计算（A） |
| 幂等 | `idem_key` 不加 UNIQUE；事实日志无条件接受，去重放写入层（B/M4） |
| work 演进 | 每个可变字段对应一条 `work_update` 事件，保证可重建（C） |
| 状态维度 | `state`（open/suspended/closed）与 `confirmed`（0/1）拆开（M3） |
| 预估改口 | 独立 `estimate` 时间序列表 + work 物化 expect_first/final（M5） |
| 子代理 | `work.parent_work_id`，总耗时用递归 CTE（M6） |
| 未归因时间 | 显式归到 session，面板单列「未归因时间」，不报红（M2） |
| 迁移时机 | **下次会话启动时**执行 + 前后各打一条 event 留断点（评审 D/G） |

## 3. 实体与关系

```text
Session 1 ──< Block（回合时间块）
Session 1 ──1 Binding ──> Work（当前跟随，可 ended）
Work 1 ──< Event（唯一事实源，append-only）
Work 1 ──< Summary / Sediment / Adjustment（结论，可重建）
```

- Work 生命周期：`open → suspended ↔ open → pending_confirm → closed`（人类确认才关闭）；
- Work 唯一性：短名全局唯一，创建时冲突检测（同名自动 `-2` 后缀），`UNIQUE(workspace, work_id)` 兜底；
- 派生（活跃分钟、提醒次数、健康度、台账）全部从 event 推导，不存冗余字段。

## 4. 表结构

```sql
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;
PRAGMA busy_timeout=5000;
PRAGMA foreign_keys=ON;

CREATE TABLE work (
  work_id TEXT PRIMARY KEY, goal TEXT, type TEXT, difficulty TEXT,
  expect_min REAL, expect_why TEXT,
  state TEXT NOT NULL DEFAULT 'open',   -- open|suspended|closed
  confirmed INTEGER NOT NULL DEFAULT 0, -- 0=等人类确认, 1=已确认
  parent_work_id TEXT,                  -- 子代理/子工作（M6）
  expect_first REAL, expect_final REAL, -- 从 estimate 物化，供快查（M5）
  workspace TEXT, created_at INTEGER, updated_at INTEGER,
  pending_at INTEGER, closed_at INTEGER, confirmed_by TEXT
);

CREATE TABLE event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL, host TEXT NOT NULL, session_id TEXT,
  work_id TEXT REFERENCES work(work_id), kind TEXT NOT NULL,
  phase TEXT GENERATED ALWAYS AS (json_extract(payload,'$.phase')) VIRTUAL,
  summary TEXT GENERATED ALWAYS AS (json_extract(payload,'$.summary')) VIRTUAL,
  cause TEXT GENERATED ALWAYS AS (json_extract(payload,'$.cause')) VIRTUAL,
  category TEXT GENERATED ALWAYS AS (json_extract(payload,'$.category')) VIRTUAL,
  minutes REAL GENERATED ALWAYS AS (json_extract(payload,'$.minutes')) VIRTUAL,
  origin TEXT GENERATED ALWAYS AS (json_extract(payload,'$.origin')) VIRTUAL,
  payload TEXT NOT NULL DEFAULT '{}',
  schema_ver INTEGER NOT NULL DEFAULT 2,
  idem_key TEXT                        -- 普通索引；事实日志不设 UNIQUE（M4）
);
CREATE INDEX idx_event_idem ON event(idem_key);
-- 部分索引：健康度「缺 work_id」类查询（E）
CREATE INDEX idx_tool_noid ON event(ts) WHERE kind='tool' AND work_id IS NULL;
CREATE INDEX idx_event_ts      ON event(ts);
CREATE INDEX idx_event_work_ts ON event(work_id, ts);
CREATE INDEX idx_event_sid_ts  ON event(session_id, ts);
CREATE INDEX idx_event_kind_ts ON event(kind, ts);

CREATE TABLE block (
  block_start INTEGER NOT NULL, session_id TEXT NOT NULL,
  minutes REAL NOT NULL DEFAULT 0,
  tools INTEGER, failed INTEGER, started_at INTEGER, ended_at INTEGER,
  PRIMARY KEY (session_id, block_start)
);
-- 归属不物化：由 work_claim / work_switch 事件按时间点派生（M1 方案 b）

CREATE TABLE binding (
  session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active',  -- active|ended
  switched_at INTEGER, ended_at INTEGER, updated_at INTEGER
);

CREATE TABLE session (
  session_id TEXT PRIMARY KEY, host TEXT, workspace TEXT,
  started_at INTEGER NOT NULL, ended_at INTEGER
);

CREATE TABLE estimate (              -- 预估改口时间序列（M5）
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  work_id TEXT NOT NULL, from_min REAL, to_min REAL,
  at INTEGER NOT NULL, reason TEXT, elapsed_min REAL
);
CREATE INDEX idx_estimate_work ON estimate(work_id, at);

CREATE TABLE work_summary (
  work_id TEXT PRIMARY KEY REFERENCES work(work_id),
  what TEXT, cost TEXT, next_time TEXT, artifact TEXT, ts INTEGER
);
CREATE TABLE work_sediment (
  id INTEGER PRIMARY KEY AUTOINCREMENT, work_id TEXT, sediment TEXT NOT NULL, ts INTEGER
);
CREATE TABLE adjustment (
  id INTEGER PRIMARY KEY AUTOINCREMENT, work_id TEXT, metric TEXT NOT NULL,
  before REAL, after REAL, evidence TEXT, note TEXT, ts INTEGER
);
```

## 5. 字段设计（为什么存在）

### work

| 字段 | 必要性 | 说明 |
|---|---|---|
| `work_id` | 必须有 | 第一公民唯一标识；跨会话/跨天/跨迭代不变 |
| `goal` | 必须有 | 一句话目标；面板/复盘上下文 |
| `type` / `difficulty` | 建议/可选 | 不同工作类型的耗时基线不同 |
| `expect_min` / `expect_why` | 可选 | AI 自报预估与依据；改期历史在事件里，不在本表 |
| `state` | 必须有 | 绑定/提醒/面板/统计全部依赖；唯一可变字段 |
| `workspace` | 建议 | 项目隔离与筛选 |
| `created_at` / `updated_at` | 必须有 | 排序、TTL、归档判定 |
| `pending_at` | 可选 | 待确认开始时间（超时提醒依赖） |
| `closed_at` / `confirmed_by` | 可选 | 完成时间与确认审计 |

### event

| 字段 | 必要性 | 说明 |
|---|---|---|
| `id` | 必须有 | 递增序号；增量拉取/游标分页/导出 |
| `ts` | 必须有 | UTC 毫秒；时间窗口与排序基准 |
| `host` | 必须有 | 多宿主隔离；缺失会把不同宿主混在一起 |
| `session_id` | 可选 | 会话归属（少数事件不属于会话） |
| `work_id` | 工作类必有 | 工时/复盘/台账关联 |
| `kind` | 必须有 | 事件类型；按类型聚合 |
| `payload` | 必须有 | kind 的其余字段（唯一写入点） |
| `schema_ver` | 必须有 | 演进与兼容判断 |
| `idem_key` | 必须有 | 幂等；多进程/重试/崩溃重放不重复写 |
| `phase/summary/cause/category/minutes/origin` | 生成列 | 从 payload 自动生成，可索引；不为事件类型加物理列 |

### block / binding / 结论表

- `block`：`block_start` 做幂等键，upsert；`work_id` 取块结束时绑定，切换由 `work_switch` 切分；
- `binding`：会话当前快照（active/ended）；切换历史在事件流；**事件优先于快照**（启动时校正）；
- `work_summary` / `work_sediment` / `adjustment`：人工/AI 结论，与事件**同一事务**写入；可重建。

## 6. 设计原则

1. **event 是唯一事实源**：一切可重建；`crop rebuild` 是恢复手段；
2. **追加写 + 幂等**：`idem_key = hash(kind+work_id+ts+关键字段)`；
3. **单写者并发**：进程内攒批（100ms / 50 条）单事务提交；`busy_timeout=5000` 兜底；
4. **NULL ≠ 0**：每个数值字段标注 0 的语义（未采集 vs 没干活）；
5. **事件优先于快照**：binding 可由事件流校正；
6. **自然日**用本地时区显式计算；存储一律 UTC 毫秒；
7. **事实日志无条件接受**：UNIQUE 约束只留给业务表；去重在写入层（应用级窗口/游标）做；
8. **每个可变字段都有对应事件**：work 的 state/expect/type 等变更写 `work_update`，否则 rebuild 只能恢复计数、恢复不了语义；
9. **未归因时间是真实时间**：绑定前的事件归到 session，面板单列「未归因」，不当作缺失报警。

## 7. 迁移

一次性切换，时机放在**下次会话启动时**（用户本来就在等宿主起来，不额外打断）：

0. **前置核查**：扫描所有候选 data_dir，列出找到的 `events*.jsonl` 的条数 / 时间跨度 / `schema_ver` 分布，打印给用户确认（避免「探测到另一个目录、只迁了一半」）；
1. 提示 + 迁移前后各打一条 event（历史里能看到断点）；
2. 暂停写入（hook 只读）→ 导入（游标 + 幂等断点）→ 切读 → 恢复写入；13k 事件 <30s；
3. 失败自动回滚 JSONL；旧文件归档 `archive/`，保留 `KIT_STORAGE=jsonl` 一个版本周期；发布时发破坏性变更公告。

## 8. 性能目标

按查询类型拆（实测数据来自评审评论）：

| 查询类型 | 10 万条 | 50 万条 | 目标 |
|---|---|---|---|
| 点查（work_id / session_id） | 0.1ms | 0.1ms | < 5ms |
| 时间窗（`ts >= ?`） | 1–6ms | 6ms | < 20ms |
| 全表条件扫描（缺 work_id 等） | 14.7ms | 87ms | **部分索引后 < 10ms** |
| 分组聚合（GROUP BY work_id） | 4.5ms | 21ms | < 30ms |
| 事件写入（批写） | — | — | P95 < 5ms |
| 迁移导入（13k） | — | — | < 5s |

**热冷分离**：主库只留 90 天；按月 `VACUUM INTO archive/YYYY-MM.db`；跨期查询 `ATTACH`；保留 2 年。

## 9. 外部评审问题与应对

| 问题 | 应对 |
|---|---|
| work_id 短名撞名 | 冲突检测 + `-2` 后缀 + `UNIQUE(workspace, work_id)` |
| block 跨多个工作 | 主工作 + `work_switch` 切分 |
| 宽表 vs JSON | generated columns（STORED）+ 索引 |
| 结论与事件双写一致性 | 同事务写入 + `rebuild` 校验 |
| NULL vs 0 | 字段级标注语义，查询显式区分 |
| 时钟与自然日 | UTC 存储，本地时区算窗口，ts 单调兜底 |
| SQLite 单写者并发 | 批写 + busy_timeout；4 进程并发基准进 CI |
| tool 高频写开销 | 批写 + synchronous=NORMAL；基准 |
| 面板延迟 | ETag/增量 + 2s 聚合缓存 |
| 事件增长 | 按月归档 + 保留策略（默认 2 年） |
| 索引写放大 | 最小索引集，新索引带基准 |
| 备份/恢复 | 每日 VACUUM INTO + 季度恢复演练 |
| 迁移断点 | 游标 + idem_key 幂等重跑 |
| 迁移窗口写入 | 只读窗口 + 失败回滚 |
| schema 版本 | user_version 检查，旧版本拒绝/自动迁移 |
| 隐私 | 0600/0700，本地不上传 |
| 跨平台 | CI 跑 Windows；WAL 同进程设置 |
| 测试覆盖 | tests/storage：迁移/并发/崩溃/基准 |
| 生成列 STORED vs VIRTUAL | 采纳 VIRTUAL（查询同级、省 2MB/10 万行、写入少 6 次 json_extract） |
| idem_key 含 ts | 采纳：hash 不含 ts；UNIQUE 去掉（M4） |
| rebuild 缺 work 状态事件 | 采纳：补 `work_update` 事件（field/from/to） |
| data_dir 探测导致迁移漏数据 | 采纳：迁移前扫描候选目录并让用户确认 |
| 性能目标混在一起 | 采纳：按查询类型拆，缺 work_id 查询加部分索引 |
| UNIQUE(workspace, work_id) 冗余 | 采纳：删掉，保持 work_id 全局唯一 + 创建层冲突检测 |
| spawn 主循环开销 | 采纳：合并 pre-step 数据为一次调用 + spawnSync 改异步（不上 daemon） |
| 归档 2 年量级 | 采纳：热冷分离（主库 90 天 + 月度归档 ATTACH） |
| M1 block 与 work 是 M:N | 采纳方案 b：不物化归属，完全派生 |
| M2 未归因时间显式化 | 采纳：归到 session，单列「未归因」，不报红 |
| M3 state 与 confirmed 正交 | 采纳：拆两列，迁移时翻译 pending_confirm |
| M4 事实表不应有 UNIQUE | 采纳：idem_key 普通索引，去重进写入层 |
| M5 预估改口是时间序列 | 采纳：独立 estimate 表 + work 物化首末值 |
| M6 缺 parent_work_id | 采纳：加列，总耗时用递归 CTE |

## 10. 讨论结论（2026-11-18）

1. **迁移窗口**：改为「下次会话启动时」执行，前后各打一条 event 留断点；13k 事件 <30s；
2. **spawn 延迟**：面板可忍；主循环 pre-step 每次 40–90ms 不可忍——合并为一次调用 + 改异步 spawn，不上 daemon；
3. **归档**：保留 2 年没问题，改为热冷分离（主库 90 天 + 月度归档 + ATTACH 跨期查询）。

## 11. 未来演进与未采纳/待验证（诚实记录）

### 11.1 这版不是最优

当前设计对「**单机、单用户、本地应用**」是合理的：能撑到百万级事件、有崩溃恢复、有逃生门。
但它不是长期最优——多机同步、分析型负载、团队共享都会要求一次结构调整。真正要保证的不是「现在最优」，而是**结构上允许未来演进**。

### 11.2 未预留、但未来会撞上的墙

| 场景 | 现在的问题 | 建议的预留 |
|---|---|---|
| 多设备/多机 | 无 `device_id`，事件无 `(device_id, seq)`；work_id 会撞、event.id 会冲突、binding 会打架 | event 增加 `device_id` 可空列 + `(device_id, seq)` 唯一索引；同步时按 seq 拉取，冲突以事件时间为准 |
| 分析型查询（OLAP） | 面板聚合直接打在热库上 | 主库只做点查/时间窗；聚合查询走 `ATTACH archive/` 或导出 DuckDB；明确「主库不做全表扫描」 |
| 数据可调试性 | SQLite + JSON payload，人工排查比 JSONL 难 | 提供 `crop inspect <event-id>` / `crop export --format jsonl`；错误日志带 event id |
| 数据删除 | 没有「删除某个工作及其事件」路径 | 提供 `crop purge --work <id>`（硬删 + 审计事件），响应误操作与隐私要求 |
| 多用户/团队 | `workspace` 不是租户维度 | 不改现有字段，但预留 `owner_id`（可空）的演进空间，不把设计写死 |

### 11.3 采纳项之间的张力（实施时必须验证）

| 组合 | 张力 | 验证方式 |
|---|---|---|
| M1（block 归属完全派生）× G（主循环减少 spawn） | 面板每次要扫事件切片，可能抵消 G 的收益 | 10 万行基准：派生归属查询 P95；不达标退回「block 物化主工作 + 事件切分」 |
| M4（事实表去 UNIQUE）× 写入层去重 | 去重逻辑漏一处，重复事件静默进库 | 写入层统一 `append_event()` 入口 + 并发重复写测试 |
| M5（estimate 表）× 事件唯一源 | 表与事件两份数据 | estimate 只由 `work_update` 事件派生，写入同事务；rebuild 校验一致 |
| M3（state+confirmed 拆列）× 迁移 | 迁移时需翻译 pending_confirm | 迁移脚本单测：pending_confirm → open + confirmed=0 |

### 11.4 长期需要的机制（比表结构更重要）

- **ADR**：每条决策记录「约束 / 取舍 / 失效条件」，未来换场景时可回看；
- **逃生门**：`KIT_STORAGE=jsonl` 回滚、`crop export` 全量导出、schema 迁移链（v2→v3→…）；
- **契约版本**：外部写入方（hook / MCP）必须带 `schema_ver`，不能只认当前实现；
- **迁移演练**：每次 schema 变更先在测试实例上跑迁移 + rebuild + 校验。

### 11.5 结论

- 这版设计**够用且可演进**，不是终态；
- 所有「未来会变」的地方都留了可空列/迁移链/逃生门，而不是承诺“不变”；
- 如果未来做多机同步或分析平台，按 11.2 的预留做一次结构调整，而不是推倒重来。
