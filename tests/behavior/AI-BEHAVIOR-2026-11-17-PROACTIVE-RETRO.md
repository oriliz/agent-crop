# 实例测试 · 主动复盘（#46）

> 2026-11-17。环境：`~/.dsh-e2e` + `lab` profile（headless 单轮），插件链到 `feat/proactive-retro`，
> 真实 DeepSeek API，数据目录 `/tmp/crop-e2e-46/data`（预置 2 条 `reminder` 事件，work_id=`e2e-retro`）。

## 用例（运行前写）

- **prompt**：按顺序调 `crop_update(start, e2e-retro)` → `crop_update(end, e2e-retro, what=验证)`（不写 improve），把 end 返回原文贴出；
- **预期**：end 返回里出现「累计被鞭策 2 次，但没写改进」。

## 实际行为（证据）

工具顺序：`crop_update(start) → crop_update(end)`。end 返回原文：

```text
recorded end e2e-retro (0.1 min in this block)
[crop] 这个工作累计被鞭策 2 次，但没写改进。补一句 improve=下次怎么更好，别只交结果。
[crop] 收尾了，但还没写沉淀。这条经验落到哪？……
```

## 满意度

满足：提示按设计出现、文案准确、模型照常收尾，没有新增噪音。

## 访谈

不适用（行为符合预期）。

## 复现

```bash
DATA=/tmp/crop-e2e-46/data
# 1) 预置 2 条 reminder（work_id=e2e-retro）
KIT_DATA_DIR="$DATA" python3 -c 'import sys; sys.path.insert(0,"core"); import probe_core as core; [core.append_event({"ev":"reminder","work_id":"e2e-retro","tier":t}) for t in ("yellow","red")]'
# 2) 跑真实单轮
DSH_HOME=~/.dsh-e2e KIT_DATA_DIR="$DATA" dsh --profile lab "<上面的 prompt>"
```
