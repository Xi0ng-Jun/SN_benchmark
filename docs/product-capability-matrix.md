# Silicon Notebook 产品能力矩阵

> 背景参考：本页是早期能力矩阵，不代表当前每项能力已有真实实验覆盖。


核对日期：2026-09-10。这是第一阶段评测设计，不是已完成的质量验收。总方案见[详细方案](product-capability-evaluation-plan.md)，字段见[数据协议](evaluation-data-contract.md)，实物见[一题到底](evaluation-case-walkthrough.md)。

## 产品入口与观测事实

依据只读检查的 [产品 README](../../project/README_zh.md)、[产品/API 文档「检索模式（问答）」](../../project/docs/product-and-api_zh.md#检索模式问答)、[Ask 模型](../../project/backend/app/models/ask.py)、[Ask 服务](../../project/backend/app/services/ask_service.py)和 [ReasoningRetriever](../../project/backend/app/services/reasoning_retrieval.py)。产品源码链接需要同级 `project/`；独立克隆时可读下表及公开样例。

- `chunk` 是大召回、选择、长上下文合成；`reasoning` 是 plan/retrieve/reflect/answer，**没有 KG 也能运行**。本阶段只测纯文本文档 Ask。
- 当前 benchmark 直接调用 `repo.ask(notebook_id, AskRequest(question=..., mode=...))`，没有经过 UI 自动路由、HTTP 意图预览或 MCP。结论必须带上入口，不能宣称覆盖整个交互产品。
- [capture_synthesis](../src/rag_eval/system_capture.py) 保存 `_answer_chunks/_answer_mix/_answer_reasoning` 的 `baseline_sink`；最后一次成功且非分节合成才提供 `retrieval_context`。它不是完整 provider 请求或全部候选检索日志。
- [evidence_checks](../src/rag_eval/benchmark_runtime.py) 的 `retrieved_document_ids` 来自最终上下文 source 映射；`ranking_available=false`。`k1,k2` 是合成顺序，不证明检索排名。
- 原生 `Citation` 使用 `source_id + element_id`；正文 `[k1]` 要通过 `response.anchors[].key` 连到 object/source/element。`Citation.label` 是显示标题，不能当作 `[k1]`。
- 原生 `reasoning_trace` 是经过公开投影且可能截断的轨迹；观测到的步骤可以分析，不足以自动构造完整 DeepEval trajectory。
- 原生 repository 澄清门目前抛 `ValueError`，历史输出保存异常类型和栈帧，不一定保存消息。普通 `ValueError` 不能直接归为澄清，也不能仅凭失败状态推断未调用模型。

## 能力—数据—观测—指标—检查—人工判定

表中“主”指对应能力的主要信号，不表示质量门槛；“诊”指定位原因的辅助信号。人工标注的产品场景仍待建立。

| capability_id / 产品问题 | 评测数据与期望 | 产品观测 | DeepEval | 确定性检查 | 人工判断 / 不适用条件 |
|---|---|---|---|---|---|
| `retrieval.document` 找到目标文档了吗 | SQuAD/DROP 冻结候选库；产品事实定位题；gold 文档与干扰文档 | 最终 context 的 source→public ID；未来另采候选列表及阶段 | 诊：Contextual Recall/Relevancy | 主：final-context 文档 hit、gold 文档覆盖；有真实排名才报 Hit@K、MRR、nDCG | 确认 gold 与候选库是否足以回答；当前无排名，@K 排序指标不适用 |
| `retrieval.evidence` 正确片段进入生成了吗 | SQuAD 字符 offset；产品人工证据区间；多文档必要证据组 | 完整 context、handle/id_map、文档哈希、切块映射 | 诊：Contextual Recall | 主：gold source 内答案 span 到达；有证据组时 all-required 覆盖 | 文档命中不等于证据齐全；DROP 无完整证据标注，证据级 recall 不适用；子串命中不证明语义支持 |
| `answer.fact` 事实回答是否准确、完整 | SQuAD 多 annotator 可接受答案；产品术语/限定条件题；允许答案及反例 | question、answer、真实 context、产品状态 | 主：GEval Answer Correctness、Faithfulness、Answer Relevancy | 可抽取短答案的 EM/token F1 作诊断；检查缺失答案与持久化 | 等价表达、遗漏、矛盾、范围/工况；无 gold 跳过 correctness，无可靠 context 跳过 faithfulness |
| `answer.numeric` 数字、日期、计算是否正确 | DROP 原始 number/date/spans；产品经人审的操作数、单位、运算、舍入规则 | 最终答案；context 操作数；公开解释（若有） | 主：Correctness；Faithfulness/Relevancy；诊：计算解释 GEval 仅在另立协议后 | 主：唯一可解析数值/日期/单位与允许答案比对；受控运算复算 | 操作数语义、时间精度、百分比/百分点、合理推导；歧义多数字答案标 unknown，不能任取第一个数字；无步骤不能声称验证了内部推理 |
| `citation.traceability` 引用真实可回查吗 | 同版本 source/element/chunk 快照；正文引用与预期支持 claim | answer `[k]`、anchors、citations、id_map、scope、对象正文哈希 | 诊：引用支持 GEval 为后续定制，当前未接入；Faithfulness 只做全答案诊断 | 主：正文锚点解析、对象存在、归属/scope、引用原文与 context 可追溯；分别报对象及锚点分母 | 每条 claim 与它实际引用的片段是否相符、关键 claim 是否漏引；对象存在不能代替语义支持，quoted_span 截断不等于引用造假 |
| `reasoning.paired` reasoning 相比 chunk 改善什么 | 同一 case、同一固定文档库与模型配置；事实/计算/多文档成对样本 | 两份独立运行结果、真实 context、公开 trace、provider usage 与延迟 | 主：两模式相同三项 QA 指标的配对差；trajectory 指标暂不启用 | 证据、引用、状态转移；单独比较延迟/调用/已观测 token | 增益原因与不必要步骤；公开 trace 不完整时 Task Completion/Step Efficiency 不适用；费用未知不填零 |
| `behavior.clarify` 是否适时请求澄清 | 指代缺失题与明确对象对照题；人审可回答性、缺失信息和允许 action | 原生状态、错误栈/结构化原因、澄清消息、调用观测 | 可选 GEval 清晰度/帮助性，需新 rubric；普通 QA 指标跳过 | 主：期望 action 与实际 action 混淆矩阵；假阳性/漏澄清；输入适配问题单列 | 公开题移入 200 段库后是否失去指代；17 次历史拦截不能全算产品 bug；没有消息无法评帮助性 |
| `behavior.abstain` 缺证据时是否承认不知道 | 语料内不可回答问题；事实冲突、范围排除的对照样本 | 答案/拒答消息、实际 scope/context、原生错误 | 可选 GEval 拒答解释；不能用 Relevancy 替代行为标签 | 主：应拒且拒/可答却拒/无证据仍答计数；服务商拒绝另列 | 判断拒答合理性、是否夹带编造事实；传输错误和 provider 拒绝不是产品有意拒答 |
| `behavior.completeness` 是否诚实披露完整性边界 | 有限文档清单、小规模全量 gold；去重、分组、聚合等能力限制题 | `completeness_unavailable`、枚举/预览覆盖、终态 trace、回答限制说明 | 可选 GEval 限制披露，未接入 | 主：完整性声明与已知全集/覆盖一致；字段缺失记 unknown | 当前产品有部分精确枚举能力，不能一概预设拒答；区分目录完整、进入合成完整、答案语义完整。本阶段只定义与离线审阅，专项不扩展 |

## 检索统计口径

令 G 为人审 gold 集合、R 为指定阶段可观测结果集：hit = `1[G∩R 非空]`；coverage = `|G∩R|/|G|`；all-required = `1[G⊆R]`。G 为空时 coverage/all-required 不适用，不能得满分。当前 R 是最终合成证据，不是检索候选全集。

未来排名齐备后，以去重且保持首次排名的列表计算 Hit@K、MRR；nDCG 还需要固定二元/分级相关性标注。公开集仅有部分正证据，未标注文档不能天然认定为负例；完整召回结论要依赖完整标注。片段跨 chunk 时用同一 source 的区间并集检查，不能把不同 source 的同文视作命中。无区间映射则只报已有子串检查。

## 模式比较与故障解释

配对键为 `(dataset_version, case_id, corpus_hash, split, config_pair_id, product_repeat)`，模式是实验变量；生成模型、embedding、输入与 judge/rubric 身份须兼容，单元 notebook/runtime ID 可以不同。原生 chunk ID 不跨运行直接比较，统一映射到 public 文档与原文区间/哈希。

每项指标只在两边均 valid 的同一配对子集上报告 `reasoning - chunk`，显示配对数与未配对原因。同时在全部计划题上报告 answer/clarify/abstain/limit/error/unknown 转移和缺失数，避免只看成功题的选择偏差。按能力、数据集、split 分组，不合成质量与成本总分；judge 重评与产品重跑不增加独立问题数。

诊断顺序：先核对输入/采集完整性，再看预期行为、最终证据、答案正确性、引用支持，最后审阅 judge 分歧。证据缺失且答案错误提示检索问题；证据齐而答错提示生成/计算问题；答案对但引用失效是引用问题。以上是定位线索，不是自动根因证明。