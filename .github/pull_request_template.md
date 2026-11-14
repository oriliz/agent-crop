## 变更说明

<!-- 一两句话说清这次改了什么 -->

## 为什么这么改（问题或场景）

<!-- 关联的 issue：`Closes #123`；没有 issue 就讲清真实场景 -->

## 怎么验证

- [ ] `python3 -m unittest tests.test_crop_cli`（20 tests OK）
- [ ] `node --test dsh/test/index.test.mjs`（7 pass）
- [ ] `bash tests/probe-smoke.sh`（PASS）

<!-- 上面三条请在本地实际跑过再勾；CI 上也会跑一遍。另外写下你能观察到的手工验证步骤。 -->

## 影响面

- [ ] 事件数据格式（events.jsonl / work / task 字段）
- [ ] 协议注入文本（注入给宿主的提示内容）
- [ ] 面板 UI
- [ ] 安装流程（README / docs/INSTALL.md）
- [ ] 无影响

## UI 变更

- [ ] 本次没有 UI 变更
- [ ] 有 UI 变更，已附 before / after 截图（同一场景、同一视口、同一主题）

## 协议与数据

- [ ] 我确认本次**没有**改动 AGENTS 协议文本或数据目录结构
- [ ] 或者：本次改动了，已在上面「变更说明」里写清迁移方式（老数据还能读）

---

<sub>Agent Crop 的边界：只本地观察、数据不出本机（`~/.agentcrop`）、不自动改代码、不评估人、不替代测试。
提交的内容里请去掉用户名、token 和私有路径。</sub>
