# DeepEval 公开评测起步方案 v1

> 历史设计：本页的起步题量和旧执行边界已被当前选题、Notebook 和原生 Agent 文档替代；保留用于回溯。


**2026-09-16 更新：IFEval 已取消人工正反例审计前置条件，N/R 直接调用 DeepEval 4.2.2 verifier。冻结数据中的 pending/audited 字段仅作来源归档，不阻止问答或评分。新 scorer 与历史记录分开，详见 [IFEval 直接评分与服务器使用说明](ifeval-direct-scoring.md)。下文早期阶段记录中的审计要求已被此决定取代。**

> 历史方案。2026-09-14 用户明确选题规模不设预先上限，具体范围以[十套公开评测任务与选题方案](public-benchmark-selection-plan.md)为准。本页每套 20 题、每类前 10 题和 DROP 首个满足题量要求 section 的安排已被新设计替代；已有冻结 bundle 保留原身份，选择器尚未按新设计修改。最新系统接入与离线回归进度见[状态文档](evaluation-status.md)。当前测试与实验暂停。

日期：2026-09-10。状态：**P0/P1 基础和 N/R 执行、报告入口已写入代码，未运行或测试新增套件**。本地核对版本为 DeepEval 4.2.2；在线文档可能随版本变化。首批实现见[代码交接](deepeval-public-starter-implementation.md)，最新接入与待验证边界见[执行与报告说明](deepeval-public-starter-orchestration.md)。

## 1. 当前选择：先用现成题目和评分建立参照

用户希望先复用 DeepEval 已接入的公开 benchmark，再设计 Silicon Notebook 特有的业务场景。因此当前顺序调整为：公开评测起步 → 产品场景广度扩展 → 按发现的问题加深诊断。[业务广度计划](product-capability-breadth-plan.md)和[深度方案](product-capability-evaluation-plan.md)保留为后续设计依据。

本版推荐五类现成任务：**事实阅读 SQuAD、数值/离散推理 DROP、是非阅读 BoolQ、逻辑阅读 LogiQA、指令遵循 IFEval**。先使用公开原题、公开参考和内置评分，不生成新的业务真值。SQuAD/DROP 提供已有能力锚点，后三类增加任务种类。

必须区分两个评测对象：

| 路径 | 实际输入与执行 | 可以回答的问题 | 不能直接推出的结论 |
|---|---|---|---|
| N：DeepEval 内置 benchmark 模型参照 | SDK 标准题目提示词（含原文、选项及配置的示例）→ 被测模型 → 内置 scorer | 模型拿到题目所需信息后能否完成这些公开任务 | Silicon Notebook 能找到资料、正确引用或利用 Memory |
| R：公开数据的 Silicon Notebook 产品适配 | 原文导入隔离 notebook；问题交给真实 Ask；保存实际上下文和回答 → 产品指标 | 在冻结资料库内，产品能否找依据并回答公开问题 | 原始 benchmark 排行榜分数、完整检索排名质量、领域专业性 |

N 是辅助参照，R 才直接评价产品链路。将 `repo.ask` 包装成 `DeepEvalBaseLLM` 并不能自动保留标准协议：原文放入问题会绕过找资料环节，删去原文则改变原任务。两条路径独立命名、独立报告，不合并总分，也不将分数相减解释为“检索造成的损失”。

## 2. 五个公开评测包

以下是官方已接入的 benchmark；数据由各自公开项目发布，并非 DeepEval 自行创建。表中评分细节以本地 SDK 4.2.2 源码为依据，不能仅按 benchmark 名称推断。

