---
name: dev-work-management
name_zh: 开发工作管理
description: 管理研发工作项全生命周期——建需求前查重并确认是否合并、建单、状态流转、需求收尾与工作箱清理。当用户说「建需求/新建需求/开个需求」「有没有重复需求/合并需求」「需求收尾/工作箱清理/工作项状态」「Aone 需求管理」时使用。触发词：建需求、新建需求、查重、重复需求、合并需求、需求收尾、工作箱、需求状态流转、Aone 工作项。
---

# 开发工作管理

覆盖「建单前查重 → 建单 → 状态流转 → 收尾 / 工作箱清理」全链路。适用于任何使用 Aone 工作项的项目，项目空间通过参数传入。

## 核心原则

1. **先查后建**：没有查重证据不建单。
2. **不静默跳过**：命中相似需求时必须列候选、问用户「合并 / 仍新建 / 取消」，不得替用户决定。
3. **状态跟事实**：收尾必须有证据（CR 状态、代码提交、交付物、评论记录），不凭感觉关单。
4. **写后回读**：每次 update 后 `workitem get` 确认。

## 1. 建单前置查重（强制）

### 1.1 拉取范围

```bash
a1 project workitem list --project <PROJECT_ID> --category req \
  --sort gmtCreate:desc --page-size 300 -f json
```

辅助脚本（标题归一化 + 相似度聚类）：

```bash
python3 <SKILL_DIR>/scripts/req_scan.py --project <PROJECT_ID> --keyword "<关键词>"
```

### 1.2 判定四类

| 类型 | 判据 | 动作 |
|---|---|---|
| 真重复 | 范围/目标相同，标题或正文高度重叠，且无父子关系 | 合并到保留项，另一条取消 |
| 父子/拆分 | `relation list` 显示「子项/父项」 | 不合并，补齐父子或关联 |
| 相关但不同 | 同一大需求下的不同切片 | 挂到大需求/父项下，不留平行孤岛 |
| 无关 | 仅关键词撞车 | 正常新建 |

```bash
a1 project workitem relation list <ID> -f json
```

### 1.3 必须向用户确认

输出候选表：ID / 标题 / 状态 / 负责人 / 更新时间 / 相同点 / 差异 / 建议。
然后问三选一：**合并到 X / 仍新建 / 取消本次**。未得到明确选择前不要 create。

### 1.4 合并动作

- 保留项：加评论写「合并 XXX 的范围与证据」，必要时补关联。
- 被合并项：状态 → 已取消，评论注明「合并至 <ID>」。
- 新范围较大：优先挂到既有大需求下做子项，不新开平行单。

## 2. 建单

```bash
a1 project workitem create --project <PROJECT_ID> --category req \
  --title "<现象+场景>" --body-file <草案.md> --assignee <花名/工号>
```

- 类型：产品类需求 type 9；技术PRD type 507。
- body 模板：背景 / 用户场景 / 证据 / 范围 / 验收标准 / 影响面。
- 部分成功（已创建但后续查询失败）：保留 stdout 中的 ID，先 `workitem get` 核对，**不要重跑 create**。

## 3. 状态流转（2026-10 实测）

产品类需求（type 9）：

```
待处理 →(补 288 评审日期)→ 已选择 →(补 79 计划开始 / 80 计划截止)→ 开发中
→ 待测试 → 测试中 → 待验收 → 已发布 / 已取消
```

type 9 **没有「已完成」**，终态用「已发布」。

技术PRD（type 507）：排期中 → 开发中 → 测试中 → 已完成（逐级推进）。

状态名不硬编码，先查实时候选：

```bash
a1 project workitem field options status --project <PROJECT_ID> --type <type> -f json
a1 project workitem field list --project <PROJECT_ID> --type <type> -f json   # 查字段 ID
a1 project workitem update <ID> --cfs 288=YYYY-MM-DD --cfs 79=YYYY-MM-DD --cfs 80=YYYY-MM-DD
a1 project workitem update <ID> --status "<状态>"
a1 project workitem get <ID> -f json
```

