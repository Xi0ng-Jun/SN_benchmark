# 能力评测数据与结果协议（草案 v1）

> 设计草案：本页定义过渡期数据协议，只有代码和具体 run 的实际字段才构成当前契约。


2026-09-10。本文件定义下一阶段的协议，**当前 runner 尚未消费新字段**。历史 JSONL 保持原状；新协议以独立 sidecar/导出承载，不就地迁移 baseline。[能力矩阵](product-capability-matrix.md)给出各字段用途，[公开历史样例](examples/drop-smoke-case.json)保留现有真实形状。

## 四类记录与身份

| 记录 | 主键与必需字段 | 含义 |
|---|---|---|
| Case | `schema_version`, `dataset_version`, `case_id`, `capability_ids`, `question`, `corpus_id`, `documents`, `references`, `answer_spec`, `gold_evidence`, `expected_behavior`, `split`, `annotation` | 固定输入及评分真值；不能携带运行生成结果 |
| Run | `run_id`, `cell_id`, `dataset_version`, `corpus_hash`, `mode`, `config_pair_id`, `product_revision`, `benchmark_revision`, `input_hashes`, `effective_config_hash`, `scoring_protocol_hash`, `judge_identity`, `planned_output_keys`, `isolation` | 运行前冻结的身份、计划分母与隔离证据；密钥、服务地址不写入公开记录 |
| Output | `run_id`, `cell_id`, `case_id`, `product_repeat`, `attempt_id`, `product_status`, `behavior`, `answer`, `request`, `response`, `context`, `retrieval`, `usage`, `latency_seconds`, `raw_artifact_ref` | 原始产品行为与观测；错误也必须有记录 |
| Assessment | output 主键 + `metric_id`, `metric_version`, `judge_repeat`, `status`, `score`, `reason_code`, `reason`, `input_sha256`, `provenance` | 确定性检查、judge 或人工标签的独立结果；不能互相覆盖 |

`product_repeat` 表示重新 Ask；`judge_repeat` 表示对同一保存输出再次判分。重新尝试使用新 `attempt_id`，报告要保留历史尝试。`missing` 由计划键与已有记录差集产生，不靠猜测进程结束补造记录。时间用带时区 ISO 8601，耗时为秒；哈希为文件原始字节 SHA-256。

## 样本字段

- `capability_ids` 可多标签，计入每类时去重；总问题数不能累加能力分桶。
- `documents[]`：`public_id`, `text_sha256`, `text_ref`, `version`。语料包括 gold 与干扰文档，`corpus_hash` 覆盖整个候选库。正文进原生导入，gold、参考答案和行为标签只进评分。
- `references: list[str]` 中每个字符串是一份完整可接受答案。SQuAD annotator 答案是备选；DROP 多 span 必须保留为一个复合答案，不可拆成多个“答中任意一个即可”。原始 number/date/spans 留在 `answer_spec.raw_answers`。
- `answer_spec`：`kind` 为 span/number/date/multi_span/free_text/mixed；包含可选 `unit`, `precision`, `normalization_version`, `operands`, `operation`, `accepted_values`。操作数和 operation 由人核验，不能把模型生成推理变成 gold。整数计数精确比对；近似量只有人审规则允许时才应用单位换算/舍入误差。这是答案容差，不是质量发布阈值。
- `gold_evidence[]`：`document_id`, `start`, `end`, `text`, `required_group`, `annotation_status`。offset 为原文 Unicode 字符下标、左闭右开，要求 `text[start:end] == span.text`；文档 UTF-8 字节哈希另存。切块变化需重新映射，不把原生 chunk ID 当稳定 gold。
- `expected_behavior`：`allowed_actions` 从 answer/clarify/abstain/limit 选取，另有 `reason`, `answerability`, `required_disclosures`。复杂请求可允许回答已有部分并提示限制，但必须写清条件。未审问题的 `answerability=unknown`，不能自动纳入行为正确率。
- `split` 为 debug/calibration/regression，按同文档/问题族分组隔离以降低泄漏；现有 `calibration` 等历史 flag 原样保留，新分组另开 dataset_version。已查看的 regression 样本不会因为展示而冒充新的未见测试集。
- `annotation` 包含 pending/accepted/rejected、评审来源与日期、无法判断原因。模型候选只能 pending；本次样例 `human_review_status=pending`。

