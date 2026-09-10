# DeepEval 与 Silicon Notebook：第一阶段调研

> 用途：团队内部学习与评审。本文把官方文档、仓库实现和当前实验结果分开记录，避免把暂定方案写成产品契约。

## 1. 先看结论

DeepEval 是一个开源的 LLM 应用评测框架：它用 pytest 风格的测试用例承载输入、输出、期望行为和上下文，用内置或自定义 metric 打分，并支持端到端、轨迹级和组件级评测。官方入口强调它适合 RAG、Agent、工具调用、对话和 MCP 等应用。[官方介绍](https://deepeval.com/docs/introduction)

对 Silicon Notebook，DeepEval 最适合承担“答案和实际证据之间的语义判断”以及回归编排；它不能替代 BM25/向量检索的确定性指标、引用 ID 校验、延迟/成本测量，也不能单独证明答案的领域真实性。

## 2. 核心对象与运行方式

| 对象 | DeepEval 语义 | Silicon Notebook 对应物 |
|---|---|---|
| `LLMTestCase` | 一条待评测行为，含 input、actual output、expected output、retrieval context 等 | 一条用户问题、模型答案和本次真正送入生成器的上下文 |
| Dataset / golden | 可重复运行的一组人工确认样本 | 项目文档、真实问题、业务场景、公开 benchmark 的版本化 JSONL |
| Metric | 对测试用例、trace 或 span 的评分逻辑 | Faithfulness、Answer Relevancy、Contextual Recall/Precision/Relevancy |
| Trace / span | 运行时步骤、模型调用、工具调用和组件行为 | `chunk` / `reasoning` 检索、PPR、精确查找、答案合成等轨迹 |
| `evaluate()` / pytest / CLI | 本地运行评测并输出分数和理由 | `rag-eval-deepeval` 加项目 adapter，在 CI 或发布前执行 |

仓库当前的 `rag_eval.deepeval_runner` 已将五个 RAG metric 接入，并保留 `retrieved_ids` 给 Recall@K、MRR、nDCG 等确定性报告；这说明 DeepEval 在本项目中是附加的语义评审层，而不是检索实现本身。

## 3. 五个已接入 metric 应该回答什么

- **Contextual Recall**：参考答案所需的信息是否出现在实际上下文中。适合发现召回缺口。
- **Contextual Precision**：相关上下文是否排在无关上下文之前。适合观察排序质量。
- **Contextual Relevancy**：送入模型的上下文与问题是否相关。适合发现 top-K 过大、切块过粗或过滤不足。
- **Faithfulness**：答案声明是否能被本次实际上下文支持。它不是世界知识真值检查。
- **Answer Relevancy**：答案是否直接回应问题。它必须与 Faithfulness 一起看。

官方文档将前三项归为 RAG 检索侧、后两项归为生成侧；所有阈值都需要结合人工复核和 baseline 校准，仓库中的默认值是实验起点，不是发布门禁。[RAG 指南](https://deepeval.com/guides/guides-rag-evaluation)

## 4. 能承担什么，不能替代什么

### 适合承担

1. 对实际答案和实际上下文做语义级判断；
2. 通过 pytest/CLI 编排小规模回归和模型、提示、检索配置对比；
3. 对失败样本给出自然语言理由，帮助定位“召回不足 / 上下文噪声 / 生成失真”；
4. 在 trace/span 可用时，评估复杂 `reasoning` 链路中的组件行为。

### 不应替代

1. `Hit@K`、Evidence Recall、MRR、nDCG 等可复现的 ID/排序指标；
2. 引用目标存在性、claim-to-evidence 映射和格式合法性校验；
3. 延迟、token、调用次数和费用的精确统计；
4. 人工确认 gold answer、必要证据、可回答性和隐私脱敏；
5. 对产品 UI、权限、数据一致性和线上可靠性的完整测试。

## 5. 映射到 Silicon Notebook 评测闭环

```text
固定数据集 -> 项目 retrieval adapter -> 答案 + 实际上下文 + IDs + latency
                                      |                    |
                            DeepEval 语义指标       确定性检索/运行指标
                                      \                    /
                                  失败分桶与回归报告
```

建议每条结果至少保留：`question`、`answer`、`expected_answer`、`retrieval_context`、`retrieved_ids`、`retrieval_mode`、模型/embedding 版本和耗时。`retrieval_context` 必须是本次运行真正交给生成器的文本，不能事后替换成 gold context。

## 6. 事实、判断与假设

### 已验证事实

- 官方文档当前说明 DeepEval 支持 50+ metrics、端到端/轨迹/组件级评测，并可本地运行。[官方介绍](https://deepeval.com/docs/introduction)
- 本仓库已有 CRUD-RAG、MultiHop-RAG、SciFact loader、BM25 baseline、内部 chunk adapter 和五个 DeepEval metric。
- 历史全库 BM25 实验为每集前 50 条问题：MultiHop-RAG Hit@10=0.9512、Evidence Recall@10=0.7642；SciFact Hit@10=0.8000、Evidence Recall@10=0.7730。这些不是项目向量检索分数。
- 2026-09-09 已完成两个公开集共 100 条问题的实际项目导入、向量检索、Ask 与 DeepEval：400 项评分中 394 项有效、6 项失败。候选库为所选问题的正证据文档并集，不能与历史全库 BM25 直接比较；详见[实际链路报告](../var/public-system-50/report.md)。
- 早期曾遇到模型连接错误，后续领域候选试运行及上述公开集评分均已完成，不能继续把早期错误写成当前阻塞。未经人审的领域候选仍不是正式基准。

### 当前判断

- 第一阶段应把 DeepEval 定位为“语义 judge + 回归编排层”，确定性指标继续作为检索质量主信号。
- `chunk` 与 `reasoning` 应共享结果协议，但按模式分桶报告，避免平均分掩盖多跳或精确标识符问题。

### 待实验确认

- 不同 judge 模型、提示和阈值对中文流程问题的稳定性；
- DeepEval 分数与人工标签的一致性及可接受误差；
- chunk Ask 已采集实际答案与完整生成上下文；更细的组件级 trace、reasoning/KG/重排分支采集仍待验证；
- 引用、拒答、延迟和成本是否需要自定义 metric 或独立报告。

## 7. 可执行课程路径

主要课件现在放在 [`lectures/`](../lectures/)。它参考 Stanford CS336 lectures 的组织方式：`lecture_XX.py` 是课程程序，章节由函数组织，运行程序会依次产生解释、代码观察和练习。完整 CS336 仓库用 `edtrace` 生成可浏览 trace；本项目先提供普通 Python 可执行版本，因此不需要额外前端就能复现。

### Lecture 01：对象与运行方式

运行：`.venv/bin/python lectures/lecture_01.py`

这一课从“为什么最终答案分数不够诊断”开始，构造一个依赖无关的 `MiniTestCase`，再把它映射到 DeepEval 的 `LLMTestCase`。重点是理解四个字段：问题、实际答案、参考答案、实际上下文。最后比较端到端、组件级和轨迹级评测，说明当前 adapter 为什么先从端到端 RAG case 开始。

### Lecture 02：RAG 数据协议与确定性指标

运行：`.venv/bin/python lectures/lecture_02.py`

这一课使用一条多跳样本区分 `gold_document_ids`、`retrieved_ids` 和 `retrieval_context`，并手算 Hit@K、Evidence Recall@K 和 MRR。它直接对应仓库的 JSONL 结果协议，也解释为什么 DeepEval 不能取代 ID 层面的检索指标。

### Lecture 03：五个 RAG metric

运行：`.venv/bin/python lectures/lecture_03.py`

这一课逐项解释 Contextual Recall、Contextual Precision、Contextual Relevancy、Faithfulness 和 Answer Relevancy，展示“高召回但低忠实性”等指标组合。程序会尝试构造当前项目的 metric；若缺少 judge key 或服务不可达，会展示环境状态，不会把它当作零分。

### Lecture 04：公开 baseline

运行：`.venv/bin/python lectures/lecture_04.py`

这一课读取 `results/public-retrieval-50.json`，展示历史 MultiHop-RAG 和 SciFact 的 BM25 结果，并解释 Hit@10 与完整证据集合率的差异。课件及 trace 保留教学阶段的观察；后续实际链路与 LLM judge 已完成，最新进度以[评测状态](evaluation-status.md)和[实验报告](../var/public-system-50/report.md)为准。

每课末尾都有练习。练习不是正式测试，而是帮助讲解者把指标改动、失败模式和实验假设连接起来。

## 8. 第一阶段学习路径

1. 先运行 Lecture 01，建立对象和评测范围的心智模型。
2. 再运行 Lecture 02，手算确定性指标并检查数据协议。
3. 运行 Lecture 03，观察 judge 指标的互补关系和环境边界。
4. 运行 Lecture 04，复现公开 baseline，再回到研究稿阅读项目内实验计划。
5. 最后用 HTML 索引页快速复习对象、指标侧重点和事实/判断/假设分层。

## 9. 官方材料索引

这些页面适合作为 lecture 中的延伸阅读；链接保留官方英文原文，课件解释使用中文。

- [Metrics 总览](https://deepeval.com/docs/metrics-introduction)：说明 metric 是“测量尺”，介绍 50+ metric、LLM-as-a-judge、阈值、reason 和四种评测范围。官方当前说明 metric 分数范围为 0 到 1，成功条件是分数不低于 threshold；课件中的阈值仍只作为实验起点。
- [Single-Turn Test Case](https://deepeval.com/docs/evaluation-test-cases)：解释 `LLMTestCase` 的字段，以及 reference-based 和 referenceless case 的区别。它对应 Lecture 01 和 Lecture 02 的数据协议。
- [RAG 指标总览](https://deepeval.com/docs/metrics-introduction#rag)：官方把 Contextual Relevancy、Contextual Precision、Contextual Recall 放在 retriever，把 Answer Relevancy、Faithfulness 放在 generator；这正是本项目的五指标组合。
- [Faithfulness](https://deepeval.com/docs/metrics-faithfulness)：用于讲“答案声明是否得到 retrieval_context 支持”，也能帮助说明它不是独立的世界知识真值检查。
- [Contextual Recall](https://deepeval.com/docs/metrics-contextual-recall)、[Contextual Precision](https://deepeval.com/docs/metrics-contextual-precision)、[Contextual Relevancy](https://deepeval.com/docs/metrics-contextual-relevancy)：分别辅助讲召回覆盖、上下文排序和上下文噪声。
- [Answer Relevancy](https://deepeval.com/docs/metrics-answer-relevancy)：用于对比“回答切题”和“回答有证据支持”这两个不同维度。
- [End-to-End / Trajectory / Component-Level Evals](https://deepeval.com/docs/metrics-introduction#using-metrics)：对应 Silicon Notebook 从黑盒答案、retrieval adapter 到 reasoning trace 的分层评测计划。
- [配置 LLM Judge](https://deepeval.com/docs/metrics-introduction#configure-llm-judges)：官方说明可以使用 OpenAI、Ollama、Anthropic、Gemini 或自定义 `DeepEvalBaseLLM`；这为后续验证 DashScope/项目模型 adapter 提供实验入口。
- [CI/CD 运行](https://deepeval.com/docs/metrics-introduction#for-end-to-end-evals)：官方建议使用 `evaluate()` 或 `deepeval test run`，并支持缓存、并行、成本统计和 CI 集成；项目当前 CLI adapter 可以作为这一层的本地入口。

## 参考

- [DeepEval Introduction](https://deepeval.com/docs/introduction)
- [DeepEval RAG Guide](https://deepeval.com/guides/guides-rag-evaluation)
- `benchmark-deepeval/README.md`
- `benchmark-deepeval/docs/evaluation-context.md`
- `benchmark-deepeval/docs/evaluation-status.md`
- `benchmark-deepeval/results/experiment-report.md`