| 场景 / 数据集 | 内置类与数据入口 | 内置主评分 | 与 Silicon Notebook 的对应 | 初期安排 |
|---|---|---|---|---|
| 从一段资料回答事实：SQuAD | `SQuAD`；`rajpurkar/squad`，validation | 内置二元 LLM judge 的答对比例 | 事实回答；产品路径另检查最终证据文档命中 | N 与 R 均纳入 |
| 根据资料计算/判断数字、日期、文本答案：DROP | `DROP`；`ucinlp/drop`，validation | 归一化后的完整预测是否属于参考字符串列表，汇总比例 | 数值及离散推理；不是每题都要求计算 | N 与 R 均纳入 |
| 根据资料判断是否成立：BoolQ | `BoolQ`；`boolq/default`，validation | `Yes` / `No` 精确匹配准确率 | 有证据的是非判断、否定表达理解 | N 与 R 均纳入；最先新增的产品题型 |
| 根据给定条件选出逻辑结论：LogiQA | `LogiQA`；SDK 从公开 GitHub 文本加载 | 选项标签精确匹配准确率 | 条件、必要/充分关系的推理参照 | 先 N；R 留作后续适配候选 |
| 按要求输出指定格式/长度/内容：IFEval | `IFEval`；`google/IFEval`，SDK 使用 train split | 规则检查；每题所有指令同时满足的比例及指令明细 | 明示指令遵循参照 | 历史先 N；当前 N/R 均直接使用固定 SDK verifier |

任务例意：SQuAD 类似“文中事件发生在哪一年”；DROP 类似“甲比乙多多少”；BoolQ 类似“资料是否支持这个说法”；LogiQA 类似“这些条件成立时哪个结论必然成立”；IFEval 类似“回答必须包含指定词且不使用逗号”。这些只是题型解释，不是新增测试题或公开原题引文。

### 评分口径中特别容易误读的地方

1. **SQuAD：内置 scorer 使用 LLM，不是原始 SQuAD EM/token-F1，也不是 GEval。** 必须显式配置 judge，保存模型、提示词与原始返回；不要因 benchmark 能构造成功就接受默认外部模型。主分是该 judge 协议下的判断，未经人审校准仅供观察。
2. **DROP：4.2.2 调用 `quasi_contains_score`。** 尽管名字包含 contains，其实现是归一化后的整个预测等于列表中的某一个字符串，不是任意子串包含。SDK 将 `answers_spans.spans` 拆入列表，多 span 答案可能只答出一个组成项就得分，因此原生分不能证明复合答案完整。保留原生分，同时标记多 span 限制；后续集合完整性检查另列，不能静默修改 scorer 后仍称同一原生分。
3. **BoolQ：格式是大小写固定的 `Yes` / `No`。** 本地 schema 限制这两个值，scorer 仅去除首尾空白后精确比较。产品自然语言长答案不能直接用整段文本做同一匹配。回答 No 是一种可回答结论，不等于拒答或资料不足。
4. **LogiQA：选择题答对不证明解释过程正确。** 本地 loader 使用 `csitfun/LogiQA2.0` 仓库 main 下的 `logiqa/DATA/LOGIQA/test.txt`；需要冻结实际文件 revision/hash，不能只根据仓库名宣称数据版本。一个问题可属于多个推理任务，题目数和任务归属次数分开统计。
5. **IFEval：直接沿用固定版本 SDK 的评分。** 本地 verifier 对未知大类返回失败，部分已知大类内的未实现小类默认返回 True；例如 combination 分支只显式处理 repeat_prompt 后即默认通过。当前按用户要求照 SDK 实现执行，不增加人工正反例审计或另行筛选“已验证”子集。逐条保存 SDK 结果与理由；不将 DeepEval 分数称为原论文 strict/loose 成绩。

这些是读取本地实现发现的口径与风险，不是本次在线实验结果，也没有修改第三方 SDK。SQuAD scorer 不保证像 GEval 一样提供详细解释；保存实际返回，不补造 reason。

## 3. 首轮范围与样本规模

先做小规模 discovery，用于理解题型和检验适配，不宣称代表整套数据或产品泛化能力。以下均为**预算建议，样本尚未冻结**：

