# 评测状态

更新时间：2026-09-10

此前已完成 DeepEval 调研与环境链路验证。目前已获用户授权执行 SQuAD / DROP 的两模式持续评测方案；这些新任务从产品能力和框架能力出发，既有实验不充当质量基线。

## 当前执行

- SQuAD / DROP 官方公开数据已冻结，每集 100 题、200 段候选原文，保留参考答案、分组与 SQuAD 位置标注。
- 首轮四个隔离候选库均完成原生导入、切块与索引；400 次 Ask 已完成，383 份答案成功落库，17 次 reasoning 调用可复现为原生澄清检查拦截。用户要求暂缓具体任务执行，未完成的 baseline judge 不再继续推进。
- 已完成的 smoke 运行有 40 个计划输出、2 个产品错误、120 项评分，完整性审计通过；baseline 已保存中间评分，不作为完整基线。
- 先前原生数据库、上下文与引用对象检查已通过；完整 baseline 的评分与最终审计尚未完成。离线测试记录为 47 项通过。每周本地 timer 已于 2026-09-10 停用，恢复运行需用户重新提出。
- 当前执行目录：`var/public-benchmark/20260909-baseline-v1/`；详见 [执行记录](public-benchmark-execution.md)。
- 人工校准仍待真实评审；当前生成和 judge 共用配置中的聊天模型，语义评分保持 advisory，不作为发布门禁。
- 40 份盲审材料已生成在本轮目录的 `review.md`，`human-labels.jsonl` 的标签保持待审。数据冻结与空格校验的执行修正均有原始记录和独立修正说明。
- 以下实验分数为历史环境验证记录，不替代新方案验收。

## 已有基础

- 公开数据集 loader：CRUD-RAG、MultiHop-RAG、SciFact。
- 本地 BM25 检索基线和 Hit@K、Evidence Recall、MRR、nDCG 等确定性指标。
- Silicon Notebook 内部 chunk 检索 adapter。
- DeepEval 的 Contextual Recall、Contextual Precision、Contextual Relevancy、Faithfulness、Answer Relevancy 接入。
- 基于项目模型的候选 golden 生成和证据子串校验。
- 评测项目测试 19 个通过；本次公开集项目链路实验只新增 1 个元数据保存回归测试。

## 第一阶段调研已完成

- 已完成 DeepEval 与 Silicon Notebook 评测需求的映射，区分了官方事实、当前判断和待实验假设；详见 `docs/deepeval-study.md`。
- 已建立中文 executable lectures：`lectures/lecture_01.py` 至 `lecture_04.py`，覆盖 test case、RAG 数据协议、五个 RAG metric 和公开 BM25 baseline。
- 已通过官方 SSH 仓库获取 `edtrace`，并用 `scripts/build_lecture_traces.py` 生成 `var/traces/lecture_01.json` 至 `lecture_04.json`。
- 官方 trace viewer 已安装依赖，按 `lectures/README.md` 启动后可查看逐步源码、Markdown 渲染和 `@inspect` 变量快照；不保证历史开发服务器仍在运行。课件中的 trace 是教学材料，不是正式评测结果。
- 四个 lecture 的 trace 均已生成；每个 trace 都包含渲染内容。当前测试结果见上方“已有基础”。

## 公开集进入实际项目链路（实验性）

