# Silicon Notebook 评测文档导航

阅读顺序是：先看当前状态，再按任务进入协议，最后按需要回溯历史记录。

## 当前状态与约束

- [评测状态](evaluation-status.md)：主线提交、当前能力、本地验证、服务器边界和下一步。
- [评测上下文](evaluation-context.md)：范围、证据分层、隔离规则、协议身份和非目标。

## 当前协议与操作入口

- [实验 Dashboard](experiment-dashboard.md)：实验地图、单题回放、结果分析和比较规则。
- [Notebook 场景](notebook-benchmarks.md)：QASPER、MultiHop-RAG、ALCE、QMSum 的资料、分区、执行和评分。
- [Benchmark 标准与实现符合性](notebook-benchmark-standards-and-conformance.md)：四套任务的数据、输入、输出、精确评分、汇总和比较条件；逐项对应代码、证据、适配差异与未验证项。
- [QASPER 最终引用证据规则](notebook-benchmark-standards-and-conformance.md#35-2026-09-24-已实现的最终引用投影与特殊情况)：多段落/截短/错误引用、快照回放、旧运行只读恢复及官方 Evidence F1 验收。
- [Notebook 实验计划](notebook-benchmark-experiment-plan.md)：v3无gold请求、官方答卷/评分、参考方法、严格比较、真实验收与未完成项；新实验从这里开始。
- [原生 Agent 评测](native-agent-evaluation.md)：SN span、组件/轨迹指标、工件和失败语义。
- [原生评分恢复](native-scoring-recovery.md)：逐项落盘、组件补评和超时排查顺序。
- [服务器 Agent 指令](server-agent-tracing-prompt.md)：服务器只读已有结果和小样本验收的操作模板。
- [Notebook 数据修正](notebook-data-corrections.md)：QASPER、QMSum、QAMPARI 等输入修正和迁移规则。
- [Notebook 重评分](notebook-rescoring.md)：从已有回答建立独立评分批次。
- [QMSum BM25 对照](qmsum-bm25-baseline.md)：常规 RAG 检索/生成对照及比较边界。
- [IFEval 直接评分](ifeval-direct-scoring.md)：DeepEval 4.2.2 verifier 的当前口径。

## 指标和背景

- [四套 Benchmark 官方资料](notebook-benchmark-official-resources.md)：QASPER、MultiHop-RAG、ALCE、QMSum 的论文、HF、GitHub、数据、评分、榜单状态及版本记录。
- [MultiHop/ALCE 真实数据验收](notebook-benchmark-real-data-validation.md)：官方文件身份、完整题目/资料、答案隔离、原始评分脚本对照和模型未验证范围。
- [指标实现对照](benchmark-metrics-reference.md)：公开 benchmark 与 Notebook 指标的输入和限制。
- [Notebook 数据可用性](notebook-benchmark-data-availability.md)：来源、许可和可获取性核对。
- [DeepEval 调研](deepeval-study.md)：官方对象、项目映射和事实/判断/假设区分。
- [产品能力矩阵](product-capability-matrix.md)：产品入口与可观测证据的设计背景。

## 历史与过程

- [历史归档](archive/README.md)：退役交接、旧执行路径和阶段性报告。
- `superpowers/plans/` 与 `superpowers/specs/`：设计和实施过程记录，保留原位置，不作为当前命令入口。
