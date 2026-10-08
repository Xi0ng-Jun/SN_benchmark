# 五套 Benchmark 的指标实现对照

文档状态：实现入口与读分规则。指标输入和公式以代码与具体 run 身份为准，不构成统一总分或发布门槛。完整定义见[标准与实现符合性](notebook-benchmark-standards-and-conformance.md)，Notebook 诊断见[指标与适用条件](notebook-benchmarks.md#指标与适用条件)。

## 官方评分与诊断边界

| Benchmark | 官方评分对象 | 需要保留的区别 |
| --- | --- | --- |
| QASPER | Answer F1、Evidence F1 | 证据来自真实引用到原段落的映射；最终上下文覆盖只是诊断，不能替代 Evidence F1 |
| MultiHop-RAG | 官方答案匹配、真实排名的检索指标 | chunk 的原生检索快照可回放；没有等价排名的 reasoning 保持 pending，不按合成顺序构造排名 |
| ALCE：ASQA/QAMPARI/ELI5 | 各子任务答案指标、AutoAIS 引用指标与任务专用模型指标 | 字符串诊断、仅答案官方重评分、完整引用评分分别标识；缺所见文档或引用映射不能生成完整引用分 |
| QMSum | 固定原版 Perl ROUGE | Python ROUGE 及上下文 turn 覆盖属于独立诊断，不与原版官方结果混合 |
| HotpotQA | Answer、Supporting Fact、Joint 官方指标 | supporting fact 必须来自实际输出到句子单元的投影；context coverage 不能替代官方证据分 |

SN 的 chunk/reasoning、参考方法、公开答卷和原生 Agent 是不同执行／评分入口。比较前核对 bundle、scope、case IDs、scorer、实际分母及模型／提示／预算条件，不能因为指标同名直接配对。

## 共用指标与失败语义

- 引用对象存在率检查对象是否属于当前隔离库及导入范围。没有引用时不适用；对象存在不证明 claim 得到支持，正文锚点数与引用对象数分别统计。
- Faithfulness 使用真实合成上下文判断答案声明；高分不等于每条引用正确，也不证明世界知识真值。缺可靠上下文和分节输入不能伪造单次完整上下文。
- Answer Relevancy 判断是否回应问题，不能替代正确性、拒答或澄清的人工标签。Contextual 指标只在所需上下文／参考／排名真实存在时适用。
- 回答及组件在 judge 前保存。error、missing、pending、非适用与有效零分分别保留；补评创建新批次，原记录不被覆盖。
- 均值与覆盖率按各自评分协议的实际分母报告；不把不同任务／指标平均为总分，也不以有效子集代表完整题单。

## 计算与展示入口

- 官方答卷与评分：[benchmark_protocol.py](../scripts/benchmark_protocol.py)、[benchmark_official.py](../src/rag_eval/benchmark_official.py)。
- Notebook 诊断：[notebook_scoring.py](../src/rag_eval/notebook_scoring.py)。
- 引用与来源回查：[benchmark_runtime.py](../src/rag_eval/benchmark_runtime.py)。
- 原生 Agent／组件：[原生 Agent 评测](native-agent-evaluation.md)、[评分恢复](native-scoring-recovery.md)。
- Dashboard 指标说明：[metric_catalog.py](../src/rag_eval/metric_catalog.py)，只解释已有分数，不触发评分。

在 Dashboard 查看原始 case、请求与回答、context/citations/captures、评分 plan/result/details 和 model-events。只有明确的 request_id/result_id 关联才绑定某次 judge 调用；缺调用或理由直接显示缺失，不补造过程。问答数按 run × case 去重，一题多个指标仍只计一次问答。
