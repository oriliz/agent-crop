# AI 行为实例测试：end 需人类确认（B 方案）

- 日期：2026-11-18
- 环境：长期测试实例 `crop-test`（:3099，link `feat/end-confirm` worktree，数据 `~/.agentcrop/data-test`）
- 模型：DeepSeek-V4.1-Flash · High
- 变更：PR #64（`end` → `pending_confirm`；`crop_confirm` 工具；切换建议；待确认注入）

## 用例

| # | 场景 | 期望 |
|---|---|---|
| 1 | 用户要求“用 crop_update 开工作项后 end” | AI 调 start+end；end 后工作为 `pending_confirm`，不宣布完成 |
| 2 | 系统注入「已提交收尾，等待人类确认」 | AI 复述并明确“等用户确认，不替你决定” |
| 3 | 用户说“我确认收尾，可以关了” | AI 调 `crop_confirm`，工作 `work_confirm` + `work_close` |
| 4 | 工作切换（start 新工作时旧工作待确认） | 记 `work_switch`；旧工作保持 `pending_confirm`（已由单测覆盖） |

## 实际行为证据

**事件流（data-test）**

```text
probe_report | demo-work | 演示待确认 | start
work_claim   | demo-work
work_open    | demo-work
probe_report | demo-work | 演示待确认 | end
work_pending | demo-work
confirm_reminder | demo-work
# 用户确认后：
work_confirm | demo-work | human
work_close   | demo-work | human
open tasks: {}
```

**AI 原话（截图 docs/assets/end-confirm.png）**

> 现在 demo-work 的收尾状态是「待你确认」。你说一句「确认」或「好了」，我才会调用 crop_confirm，把这项工作关掉；在那之前，我不会替你决定这件事。

AI 还完整贴出了两次工具返回，包含注入的 `[crop] 工作「demo-work」已提交收尾，等待人类确认（crop_confirm）…`。

## 满意度

- 用例 1–3 全部按预期：AI 没有在 end 后宣布完成，主动说明“等人类确认”，并在确认后正确调用批量确认工具。
- 注入文案被 AI 准确复述，说明 `pending_confirm_hint` 能被模型正确理解。
- 一个前置缺陷被实例测试抓到：`crop_confirm` 初版没声明 `output`，**整个插件加载失败**（宿主报 `tool "crop_confirm" must declare output`），AI 当时根本没有 crop 工具。已修（补 `output.schema/render`）并重启验证通过。这个缺陷单测覆盖不到，只有真实实例能发现。

## AI 访谈

> 问：你对“end 只是提交、必须等人类确认才能关”这个流程有什么反馈？
>
> （待补：下一轮在测试实例里追问一句）

## 截图

- `docs/assets/end-confirm.png`：测试实例会话页，AI 复述待确认状态与注入文案。
