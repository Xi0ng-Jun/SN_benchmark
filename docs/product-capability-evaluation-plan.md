# Silicon Notebook 产品评测体系详细方案

> **最新优先级：先复用 DeepEval 公开评测。** 见[公开评测起步方案](deepeval-public-starter-plan.md)，先建立公开任务的模型参照与产品适配，再进入业务广度场景。

> **此前方向：广度优先。** 用户在本方案之后选择先扩展可评测的用户任务，并明确排除交互可靠性、资料更新后的知识一致性。后续业务设计见[能力广度扩展计划](product-capability-breadth-plan.md)，Memory 与 Agent 上下文见[专门说明](memory-and-agent-context.md)。本文保留为已有能力的深度设计依据，不再要求先完成全部协议/引用/报告改造才开始新增场景。在线暂停与生产只读边界继续有效。

更新：2026-09-10。基于仓库已有详细草稿完成产品实现与官方文档核对；以下设计保持实验性，未固化产品契约或质量阈值。本次在 `docs/product-capability-evaluation` 分支交付文档与公开历史样例，未新增运行代码。

## 第一阶段已形成的交付

| 交付 | 内容 |
|---|---|
| [产品能力矩阵](product-capability-matrix.md) | 9 个能力条目，覆盖检索、事实、数值、引用、模式对比及澄清/拒答/完整性限制；逐项列出数据、观测、指标、确定性检查、人审与缺失条件 |
| [统一数据协议草案](evaluation-data-contract.md) | Case/Run/Output/Assessment、历史字段映射、分母、状态、指标适用性、notebook/runtime 隔离；新字段尚未实现 |
| [真实样本全链路说明](evaluation-case-walkthrough.md) | 一条已完成 DROP smoke 从原始 QA 到实际 context、引用及三项历史评分的解释 |
| [可分发的公开样例](examples/drop-smoke-case.json) | 保存真实文档、QA、产品输出、全部实际上下文、评分与原工件哈希；人审仍 pending |
| [后续最小实施计划](superpowers/plans/2026-09-10-product-capability-evaluation.md) | 离线协议、可追溯性检查、报告解释、人审及后续在线进入条件 |

下文原有示例用于说明目标结构，不表示当前 API 已暴露这些字段。具体字段语义以本次数据协议草案与真实样例为准，不能把示意 JSON 当成历史观测或现有 runner 的输入契约。

### 核对后必须保留的区别

- 现有最终 context IDs 不等于候选检索排名；当前只能报最终文档命中/覆盖，不能给这些记录计算 MRR/nDCG 或命名为候选 Hit@K。
- 原生引用是 `Citation.source_id/element_id` 加正文 `anchors.key/object_id` 的关联，不是下文教学例子的固定 chunk_id 结构。正文锚点数与 response.citations 数分别统计。
- `context_supported=true` 只表示合成上下文可采集，不表示答案语义得到支持；分节合成当前不可用于普通 Faithfulness。
- 原生 repository 澄清门抛 `ValueError`，不是已存在的 `clarification_required` 异常类。结构化行为分类是待实现协议；未观测模型调用不能默认填 false。
- reasoning 无 KG 也可用；公开 trace 并非完整模型/工具调用轨迹。部分清单/计数已有精确枚举支持，完整性限制必须按请求与观测判断。
- baseline 仍是 400 次 Ask、383 成功、17 次 reasoning 澄清拦截的未完成评分运行；smoke 完整性通过与语义质量达标是不同结论。人工校准仍待完成。

### 路线取舍

优先把能力矩阵、可追溯样例和离线协议做实，再补人审和报告。扩大公开 benchmark 会增加调用而不能弥补解释缺口；先建 Web 平台则增加维护而缺少稳定语义。本阶段选择文档与保存结果的离线分析，暂不需要新增生产或 runner 代码。

## 目标

建立一套以 Silicon Notebook 产品能力为中心、DeepEval 为语义评审工具的评测体系。第一阶段解决三个问题：

