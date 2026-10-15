# SN 的 ASQA 论文对照选型与协议复核

核查日期：2026-10-15。本文修正[短期选型建议](../notebook-benchmark-selection-audit.md#短期结果表推荐2026-10-15)，补充[已有外部成绩](2026-10-14-alce-external-results.md)。下列分数均为百分制；[正式结果表](../benchmark-result-tables.md#表-4f近期论文的-asqa-发表参考)使用 0–1。只做资料、代码及历史工件核查，没有生成答案、运行 judge 或访问运行服务器。

## 结论与需要纠正的说法

**ALCE-ASQA 适合作为近期 RAG 系统的比较任务；当前 human_eval 100 题不是这些论文普遍使用的评测范围。** 已确认可直接利用的 100 题公开答卷来自 ALCE 一篇 EMNLP 2023 论文的四种配置，不能称为四篇近期方法。InstructRAG、Think&Cite 等使用 948 题。不能用 SN 的 100 题汇总值与这些全量成绩推断排名或“大致位置”。

若目标是论文主表，建议选择 **ALCE ordinary ASQA 948 题 + STR-EM + Citation Precision/Recall**，优先引用 InstructRAG、Think&Cite 的发表结果，保留 ALCE 和 Self-RAG 作基础参照。外部模型不必重跑；SN 若已有同范围输出则复用，否则需要补齐 SN 全量输出。当前只收到 SN 100 题结果，不能把全量 SN 视为已完成。这个建议不派发实验或改变当前执行范围。

若严格要求零新增生成，现阶段交付现有 100 题样本表，并将近期论文另列为背景。只有取得近期方法覆盖这些题的预测，才能在不重跑其模型的情况下计算其 100 题分数；全量均值不能反推出子集均值。

## 当前 100 题表的证据

本轮再次读取四份 `var/external-comparison/alce-human-246c476-20260928/asqa/*/scored/scores.json`：case IDs 及顺序一致，均为 100 题，scorer_id 均为 `2f00de0a4202b41d0cd3d8d9457dda0ce27ca5a69418142e535a44be95c789a2`。数值与[历史账本](2026-10-14-local-score-audit.json)一致，无需修改：

| 配置 | STR-EM | STR-HIT | 证据范围 |
| --- | ---: | ---: | --- |
| SN chunk | 44.433 | 13.000 | 服务器转述；题单、scorer 尚待原工件核对 |
| ALCE GPT-3.5 VANILLA | 35.300 | 9.000 | 作者公开答卷，历史重评分 |
| ALCE GPT-3.5 interactive summary | 38.200 | 9.000 | 同上 |
| ALCE GPT-3.5 sample4 RERANK | 36.550 | 11.000 | 同上 |
| ALCE Vicuna-13B | 26.467 | 5.000 | 同上 |

代码核查：[`benchmark_official.py::_alce_text`](../../src/rag_eval/benchmark_official.py)执行固定 ALCE 源文件中的预处理和 `compute_str_em` 等字符串评分函数；这是默认轻量文本评分路径，不能写成已经执行包含 QA 模型、MAUVE、AutoAIS 的完整 `eval.py`。STR-EM 是各消歧短答案组的覆盖率，STR-HIT 要求一题所有组均覆盖。

[`alce_human_import.py::_variant/prepare_method`](../../src/rag_eval/alce_human_import.py)记录的输入分别为 5 段、10 段交互摘要、5 段四候选重排、3 段；精确 shown-doc 映射没有恢复。四份答卷的同题同文本 scorer 不意味着同输入预算，也不提供共同引用评分。保留空答案在分母中，不能把缺失引用分填零。来源为 [ALCE 固定 human_eval](https://github.com/princeton-nlp/ALCE/tree/246c476a4edfc564266b7346b6e29ef4861ae937/human_eval)。

## 优先选择的近期论文

以下是作者发表值，全部为检索增强方法，不是本项目复现。Citation 列顺序统一为 **Precision / Recall**，避免与 Think&Cite 原表的 Recall / Precision 顺序混淆。未报告 STR-HIT 的论文不补造该值。

| 方法／backbone | 发表 | ASQA 范围 | STR-EM | Citation P / R | 选入理由与条件 |
| --- | --- | --- | ---: | --- | --- |
| InstructRAG-ICL / Llama-3-Instruct-8B | ICLR 2025 | 948 | 44.7 | 70.9 / 74.1 | 核心对照：无需参数微调的 rationale 示例方法；GTR top-5，默认 2 个示例 |
| InstructRAG-FT / Llama-3-Instruct-8B | ICLR 2025 | 948 | 47.6 | 65.7 / 70.5 | 核心对照：同论文的训练方法；ASQA 专项微调，不能写成 zero-shot |
| Think&Cite / Llama-3.1-8B-Instruct | ACL 2025 主会 | 948 | 45.2 | 80.6 / 82.3 | 核心对照：多步搜索与引用生成，和 SN 的有据问答目标贴近 |
| Think&Cite / GPT-4o | ACL 2025 主会 | 948 | 50.1 | 87.1 / 89.5 | 同方法的较强配置；额外 DPO/NLI 模型与树搜索成本须披露 |
| Cooperative CoRAG / Llama-3-Instruct-8B | Findings ACL 2026 | 948 | 45.8 | 54.9 / 48.9 | 可选最新补充：联合优化 reranker/generator，只在 PopQA 训练；ASQA 推理选 3 段 |

**InstructRAG** 的正式论文表 2 明确 N=948、GTR、top-5，§3.1 明确采用官方 STR-EM 和引用指标，表 3 给出上述值。ICL 使用训练集产生的 rationale 示例，所谓无需训练指不更新模型参数，不等于没有任务示例。若表格空间允许，可加 ICL-70B（47.8；P/R 69.1/71.2）；不将同一论文的多个模型版本计为多个独立工作。[ICLR 2025 正式论文，表 2/3、附录 B](https://proceedings.iclr.cc/paper_files/paper/2025/file/cdce0de17dc3cf97019a35baa65aebd0-Paper-Conference.pdf)、[作者仓库](https://github.com/weizhepei/InstructRAG)。

**Think&Cite** 的附录 A 明确 948 题，§5.1 使用 ALCE 指标及 GTR top-100 候选，每次内部查询选 3 段，表 1 给出上述值。默认最多 30 次 MCTS 迭代、树深 6、反思步数 10；其 policy 不做任务微调，但使用现成 DPO reward model 和 TRUE NLI。作者代码的 `compute_str_em` 计算短答案覆盖，引用 evaluator 使用 `t5_xxl_true_nli_mixture`。这是共同任务下不同系统的比较，不能写成与 top-5 单次生成等成本。[ACL 正式论文，§5.1、表 1、附录 A](https://aclanthology.org/2025.acl-long.490.pdf)、[作者仓库](https://github.com/nusnlp/Think-Cite)。

**CoRAG** 在表 1 明确 948 题，§4.1 声明沿用 InstructRAG 的任务设置与 ALCE correctness/citation 指标；表 2 是上述成绩。其第一阶段检索结果的逐文件身份本轮没有恢复，保留为扩展参考。它是 *Rethinking Retrieval-Augmented Generation as a Cooperative Decision-Making Problem* 的 Cooperative CoRAG，不能与其他同名 CoRAG 工作混淆。发表较新是补充理由，不据此声称它在 ASQA 上最强。[Findings ACL 2026 正式论文](https://aclanthology.org/2026.findings-acl.2157/)、[表 1/2 与 §4.1](https://aclanthology.org/2026.findings-acl.2157.pdf)、[作者仓库](https://github.com/CoderrrSong/CoRAG)。

## 原有方法如何处理

| 工作 | 处理 | 原因／来源 |
| --- | --- | --- |
| ALCE，EMNLP 2023 | 保留基础参照和 100 题答卷 | 历史基线有价值，但不承担“近期强方法”的角色。原文约 1,000 与普通发布文件 948 的差异保留；样本分与主表分分开。[正式论文](https://aclanthology.org/2023.emnlp-main.398/) |
| Self-RAG，ICLR 2024 | 保留经典参照，来源升级为正式版 | 正式表 2 的 7B/13B STR-EM 仍是 30.0/31.7；13B P/R 为 70.3/71.3。Inference settings 明确 ASQA 用作者 GTR-XXL top-5，任务设置明确官方指标。后续 InstructRAG 表中的 31.4 不能覆盖原论文 31.7；二者来源不同。[ICLR 正式论文](https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf) |
| MAIN-RAG，ACL 2025 | 保留发表参考，优先级低于上述明确 948 的方法 | 原有 Mistral-7B 35.7 / Llama3-8B 39.2 不改；论文未明确该表测试 N，使用 Contriever 最多 20 段再过滤，不擅自补成同一 GTR-948。[正式论文表 1、§4](https://aclanthology.org/2025.acl-long.131.pdf) |
| Adaptive-Note | 放答案覆盖补充组 | 原有 47.3/22.0 等值保留其版本来源；移除了引用生成要求，不与完整带引用任务混为同条件结果。[原文实验设置](https://arxiv.org/html/2410.08821v1) |
| AlignRAG，NeurIPS 2025 | 新增候选，暂不进核心引用表 | 表 1 的 Qwen2.5-7B + 8B critic STR-EM 48.9；只报告答案指标，未明确 ASQA 测试 N。本轮仓库无预测文件，README 只有标题，不把“使用 STR-EM”升级为“已核实 948 题完整引用协议”。[正式论文 §4.1/表 1](https://proceedings.neurips.cc/paper_files/paper/2025/file/1f09e1ee5035a4c3fe38a5681cae5815-Paper-Conference.pdf)、[仓库](https://github.com/qqw-ing/rag-reasonalignment) |
| LCR-RAG，ACL 2026 | 不加入 STR-EM 比较表 | ASQA 报 ROUGE-L 和 RAGAS Faithfulness/Answer Relevance，与本表指标不同；发表更新不能解决协议差异。[正式论文 §4.3/表 1](https://aclanthology.org/2026.acl-long.814.pdf) |

Self-RAG 的“采用 ALCE 发布数据”有原文和代码依据，但论文没有单独给出主实验 948 分母，保留该事实，不与其 150 题消融混淆。外部发表值可以合法引用；没有完整 manifest 不等于分数无效，也不等于必须重跑。

## 能否免重跑得到近期方法在这 100 题上的分数

本轮没有找到可直接用于上述 100 题的新方法完整预测。以下是有边界的检查结果，不是对所有可能存储位置的不存在证明：

| 作者仓库／版本 | 检查所得 | 对 100 题比较的影响 |
| --- | --- | --- |
| ALCE `246c476a4edfc564266b7346b6e29ef4861ae937` | human_eval 有四配置答卷，本地已保存评分 | 现成可用；SN 身份仍需核对 |
| InstructRAG `8147bbc6a4794b15145183cb8b26761b74efed76` | Git tree 只有代码和数据说明；README 链接 QA 数据、预检索/rationale 数据及模型；`eval.sh` 指向运行后生成的 `eval_results` | 未发现论文测试预测发布；训练 rationales、检索资料与模型权重都不能当作测试答卷 |
| Think-Cite `3457002d7e098a085c1b60a134e39fac70299046` | `data/asqa.json` 实读 948 行，字段为 `qa_pairs/wikipages/annotations/sample_id/question/answer`，没有预测；其余文件为代码等 | `answer` 是数据集参考答案，不能作为 Think&Cite 的生成结果；没有可提取的公开 100 题预测 |
| Self-RAG `1fcdc420e48f50a7d7ab1ece5494221b93252e99` | README 提供模型、预检索评测输入、推理与 ALCE 评分命令；Git tree 无评测输出 | 未找到已发布的对应答卷；不把“有 inference code”写成“有 predictions” |
| Cooperative CoRAG、AlignRAG | 已检查作者 Git tree；前者发布 PopQA 数据和 adapter，后者发布训练/评测代码 | 本轮未发现 ASQA 测试答卷 |

InstructRAG 的 Google Drive 预处理压缩包及 Self-RAG 的评测输入压缩包没有在本轮完整下载，不能宣称所有附件均已穷尽。后续若取得作者答卷，只需匹配题单和重评分，无需再生成；同样不能因公开代码可运行就承诺免重跑有结果。

## 如果作为 SN 论文作者，我会这样组织

1. **核心主表选 ALCE-ASQA 948 题。** 外部行采用 ALCE Vanilla、Self-RAG-13B、InstructRAG-ICL/FT、Think&Cite-8B/GPT-4o；想覆盖 2026 年工作时加 Cooperative CoRAG。正文重点放 InstructRAG 和 Think&Cite，不用大量旧模型配置营造近期对照数量。
2. **主指标用 STR-EM 与引用 P/R。** STR-HIT 作为 SN 和可取得预测的方法的补充；没有该指标的论文填未报告。SN 暂无可用引用指标时只交付答案覆盖结论，不称为已经完成引用可靠性比较。
3. **按研究主张披露输入与训练。** SN 当前是每题官方候选集内的 Notebook 检索/阅读，主要检验多段资料下的综合回答和引用，不能据此证明整库第一阶段检索更强。top-5、top-100 内搜索、训练数据、backbone 和调用预算都列出；不同模型可做系统比较，不要求全部重跑成同模型。若要归因于 SN 某模块，再另做匹配其余条件的实验。
4. **复用已完成输出。** 先确认 SN 是否已有 full 工件；若只有当前 100 题且它们确属同一 948 发布范围、输入和配置兼容，理论上只需补其余 848 题。不能为了换方法名重跑已有合格答案；也不能把不同版本输出静默拼接为一次统一配置的 full 结果。
5. **保留 100 题为样本表／附录。** 当前数字可以交付，但结论限定在这个公共样本。零新增生成与“立即得到近期方法的同题主表”目前不能同时保证。

这里的“优先”来自任务匹配、正式发表、协议披露和可引用结果，不是对论文质量或截至当日 SOTA 的全面排名。没有将所有 ASQA 论文视为同一协议，也没有要求用户为普通发表结果比较重跑外部方法。
