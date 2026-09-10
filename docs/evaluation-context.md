# Silicon Notebook 评测上下文

## 目标

围绕 Silicon Notebook 建立一个可以持续运行、持续发现问题并验证优化效果的评测闭环。

## 当前确定的范围

- 评测框架：DeepEval。
- 数据来源：项目现有文档、真实用户问题、典型业务场景、公开评测集。

除此之外的内容暂不视为定案。评测对象、指标、标注粒度、阈值、样本规模和运行频率，都通过后续调研、实验和人工核验逐步确定。

## 研究输入

- `../../rag-benchmark-docs/RAG评测框架与软件选型表.xlsx`
- `../../rag-benchmark-docs/RAG评测论文数据集分类与来源审计.xlsx`
- `../../rag-benchmark-docs/01-rag-evaluation-tutorial.md`
- `../../rag-benchmark-docs/02-rag-evaluation-leader-brief.md`

Excel 文件保留为原始调研材料；其中的结论在经过复核后再写入本项目文档或代码。

## 收敛原则

每次新增评测能力时，尽量同时留下三类记录：

1. 需要回答的问题或假设；
2. 实验设置和可复现输入；
3. 观察结果、限制以及下一步决定。

暂定方案应明确标记为实验性，避免把一次实验结果误认为系统质量结论。

## 当前交接入口

截至 2026-09-10，DeepEval 调研和历史环境验证已完成。SQuAD/DROP 的 400 次 Ask 已完成、baseline judge 部分完成，独立 smoke 已通过完整流水线和审计。用户已暂停具体评测执行，现优先讨论评测项目设计与产品能力。每周任务已停用；人工校准、完整 baseline 和质量门禁尚未完成。

- Git 仓库：`git@github.com:Xi0ng-Jun/SN_benchmark.git`，原地初始化，历史运行仍依靠当时保存的源码快照与哈希，不回填新的 Git commit 为历史身份。
- [当前结果摘要](../results/public-benchmark-status.md)与[开发路线讨论稿](development-roadmap.md)。
- 下列 `var/` 路径是本地历史工件，未随 Git 仓库上传。

- 当前状态与下一步判断：[evaluation-status.md](evaluation-status.md)。
- 本轮结果目录：`/home/wabiwabi/silicon-notebook/benchmark-deepeval/var/public-system-50/`。
- [完整报告](../var/public-system-50/report.md)、[指标汇总](../var/public-system-50/summary.json)、[结果文件索引](../results/README.md)。
- [已执行计划](public-system-plan.md)与[历史隔离及版本记录偏差](../var/public-system-50/provenance-notes.md)。

本轮使用 MultiHop-RAG、SciFact 各 50 个问题及其正证据文档并集，完成原生导入、切块、向量索引、chunk Ask 和 DeepEval。它是受限候选库实验，未覆盖全库、KG、重排或所有产品能力。具体分数与失败记录以本轮产物为准，不把历史 BM25、未经人审的领域候选和本轮结果合并比较。

## 后续工作约定

- 最新优先级是[DeepEval 公开评测起步](deepeval-public-starter-plan.md)：先复用现成 benchmark 的数据与评分，区分模型参照和产品适配；之后再按[广度计划](product-capability-breadth-plan.md)构造业务场景，原深度方案按需复用。明确排除交互可靠性与资料更新后的知识一致性。用户已授权开始实施及执行/报告接入，仍要求只写代码和逻辑，暂不运行和测试；模型调用未恢复。[首批代码交接](deepeval-public-starter-implementation.md)与[执行报告说明](deepeval-public-starter-orchestration.md)记录未验证实现和后续边界。

- 2026-09-10 起暂停在线 benchmark 与自动触发，恢复运行需用户重新提出；当前在新分支整理产品能力矩阵、数据协议、真实样例和离线实施计划，入口为 [产品能力评测方案](product-capability-evaluation-plan.md)。
- 用户希望按完整实验交付推进，包含实际运行、审计和报告；避免把工作拆成反复确认的小步骤。
- 生产代码保持不变，评测使用独立数据库、存储、索引、缓存和日志。
- 不人为添加外层运行时限，不因单项指标耗时较长就判定卡住；底层客户端的请求超时与重试配置另行记录。
- 保持测试精简，优先复用现有测试和直接核对产物，仅为确有风险的改动增加必要测试。
- 已完成运行目录用于查阅和审计。新实验使用新的 run-dir；续跑用于未完成实验，不静默重评已有失败条目，不补造历史快照或分数。

## 学习与展示入口

第一阶段 DeepEval 调研和中文 executable lectures 位于：

- `docs/deepeval-study.md`：研究讲义、官方材料索引、项目映射和事实/判断/假设分层；
- `lectures/lecture_01.py` 至 `lectures/lecture_04.py`：可执行课程源码；
- `scripts/build_lecture_traces.py`：使用官方 `edtrace` 生成逐步 trace；
- `var/traces/`：生成的 trace JSON；
- `lectures/README.md`：启动官方浏览器 viewer 的说明。

这些材料用于学习和内部展示，不构成正式数据集、指标阈值或发布门禁。正式评测仍以版本化数据、人工审核和可复现实验记录为准。