- MultiHop-RAG、SciFact 各取原始顺序前 50 个问题，导入对应正证据文档并集：78 / 51 篇文档，1126 / 51 个 chunk，向量全部生成并建成 ANN 索引。
- 已通过独立 SQLite、存储、索引和缓存调用项目原生 `upload_sources` 与 `repo.ask(mode=chunk)`，100 条实际答案和送入生成器的完整上下文已保存并与数据库核对。生产代码未修改。
- 确定性文档级 @10 与 DeepEval 已全部完成并通过产物审计。完整报告：[`var/public-system-50/report.md`](../var/public-system-50/report.md)，逐条记录与配置位于同目录。
- 文档级 Recall@10：MultiHop-RAG 0.8943（41 条有正证据问题）、SciFact 0.9300（50 条）；完整证据集合率分别为 0.7561 / 0.9200。未命中和证据不完整的问题 ID 已列入 `summary.json`。
- DeepEval 共尝试 400 项：394 项有效、6 项失败，失败均来自 MultiHop-RAG（5 项 ModelInvocationError、1 项 ValidationError），缺失分数不填零、不计入均分。SciFact 没有参考答案，另有 100 项 Contextual Recall/Precision 明确跳过；未生成替代答案。
- Contextual Relevancy 均分为 0.2145 / 0.1569，Faithfulness 为 0.7716 / 0.7174。文档命中与上下文相关性不是同一口径，分数差异需结合原始片段和人工核验解释。
- Judge 累计指标耗时约 116.4 分钟，无外层人为时限；过程中有一次连接错误，相关 SciFact 样本最终获得有效分数，底层重试记录仍保留。
- 这是受限候选库实验，不能与此前全库 BM25 分数直接比较；本次未构建 KG，也未触发配置中的重排分支。
- 首次问答中有 1 次服务商拒绝输入，恢复后成功，101 次数据库尝试均保留；初始导入还有日志隔离与代码快照记录偏差，详见 `var/public-system-50/provenance-notes.md`。

结果目录：`/home/wabiwabi/silicon-notebook/benchmark-deepeval/var/public-system-50/`。文件用途见[结果索引](../results/README.md)；机器可读完成状态为 `completed_with_metric_errors`，表示整轮执行与审计完成，但存在 6 项评分失败，不是全部指标成功。

## 当前未定

- 正式领域测试集的组织方式和人工标注规范。
- 评测覆盖哪些问答模式及其他产品能力。
- 确定性指标与 LLM judge 的最终组合、阈值和报告形式。
- 拒答、引用可靠性、响应效率和成本的具体测量方法。
- 回归评测的运行频率与门禁方式。

## 当前开发重点

具体公开集评分任务暂缓，后续工作回到 Silicon Notebook 产品本身：先梳理文档导入、切块、检索、引用、chunk/reasoning 及澄清策略的可测契约，再设计与真实使用场景对应的人工核验集。公开 benchmark 保留为通用能力和回归流程的补充，不作为产品领域质量的唯一依据。

## 历史候选试运行与限时更正

- 已能从现有 notebook 生成 2 条候选问题并完成只读检索；候选仍需人工审核，不能作为正式基准。
- DeepEval 完整运行已完成：取消诊断时人为添加的外层 `timeout`，保留原始 2 条样本、每条 7 段上下文及全部五个指标，耗时 78.91 秒，退出码 0，10 项指标结果均无执行错误。结果见 `results/domain-deepeval-unbounded.json`。
- 更正：此前 15/20/45 秒终止均来自排查命令设置的外层时限，不是 DeepEval 或产品的默认限制，不能据此认定系统卡住或模型服务不可用。本次未删减或合并实际上下文，也未改变模型客户端配置。
- 该运行仅验证候选样本上的评测执行流程；分数及现有示例阈值不构成正式质量结论或发布门禁，judge 判断仍待人工核验。
## 仍待验证

- 全库干扰文档、KG 和重排链路仍未覆盖。
- 需要验证 DeepEval judge 在中文领域流程问题上的稳定性及与人工判断的一致性，再决定是否设定阈值。
- 需要把引用、拒答、延迟和成本纳入独立的可复现测量；它们不能由五个 RAG metric 自动覆盖。

## 下一步探索顺序

1. 先完成产品能力地图和真实场景分类：导入/解析、单文档问答、多文档检索、数值与集合请求、引用追溯、拒答与澄清、reasoning。
2. 为每类场景定义输入、预期行为、可观测证据和人工判定规则，优先建立小规模高质量产品集。
3. 检查产品实际暴露的检索排序、上下文、引用和 reasoning 轨迹，确定哪些 DeepEval metrics 有可靠输入，哪些只能做诊断。
4. 用人工标签校准 judge，再决定指标汇总、告警范围和是否需要门禁；在此之前不固化阈值。
5. 将公开 benchmark、产品场景集和离线契约检查接入同一报告，再考虑扩展 KG、重排、PDF/OCR、中文领域数据及完整性专项。
