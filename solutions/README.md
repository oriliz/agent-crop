# 方案目录

探针只采集、不插话。新装默认开启 `inline-reminders`、`tempo-rules`、`detour-signal` 三条，
其余方案逐条选择；已有开关配置时安装器不覆盖。

## 用法

```bash
python3 solutions/cli.py list            # 有哪些方案、什么状态、开没开
python3 solutions/cli.py review          # 近 7 天触发读数、任务事实、资产命中、弯路声明
python3 solutions/cli.py enable inline-reminders
python3 solutions/cli.py disable inline-reminders
```

开关写在本机数据目录的 `solutions.json`（`$IDE_HOME/task-metrics/data/`），改完立即生效，
不需要重装插件、不碰宿主配置。没启用任何方案时，探针只记账、不输出任何提示。

## 状态四档

| 状态 | 含义 |
|---|---|
| 未验证 | 没有对照数据，只是假设 |
| 试用中 | 有触发读数，但样本不足以下结论 |
| 已验证 | 过 A/B 门槛且结果指标同向 |
| 已停用 | 试过无效或反作用 |

`不适用` 表示它不属于提效干预（例如报告交付方式）。

## 加一个方案

```
solutions/<id>/
  solution.json   {id,name,problem,status,events,evidence}
  hook.py         argv[1]=事件名；stdin=宿主事件 JSON；stdout=要插入的文本；5s 超时，异常静默
```

`events` 取 `session-start` / `user-prompt-submit` / `post-tool-use` / `stop`，
由探针的固定入口调用 `dispatch.py` 分发。方案不需要改宿主配置。

外部方案除了 stdout 注入，还可以把记录写进 `$DATA_DIR/<自己的目录>/`，供验证脚本复读。
`alignment-gate` 是第一个这么做的方案：注入对齐清单，同时把 gate/ack/edit-before-ack 写进
`data/gates/`，`problem_class.py --gates` 直接算覆盖率。

内置方案（节奏纪律、实时提示、LSP 自动配置、弯路信号）的判定逻辑仍在探针脚本里，用的是同一个开关文件；
外部方案直接写 `hook.py` 即可。弯路信号的采集结果在 `data/detours/`，复查视图会同时给出
「自述已恢复」和「后续有证据」两个数，避免把口号当效果。