## 4. 收尾 / 工作箱清理

1. 拉个人工作箱：`a1 project workitem list -f json --page-size 300`。
2. 过滤未终态。
3. 逐条找证据：关联 CR 状态（`a1 app cr get <id> -f json`）、代码提交、交付物、评论。
4. 输出「建议表」给用户确认；只执行用户确认的动作。
5. 执行后回读状态，汇报终态。

## 5. 定期巡检（解决「做完忘了关」）

人不会记得关单，所以关单检查必须自动挂在定时任务上，而不是等用户想起来。

```bash
python3 <SKILL_DIR>/scripts/closeout_check.py --project <PROJECT_ID> --owner <花名> --days 14
python3 <SKILL_DIR>/scripts/closeout_check.py --days 14          # 个人工作箱，跨空间
python3 <SKILL_DIR>/scripts/closeout_check.py --json > /tmp/closeout.json
python3 <SKILL_DIR>/scripts/closeout_check.py --owner <花名> --advance   # 顺手推进到「待验收」
```

输出分四类，处置口径：

| 分类 | 含义 | 处置 |
|---|---|---|
| 可关闭 | 待测试/测试中/待验收 且关联研发任务全部完成 | 推进到「待验收」，再报用户确认是否关单 |
| 想关但被拦 | 同上状态，但仍有 CR/DEF 没完成 | **不能硬关**：找卡住的研发任务，催 CR 或说明等待原因，把原因写进评论 |
| 僵尸项 | 待处理/已选择/开发中，超过 `--days` 天无更新 | 三选一：继续（补计划日期）/ 挂到大需求下 / 取消并写明原因 |
| 孤岛项 | 无父项、无关联研发任务 | 建父子关系或并入大需求，避免平行孤岛 |

**自动 / 人工的边界（用户已授权）**：

- 研发任务（CR/DEF）已全部完成时，`--advance` 自动把工作项推进到「待验收」——研发结束的边界，可以自动做。
- 「已发布」终态**永远不自动走**，脚本只列「待你确认关单」，等用户点头再 `--status 已发布`。
- 只自动推进已经进过开发态（开发中/排期中/待测试/测试中）的单；停在待处理/已选择却挂着已完成研发任务的属于流程异常，只给建议，不替用户补评审/立项日期。

纪律：
- 巡检主体只读；`--advance` 是唯一写操作，上限「待验收」，执行后逐条回读。
- 关单前置条件会报「需求关联研发任务未完成」——这是 CR 没到终态，不是本次要绕过的阻力。
- 定时任务必须带 `--owner`（项目空间里混着别人的工作项），且用本地执行：云端跑不了 `a1`（没有本地凭证与 CLI）。

## 6. 常见坑

- 父子项不是重复：先看 `relation list`，别把父项和子项当成两张重复单。
- 同一大需求下的平行单：应挂父项或大需求，不要各建各的。
- 命中相似就静默跳过：等于替用户做了合并决定，违反原则 2。
- 直接跳状态：必填字段会拦，按报错补 288 / 79 / 80。
- 部分成功重跑 create：会重复建单。
- 关联研发任务没完成时关不了单：报错「需求关联研发任务未完成」，先查 `a1 app cr get <id>` 的 status（PREINTG=待发布）。
- 按项目空间 `list` 会带上别人的工作项：巡检必须 `--owner` 过滤，否则结论会被别人的僵尸项污染。
- `list` 与 `get` 的 JSON 结构不同：`list` 用 `subject`/`status`，`get` 用 `title`/`fields[]`（状态在 fields 里），写解析代码时别混用。
- 关联研发任务的状态词汇是 `DOING` / `DONE` / `CANCEL`：`a1 app cr close` 关掉的 CR 在 relation 里显示 `CANCEL`，判定「研发已完成」时不能只认 DONE。
- 停在「待处理」的工作项直接推「待验收」会先被要求补评审日期（288），补完还可能要计划日期（79/80）；这类流程缺口不自动填，交人工。
