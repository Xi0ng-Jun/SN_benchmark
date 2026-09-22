# Silicon Notebook 评测上下文

2026-09-22 新增当前任务：用户已批准重构离线 Dashboard 为实验地图、单题流程/原生 span 检查、可比结果分析。只读现有工件；本机以明确标注的构造 QMSum 数据验证，不触发 SN、judge 或新数据下载。设计见[实验地图](superpowers/specs/2026-09-22-experiment-map-design.md)，用法见[Dashboard](experiment-dashboard.md)。此前原生评测改造已交付，服务器独立推进真实实验。

2026-09-22 当前授权：实施[SN 原生 DeepEval 评测](native-agent-evaluation.md)。可继续修改独立 SN 分支并在服务器重新采集；本机只做离线验证，不部署生产、不运行在线模型。原生 observe + iterator 替代自制采集树/私有回放，默认组件、显式完整轨迹；旧普通答案/客观分保留，旧 Agent 分不迁移。服务器先最大题两模式，再 meeting18 六题两模式。下文按日期保留的旧限制与旧命令不是当前操作入口。

服务器最近报告：DeepSeek judge 已完成旧 meeting18 chunk 12 项、reasoning 24 项 Agent 评分，均已停止运行；GLM 超窗记录仍保留为失败证据。此前“reasoning 尚未完成”状态已过期。数字来自用户转述，本机未读取服务器原始结果。

本轮 SN 主实验及后续对照的执行口径见 [Notebook 实验计划](notebook-benchmark-experiment-plan.md)。

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

服务器预检反馈：QASPER 空白差异误排除、QMSum 空 turn 和 QAMPARI 空 alias 已在本地复现并定向修正，新 prepare 标记 notebook-data-v2，旧包按原规则加载。来源与迁移见 [数据修正记录](notebook-data-corrections.md)。服务器报告仅 prepare 完成，尚未导入/Ask；真实计数为用户转述，本机未重算。

2026-09-17：新增四套 Notebook 场景协议 `sn-notebook-benchmarks-v1`，不改变旧十套协议。使用独立 prepare/run 命令与既有 Dashboard；ALCE 模型分显式独立执行并挂接到新 run。服务器使用与边界见 [Notebook 评测说明](notebook-benchmarks.md)。当前不下载数据、不运行 SN/在线模型、不修改生产；本地回归仅构造样本。

2026-09-16 最新决定：IFEval 按现成 DeepEval benchmark 使用，取消本项目的人工正反例审计前置条件；不得再因未审核而跳过 Native 生成或将主分记 N/A。固定 SDK 直接评分，原始 SN 正文含引用；新旧 scorer 分开。v1 冻结来源中的 pending 字段保留为归档，不是当前门槛。操作见 [IFEval 直接评分](ifeval-direct-scoring.md)。

2026-09-16 更新：用户反馈旧 Dashboard 只有表格，现要求多标签组合筛选、图表、逐条评分过程和可比多组比较。当前本地工作是离线结果探索器及[实际指标实现表](benchmark-metrics-reference.md)，入口见[Dashboard 指南与服务器交接](experiment-dashboard.md)。公司服务器实验由用户另行推进，本地不重复在线任务、不下载 Benchmark 数据或修改 SN。以下日期段保留为历史上下文，不能据此推断公司服务器实验是否完成。

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

- 2026-09-14 最新要求：测试和实验先暂停，选题规模不设预先上限，重点设计 task、split、纳入规则及能力解释。已沉淀[十套公开评测选题方案](public-benchmark-selection-plan.md)，替代此前每套 20 题与“小样本优先”的选题安排。本轮仅文档；选定范围内完整数据、后续分库和覆盖对账尚未准备或实现。此前离线测试的执行授权不表示现在继续运行。

- 2026-09-14 用户授权先做离线回归，取代此前“不运行测试”的限制，在线调用仍暂停。[回归记录](offline-regression-2026-09-14.md)：全量 205 项通过，无跳过、警告或网络请求尝试。覆盖真实 SDK 的客观评分与合成样本准备；不启动 SN。当前下一步为正式小样本准备，真实产品验收与 Agent/DAG 仍待开展。

- 2026-09-13 最新已批准任务是[公开题的 SN 系统接入](sn-public-system-adaptation.md)，[实施计划](superpowers/plans/2026-09-13-sn-public-system.md)：七套新系统适配以资料问答、题面推理、知识/常识和指令遵循分类；保留 Native 参照。新七套 R 默认 `sn-public-system-v1`，明确指定 legacy 才走旧 N/A。只写代码与回归用例，不运行测试、应用或在线任务。此前[Native 补齐](superpowers/plans/2026-09-13-expansion-code-completion.md)保留为阶段记录；Agent/DAG 尚未实现，所有新代码待验证。

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
