# 评测状态

## 已确认的 Notebook 实验计划

用户确认先执行四套资料型 benchmark 的 SN chunk/reasoning 主实验，并加入 QMSum BM25 对照。见 [实验计划](notebook-benchmark-experiment-plan.md)。本地仅整理代码、文档与同步数据修正；服务器负责真实数据重新准备、运行与评分。向量检索和全文输入基线仍待实现，论文数字须区分参考值、同子集重算和同协议重跑。

2026-09-18：已实现 QMSum BM25 turn 检索 + 显式 tested 生成模型，保存为 `mode=bm25`，复用冻结资料、ROUGE 和上下文诊断；支持 Dashboard 查看及独立同题 JSON/Markdown 比较。执行前固定计划与数据，逐项保存回答/分数；报告重建核验输入、prompt/context，缺失与错误不补 0。详见 [方法、服务器命令与比较边界](qmsum-bm25-baseline.md)。

本地 **358 项 Python 离线回归通过（含 25 项新增 baseline/比较测试），网络尝试 0；18 项 Dashboard JavaScript 回归通过**，两条新 CLI 帮助与补丁空白检查通过。独立审阅发现的报告输出目录隔离、单项分数中断落盘及缺失原因展示已修复并复核。数据全部为合成 fixture；使用真实模型适配器但替换网络传输，本机无 rouge-score，尚未验证真实 ROUGE 或真实模型请求。未下载数据、未启动 SN 或修改生产/timer。

服务器应对齐 SN 最终回答角色的实际模型与采样设置，先做一个 QMSum 会议分区验收再决定全量安排。报告不会自动证明两侧生成模型一致，SN 内部提示与 BM25 提示也不同；这是端到端系统对照，不能把差值仅归因于检索，更不是论文榜单复现。原严格 chunk/reasoning 配对保持原规则，新代码不要求服务器重跑正在进行的 SN 实验。

## 服务器真实文件预检后的适配修正

用户提供的服务器汇报（版本 525633f）确认目前四套均仅 prepare，未导入/Ask。发现 QASPER 139 条段落映射排除中 138 条为空白差异；QMSum 原始 17 条空/空白 turn 被临时填占位；QAMPARI 有 5 处空字符串 alias，ELI5 实际可直接适配 1000 cases。真实数量与哈希来自服务器汇报，本机未下载或复算。

本地已修正适配器：QASPER 空白归一化定位但保留原证据/原文，QMSum 原样保留空发言和位置，QAMPARI 原样保留空 alias 与答案组分母。新 prepare 标记 notebook-data-v2，缺版本字段的旧包继续按原规则重建；QASPER/QMSum 证据诊断分别使用新 scorer，主答案指标不变。服务器应在新目录重建 QASPER 和原始 QMSum，QAMPARI/ELI5 可新增准备，MultiHop/ASQA 原包可保留。详见[修正与服务器交接](notebook-data-corrections.md)。

本次 **333 项 Python 离线回归通过，网络尝试 0**，含旧版冻结 fixture、空值/空白映射、分数与分母检查；未下载、未运行 SN/模型、未修改生产。本轮将修正与实验计划一并交接；服务器必须核对拉取版本已含 notebook-data-v2，不能仅凭旧 525633f 判断已获得修复。

## 2026-09-17：Notebook 场景四套接入

新增 QASPER、MultiHop-RAG、ALCE、QMSum 的本地原始文件适配、gold/资料分离、不可拆分资料分区、SN Product R 执行、评分与 Dashboard。独立协议 `sn-notebook-benchmarks-v1`，旧十套保持原解释。详见[服务器使用与指标表](notebook-benchmarks.md)及[实施计划](superpowers/plans/2026-09-17-notebook-benchmarks.md)。

QASPER 按论文、QMSum 按会议；MultiHop 使用完整 corpus；ALCE 每题完整候选，不按 gold 缩小检索范围。容量可显式声明并仅写入隔离进程，不能据容量删题或拆散单题资料。新增连续主指标独立解释，澄清/缺评分不补 0。ALCE 引用通过真实 SN anchor/对象映射到官方编号，官方模型分显式执行、保存来源后挂接到新 run，不覆盖原始实验。

**验证：319 项 Python 离线回归通过，无跳过，测试进程网络尝试 0；18 项 Dashboard JavaScript 回归通过。** 独立审阅的上下文前导说明映射、Dashboard 分区标签两项问题已修复并复核。当前机器未安装 rouge-score，缺依赖分支已验证；真实 ROUGE、ALCE 模型推断、官方完整文件与服务器 SN 验收未执行。不下载数据/权重、不改生产、不恢复 timer，不推断服务器正在进行的实验成绩。

2026-09-17 同步：上述代码与文档已提交并推送到远程开发分支 `docs/public-benchmark-agent-expansion`（未合并 `main`）。推送内容仅为本机实现与离线回归，不改变上面的验收状态：真实数据适配、依赖安装、服务器 SN 端到端执行与 ALCE 模型评分仍需服务器完成。

