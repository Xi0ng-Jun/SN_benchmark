# Silicon Notebook 评测文档导航

阅读顺序是：先看当前状态，再按任务进入协议，最后按需要回溯历史记录。

## 当前状态与约束

- [评测状态](evaluation-status.md)：主线提交、当前能力、本地验证、服务器边界和下一步。
- [评测上下文](evaluation-context.md)：范围、证据分层、隔离规则、协议身份和非目标。

## 当前协议与操作入口

- [实验 Dashboard](experiment-dashboard.md)：实验地图、单题回放、结果分析和比较规则。
- [Notebook 场景](notebook-benchmarks.md)：QASPER、MultiHop-RAG、ALCE、QMSum 的资料、分区、执行和评分。
- [Notebook 实验计划](notebook-benchmark-experiment-plan.md)：主实验、对照和报告边界。
- [原生 Agent 评测](native-agent-evaluation.md)：SN span、组件/轨迹指标、工件和失败语义。
- [原生评分恢复](native-scoring-recovery.md)：逐项落盘、组件补评和超时排查顺序。
- [服务器 Agent 指令](server-agent-tracing-prompt.md)：服务器只读已有结果和小样本验收的操作模板。
- [Notebook 数据修正](notebook-data-corrections.md)：QASPER、QMSum、QAMPARI 等输入修正和迁移规则。
- [Notebook 重评分](notebook-rescoring.md)：从已有回答建立独立评分批次。
- [QMSum BM25 对照](qmsum-bm25-baseline.md)：常规 RAG 检索/生成对照及比较边界。
- [IFEval 直接评分](ifeval-direct-scoring.md)：DeepEval 4.2.2 verifier 的当前口径。

## 指标和背景

- [指标实现对照](benchmark-metrics-reference.md)：公开 benchmark 与 Notebook 指标的输入和限制。
- [Notebook 数据可用性](notebook-benchmark-data-availability.md)：来源、许可和可获取性核对。
- [DeepEval 调研](deepeval-study.md)：官方对象、项目映射和事实/判断/假设区分。
- [产品能力矩阵](product-capability-matrix.md)：产品入口与可观测证据的设计背景。

## 历史与过程

- [历史归档](archive/README.md)：退役交接、旧执行路径和阶段性报告。
- `superpowers/plans/` 与 `superpowers/specs/`：设计和实施过程记录，保留原位置，不作为当前命令入口。
