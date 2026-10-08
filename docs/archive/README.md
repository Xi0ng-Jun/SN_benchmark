# 历史归档

这里保存已经结束、替代或仅用于回溯的评测阶段记录。归档文件仍是证据，不能当作当前命令或当前服务器状态。

**2026-09-29 文档清理：五套之外的旧 benchmark 专属内容已从当前文档树删除。** 保留 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 与共用工程历史；混合文件已移除退役部分，原文可由 Git 回溯。实际删除见 [DELETION_LOG](../../DELETION_LOG.md)，剩余清单见 [TRIAGE](../../TRIAGE.md)。

## 状态标签

- **当前协议**：当前代码仍读取或生成的输入、输出和身份约定。
- **当前实现说明**：当前代码入口、字段和失败语义的说明。
- **服务器操作说明**：给服务器执行已授权小任务的模板；执行前必须核对实际 checkout、配置和产物。
- **历史快照**：某个日期的本地状态或服务器转述，不代表现在仍成立。
- **历史设计**：曾经讨论或实施过的方案，是否继续采用要看当前协议和代码。
- **退役协议**：保留旧数据和迁移背景，但不再作为新运行入口。

## 已归档文件

`2026-09/` 保存以下退役快照：

- `session-handoff-2026-09-23.md`：对话切换时的 worktree、分支和服务器交接快照。
- `notebook-benchmark-experiment-plan-pre-v3.md`：v3协议修正前的实验计划；要求先取得旧服务器结果及禁止本机新实验的阶段限制已经被用户后续授权替代。
- `agent-judge-input-inspection.md`：旧 Agent judge 输入体积检查，已由原生 Agent/组件评分协议替代。
- `sn-execution-tracing.md`：旧 SN 执行轨迹采集方案，已由 `native-agent-evaluation.md` 替代。
- `qmsum-next-iteration.md`：QMSum 首轮结果收口记录，新的服务器动作以 Notebook 和评分恢复说明为准。

产品能力设计等混合文件在 `docs/` 保留共用工程知识；公开起步、扩展、旧选题及退役套件执行台账已删除。`superpowers/plans/` 和 `superpowers/specs/` 只保留五套／共用记录，不作为当前入口，也不要求为退役测试维持兼容。

## 现行替代入口

- 当前总览：[评测状态](../evaluation-status.md)
- 稳定约束：[评测上下文](../evaluation-context.md)
- Dashboard：[实验 Dashboard](../experiment-dashboard.md)
- Notebook：[Notebook 场景](../notebook-benchmarks.md)
- Agent：[原生 Agent 评测](../native-agent-evaluation.md)
- 评分恢复：[原生评分恢复](../native-scoring-recovery.md)