## 2026-09-16：IFEval 直接评分

按用户要求取消项目自加的人工正反例审核门槛：Native 正常生成回答，Native/SN Product 均直接调用固定版本 DeepEval verifier，按所有指令是否通过评分。SN 完整正文及引用原样参与检查。新运行记录独立策略和 scorer；已有数据包、分区计划和旧报告保持兼容，不改历史 N/A。详见 [实施决定与服务器使用](ifeval-direct-scoring.md)。本地全量离线回归 **281 passed，联网尝试 0**，含本地合成样本与真实 SDK；独立代码审阅未发现阻断问题。未启动模型实验或改动 SN；服务器实验进度仍以服务器记录为准。

更新时间：2026-09-16

## 当前离线报告开发

已将旧表格 Dashboard 扩展为结果探索器：组合标签筛选、状态/分数图、分面均值、保存比较组及共同题配对图、条目详情和原始记录查看。数据以 planned ledger 为基表，问答按 run×case 去重，缺评分条目仍保留；没有跨指标总分。相同资料/配置/评分口径且配对身份一致的 chunk/reasoning 可计算共同有效题差值；不同轨道或模型不强行配对。

[四列指标表](benchmark-metrics-reference.md)按代码列出十套 Benchmark 的 Native/Product 默认 scorer、输入和公式/步骤，纠正把通用 RAG 指标当作全部默认指标、把 DROP Product 描述为自动数字匹配的说法。使用与服务器执行步骤见[Dashboard 指南](experiment-dashboard.md)。

本次验证仅使用离线合成记录，不构成 Benchmark 成绩。公司服务器真实实验由用户另行执行，未在本地重新运行；SN、旧 baseline 和 timer 未改动。下方此前“未实现分区”“测试暂停”等描述是各日期的历史状态，不代表当前代码或服务器进度。

验证结果：26 项 Python 报告回归、13 项 JavaScript 筛选/统计/配对回归通过；Chromium 实际打开合成报告，验证组合筛选、图表、保存比较组、六类详情、导出、搜索、Native/judge 展示、文本注入隔离及 390px 窄屏，无页面脚本错误或 HTTP 请求。Python wheel 构建成功并包含四个前端静态资源；不需要在服务器安装浏览器依赖来生成报告。

同日后续修正：侧栏标签与数量按其他已选条件和搜索词动态联动，隐藏无匹配的未选项，保留已选零结果条件和同组多选。新增 3 项回归先复现旧行为失败，修正后 16 项 JavaScript 回归与 10 项 Dashboard Python 回归通过；Chromium 验证标签联动、零结果恢复、搜索、清除/移除/恢复比较组、焦点与节点保留及窄屏，无页面错误或 HTTP 请求。

## 最新选题设计

用户明确规模不设预先上限，重点确定怎么选、选哪些。已沉淀[十套公开 Benchmark 任务与选题方案](public-benchmark-selection-plan.md)：按能力确定 task/split，纳入选定范围内全部符合条件的原题，替代每套 20 题及每类前 5/10 题的预算建议。MMLU 四个学科、BBH 四类推理任务仍是当前建议范围。

完整题单与 notebook 分库分别设计，现有评测侧 40 篇单库限制不作为选题配额；选择器调整、多分区执行与整体覆盖报告尚未实施。当前只更新文档，测试与实验暂停，未下载或冻结数据，未新增代码改动。下方 205 项为此前完成的离线回归，不验证本次新选题/分库设计。

## 最新离线回归

用户已授权执行离线回归。当前分支 `docs/public-benchmark-agent-expansion` 的全量测试结果为 **205 passed**，无失败、跳过或警告，网络请求尝试为 0。新增真实 SDK 的七套系统评分衔接与三套 legacy 适配检查；SN 执行使用测试替身，未启动服务或调用模型。详见[2026-09-14 回归记录](offline-regression-2026-09-14.md)。

上一阶段代码已以 `63214c7` 提交并推送到对应远程分支，未合并 main。本轮修正测试目录定位与正则警告、补充回归用例并更新文档；尚未提交。新公开样本仍未正式冻结，SN 在线验收和人工校准仍待开展；下列“未运行”描述保留为此前阶段的历史事实。

## 公开 Benchmark 与 Agent 扩展设计

此前已完成[公开题的 SN 系统接入](sn-public-system-adaptation.md)代码与独立静态审阅，实施入口为[系统适配计划](superpowers/plans/2026-09-13-sn-public-system.md)。2026-09-13 仅执行补丁空白检查；2026-09-14 已完成上述离线回归并实际调用客观 SDK scorer/verifier，SN 和在线模型调用仍未执行。

此前将 Task 1–5 全部勾选为完成不准确：新增 Native 请求尚未接通，Product 不适用分支也不能完整落盘。本轮按[代码补齐计划](superpowers/plans/2026-09-13-expansion-code-completion.md)修正这些缺口；代码和回归用例仅作静态审阅，未运行测试或应用，不代表验收通过。

