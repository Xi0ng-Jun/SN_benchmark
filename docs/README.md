# Silicon Notebook 评测文档导航

阅读顺序是：先看当前状态 SPEC，再按需查看操作、架构与协议；实验记录提供证据，历史归档提供背景。2026-09-29 起仅保留 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA；退役 benchmark 专属文档已删除，历史由 Git 回溯。

## 当前状态与约束

- [删除记录 DELETION_LOG](../DELETION_LOG.md)：本轮实际删除、共用职责迁移、原因与验证。
- [工程清理清单 TRIAGE](../TRIAGE.md)：清理后的保留／待删／未知；剩余旧假设、测试、兼容层、门禁和配置债务。

- [当前状态 SPEC](../CURRENT_STATE.md)：当前目标、不变量、有效约束、废弃假设、暂缓债务与明确非目标；开发首读页。
- [当前架构 ARCHITECTURE_CURRENT](../ARCHITECTURE_CURRENT.md)：代码入口、数据与调用关系、外部依赖、Git 演进及高风险边界。
- [验证与实验记录](evaluation-status.md)：代码快照、本地验证、服务器转述和阶段进展。
- [评测上下文](evaluation-context.md)：范围、证据分层、隔离规则、协议身份和非目标。

## 当前协议与操作入口

- [项目执行指南 RUNBOOK](../RUNBOOK.md)：安装、启动、测试、构建与服务器交付、配置／外部服务和过时步骤；当前 SN-only 操作从这里开始。
- [共享结果存储与导出](result-storage-and-export.md)：已有 bundle 完整校验安装、共享不可变对象与 capsule、bounded 报告／canonical 导出、compact 答卷、results/review 包及保留边界。
- [服务器存储与回传指令](server-result-storage-export-prompt.md)：当前 SN-only 的共享存储和回传交接模板、阶段实测项目与失败后停止条件；不代表服务器已执行。
- [实验 Dashboard](experiment-dashboard.md)：实验地图、单题回放、结果分析和比较规则。
- [Notebook 场景](notebook-benchmarks.md)：QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 的资料、分区、执行和评分。
- [Benchmark 标准与实现符合性](notebook-benchmark-standards-and-conformance.md)：五套任务的数据、输入、输出、精确评分、汇总和比较条件；逐项对应代码、证据、适配差异与未验证项。
- [QASPER 最终引用证据规则](notebook-benchmark-standards-and-conformance.md#35-2026-09-24-已实现的最终引用投影与特殊情况)：多段落/截短/错误引用、快照回放、旧运行只读恢复及官方 Evidence F1 验收。
- [Notebook 实验计划](notebook-benchmark-experiment-plan.md)：v3无gold请求、官方答卷/评分、参考方法、严格比较、真实验收与未完成项；新实验从这里开始。
- [外部方法登记表](../configs/notebook-external-method-registry-v1.json)：五套 benchmark 的候选方法、比较类别、输入条件、评分器和准入状态；未执行前不代表结果。
- [外部方法来源审计（2026-09-26）](notebook-external-method-audit-2026-09-26.md)：QMSum HMNet gold-input 输出与 ALCE ordinary/oracle 资料的文件身份、映射、评分校准和比较准入结论。
- [外部答卷与受控方法（2026-09-28）](notebook-external-results-2026-09-28.md)：Multi-Meta-RAG 全量、ALCE 样本和仅答案模型评分入口、QMSum Socratic 281 题重评分、QASPER LAB/HotpotQA KG2RAG/ALCE VANILLA 受控入口、候选可用性与复现命令；当前外部比较入口。
- [比较案例复核与指标解释](notebook-comparison-case-review-2026-09-28.md)：自动全范围题型汇总、可重复的逐题复核入口、MultiHop 题型差异、官方字符串指标的实际案例及解释限制。
- [MultiHop 146 题配对执行](notebook-multihop-subset-comparison.md)：历史子集链路验收与输入包；主比较已升级为上述完整 2,556 题。
- [原生 Agent 评测](native-agent-evaluation.md)：SN span、组件/轨迹指标、工件和失败语义。
- [原生评分恢复](native-scoring-recovery.md)：逐项落盘、组件补评和超时排查顺序。
- [服务器 Agent 指令](server-agent-tracing-prompt.md)：服务器只读已有结果和小样本验收的操作模板。
- [外部比较 campaign 手册](notebook-external-campaign-runbook.md)：服务器 smoke/full 执行顺序、冻结字段和回传工件。
- [Notebook 数据修正](notebook-data-corrections.md)：QASPER、QMSum、QAMPARI 等输入修正和迁移规则。
- [Notebook 重评分](notebook-rescoring.md)：从已有回答建立独立评分批次。
- [QMSum BM25 对照](qmsum-bm25-baseline.md)：常规 RAG 检索/生成对照及比较边界。

## 指标和背景

- [五套 Benchmark 官方资料](notebook-benchmark-official-resources.md)：QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 的论文、HF、GitHub、数据、评分、榜单状态及版本记录。
- [MultiHop/ALCE 真实数据验收](notebook-benchmark-real-data-validation.md)：官方文件身份、完整题目/资料、答案隔离、原始评分脚本对照和模型未验证范围。
- [指标实现对照](benchmark-metrics-reference.md)：五套官方评分、Notebook 诊断与 Agent 指标的边界、计算入口和读分规则。
- [Notebook 数据可用性](notebook-benchmark-data-availability.md)：来源、许可和可获取性核对。
- [DeepEval 调研](deepeval-study.md)：官方对象、项目映射和事实/判断/假设区分。
- [产品能力矩阵](product-capability-matrix.md)：产品入口与可观测证据的设计背景。

## 历史与过程

- [历史归档](archive/README.md)：五套及共用工程的交接、旧执行路径和阶段性报告。
- `superpowers/plans/` 与 `superpowers/specs/`：保留五套及共用过程记录；退役 benchmark 专属记录已删除，混合文件仅保留共用内容。不作为当前命令入口。

- [2026-10-08 保存／导出离线验证与合成规模记录](result-storage-export-verification-2026-10-08.md)

- [已有完成实验的服务器导出升级指令](server-upgrade-running-export-prompt.md)
