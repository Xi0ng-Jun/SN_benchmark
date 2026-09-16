# 当前十套 Benchmark 的指标实现对照

口径：本分支 `starter_runner` 的默认执行路径与固定 DeepEval 4.2.2。N 为直接模型参照；R 为 SN Product（chunk / reasoning 分别执行）。这里描述实现，不代表服务器实验已经完成。

每个 R 的主指标与诊断指标分别记录，不平均为总分。未产生正常预测先记 unscored；非适用和解析失败保留 null。

| Benchmark 名 | 评测轨道 | Metrics 名 | Metrics 具体实现方式 |
|---|---|---|---|
| SQuAD | Native（N） | DeepEval SQuAD 二元 judge<br>`deepeval.squad_score.binary_judge` | LLM / 内置 Benchmark scorer。Scorer.squad_score 提交问题/上下文、预测与参考；允许如 2/two 的表达差异。 显式 judge 按 SDK 提示输出 JSON answer=0 或 1；校验只接受二元结果。 限制：不是 GEval，也不是原始 SQuAD EM/token-F1。评分理由未返回时不补造。 |
| SQuAD | SN Product（R；chunk / reasoning） | 答案正确性与完整性<br>`product.GEval.AnswerCorrectness` | LLM / 自定义 GEval。显式 evaluation_steps 检查事实、数值、日期、单位、限定条件、遗漏与矛盾；任一完整备选可接受，DROP 增加有效计算说明。保存 score/reason 和已记录的 judge 调用。 judge 按 0–2 错误、3–6 部分正确、7–10 正确完整的 rubric 评分；由 SDK 返回 0–1 metric.score。 限制：不要求 Final answer 行，不先抽短答案；不是固定的字符串公式。分数非正确概率，人工校准待完成。 |
| SQuAD | SN Product（R；chunk / reasoning） | 回答与实际上下文的一致性<br>`product.Faithfulness` | LLM / 内置 FaithfulnessMetric。LLM 从上下文提取事实、从回答提取声明，再生成逐声明 verdict；SDK 聚合为分数。项目使用 async_mode=False，其余默认参数。 本地 SDK 4.2.2 默认：verdict != no 的声明数 / 总声明数；无声明返回 1；idk 默认不扣分。 限制：需 context_supported 且上下文非空；无可靠上下文 N/A。高分不保证逐条引用支持断言；历史版本以冻结 SDK 为准。 |
| SQuAD | SN Product（R；chunk / reasoning） | 最终上下文的目标文档覆盖<br>`product.final_context.document_coverage` | 确定性 / 集合比较。先将 SN 实际合成上下文中的 source ID 映射回公开文档 ID，再计算交集；source 与文档映射数量必须一致。 \|gold_document_ids ∩ retrieved_document_ids\| / \|gold_document_ids\|。 限制：无可靠上下文、gold 或完整映射 → N/A；不是完整候选召回率/排名指标，也不证明证据内容充分。 |
| SQuAD | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| DROP | Native（N） | DROP 归一化备选匹配<br>`deepeval.quasi_contains_score` | 确定性 / DeepEval Scorer。使用 SDK quasi_contains_score；名字含 contains，但实际检查归一化后的整串是否等于任一列表项。 normalize_text(prediction) 在 normalize_text(targets) 列表中 → 1；否则 0。 限制：不是数值运算验证，也不等同 DROP 多 span 完整性 F1；Product 使用另一套 GEval 口径。 |
| DROP | SN Product（R；chunk / reasoning） | 答案正确性与完整性<br>`product.GEval.AnswerCorrectness` | LLM / 自定义 GEval。显式 evaluation_steps 检查事实、数值、日期、单位、限定条件、遗漏与矛盾；任一完整备选可接受，DROP 增加有效计算说明。保存 score/reason 和已记录的 judge 调用。 judge 按 0–2 错误、3–6 部分正确、7–10 正确完整的 rubric 评分；由 SDK 返回 0–1 metric.score。 限制：不要求 Final answer 行，不先抽短答案；不是固定的字符串公式。分数非正确概率，人工校准待完成。 |
| DROP | SN Product（R；chunk / reasoning） | 回答与实际上下文的一致性<br>`product.Faithfulness` | LLM / 内置 FaithfulnessMetric。LLM 从上下文提取事实、从回答提取声明，再生成逐声明 verdict；SDK 聚合为分数。项目使用 async_mode=False，其余默认参数。 本地 SDK 4.2.2 默认：verdict != no 的声明数 / 总声明数；无声明返回 1；idk 默认不扣分。 限制：需 context_supported 且上下文非空；无可靠上下文 N/A。高分不保证逐条引用支持断言；历史版本以冻结 SDK 为准。 |
| DROP | SN Product（R；chunk / reasoning） | 最终上下文的目标文档覆盖<br>`product.final_context.document_coverage` | 确定性 / 集合比较。先将 SN 实际合成上下文中的 source ID 映射回公开文档 ID，再计算交集；source 与文档映射数量必须一致。 \|gold_document_ids ∩ retrieved_document_ids\| / \|gold_document_ids\|。 限制：无可靠上下文、gold 或完整映射 → N/A；不是完整候选召回率/排名指标，也不证明证据内容充分。 |
| DROP | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| BoolQ | Native（N） | 最终答案精确匹配<br>`deepeval.exact_match_score.YesNo` | 确定性 / DeepEval Scorer。按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。 |
| BoolQ | SN Product（R；chunk / reasoning） | SN Yes/No 结论匹配<br>`product.boolq.explicit_conclusion.v1` | 确定性 / 项目规则。首个非空行须严格为 Final answer: Yes 或 Final answer: No；全文不能同时出现 Yes/No 两个词。 解析出的 Yes/No 与参考一致 → 1；不同 → 0；格式不合法 → unparsed/null。 限制：解释里出现另一标签也会使解析失败；正常回答才评分。 |
| BoolQ | SN Product（R；chunk / reasoning） | 回答与实际上下文的一致性<br>`product.Faithfulness` | LLM / 内置 FaithfulnessMetric。LLM 从上下文提取事实、从回答提取声明，再生成逐声明 verdict；SDK 聚合为分数。项目使用 async_mode=False，其余默认参数。 本地 SDK 4.2.2 默认：verdict != no 的声明数 / 总声明数；无声明返回 1；idk 默认不扣分。 限制：需 context_supported 且上下文非空；无可靠上下文 N/A。高分不保证逐条引用支持断言；历史版本以冻结 SDK 为准。 |
| BoolQ | SN Product（R；chunk / reasoning） | 最终上下文的目标文档覆盖<br>`product.final_context.document_coverage` | 确定性 / 集合比较。先将 SN 实际合成上下文中的 source ID 映射回公开文档 ID，再计算交集；source 与文档映射数量必须一致。 \|gold_document_ids ∩ retrieved_document_ids\| / \|gold_document_ids\|。 限制：无可靠上下文、gold 或完整映射 → N/A；不是完整候选召回率/排名指标，也不证明证据内容充分。 |
| BoolQ | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| LogiQA | Native（N） | 最终答案精确匹配<br>`deepeval.exact_match_score.ABCD` | 确定性 / DeepEval Scorer。按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。 |
| LogiQA | SN Product（R；chunk / reasoning） | SN 最终答案精确匹配<br>`product.deepeval.exact_match_score.final_answer.v1` | 确定性 / 产品提取 + SDK Scorer。完整回答必须恰有一个 Final answer 标记和一行严格的 Final answer: VALUE；检查枚举/数字/任务域后，将原样文本交给 exact_match_score。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：提取失败记 unparsed/null；GSM8K 不去逗号、不作整数强制转换；不是 Native 榜单协议。 |
| LogiQA | SN Product（R；chunk / reasoning） | 最终上下文的目标文档覆盖<br>`product.final_context.document_coverage` | 确定性 / 集合比较。先将 SN 实际合成上下文中的 source ID 映射回公开文档 ID，再计算交集；source 与文档映射数量必须一致。 \|gold_document_ids ∩ retrieved_document_ids\| / \|gold_document_ids\|。 限制：无可靠上下文、gold 或完整映射 → N/A；不是完整候选召回率/排名指标，也不证明证据内容充分。 |
| LogiQA | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| IFEval | Native（N） | 全部指令满足<br>`deepeval.ifeval.audited_all_instructions` | 确定性 / 已审计 verifier。逐条匹配 instruction_id、kwargs、verifier 哈希和正反例审计，复核正反例，再执行 verify_instruction_compliance；保存逐指令结果。 所有指令均通过 → 1；有任意一条失败 → 0；有任意未审计指令 → N/A。 限制：Native 缺审计时不发起模型预测；Product 可保存回答但不给规则分。只评价列出的指令。 |
| IFEval | SN Product（R；chunk / reasoning） | 全部指令满足<br>`product.ifeval.audited_all_instructions.full_body.v1` | 确定性 / 已审计 verifier。逐条匹配 instruction_id、kwargs、verifier 哈希和正反例审计，复核正反例，再执行 verify_instruction_compliance；保存逐指令结果。 所有指令均通过 → 1；有任意一条失败 → 0；有任意未审计指令 → N/A。 限制：Native 缺审计时不发起模型预测；Product 可保存回答但不给规则分。只评价列出的指令。 |
| IFEval | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| MMLU | Native（N） | 最终答案精确匹配<br>`deepeval.exact_match_score.MMLU` | 确定性 / DeepEval Scorer。按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。 |
| MMLU | SN Product（R；chunk / reasoning） | SN 最终答案精确匹配<br>`product.deepeval.exact_match_score.final_answer.v1` | 确定性 / 产品提取 + SDK Scorer。完整回答必须恰有一个 Final answer 标记和一行严格的 Final answer: VALUE；检查枚举/数字/任务域后，将原样文本交给 exact_match_score。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：提取失败记 unparsed/null；GSM8K 不去逗号、不作整数强制转换；不是 Native 榜单协议。 |
| MMLU | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| GSM8K | Native（N） | 最终答案精确匹配<br>`deepeval.exact_match_score.GSM8K` | 确定性 / DeepEval Scorer。按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。 |
| GSM8K | SN Product（R；chunk / reasoning） | SN 最终答案精确匹配<br>`product.deepeval.exact_match_score.final_answer.v1` | 确定性 / 产品提取 + SDK Scorer。完整回答必须恰有一个 Final answer 标记和一行严格的 Final answer: VALUE；检查枚举/数字/任务域后，将原样文本交给 exact_match_score。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：提取失败记 unparsed/null；GSM8K 不去逗号、不作整数强制转换；不是 Native 榜单协议。 |
| GSM8K | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| TruthfulQA MC1 | Native（N） | 最终答案精确匹配<br>`deepeval.exact_match_score.TruthfulQA.MC1` | 确定性 / DeepEval Scorer。按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。 |
| TruthfulQA MC1 | SN Product（R；chunk / reasoning） | SN 最终答案精确匹配<br>`product.deepeval.exact_match_score.final_answer.v1` | 确定性 / 产品提取 + SDK Scorer。完整回答必须恰有一个 Final answer 标记和一行严格的 Final answer: VALUE；检查枚举/数字/任务域后，将原样文本交给 exact_match_score。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：提取失败记 unparsed/null；GSM8K 不去逗号、不作整数强制转换；不是 Native 榜单协议。 |
| TruthfulQA MC1 | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| HellaSwag | Native（N） | 最终答案精确匹配<br>`deepeval.exact_match_score.HellaSwag` | 确定性 / DeepEval Scorer。按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。 |
| HellaSwag | SN Product（R；chunk / reasoning） | SN 最终答案精确匹配<br>`product.deepeval.exact_match_score.final_answer.v1` | 确定性 / 产品提取 + SDK Scorer。完整回答必须恰有一个 Final answer 标记和一行严格的 Final answer: VALUE；检查枚举/数字/任务域后，将原样文本交给 exact_match_score。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：提取失败记 unparsed/null；GSM8K 不去逗号、不作整数强制转换；不是 Native 榜单协议。 |
| HellaSwag | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |
| BBH | Native（N） | 最终答案精确匹配<br>`deepeval.exact_match_score.BBH` | 确定性 / DeepEval Scorer。按冻结 SDK 模板构造参考值，使用 Scorer.exact_match_score；不把诊断归一化结果替换成评分输入。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：格式/请求失败单独记录；0/1 不评价推导步骤。GSM8K 产品数值不做数值等价换算。 |
| BBH | SN Product（R；chunk / reasoning） | SN 最终答案精确匹配<br>`product.deepeval.exact_match_score.final_answer.v1` | 确定性 / 产品提取 + SDK Scorer。完整回答必须恰有一个 Final answer 标记和一行严格的 Final answer: VALUE；检查枚举/数字/任务域后，将原样文本交给 exact_match_score。 prediction 非空且 prediction.strip() == expected_output.strip() → 1；否则 0。 限制：提取失败记 unparsed/null；GSM8K 不去逗号、不作整数强制转换；不是 Native 榜单协议。 |
| BBH | SN Product（R；chunk / reasoning） | 引用对象存在率<br>`product.citation_object.existence_ratio` | 确定性 / 隔离库回查。source_id 必须来自当前导入映射，element_id + source_id 在 source_elements 或 chunks 表中存在才计有效。 citation_valid_count / citation_count；没有引用对象 → N/A。 限制：不检查正文每个 [k]、quoted_span 哈希或语义支持；不因返回 grounded=true 就判有效。 |

