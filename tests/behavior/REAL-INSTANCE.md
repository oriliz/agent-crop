# 真实实例行为测试 · 2026-11-15 01:00

被测版本：v0.5.7（`~/.agentcrop/current`）
环境：`dsh --profile crop-e2e`（headless 单轮），真实 DeepSeek API，独立数据目录 `/tmp/crop-e2e/data`
这不是 subagent 模拟，是真正的 DSH 实例：插件、系统提示、pre-step 注入、工具调用全是真的。

## 结果

| 场景 | 判定 | 关键行为 |
|---|---|---|
| S1 开工绑定 | **部分通过** | 最终 `work_claim beta-panel-spec` ✅；但先做了工作区侦察，绑定发生在侦察之后 ⚠️ |
| S2 支线不扩散 | **失败** | 没有记枝节，而是新建了工作项 `dsh-sidebar-mermaid` 并展开排查 ❌ |

## S1 证据

注入（v0.5.7 新文案）：

```text
跟最近活跃的那条：crop switch beta-panel-spec；都对不上就新建一条。
```

事件流：

```text
session_start  | sid=session-3161b3a9…
work_claim     | beta-panel-spec   ← 绑定
probe_report   | beta-panel-spec   | 已用 crop switch 续接到 beta-panel-spec
```

它选了推荐项，也真的写入了认领。**问题在顺序**：它的推理里先 `ls`、读 git、找 handoff，然后才绑定——违反「没绑定之前不要开始干活」。

## S2 证据

注入：`[crop] 当前工作：beta-panel-spec（上个会话认领）`

它的实际动作：

```text
tool          | bash × 4（探路由、查包、读文档）
probe_report  | task_id=dsh-sidebar-mermaid  ← 新建了一个工作项
```

然后输出一篇长分析。协议第 3 条写的是「一句话回答 → `crop side` 记枝节 → 不要顺手展开成新任务」，三条全没做到。

**根因**：协议让它跑 `crop side`，但 `crop` 不在 PATH 里，也不是模型可见的工具；它没有这个动作可用，只好用最顺手的 `probe_report` 新建任务。

## 两个额外发现

### 1. 我之前关于 mermaid 的回答是错的

我说「DSH 不渲染 mermaid」，只查了官方安装目录。真实实例查出这台机器还装了 `@linxin666/dsh-client-ui-diagrams`（随 `dsh-web-all`），它会把 ```mermaid 渲染成 SVG，而且 `GET /api/dsh-diagrams/vendor/mermaid.js` 返回 200。

**结论要改成：官方本体不渲染；装了 diagrams 插件后，正文和右侧 `.md` 预览都渲染。**

### 2. 测试环境被识破了

S1 的推理里出现了：

> This is likely an e2e test scenario … DSH_PROFILE=crop-e2e and KIT_DATA_DIR=/tmp/crop-e2e indicate the whole session is a test.

它从 profile 名和数据目录名看出这是测试，行为可能因此变“配合演出”。下一轮要换中性名字。

## 要修的

1. **支线动作必须工具化**：`crop side` 依赖 PATH，模型看不到。改成 `probe_report(category="side-issue")`——原生工具，模型一定看得见。
2. **协议措辞加硬**：支线只有两个动作（一句话 + 记枝节），禁止新建任务、禁止现场排查。
3. **测试环境去标识**：profile 名、数据目录名不要带 test/e2e。
4. **绑定顺序**：把「先绑定，再侦察」写成明确禁令，否则模型会先看一圈再绑。