| 公开包 | N 目标题数 | 选择方式 | R 首批目标题数 / 模式 |
|---|---:|---|---:|
| SQuAD | 20 | 例如 NORMANS、STEAM_ENGINE 两个官方 task 各最多 10 题 | 20 × chunk/reasoning |
| DROP | 20 | 选择有足够验证题的 history、nfl section；各最多 10 题，记录 number/date/span 构成 | 20 × chunk/reasoning |
| BoolQ | 20 | 原生前 20 题作为实现核验，报告 Yes/No 分布 | 20 × chunk/reasoning |
| LogiQA | 最多 20 个任务归属 | 必要条件、充分条件两类各最多 10 题；另报去重问题数 | 暂不纳入 |
| IFEval | 最多 20 | 历史顺序前 20 题；规则不再按审计结果筛选 | 暂不纳入 |

N 上限 100 次主预测；R 若三个数据集均通过适配审阅并各保留 20 题，则为 60 × 2 = 120 次主 Ask。若数量不足、重复或不适用，按实际清单缩减并公开原因，不用模型造题补齐。N、R 题目可有重叠，但因格式与上下文不同仍是不同协议。

原生 SDK 的限量一般是按数据顺序截取，不称随机抽样或分层代表样本。后续平衡标签、随机抽样或改变题目，应另建版本。SQuAD/DROP/BoolQ/LogiQA 首轮建议显式 `n_shots=0`；IFEval 无此配置。这样是指定配置的小型参照，不能假装复现官方推荐 few-shot 或全量成绩。原生 schema/输出提示也属于被测配置。

次数不等于 API 调用数：产品导入 embedding、内部规划/检索/合成、schema 回退和重试，以及一个 metric 的多次 judge 请求均另记账。SQuAD 还需要内置 judge；其他四类的原生打分为确定性计算。暂不承诺具体费用。

**无需续跑历史 baseline。** 已有 SQuAD/DROP 冻结资料、400 次 Ask 和 smoke 可用于适配审计与结果解释。历史 GEval/Faithfulness/Relevancy 分数保持原身份，不能改标成 N 的原生 benchmark 成绩。新 N/R 试验另建运行目录；R 是否复用历史题目必须在新 manifest 中声明，不能回填历史缺失观测。

## 4. 产品路径如何复用公开数据

### 4.1 输入规则与隔离

- 每个数据集独立冻结候选库；先选问题对应的正证据段落，另加固定同集干扰段落。首版建议每集最多 40 个去重段落（正证据优先，余量作干扰），实际规模公开记录。这是受限候选库，不能与已有 200 段实验直接比较。
- 原文经产品原生文本导入、切块、索引；Ask 只接收问题和预先固定的答题要求，公开答案与 gold 证据只供评测侧使用。文档标题不编码答案，不把正证据单独作为每题 source_scope 提示给产品。
- 先人工审阅问题离开原段落后是否仍可定位对象。例如“他在哪出生”在多个段落中可能含混；保留其不适配原因，不把澄清自动判为错误。首版不改写原题；需改写则另立 adapted 版本并审核。
- 每个 dataset × mode 独立 runtime、数据库、存储、索引、缓存和日志，使用同一冻结语料及产品快照。独立题不传上一题 conversation；不注入用户 Memory、偏好或跨运行检索经验。记录隔离配置与原生 ID 到公开 source ID 的映射，沿用[隔离协议](evaluation-data-contract.md)。
- IFEval 涉及自由输出约束，直接套到有引用要求的 Ask 会引入规则冲突，首批保持 N。LogiQA 去掉段落后常难用独立问题检索到对应题设，需要专门适配审阅，首批保持 N。

### 4.2 主指标、诊断和人工判断

