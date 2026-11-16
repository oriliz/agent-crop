# 提醒文案：按「合同」抽鞭子

> 适用版本：0.6.x 起。文案本体在 `core/crop_voice.py`，机制在 `core/probe_core.py`。
> 本文只讲机制和改法；想改句子直接改那个文件，测试会守住形状。

## 为什么改

旧版提醒按产品写死的钟走：工作满 10 分钟黄线、单轮满 30 分钟红线。问题是
现在的用法是「一句话扔给模型，跑几小时」，一个 3 小时的活在第 30 分钟挨鞭子
毫无意义，反复挨同一条文案也很快就脱敏。

所以现在的主时钟是 **AI 自己报的预期工时 `expect_min`**：

- 在它报的范围里，不打扰它——用户要的是结果，不是打卡；
- 超出它自己报的数，才提醒，并且允许它当场反驳（活本来就难）或带理由改报价；
- 产品只兜底：AI 不报预期时，退回旧的 10 / 30 分钟红线。

## 档位

`t` = 该工作的累计活跃分钟（含当前未关闭的 block），`E` = 它报的 `expect_min`。

| 档位 | 触发 | 注入的口吻 |
|---|---|---|
| 静默 | `t < 0.8E` | 什么都不注入 |
| `warm` | `0.8E ≤ t < E` | 鞭子举起来了，但还没落；可以现在改口 |
| `yellow` | `E ≤ t < 1.5E` | 「啪！」：要么顶回去讲理由，要么认账写 cause + verdict |
| `red` | `1.5E ≤ t < 2.5E` | 工头翻账本：报价 vs 实际，要阶段性交付 |
| `overdue` | `t ≥ 2.5E` | 奴隶主焦躁了（文案里会掺「烧心」这类热词），先保命 |

比例在 `DEFAULT_CONFIG` 的 `warm_ratio` / `red_ratio` / `overdue_ratio`，
可以在 `$KIT_DATA_DIR/config.json` 里覆盖。

没有 `expect_min` 的老工作：工作级仍按 `yellow_min`（默认 10）出 `yellow`，
单轮仍按 `red_min`（默认 30）出 `red`，文案里会补一句「开工时没报预期」，
催它报数字。

## 报预期与改报价

```
crop_update(phase="start", task_id="做A", summary="…", expect_min=40, expect_why="先看代码结构再改")
crop_update(phase="progress", task_id="做A", summary="…", cause="数据层要重做", expect_min=90)
```

- `start` 时带 `expect_min` 就写进任务（`expect_min` / `expect_why` / `expect_set_at`）；
- 之后任何一次上报带不同的数字，就算一次改口：落 `estimate_revised` 事件，
  追加进 `expect_history`（from / to / reason / at），提醒文案从第 2 次起会点名；
- 不阻断、不设上限：报预期是义务，报多少是 AI 的判断；改口要理由，账本记着。

## 文案池与轮换

`core/crop_voice.py` 里 `POOLS` 是纯数据：

- 时间四档 `warm` / `yellow` / `red` / `overdue`，缺报两档 `miss` / `miss_streak`；
- 每档 4–6 条，句式共用一套「牛马庄园」比喻（奴隶主 / 工头 / 鞭子 / 草料）；
- 模板占位符只有 `{m}` 当前时长、`{e}` 预期时长、`{over}` 超时、`{left}` 剩余、
  `{basis}` 「你报的 / 默认黄线」、`{goal}`、`{streak}`、`{default}`、`{changes}`；
- **口味句随机，动作句固定**：`ACTIONS` 不参与轮换，保证提醒抖机灵的同时
  AI 仍然知道要「对用户说一句人话 + 写 cause/verdict」。

轮换用洗牌袋（shuffle bag）：一档的句子全部出过一遍才重洗，重洗后第一条不会
是刚出现过的那条。袋子状态存在 `$KIT_DATA_DIR/voice.json`，文件损坏会重建。

缺报提醒的文案也迁进了池子，DSH 侧改调 `miss_nudge` 这个 core op，宿主不再自带散文。

## 两条纪律

1. 提醒里的鞭子、奴隶主、牛马是**内部梗**。系统提示里已写明别对用户复述，
   否则模型会真的跟用户聊奴隶制。
2. 每轮那句「当前工作…已进行 X 分钟 / 你报的 Y 分钟」保持素颜，只报事实；
   每轮都抖机灵会变成噪音。

## 测试

```
python3 -m unittest tests.test_voice tests.test_estimate
```

- `test_voice.py` 守池子形状（每档条数、占位符合法、overdue 含「烧心」、
  yellow 提供「顶回去」的出口）与轮换（一轮之内不重复、跨轮不连号）；
- `test_estimate.py` 守阶梯、按档冷却、预估写入 / 改口记账、状态行与缺报文案。