新增 MMLU、GSM8K、TruthfulQA MC1、HellaSwag、BIG-Bench Hard 的 Native 请求与官方 scorer 分派，使用 `public-expansion-v2` 冻结原始数据、答案映射和 SDK 源码/资源身份。旧扩展 v1 bundle 不自动迁移，需要以后重新准备；原五套 `public-starter-v1` 保留兼容路径。归一化结果只供诊断，不能替换官方 scorer 的原始答案输入。

此前新增五套 Product 的 N/A 记录现在保留在显式 legacy 路径。新 `sn-public-system-v1` 为 LogiQA/GSM8K/BBH/MMLU/TruthfulQA MC1/HellaSwag/IFEval 增加正常 SN Ask：LogiQA 使用配套文章，其余导入原题面和候选项，不导入解答或正确标签。无需自行配教材或填写虚构的人审意见。新系统适配由独立 registry 声明，冻结 Native manifest 的 product=False 不修改。

新路径保存原生澄清、无答案、错误、答案正文与引用；文本拒答只作待核验候选。主指标为适配后的官方客观 scorer/IFEval verifier，诊断引用和资料覆盖分开，不默认加 Faithfulness。此前 IFEval 缺少规则审核时为 N/A；2026-09-16 已取消此前置条件，当前直接调用 SDK 评分。Agent 和 DAG 指标未接入；当前读取到的 reasoning_trace 至多标记 partial，完整度不是 Agent 分数。离线审计入口为 `scripts/check_public_expansion_offline.py`。

新增数据尚未冻结，Native/Product/Agent 实验均未执行；生产、旧 baseline 和 timer 保持原状态。阶段边界见[扩展设计](public-benchmark-agent-expansion-design.md)。

此前已完成 DeepEval 调研与环境链路验证。SQuAD / DROP 两模式持续评测曾获授权执行，现已暂停；当前授权工作已推进到公开起步方案的代码与逻辑实现，用户要求本轮不运行和测试。既有实验不充当质量基线。

## 公开起步方案首批代码（未验证）

已新增 `starter_protocol/native/model/product/results.py` 与本地准备入口 `scripts/prepare_public_starter.py`，覆盖五个 suite 选题和数据身份、SDK 模板/scorer 接口、显式被测模型/judge、BoolQ 产品解析、候选库准备、结果分母和失败记录。[代码交接](deepeval-public-starter-implementation.md)列明输入和限制。

随后已按用户“做吧”的指示编写执行编排和报告接入：新增 `starter_runtime/runner/report.py`、`run_public_starter.py`、`report_public_starter.py` 和模型配置示例，见[执行与报告说明](deepeval-public-starter-orchestration.md)。代码串起单套件 N/R、先保存回答后评分、阶段/错误记录、缺失分母与 chunk/reasoning 配对；报告入口只读本地产物。

本轮未执行新代码、未测试、未下载/冻结新数据、未创建模型/runtime；因此不是 P0/P1 或 P2/P3 验收完成。IFEval 规则正反例、人审适用性、模型协议一致性、产品隔离和新入口全部待验证，历史 baseline/timer 保持暂停。旧测试通过数不适用于新代码。

## 本次能力评测设计交付

最新选择是先用 DeepEval 提供的公开评测构成起步方案，见[公开评测起步方案](deepeval-public-starter-plan.md)：五类模型参照、三类首批产品适配，均未运行。后续再构造产品业务场景。

此前用户选择广度优先；已新增[广度扩展计划](product-capability-breadth-plan.md)与[Memory/Agent 上下文说明](memory-and-agent-context.md)。拟覆盖八类用户任务，排除交互可靠性、资料更新一致性。所有新套件均处于设计阶段，不能标为产品已验证；现有 400 次 Ask 不覆盖本次规划的多轮、Memory 或偏好套件。

已整理[详细方案](product-capability-evaluation-plan.md)、[能力矩阵](product-capability-matrix.md)、[数据协议](evaluation-data-contract.md)、[真实样例说明](evaluation-case-walkthrough.md)及[后续实施计划](superpowers/plans/2026-09-10-product-capability-evaluation.md)。公开 smoke 单题快照随 Git 保存；字段、引用数量和历史分数可脱离本地 var 阅读。新协议、行为分类与报告适配仍是待实施设计，没有新增模型调用或填写人工标签。

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

具体在线评分任务继续暂停。当前已为七套公开题编写 SN 系统适配，SQuAD/DROP/BoolQ 沿用已有产品路径；此前 LogiQA/IFEval 仅作模型参照的安排已由新系统协议扩展。下一步在允许执行后先做离线回归，再冻结小样本，在恢复在线任务后建立模型参照及产品评测。之后再设计真实业务人工核验集。公开成绩不作为产品领域质量的唯一依据。

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