| 产品能力 | 首批数据 | 主检查/指标 | 诊断指标与所需观测 | 人工判断与不适用条件 |
|---|---|---|---|---|
| 事实回答 | SQuAD | 沿用项目 GEval Correctness；参考答案来自公开数据 | Faithfulness：实际送入最终合成的上下文；必要时 Answer Relevancy | 同义表达、问题歧义和 judge 分歧；无答案或调用失败不评分 |
| 数值与离散答案 | DROP | 可无歧义提取完整最终答案时做数字/日期/完整 span 集合核对；否则 GEval Correctness 与人审 | Faithfulness；保存原文、公开答案类型、完整输出和提取状态 | 不能抓正文第一个数字当答案；单位、复合答案、等价日期需规则/人审；未可靠解析记未评分 |
| 是非判断 | BoolQ | 固定要求结论明确 Yes/No；可靠提取后的标签准确率，另报解析覆盖率 | Faithfulness 检查解释是否受实际上下文支持 | 同时给出相反结论或无法解析时不猜标签；确定性标签正确也可能解释错误 |
| 最终证据覆盖 | 上述三集 | 最终 context 对 gold 文档的命中/覆盖，须有完整 ID 映射 | Contextual Recall 仅在参考和实际 context 输入充分时作为语义诊断 | 单段 gold 不保证穷尽所有支持证据；最终 context 不是检索候选排名，不报 MRR/nDCG |
| 引用追溯 | 上述三集的实际引用 | 引用对象与正文锚点能否映射回隔离库原文 | 保留 source/element/anchor 与文本，供逐条查看 | 对象存在不证明逐项断言获支持；Faithfulness 不替代引用支持人审 |
| chunk/reasoning 差异 | 相同适配样本与冻结库 | 配对展示答案检查、状态及有效评分覆盖率 | 按失败、澄清、缺证据、答案错误等类别诊断 | 不只比较成功答案均分；不同适用分母必须显式显示 |

每个产品输出先选择一个主要答案判据，通常配一个 Faithfulness 诊断；相关性或 Contextual 指标按问题需要补充，不强制每题运行所有 metrics。`LLMTestCase.input` 为实际问题，`actual_output` 为完整产品答案，`expected_output` 为公开参考的固定表示，`retrieval_context` 为实际合成上下文，绝不以 gold 原文冒充实际检索结果。

产品层提取与评分是本项目协议，不称 DeepEval 原生 DROP/BoolQ 分数。确定性核对规则须先离线证明能正确处理适用格式，原始答案始终保留。无法获取完整合成上下文时 Faithfulness 不适用；分节合成等特殊路径也不能硬套普通单次 context。没有可比较候选排序时不启用 Contextual Precision 来宣称排序质量。

澄清/拒答/错误仍保存原生状态与原因，但这些公开集未提供完备的行为真值，因此只作现象分类，不发布“澄清正确率”或“拒答能力分”。缺失、失败、不适用不填 0 混入语义均分；同时报告计划数、有效输出数、有效评分数，避免隐藏未完成部分。

## 5. 最小实现顺序与交付

以下是阶段计划。用户随后已授权开始代码实施，并要求暂不运行和测试；本轮已写入 P0/P1 基础逻辑，数据冻结、规则审计和运行验收仍未执行。具体文件与后续顺序见[实施计划](superpowers/plans/2026-09-10-public-starter-implementation.md)。

| 阶段 | 要做的事 | 具体交付与完成依据 | 在线边界 |
|---|---|---|---|
| P0 公开协议冻结 | 核对来源许可、split/revision、样本 ID、题设、答案、shots/schema/scorer；记录 IFEval 指令参数并核对 DROP 多 span | 五个 suite manifest 草案、可读题卡、适用性清单、数据与 scorer 哈希；数量能对账 | 仅公开资料获取/本地分析，不调模型 |
| P1 最小离线适配 | 在 benchmark 内新增原生模型适配与结果保存；复用现有模型客户端但分开 tested model/judge；补 BoolQ 数据适配 | 用预存/伪造响应验证 schema、解析、分母、失败记录；未配置模型时不能落到默认供应商；不改产品 | 仅 mock/本地单元验证 |
| P2 N 小样本模型参照 | 按已冻结配置执行五类适用题，逐类保存原生结果 | 原始 prompt、模型输出、scorer 输出、状态和逐指令结果可回放；执行失败单列 | 用户重新提出在线运行后才进入 |
| P3 R 小样本产品评测 | SQuAD/DROP 复用接入，新增 BoolQ；先各少量题核对导入/捕获，再完成剩余清单 | 三集 × 两模式的实际产物及隔离审计；每个分数能追到问题、答案、证据；不重复已完成条目 | 同样需恢复在线任务；不续跑旧 baseline |
| P4 解释与下一步选择 | 每类审阅至少 5 个样本或全部可用样本（不足 5 时），包含错误、成功及评分争议 | 分套件能力卡、失败案例、适用性限制和待验证结论；人工标签仅由人填写 | 保存结果可离线分析；补 judge 仍属于在线工作 |