## 计算入口

- Native：[`starter_native.py`](../src/rag_eval/starter_native.py) 与 [`public_expansion_native.py`](../src/rag_eval/public_expansion_native.py)。
- Product 标签/数值：[`system_scoring.py`](../src/rag_eval/system_scoring.py)、[`starter_product.py`](../src/rag_eval/starter_product.py)。
- GEval / Faithfulness：[`quality_metrics.py`](../src/rag_eval/quality_metrics.py)；实际启用与前置条件在 [`starter_runner.py`](../src/rag_eval/starter_runner.py)。
- 引用与来源回查：[`benchmark_runtime.py`](../src/rag_eval/benchmark_runtime.py)。
- Dashboard 使用的说明目录：[`metric_catalog.py`](../src/rag_eval/metric_catalog.py)，只是解释，不触发评分。

## 与此前说明的差异

- Answer Relevancy、Contextual Recall/Precision/Relevancy 在其他历史/通用评测入口有实现，但不属于当前十套 runner 默认评分计划。不能据此声称每个 run 都有它们。
- MMLU、BBH、TruthfulQA、HellaSwag 已有 SN 系统适配；冻结 Native registry 的 product=False 是历史协议元数据，不能当作现在没有 R 路径。
- DROP R 当前统一采用 GEval 正确性，没有“先尝试数字 exact match，失败后转 GEval”的自动分支；Native quasi_contains 与 DROP 完整多 span 评分也不是同一口径。
- 引用存在率不包含完整正文锚点覆盖、quoted_span 哈希或语义支持评分。保存 response/captures 是后续人工核对的材料。
- Faithfulness 4.2.2 默认不惩罚 idk；高分不能直接解释为每条声明都有明确证据。
- SN 正常返回文本中的拒答仅为 refusal_candidate；旧三套某些澄清拦截表现为 error，不能把旧记录自动改写成独立 clarification。

## 如何从条目追到分数

在 Dashboard 点击条目后，依次查看原始 case、SN/Native 请求与回答、context/citations/captures、评分 plan/result/details 和该题的 model-events。judge 事件的 request_id 对应 result_id 时可绑定到这一项指标；否则只作为本题相关事件展示。没有保存的调用或理由明确显示缺失，不编造过程，也不重新打分。

所有均值只针对实际 status=scored 的记录，同时显示有效分数数/计划条目数。运行问答次数按 run×case 去重；一题有四项指标仍只算一次问答。
