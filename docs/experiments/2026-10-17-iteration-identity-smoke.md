# 迭代与候选身份契约：第一阶段 smoke（2026-10-17）

范围：[迭代树设计](../product/sn-workbench/iteration-tree-design.md)第 10 节第 1 步“身份”。分支 `feat/workbench-iteration-tree`（独立 worktree）。本记录区分实验事实、限制与未完成项；**没有任何成绩结论**。

## 做了什么

- 新增迭代／候选／worktree intent／attempt／遗留关联表（`src/rag_eval/workbench/iterations.py`）与 HTTP 动词（`iterations_api.py`）。
- 建迭代：独立 backing Task 保存冻结题单修订，C0 从**明确起点 commit** 建 worktree 并直接捕获冻结（不新增 commit）。
- 派生：子候选 worktree 从**父候选冻结 commit** 建出，不再读项目 HEAD；一张提议可派生多个候选；`integrated_from` 只记录、不画边。
- 冻结：协调服务以 `sn-eval-coordinator` 身份提交（拒绝私有路径、拒绝空改动、拒绝 Agent 自行提交），随即捕获并核对 head／tree／无修改；已冻结再改须派生子节点。崩溃后只续接自己的提交。
- 运行：每次运行是候选内编号 attempt，绑定 Job 与 preparation；runner 接收候选关联并在**实际加载源码后**核对 `frozen_commit`，manifest 写 `identity.candidate`，ledger 增加 `candidate` 列。Job／preparation／execution 的 v1 封闭格式未改，旧回执照常校验。
- 实现 Agent 按候选 worktree 取 cwd；`sn-eval` 增加 `--parent`（提议挂在已评测候选下）与 `--candidate`（实现方泄漏扫描）。
- 遗留 Task 映射：默认 dry-run；只填有工件证据的字段；人工节点与父边须写依据。

## 实验事实（本机、smoke 专用 state）

环境：独立 state `var/sn-workbench`（本 worktree）与新 artifact store（qasper bundle/index 引用与原配置一致）。SN 原仓库是 `blob:none` partial clone，任何 clone 都缺对象，因此用 `git archive` 导出 `0680aa12` 的 `backend/` 建独立 smoke 仓库：起点 `995c9fc`（backend 字节同 `0680aa12`），HEAD 另前进到 `f937dc8`。未写 SN 原仓库与生产配置。

| 检查 | 结果 |
| --- | --- |
| C0 worktree HEAD = 起点 `995c9fc`，≠ 项目 HEAD `f937dc8` | 通过（真实 Paseo worktree） |
| 题单 selection_hash | `5cb19a8a…`，与 2026-10-14 三题实验一致 |
| 父未评测时提议 | 409 拒绝 |
| A 派生自 C0：worktree base = C0 冻结 commit | 通过 |
| A 冻结 commit `f00219c` 的父 = `995c9fc`，作者为协调者，项目 HEAD 未变 | 通过 |
| 冻结后再改 A、再冻结 | 409，提示派生子节点 |
| A1 worktree HEAD = A 冻结 commit | 通过 |
| A 目录有冻结后未提交改动时运行 A | runner 加载 head = `f00219c`、修改路径为空；manifest／ledger 带 `candidate` |
| C0 四次 attempt 的 manifest／ledger | attempt 1–4 编号、preparation、frozen_commit 均与记录一致 |
| 遗留 dry-run（live state 只读副本，10 个 Task） | 首版规则把 R1 两次基线错挂到候选 A（同一目录先后承载起点与改动）；已修正为“等于目录起点快照的捕获只作父状态证据”，修正后与设计第 9 节人工结论一致 |
| R1 迁移样例（副本上写入） | 起点 commit 标未知；C0 为人工节点（附依据）→ A（`50f00516`，依据为起点之后的捕获）；未分配执行清空 |

失败保留：C0 attempt 1 因执行解释器路径与 runtime expectation 不一致在 initializing 失败（配置问题，已改为期望路径）；attempt 2–4 及 A attempt 1 全部 3/3 `error`：聊天模型 qwen-turbo 持续 429 `limit_requests`（约 2.5 小时，embedding 调用正常且有观测 token，单次实际花费约 ¥0.006）。官方 QASPER 评分按既有协议把 error 计入分母（answer_f1 0.0，coverage `error: 3`），这不是成绩。

## 回看

里程碑边界按风险做一次独立回看（Opus）：Important 1（provision 在派发标记写入后崩溃会永久卡在 provisioning）已修复并加测试；Minor 2（提议／决策不受 `create_candidates` 写闸门约束——有意，仅数据库写入，已加注释）、Minor 3（私有路径是黑名单，自定义名依赖 `.gitignore`——已在错误提示中说明）。离线回归：相关套件 1179 passed；`test_workbench_plan_cli.py` 需 `PYTHONPATH=src`，17 passed。

## 未完成与限制

- **pi-flash 分析→提议→批准→实现 Agent→冻结→运行链路未做真实 smoke**：没有任何成功基线，按“只对已评测节点提议”无法开始。待模型额度恢复后补：新 attempt 跑 C0，再走完整链路。
- 一次 execution 只要有题出错，supervisor 记为 `failed`，候选即不算“已评测”；部分成功的运行能否作为提议依据，留待第二阶段比较记录一起定。
- 派发标记重置依赖 `creator.list()` 在创建成功后立即可见；若外部守护进程存在可见性延迟，理论上可能二次派发（同名分支会使第二次创建失败，不会静默产生两个候选）。
- scorer／model／config 仍未进入 preparation 契约，迭代协议里记为 `recorded-per-run`；比较阶段须显示该限制。
- `CURRENT_STATE.md` 未更新（另一条线在同一文件上工作），合并时同步。