P4 的少量人审用于发现评分与适配问题，不等于完成统计校准。之后再选择业务广度套件，例如公开题无法回答的多文档综合、摘要、多轮与 Memory。无需先建 Web 平台、完整历史协议迁移或全量重跑。

最小结果字段复用现有协议，只补 `track=N|R`、suite/dataset/sample/scorer 身份、native task/instruction IDs、实际 prompt/schema、raw prediction、解析状态、适用性与跳过原因。保存 SDK 源码/哈希、模型实际标识、参数、judge 身份；不只写笼统的“accuracy”。协议升级另行版本化。

报告首页按五个公开包和两条路径列出：计划量、完成量、有效评分量、分数名称、分数、失败/不适用数量及代表错误。没有运行的产品项写“未覆盖”，不能用模型参照填格。不设置跨套件总分或发布门槛。

## 6. 首版明确不覆盖什么

- Memory、多轮连续对话、持久偏好：IFEval 的当题指令遵循不能替代这些能力。
- 摘要与文档导读：DeepEval 提供 SummarizationMetric，但本次未核实到可直接替代 notebook 业务数据的专门内置公开摘要 benchmark；有 metric 不等于已有完整场景/数据/真值组合。
- 多文档综合、全量清单完整性、可靠拒答/澄清、逐断言引用支持：仍需专门数据和标注。现有五包不能声称全部覆盖。
- 中文及半导体专业能力：不翻译原题后沿用原版成绩，不用英语通用题分数宣称领域达标。
- 交互可靠性、资料更新后的知识一致性按用户要求排除；KG、重排、PDF/OCR 继续不做。

GSM8K 与 DROP 的首轮数值能力部分重叠，BBH 会引入更多任务选择，MMLU/HumanEval 与本轮阅读式问答距离较远，暂不铺开。TruthfulQA 主要提供常见误区相关的真实性参照，不能替代基于 notebook 资料的 Faithfulness 或拒答评测，列为以后候选。

## 7. 本次核对依据和状态

官方资料：

- [Benchmarks introduction](https://deepeval.com/docs/benchmarks-introduction)、[Metrics introduction](https://deepeval.com/docs/metrics-introduction)、[Evaluation introduction](https://deepeval.com/docs/evaluation-introduction)。
- [SQuAD](https://deepeval.com/docs/benchmarks-squad)、[DROP](https://deepeval.com/docs/benchmarks-drop)、[BoolQ](https://deepeval.com/docs/benchmarks-bool-q)、[LogiQA](https://deepeval.com/docs/benchmarks-logi-qa)、[IFEval](https://deepeval.com/docs/benchmarks-ifeval)。
- [SummarizationMetric](https://deepeval.com/docs/metrics-summarization)、[KnowledgeRetentionMetric](https://deepeval.com/docs/metrics-knowledge-retention)。

本地实现核对根目录：`.venv/lib/python3.13/site-packages/deepeval/`，不随 Git 分发。主要路径为 `benchmarks/{squad,drop,bool_q,logi_qa,ifeval}/` 下同名实现、`benchmarks/schema.py`、`scorer/scorer.py`。重点核对了 loader/split、提示词、schema、评分函数以及 IFEval 默认分支；未声称已完成数据下载、规则正反例验证或 SDK 运行验收。

本次未启动新增预测、产品 Ask、judge 或数据导入；未恢复 baseline 或 weekly timer，未修改生产代码/配置。首批代码采用上述顺序前缀和数量上限，实际样本尚未冻结，参数效果尚未验证；未设置质量阈值。