## 输出与观测

`product_status` 是 execution 层（success/product_error），`behavior.action` 是 answer/clarify/abstain/limit/unknown，二者独立。成功响应也可能是限制提示；澄清在当前 adapter 下可能表现为 product_error。`behavior` 要带 `evidence_ref` 与 `classification_source`（native/deterministic/human），普通异常无法归类时保留 unknown。

| 新字段 | 现有来源及映射规则 | 缺失时 |
|---|---|---|
| `case_id` / `product_repeat` | 历史 `id` / 输出 `repeat` | 不合成 ID |
| `request` | 当前 runner 显式构造 question/mode；未来保存显式字段、有效默认值、conversation/source_scope | 历史请求仅能按源码重建并标 reconstructed，不声称有 wire payload |
| `context.capture_status` | `context_supported` 与 `context_unavailable_reason` | unavailable；这不是答案“有事实支持”的布尔值 |
| `context.block`, `context.items`, `context.id_map`, `context.synthesis_calls` | context_block、retrieval_context、最后成功 capture 的 id_map、captures | 保留原顺序、完整文本和可能的前缀；不得拿 gold 填入 |
| `context.items[].context_index/handle/source_id/object_id/object_type/element_id` | 使用 capture_context 的 handle 解析及 id_map 关联，不盲目 zip 平行数组 | 未映射前缀仍保留文本，对象字段 null |
| `retrieval.stage` | 历史固定 `final_synthesis_context` | 未捕获 candidates 就不能标 candidates |
| `retrieval.ranking_available`, `ranked_items` | 当前 deterministic.ranking_available=false，ranked_items=null | @K/MRR/nDCG/Contextual Precision 不适用 |
| `response.anchors`, `response.citations` | 原生结构原样保存；object_id 未必是 chunk | 类型未知不强转 chunk_id；引用对象缺失为检查失败，观测缺失为 unknown |
| `response.reasoning_trace` | 原生公开轨迹，另标 `trace_completeness=partial_or_unknown` | 不生成虚构步骤或调用次序 |
| `usage` | provider_logged_events、calls、calls_with_usage、token 部分和、cost | 无值为 null；部分 token 不是总成本；调用计数是日志事件数 |

多节合成时当前 `final_context()` 返回不可用，但保留各次 captures；新协议也不得将各节直接拼接冒充一次最终输入。最终答案改写、Memory 注入或新 synthesis 方法覆盖不全时，先标观测不足再评分。

## 评分适用性和状态

`status` 统一为 valid/skipped/error/missing。valid 必须是有限数值；其他状态 score=null 且有 reason_code。确定性检查观察充分且失败是 valid/0，观察不足是 skipped/unknown_input；unknown 是业务观测值，不是第五种评分状态。error 是适用但执行/校验失败，missing 是计划有而尚未产生。非计划诊断项属于 not_planned，不能伪装成 missing。

| 指标 | 优先级 / LLMTestCase 输入 | 跳过条件 |
|---|---|---|
| GEval Answer Correctness | QA 主；input/actual_output/expected_output；参考值序列化为完整备选答案 JSON | 无可信参考、应澄清/拒答/限制而非普通 QA、产品失败或空答案 |
| Faithfulness | QA 主；input/actual_output/retrieval_context | context 未可靠捕获或为空、分节未统一、非 QA；高分不保证每个 claim 有明确蕴含证据 |
| Answer Relevancy | QA 主；input/actual_output | 产品失败、空答案、行为专用单元；不据此判断正确性 |
| Contextual Recall | 检索诊；input/actual_output/expected_output/retrieval_context | 无参考或真实 context；纯数值答案缺证据说明时只作诊断，不当作完整推理召回 |
| Contextual Relevancy | 检索诊；input/actual_output/retrieval_context | 无真实 context；不是 ID recall |
| Contextual Precision | 排序诊；input/actual_output/expected_output/有顺序的 retrieval_context | 缺参考/context/可解释排名；当前数据全跳过 |
| 行为或引用 GEval | 将来独立 rubric 与 metric_version | 未标注预期行为、缺消息/claim-evidence 映射；本次未接入 |

