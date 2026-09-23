# 评测文档现状整理设计

## 目标

让新读者先看到合并后主线的真实能力、当前运行边界和下一步入口，同时保留历史实验、旧协议和服务器交接记录的可追溯性。

## 当前事实基线

- 代码主线为本地 `main`，当前 HEAD 为 `03ca577`；Dashboard 和原生评分恢复已合并。
- 本地 `main` 尚未推送，`origin/main` 仍为 `8b4b4de`；远程 Dashboard 和原生评分分支仍分别保留。
- 当前本地验证为 Python `447 passed`、Dashboard JavaScript `34 passed`。
- Dashboard、Notebook benchmark、原生 Agent 评测和已保存组件补评是当前实现入口。
- 服务器实验结果来自转述，除明确标注的本地验证外不改写为本机事实。

## 文档分层

### 当前入口

`README.md`、`docs/README.md`、`docs/evaluation-status.md` 和 `docs/evaluation-context.md` 是默认阅读路径。

- `README.md` 说明项目用途、当前能力、常用入口、验证边界和导航。
- `docs/README.md` 按当前状态、现行协议、专题说明和历史归档组织链接。
- `evaluation-status.md` 只记录当前快照、验证证据、服务器转述和下一步，不继续追加完整日期流水账。
- `evaluation-context.md` 只记录稳定范围、证据分层、隔离原则、非目标和协议身份。

### 当前专题

Dashboard、Notebook benchmark、原生 Agent、组件补评、指标实现、数据修正、Notebook 重评分、QMSum BM25 和 IFEval 说明继续留在 `docs/` 根目录。它们顶部增加状态标签，说明是当前协议、实现说明或服务器操作说明。

### 历史归档

新增 `docs/archive/README.md`。明确退役的交接和旧操作文档移入 `docs/archive/2026-09/`；仍被多个当前文档引用的设计和计划文件先留在原位置，只加“历史设计/当前替代入口”说明。`docs/superpowers/plans/` 与 `docs/superpowers/specs/` 保留原位置作为过程档案。

每个归档文件保留原内容，顶部注明历史日期、退役原因和当前替代入口。归档不删除实验数据、不改变结果文件身份。

## 需要淘汰的现行误导

- 当前入口不得把 `inspect_agent_inputs.py`、`--capture-agent-trace`、`--judge/--dag` 描述成现行操作。
- 当前入口不得把 Dashboard 与原生评分恢复描述成未合并分支。
- 历史测试数字必须带日期和适用范围，不能覆盖合并后新验证。
- 服务器转述、历史本地验证和本次本地验证分开标注。

## 验收

- 当前入口可从 `README.md` 经 `docs/README.md` 到所有现行专题文档。
- Markdown 相对链接全部可解析；归档后的链接指向新路径。
- 当前入口和现行专题不再引用已退役脚本或旧协议命令。
- `git diff --check` 通过；文档改动后运行现有 Python 和 Dashboard JavaScript 回归，确认没有因链接或资源变更破坏项目。
