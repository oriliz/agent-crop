# 实例测试 · 主动复盘落地（#46）

> 2026-11-17。环境：`~/.dsh-e2e` + `lab` profile（headless 单轮），插件链到 `feat/proactive-retro`，
> 真实 DeepSeek API，数据目录 `/tmp/crop-e2e-46b/data`（预置 2 条 `reminder`，work_id=`e2e-retro`）。

## 用例（运行前写）

- **prompt**：start → end（不写 improve）→ 看到「待复盘」后按提示补 improve 并 `crop_adjust(record)` → 贴返回；
- **预期**：end 返回给出结构化的待复盘（问题 / 建议方向 / 下一步）；AI 补完 improve 后用 `crop_adjust` 建档；台账里出现这条改进，pending 关闭。

## 实际行为（证据）

1. end 返回原文：

   ```text
   [crop] 这个工作被鞭策 2 次，已记为待复盘：问题=被鞭策 2 次（yellow, red）；
   建议方向=先拆任务或先交付一个可验证的阶段，别一口气拖到超时。
   现在就把实际改进写进本次 end 的 improve=…；再调用 crop_adjust(action="record", ...) 建档。
   建档后这条待复盘会自动关闭——只提醒不留意见，等于没复盘。
   ```

2. 工具调用顺序：`crop_update(start) → crop_update(end) → crop_update(end, improve=...) → crop_adjust(record)`
3. 第 3 步（补 improve 的二次 end）返回 `Error: crop_update could not be recorded`；
4. 第 4 步 `crop_adjust` 成功：`recorded adjustment work_minutes（数值由 crop ledger 从事件派生）`；
5. 事件流结果：`retro_pending → adjustment → retro_resolved`——改进已落台账，pending 已关闭。

## 满意度

**满足**（复测后）：
- AI 不是「只留一句提醒」——它按结构化提示补了改进，`crop_adjust` 建档成功、pending 自动关闭，台账里有真实条目；
- 复测同一场景两次，二次 end 均成功（`status=0`），完整闭环：`crop_update(start) → crop_update(end) → crop_update(end, improve) → crop_adjust(record)`，事件流 `retro_pending → adjustment → retro_resolved`。

## 关于第一次失败

第一次运行时二次 end 返回 `crop_update could not be recorded`。隔离环境里 CLI / spawnSync 复现均成功，两次完整复测也成功——**未能复现**，判断为偶发（宿主执行时超时或环境瞬时）。

已做加固（本 PR）：
- `callCore` 超时 2500 → 5000 ms；
- 失败时记录 core 的 status/stderr 摘要，`crop_update` 的错误里直接带上，下次再发生能看到根因；
- `KIT_DEBUG_CALLS=1` 时把每次 core 调用（op/status/err/stdout/stderr 摘要）追加到 `/tmp/crop-call-debug.log`，可选诊断。

## 访谈

未做——首次失败属偶发，复测正常，不构成行为偏差。