产品错误或空答案先阻断普通 QA judge，但错误题仍留在全计划分母与行为报告。历史 runner 有这个 eligibility 守卫；新增行为路由尚未实现。主 QA 三项加最多两项适用诊断是本阶段建议；现有 diagnostic flag 会枚举三项诊断，不能声称五项上限已实现。纯检索主指标是确定性检查，行为单元主指标是 action/人工判定，不能强套 QA 三项。

只在 valid 上算均分，同时显示 planned/valid/skipped/error/missing/not_planned，以及产品成功、失败、空答案和人审覆盖。声明质量回归前还需校准；SDK 自带 threshold、rubric 分档或 success 字段均不自动成为门禁。保留历史 judge 与生成共用模型的事实。

## notebook/runtime 隔离

评测单元为 `dataset_version × corpus_hash × mode × config × product_repeat`。每单元独立 notebook、数据库、存储、索引、缓存、日志和进程；同单元共享固定文档库，问题独立 Ask、不传 conversation_id、不接续上一题答案。新协议要求核验响应 conversation_id 的独立性，历史行为以保存数据为准。多轮另立会话单元及 ConversationalTestCase，本阶段不实施。

当前 [open_runtime](../src/rag_eval/benchmark_runtime.py) 将 DATABASE_URL、SILICON_NOTEBOOK_STORAGE_DIR、LLM_CACHE_PATH、EVENT_LOG_DIR、LLM_LOG_PATH 指向 `cells/<dataset>/<mode>/runtime/`，校验 resolve 后路径；CWD 为 cell，避免相对 `.local`/DeepEval 输出落入产品。`.env`/model-services.toml 只读，生成和 judge 的调用观测分开记录。缓存关闭、profile/检索经验/Memory consult/KG 自动抽取/generated questions 关闭是实验控制，不是生产配置更改。

后续执行还须记录：有效 scope 为本单元导入 source 集、无挂载 base/Memory/knowhow、导入/embedding/index 准备状态、有效参数与模型绑定哈希。不同 mode 共享原文哈希而不共享可变 DB。原生 source/chunk ID 随导入变化是正常的。`product_repeat>0` 的全新 runtime 是新设计，历史同 cell 重跑机制不能冒称符合它。

观察 wrapper 会临时替换实例方法/日志类方法，因此同进程不得交错跑多个单元；并行需独立进程。只读离线工作不得调用 open_runtime 或实例化生产 repository。要查历史 DB，使用不创建 WAL/SHM 的只读快照访问；本次仅使用保存 JSON 工件。禁止写生产数据库、配置、源码、恢复 timer、启动 Ask/judge、续跑 baseline，禁止补写历史身份或分数。

## 官方文档依据

2026-09-10 复习：DeepEval 的 [Test case](https://deepeval.com/docs/evaluation-test-cases) 承载输入/实际输出/参考及上下文；[GEval](https://deepeval.com/docs/metrics-llm-evals)承载自定义正确性 rubric；[Faithfulness](https://deepeval.com/docs/metrics-faithfulness)与[Answer Relevancy](https://deepeval.com/docs/metrics-answer-relevancy)用于生成侧；[Recall](https://deepeval.com/docs/metrics-contextual-recall)、[Precision](https://deepeval.com/docs/metrics-contextual-precision)、[Relevancy](https://deepeval.com/docs/metrics-contextual-relevancy)用于检索诊断。

[Metrics 总览](https://deepeval.com/docs/metrics-introduction)区分端到端、trajectory、component 与 standalone；本阶段复用保存输出的 standalone metric.measure，不新增生产 tracing。[Evaluation 总览](https://deepeval.com/docs/evaluation-introduction)提供 evaluate/CLI、dataset 与多轮编排；这是可用能力，并不表示本仓库已改用它们。官方新 score-only API 与本地安装需分别核对，本次不改 SDK 参数。

[Benchmarks 总览](https://deepeval.com/docs/benchmarks-introduction)的标准模型 benchmark 有固定 scorer。现有 SQuAD/DROP 是经改造的产品 RAG 评测：候选库与 Ask 输入已变化，使用自定义 judge；不得命名为官方 DeepEval DROP/SQuAD 标准分数或与榜单直接比较。