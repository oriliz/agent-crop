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
| block 归属 | upsert；主工作 + `work_switch` 事件切分 |

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
  state TEXT NOT NULL DEFAULT 'open',   -- open|suspended|pending_confirm|closed|archived
  workspace TEXT, created_at INTEGER, updated_at INTEGER,
  pending_at INTEGER, closed_at INTEGER, confirmed_by TEXT
);

CREATE TABLE event (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  ts INTEGER NOT NULL, host TEXT NOT NULL, session_id TEXT,
  work_id TEXT REFERENCES work(work_id), kind TEXT NOT NULL,
  phase TEXT GENERATED ALWAYS AS (json_extract(payload,'$.phase')) STORED,
  summary TEXT GENERATED ALWAYS AS (json_extract(payload,'$.summary')) STORED,
  cause TEXT GENERATED ALWAYS AS (json_extract(payload,'$.cause')) STORED,
  category TEXT GENERATED ALWAYS AS (json_extract(payload,'$.category')) STORED,
  minutes REAL GENERATED ALWAYS AS (json_extract(payload,'$.minutes')) STORED,
  origin TEXT GENERATED ALWAYS AS (json_extract(payload,'$.origin')) STORED,
  payload TEXT NOT NULL DEFAULT '{}',
  schema_ver INTEGER NOT NULL DEFAULT 2,
  idem_key TEXT UNIQUE
);
CREATE INDEX idx_event_ts      ON event(ts);
CREATE INDEX idx_event_work_ts ON event(work_id, ts);
CREATE INDEX idx_event_sid_ts  ON event(session_id, ts);
CREATE INDEX idx_event_kind_ts ON event(kind, ts);

CREATE TABLE block (
  block_start INTEGER NOT NULL, session_id TEXT NOT NULL,
  work_id TEXT, minutes REAL NOT NULL DEFAULT 0,
  tools INTEGER, failed INTEGER, started_at INTEGER, ended_at INTEGER,
  PRIMARY KEY (session_id, block_start)
);
CREATE INDEX idx_block_work ON block(work_id, block_start);

CREATE TABLE binding (
  session_id TEXT PRIMARY KEY, work_id TEXT NOT NULL,
  state TEXT NOT NULL DEFAULT 'active',  -- active|ended
  switched_at INTEGER, ended_at INTEGER, updated_at INTEGER
);

CREATE TABLE session (
  session_id TEXT PRIMARY KEY, host TEXT, workspace TEXT,
  started_at INTEGER NOT NULL, ended_at INTEGER
);

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
6. **自然日**用本地时区显式计算；存储一律 UTC 毫秒。

## 7. 迁移

一次性切换：提示 → 暂停写入（hook 只读）→ 导入（游标 + `idem_key` 幂等断点）→ 切读 → 恢复写入；全程 <30s（13k 事件实测）；失败自动回滚 JSONL。旧文件归档 `archive/`，保留 `KIT_STORAGE=jsonl` 一个版本周期；发布时发破坏性变更公告。

## 8. 性能目标

| 场景 | 目标 |
|---|---|
| 事件写入（批写） | P95 < 5ms |
| 面板聚合（10 万事件） | P95 < 50ms |
| 迁移导入（13k 事件） | < 5s |
| 并发写（4 进程 × 1000） | 无丢失、无损坏 |

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

## 10. 待讨论

1. 一次性迁移的「暂停写入窗口」是否可接受，还是改成双写过渡？
2. 继续 spawn python 的 30–60ms 延迟，面板 5s 轮询下是否可接受？
3. 事件表按月归档/清理策略（默认保留 2 年）是否合理？