1. 产品的每项能力到底要测什么；
2. 每项能力需要什么数据和产品观测；
3. 评测结果如何定位到“检索、生成、引用、reasoning 或澄清”中的具体环节。

公开 SQuAD / DROP 只作为通用能力和流程回归数据，不作为 Silicon Notebook 领域质量的唯一依据。当前未完成的 baseline 评分暂停，不作为后续设计的前置条件。

DeepEval 官方将评测分为端到端、trajectory、component-level 和 standalone 四种方式；RAG 指标又分为 retriever 与 generator。官方建议限制指标数量，优先组合 2–3 个通用系统指标与 1–2 个用例专用指标。[Metrics 文档](https://deepeval.com/docs/metrics-introduction) [Benchmarks 文档](https://deepeval.com/docs/benchmarks-introduction)

## 一、先建立产品能力矩阵

新增一份产品能力矩阵，逐行描述一项可验证的产品行为。每行必须包含：

| 字段 | 内容 |
|---|---|
| capability_id | 稳定 ID，例如 `retrieval.single_doc_fact` |
| 产品能力 | 检索、事实回答、数值计算、引用、reasoning、澄清等 |
| 用户场景 | 用户实际会提出什么问题 |
| 输入数据 | 问题、文档、参考答案、证据、期望行为 |
| 产品边界 | 单轮、多轮、单文档、多文档、是否需要 KG |
| 预期行为 | 产品应该完成、拒答、澄清或提示限制 |
| 必须观测 | source、chunk、上下文、引用、轨迹、错误状态等 |
| DeepEval 指标 | 使用哪些指标及原因 |
| 确定性检查 | 不依赖 LLM 的检查 |
| 人工判断 | 需要人工确认的内容 |
| 不适用条件 | 何时不能运行该指标 |
| 输出分桶 | 产品问题、数据问题、指标问题、judge 分歧 |

第一版矩阵固定覆盖以下六类能力：

### 1. 文档检索

目标：判断产品是否找到了正确文档和正确片段。

数据：

```json
{
  "question": "问题",
  "gold_document_ids": ["doc-001"],
  "gold_chunk_ids": ["chunk-001"],
  "gold_answer_spans": [
    {"text": "London", "start": 248, "end": 254}
  ]
}
```

必须观测：

```json
{
  "retrieved_document_ids": ["doc-001", "doc-087"],
  "retrieved_chunk_ids": ["chunk-001"],
  "retrieval_rank": [
    {"id": "chunk-001", "rank": 1}
  ],
  "final_context_ids": ["chunk-001"]
}
```

指标和检查：

- 文档 Hit@K；
- 片段 Recall@K；
- MRR / nDCG，只有产品暴露可比较排序时才启用；
- Contextual Recall 作为辅助；
- SQuAD 答案 span 是否进入最终生成上下文；
- 不把“命中文档”当成“命中答案片段”。

### 2. 事实回答

目标：判断答案是否正确、完整且没有矛盾。

数据：

```json
{
  "question": "Where did the event take place?",
  "references": ["London"],
  "answer_type": "span"
}
```

产品输出：

```json
{
  "answer": "The event took place in London.",
  "status": "success"
}
```

指标：

- DeepEval `GEval` 定制的 Answer Correctness；
- Answer Relevancy；
- Faithfulness；
- 官方参考答案只进入评分，不进入产品输入。

正确性规则必须检查：

- 是否回答了问题要求的事实；
- 是否遗漏数字、日期、单位和限定条件；
- 是否与参考答案矛盾；
- 是否允许等价表达；
- DROP 是否允许基于文档中的数字完成合理计算。

### 3. 数值、日期和多步推理

目标：判断产品是否正确提取操作数，并得出正确结果。

数据增加：

```json
{
  "question": "How many players scored more than 10 points?",
  "references": ["3"],
  "answer_type": "number",
  "required_facts": [
    {"text": "Player A scored 12"},
    {"text": "Player B scored 15"},
    {"text": "Player C scored 8"},
    {"text": "Player D scored 11"}
  ],
  "expected_operation": "count(values > 10)"
}
```

评测分两层：

1. 确定性检查答案中的数值、日期、单位和最终结果；
2. DeepEval GEval 判断解释和结论是否完整。

DROP 没有完整的人工推理证据标注时，只报告：

- 最终答案正确性；
- 上下文是否包含相关段落；
- 产品是否成功完成计算。

不报告未经标注支持的“完整推理证据召回率”。

### 4. 引用可追溯性

目标：判断引用是否指向本次运行中真实存在并实际授权的内容。

产品输出结构：

```json
{
  "answer": "The event took place in London. [1]",
  "citations": [
    {
      "label": "[1]",
      "source_id": "source-8f1",
      "chunk_id": "chunk-91b"
    }
  ]
}
```

确定性检查：

```text
引用标签是否存在；
source_id 是否属于当前 notebook；
chunk_id 是否属于该 source；
chunk 是否属于本次允许的文档范围；
chunk 原文哈希是否匹配；
引用对象是否位于实际生成上下文。
```

必须分别报告：

- citation object validity；
- citation target existence；
- citation semantic support。

前两项可程序检查，第三项需要人工或专门的引用语义 judge。不能把 Faithfulness 分数直接当成引用正确率。

### 5. Reasoning 模式

目标：比较 reasoning 是否带来可验证的收益，以及成本增加多少。

实验约束：

```text
同一问题
同一文档库
同一模型配置
独立 notebook
分别运行 chunk / reasoning
```

需要保存：

```json
{
  "question_id": "q-001",
  "mode": "reasoning",
  "trajectory": [
    {"stage": "intent", "status": "ok"},
    {"stage": "retrieval", "status": "ok"},
    {"stage": "evidence_refine", "status": "ok"},
    {"stage": "synthesis", "status": "ok"}
  ],
  "llm_calls": 8,
  "latency_seconds": 6.8,
  "token_usage": {
    "prompt_tokens": 12000,
    "completion_tokens": 900
  }
}
```

比较维度：

- Answer Correctness；
- Faithfulness；
- Answer Relevancy；
- 证据命中；
- 引用有效性；
- 延迟；
- 模型调用次数；
- token；
- 产品错误率；
- 澄清率。

报告按问题配对，显示：

```text
reasoning - chunk
```

并分别给出质量变化和成本变化。不得只用一个加权总分判断 reasoning 是否更好。

DeepEval 的 trajectory metrics 需要完整有序 trace；如果 Silicon Notebook 的轨迹字段不能完整反映真实调用，则先使用保存的产品轨迹做确定性分析，暂不虚构 trajectory metric 输入。[官方说明](https://deepeval.com/docs/metrics-introduction)

### 6. 澄清、拒答和完整性限制

目标：判断产品何时应该回答、何时应该澄清、何时应该说明能力限制。

每个样本增加期望行为：

```json
{
  "question": "列出所有作者并按机构去重分组",
  "expected_behavior": {
    "action": "clarify_or_limit",
    "reason": "当前检索无法保证完整枚举和去重"
  }
}
```

产品结果：

```json
{
  "status": "product_error",
  "error_type": "clarification_required",
  "message": "请先明确对象名称或背景",
  "model_called": null
}
```

评测分开统计：

- 应该澄清且确实澄清；
- 应该直接回答却错误澄清；
- 应该拒答却生成不完整答案；
- 澄清原因是否说明清楚；
- 澄清问题是否能帮助用户继续操作。

这类样本不直接使用 Answer Correctness 评分。主要依靠：

- 固定期望行为；
- 产品状态和错误类型；
- 澄清消息规则；
- 人工判断；
- 必要时使用 DeepEval GEval 评估澄清消息是否清楚、有帮助。

## 二、统一数据协议

所有正式评测样本使用统一结构：

```json
{
  "case_id": "squad-001",
  "dataset": "product-scenario-v1",
  "capability_id": "retrieval.single_doc_fact",
  "question": "Where did the event take place?",
  "documents": [
    {
      "public_id": "doc-001",
      "title": "Example Article",
      "text_sha256": "..."
    }
  ],
  "references": ["London"],
  "gold_document_ids": ["doc-001"],
  "gold_chunk_ids": [],
  "gold_answer_spans": [
    {"text": "London", "start": 248, "end": 254}
  ],
  "expected_behavior": {
    "action": "answer"
  },
  "split": "debug",
  "calibration": true,
  "regression": false
}
```

产品输出统一结构：

```json
{
  "case_id": "squad-001",
  "mode": "chunk",
  "status": "success",
  "answer": "The event took place in London.",
  "retrieved_document_ids": ["doc-001"],
  "retrieved_chunk_ids": ["chunk-91b"],
  "retrieval_context": [
    "The event eventually took place in London."
  ],
  "context_supported": true,
  "citations": [
    {
      "source_id": "source-8f1",
      "chunk_id": "chunk-91b"
    }
  ],
  "trajectory": [],
  "latency_seconds": 2.31,
  "usage": {
    "prompt_tokens": 3000,
    "completion_tokens": 120
  }
}
```

评分记录统一结构：

```json
{
  "case_id": "squad-001",
  "mode": "chunk",
  "metric": "Answer Correctness",
  "repeat": 0,
  "status": "valid",
  "score": 1.0,
  "reason": "The answer matches the reference.",
  "seconds": 0.8,
  "judge_model": "configured-judge"
}
```

所有评分记录状态必须使用以下四类之一（产品执行和行为状态另列）：

```text
valid
skipped
error
missing
```

其中：

- `skipped`：指标不适用或输入不可用；
- `error`：指标本来适用，但 judge 执行失败；
- `missing`：计划中的记录尚未产生；
- 产品错误单独保留在产品输出中，不能伪装成评分错误。

## 三、notebook 和运行边界

采用“评测单元一个 notebook”的方式：

```text
一个数据集 + 一个产品模式 + 一个配置
    → 一个隔离 notebook
```

例如：

```text
SQuAD / chunk       → notebook A
SQuAD / reasoning   → notebook B
DROP / chunk        → notebook C
DROP / reasoning    → notebook D
```

同一 notebook 中：

- 可以运行多个独立问题；
- 每个问题独立调用 Ask；
- 不共享上一题的对话上下文；
- 不共享回答记忆；
- 不把前一题答案放入下一题输入；
- 共享该评测单元固定的文档库、索引和产品配置。

以下场景需要独立 notebook 或独立运行环境：

- 不同数据集；
- 不同产品模式；
- 不同检索配置；
- 不同模型配置；
- 不同文档版本；
- 需要测试多轮会话状态的场景。

多轮场景不拆成单问题，而是使用独立的 `ConversationalTestCase` 和 multi-turn metrics；单轮问题继续使用 `LLMTestCase`。DeepEval 官方明确区分这两类 test case。[官方说明](https://deepeval.com/docs/metrics-introduction)

## 四、DeepEval 指标组合

第一阶段每个端到端评测单元最多使用五个主要语义指标：

### RAG 问答单元

主指标：

1. Answer Correctness（GEval 定制）；
2. Faithfulness；
3. Answer Relevancy。

诊断指标：

4. Contextual Recall；
5. Contextual Relevancy 或 Contextual Precision。

Contextual Precision 只有在产品暴露可比较的检索排序时启用。否则明确记录为 skipped。

### Reasoning 轨迹单元

主指标：

1. Answer Correctness；
2. Faithfulness；
3. Answer Relevancy。

辅助分析：

- 调用次数；
- 阶段成功率；
- 重复步骤；
- 延迟；
- token；
- 产品错误类型。

只有在轨迹字段完成统一、真实且有序时，才加入 Task Completion、Step Efficiency 等 trajectory metrics。

### 澄清单元

主指标：

1. 固定期望行为命中；
2. 澄清消息人工判断；
3. 必要时使用 GEval 评估清晰度和帮助性。

不将普通 Answer Correctness 作为澄清单元唯一指标。

DeepEval 的标准 benchmark 仍单独保留。官方 benchmark 的标准 scorer 和输出格式用于评测模型通用能力；产品场景则使用自定义 `LLMTestCase` 和产品实际输出。两者在报告中分开，不能合并成一个分数。[官方 benchmark 说明](https://deepeval.com/docs/benchmarks-introduction)

## 五、开发阶段与交付物

### 阶段 1：产品能力矩阵

交付：

- `docs/product-capability-matrix.md`；
- 每项能力的场景、输入、预期行为、观测字段和指标；
- 指标适用性矩阵；
- notebook 隔离规则。

验收：

- 每个指标都能对应一个明确产品问题；
- 每个指标的输入字段都有来源；
- 不适用指标有明确原因；
- 不存在“有指标但没有可靠数据”的项目。

### 阶段 2：一题到底的说明

交付：

- `docs/evaluation-case-walkthrough.md`；
- 选取一个已保存的真实 SQuAD 或产品场景样本；
- 展示原文、问题、产品请求、检索结果、实际上下文、答案、DeepEval test case、metric 输入、score 和汇总。

验收：

- 不具备软件工程背景的人可以沿着同一个 `case_id` 理解整个过程；
- 官方参考答案没有进入产品请求；
- `retrieval_context` 可以追溯到真实 synthesis 输入；
- 示例明确区分产品事实、judge 结果和人工判断。

### 阶段 3：产品场景数据集

交付：

- 事实定位集；
- 多文档比较集；
- 数值/日期计算集；
- 引用追溯集；
- 澄清和拒答集；
- 每类先建立少量人工确认样本，再扩大规模。

建议首批规模：

```text
每类 10–20 题；
总计 60–100 题；
其中 20% debug；
20% calibration；
其余 regression。
```

验收：

- 每题有人工确认的预期答案或预期行为；
- 每题有证据来源；
- 失败样本不能通过删除或改写问题消失；
- 数据版本和哈希可复现。

### 阶段 4：产品观测和评测适配

交付：

- 统一输出协议；
- source/chunk/citation 映射；
- reasoning 轨迹适配；
- 产品错误和澄清状态分类；
- 离线结果审计。

验收：

- 能区分检索失败、生成失败、引用失效、澄清拦截和 judge 错误；
- 上下文只使用产品真实观测；
- 产品生产代码不因评测而修改；
- 运行目录、数据库、缓存和日志隔离。

### 阶段 5：人工校准

交付：

- 评审规则；
- 盲审材料；
- 正确性、忠实性、引用支持和澄清行为标签；
- judge 与人工标签的分歧报告；
- 指标适用性修订记录。

验收：

- 人工标签由真实评审完成；
- 无法判断单独记录；
- 不将 judge 分数直接当成人工真值；
- 在校准完成前不设发布硬门槛。

### 阶段 6：报告和比较

交付：

- 按能力、数据集、模式和 split 分组的报告；
- 逐问题差异；
- 产品错误、judge 错误、跳过和缺失分母；
- chunk/reasoning 配对比较；
- 延迟、调用次数、token 和费用未知项。

验收：

- 输入、代码、配置或评分协议变化会阻止误比较；
- 跨数据集不合并为总分；
- 缺失结果不能隐藏；
- 报告显示结果是否来自公开 benchmark 或产品场景集。

### 阶段 7：持续运行

交付：

- 离线数据和协议检查；
- 在线评测入口；
- 状态查看命令或静态报告；
- baseline 指针；
- 运行失败记录；
- 可选的每周 timer。

恢复自动任务前必须满足：

- 产品场景集已经存在；
- 人工校准已有结果；
- 完整 baseline 已通过审计；
- 运行状态能区分运行中、成功、失败和中断；
- 用户明确恢复在线模型调用。

## 六、测试和验收场景

必须覆盖以下测试：

1. 一题成功回答，三项主指标均有效；
2. 产品澄清拦截，所有依赖答案的指标跳过；
3. 产品生成空答案，不能被计为正确；
4. 正确文档命中但答案片段未进入上下文；
5. 答案正确但引用对象不存在；
6. 引用对象存在但引用语义不支持答案；
7. DROP 数值计算正确；
8. DROP 多 span 答案保持一个复合答案；
9. SQuAD 多 annotator 答案保持多个可接受答案；
10. Contextual Precision 无排序时明确跳过；
11. judge 调用失败记录为 error，不填零；
12. 产品重复运行和 judge 重评分分别统计；
13. baseline 缺少样本时比较失败；
14. 数据哈希或评分协议变化时禁止比较；
15. 多轮场景保持会话上下文，单轮场景之间不共享上下文；
16. 单独克隆 benchmark 项目时，缺少 Silicon Notebook 后端只跳过原生适配测试，不影响离线协议检查。

## 七、默认决策和边界

- 第一阶段评测对象是 Silicon Notebook 的文档问答和相关 reasoning 行为；
- 公开 SQuAD / DROP 是补充数据，不代表领域质量；
- 先做产品场景集，再扩大公开 benchmark；
- 每个数据集/模式/配置使用一个隔离 notebook；
- 同一 notebook 中的问题独立调用；
- 多轮对话使用单独的会话评测单元；
- 每个 QA 评测单元建议最多五个语义指标；现有 diagnostic 会枚举三项诊断，这个预算规则尚未实现；
- 主指标暂定为 Correctness、Faithfulness、Answer Relevancy；
- Contextual 指标主要作为诊断；
- 引用有效性以确定性检查为主，引用语义需要人工校准；
- reasoning 质量和成本分开报告；
- 澄清行为单独评测；
- 人工校准完成前不设置硬门禁；
- 暂不覆盖 PDF/OCR、权限、安全、KG 完整性、重排专项和完整 UI 测试；
- 不恢复当前暂停的在线 benchmark，直到用户明确要求恢复。

## 八、官方文档复习与当前接入边界

2026-09-10 已复习用户指定的 [Metrics Introduction](https://deepeval.com/docs/metrics-introduction)、[Benchmarks Introduction](https://deepeval.com/docs/benchmarks-introduction)、[Evaluation Introduction](https://deepeval.com/docs/evaluation-introduction)，并核对各 RAG metric、GEval 与 LLMTestCase 页面，链接汇总在[数据协议](evaluation-data-contract.md#官方文档依据)。

当前使用 LLMTestCase、GEval Correctness、Faithfulness、Answer Relevancy、条件化 Contextual 指标、DeepEvalBaseLLM 自定义 judge 与 standalone measure。EvaluationDataset/Golden、evaluate/CLI、ConversationalTestCase、component/trajectory tracing 是框架可用的后续选项，不是此次新增接入。公开数据已被改造成文档库 Ask，不能声称是 DeepEval 官方标准 benchmark scorer 成绩。

## 九、第一阶段验证与后续边界

2026-09-10 验证结果：

- `.venv/bin/python scripts/check_public_benchmark_offline.py`：47 项测试通过，prepare/report/audit 三个 CLI 的帮助检查通过，退出码 0。首次运行被沙箱禁止 pytest-rerunfailures 创建本地 socket；经提升权限重跑通过，未改测试或产品代码。
- 公开样例与本地原始 output/scores 逐字段一致；原工件 SHA-256、823 字符原文哈希、16 段完整 context、正文 k1 到 chunk/element 的映射、三项分数及 token 合计核对通过。离线构造 LLMTestCase，未调用 measure。
- 62 个本地 Markdown 链接目标存在，13 个 JSON 代码块可解析；新增文档无尾随空白或未完成占位项，`git diff --check` 通过。未重新判分、刷新历史 run 产物或启动产品进程；生产仓库仍只有开始时已有的 `.deepeval/` 与 `results/` 未跟踪目录。

后续顺序：离线协议归一化 → 引用与证据来源审计 → 能力分桶和配对报告 → 真实人审及产品场景核验 → 用户明确恢复后才考虑新的在线小实验。协议/数据/配置变化需新运行身份；完整 baseline、人工校准和明确恢复意图满足前不恢复自动 timer。KG、重排、PDF/OCR、完整 UI、权限专项及完整性算法扩展仍不在本次范围。
