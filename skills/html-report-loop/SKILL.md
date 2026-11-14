---
name: html-report-loop
description: 把 Markdown/文字报告做成"单文件可视化 HTML 页面"（状态卡置顶、锚点导航、图表内嵌、🆕 变更标记），并驱动"Qoder 浏览器批注 → 查证 → 原地更新 → 截图自证"的迭代阅读闭环。Use when 用户要求报告网页化/网页版/可视化形式/HTML 版报告，或在报告页面留浏览器批注要求解答与更新页面，或要求标注版本间改动（"哪里改了"），或说"不想看 Markdown / 不想点来点去 / 看不出哪里变了 / 这样读好爽"。产出的页面支持按 § 提问的阅读方式，适用于接手快报、调研报告、方案对齐等场景。
---

# html-report-loop：报告网页化 + 批注迭代闭环

把"读报告"变成"读一个可批注、可原地演进、每次变更可见的网页"。本 skill 只管流程与准则；需要视觉打磨时叠加 web-dev-loop / frontend-design，需要综合自检时叠加 verification-before-completion。

## 何时用

- 用户要"网页版报告 / 可视化 / 不想看 Markdown"
- 用户在报告页面留浏览器批注（对话里以 `<Browser comments>` 注入），要求解答并更新
- 用户说"看不出哪里改了 / 把改动标注出来"
- 用户反复在同一份报告上追问（需要"按 § 提问"的阅读方式）

## 闭环总览

```
建档（§A）→ 用户浏览器批注 → 查证 + 答问 + 原地更新（§B）→ 自证截图（§C）→ 循环
```

## A. 建档：内容 → 单文件 HTML

产出：与源文档同目录的 `同名.html`。硬约束：

- **单文件、内联 CSS、零 JS、零依赖**，`file://` 双击即开；从 [skeleton.html](skeleton.html) 起步（自带全套 token 与组件样式）
- 页面结构（自上而下）：
  1. 页头：eyebrow + 标题 + 日期/图例行 + **状态卡 ×3**（最重要结论各一张，左色条 ok 绿 / bad 红 / warn 琥珀）
  2. nav 锚点导航，**章节号与源文档一致**（养成"按 §4 第二问"的引用习惯）
  3. section 卡片流，每节独立成卡
- 内容准则：
  - **一屏速览置顶**，不滚动就能拿到关键结论（ADHD 友好）
  - 结论旁给**可自查证据**（命令、文件路径、`file://` 链接），照实标注证据覆盖边界（未验项单列）
  - 图直接 `<img>` 内嵌（相对路径引用源目录资源）；>300KB 不 base64，用相对路径
  - 可视化优先：状态徽章 `.t ok/bad/warn`；commit/时间线用色块（绿 ✅ 红 ❌）；表格用 `.tw` 包裹
  - 链接只出现在"索引"区，正文信息一律内联（用户不想点来点去）
- 源 md 处理：顶部加一行指针"内容以网页版为准"，**不做双源维护**
- 登记：把 html 路径写进相关 README / 索引文件

## B. 批注迭代（核心闭环）

批注以 `<Browser comments>` 注入对话（含 selector + 选中元素 HTML 快照）。纪律：

1. **先查证再答**：每条批注先看代码 / 日志 / 测试实据，不许凭记忆答；答问与改页一次做完，别只答不改
2. **想看点 → 搬进页面**：用户指向链接/文档说"直接给我看" → 内容内嵌 + 配说明文字，不要只回链接
3. **🆕 变更可见 + 新鲜度梯度**：所有改动处打 `<span class="newtag rN">🆕 更新</span>`（N = 批注轮次，从 r1 起；页头图例行注明本批摘要）。样式上**越新的 N 越亮、越小的 N 越淡**——每轮开始时给上一轮的 rN 补"降一档"样式（r1 淡色版骨架已备），旧标记自动退场、不打扰新问题；用户看不出"哪儿改了"就是没标够
4. 改动一律 SearchReplace 原地改，不整文件重写
5. 批注属"等待用户输入"类 → 页内打 ⏳ 标记，回复中确认后续动作
6. 新增 section 时同步 nav 锚点；**保持章节号稳定**（新内容挂子块或追加，不重排既有编号）

## C. 自证验证（每轮更新必做，不做不算完）

chrome-devtools MCP 五连（调用前先读对应工具 schema）：

1. `navigate_page {type: "reload", ignoreCache: true}`
2. `evaluate_script` 自检（函数保持简单，别堆 `?:` 链——运算符优先级踩过坑）：

```js
() => ({
  overflowX: document.documentElement.scrollWidth > window.innerWidth,
  // 期望只出现 rN（如 "r1,r1,r2,r2"）；出现 none = 有标记漏标轮次
  newtags: [...document.querySelectorAll('.newtag')].map(t => [...t.classList].find(c => /^r\d+$/.test(c)) || 'none').sort().join(','),
  anchorsOk: [...document.querySelectorAll('nav a')].every(a => document.getElementById(a.hash.slice(1))),
  imgs: [...document.querySelectorAll('img')].map(i => i.complete && i.naturalWidth > 0)
})
```

3. 桌面 1440 与窄屏 480 两种宽度分别复查 `overflowX`
4. `take_screenshot {fullPage: true, filePath: "/tmp/web-report-loop/<name>-vN.png"}`（先 `mkdir -p`）
5. Read 截图肉眼复核（结构、徽章位置、有无挤压）

回复用户时附：**截图（markdown 内嵌）+ 脚本自检原始结果 + 覆盖边界**（未验项照实说，不夸大）。

## 边界

- 批注环节依赖 Qoder IDE 内置浏览器；无批注环境时退化为聊天批注 / 用户口述，流程其余不变
- 页面保持零 JS；确需交互组件（排序/过滤）单独立项，不顺手加
- 图不重画：优先内嵌现成 PNG / SVG / drawio 导出件
- 大文档拆分：单页 >6000px 长时考虑拆多页或加页内二级导航，先在回复中提示用户

## 参考实现

完整实例（状态卡 / 锚点导航 / 红点时间线 / 协同表 / 🆕 标记 / 图内嵌全套）：
本仓 `skills/html-report-loop/skeleton.html` 为最小骨架；完整实例请参考你所在团队已交付的报告页面。
