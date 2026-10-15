# ALCE-ASQA 外部结果核查

核查日期：2026-10-14。所有展示分数为百分制。SN 为用户服务器转述；外部公开答卷的历史评分文件已在本轮回读，未重新生成或评分。综合表见 [四套结果对照](../external-result-comparison-2026-10-14.md)。

2026-10-15 补充：[近期方法选型复核](2026-10-15-asqa-comparison-selection.md)已核查 InstructRAG（ICLR 2025）、Think&Cite（ACL 2025）和 Cooperative CoRAG（Findings ACL 2026），成绩进入[表 4f](../benchmark-result-tables.md#表-4f近期论文的-asqa-发表参考)。下列 100 题外部答卷来自 ALCE 2023 同一篇论文的四配置，不能据此称有很多近期方法在这 100 题上评测。正式论文主表建议使用 948 题；本节仍保留已有样本证据。

## 优先使用：固定 human_eval 100 题

| 方法 | N | STR-EM ↑ | STR-HIT ↑ | 来源性质 |
| --- | ---: | ---: | ---: | --- |
| SN chunk | 100 | 44.433 | 13.000 | 服务器转述，100/100 success |
| GPT-3.5 VANILLA | 100 | 35.300 | 9.000 | 作者答卷，本项目历史重评分 |
| GPT-3.5 interactive summary | 100 | 38.200 | 9.000 | 同上 |
| GPT-3.5 sample4 RERANK | 100 | 36.550 | 11.000 | 同上 |
| Vicuna-13B | 100 | 26.467 | 5.000 | 同上 |

外部来源是 [ALCE 固定提交 human_eval](https://github.com/princeton-nlp/ALCE/tree/246c476a4edfc564266b7346b6e29ef4861ae937/human_eval) 的 `human_eval_citations_completed.json`，不是论文主表的全量分数，也不是人工正确性标签。导入和重评分过程见 [历史记录 §3](../notebook-external-results-2026-09-28.md#3-alce两任务四配置各-100-题)。

本轮直接读取 `var/external-comparison/alce-human-246c476-20260928/asqa/{vanilla,interactive,rerank,vicuna}/scored/scores.json`：四份均有相同 100 个 case IDs、相同 scorer_id、每项指标分母 100；保留空答案 1/1/2/0 条。详细文件哈希与分数见 [回读账本](2026-10-14-local-score-audit.json)。实际保存的数值为 0–1，表中乘 100。

方法 key 分别指向 5 段 VANILLA、10 段交互摘要、5 段四候选重排和 3 段 Vicuna；这些是作者文件名记录，不是本轮恢复了完整运行配置。精确 checkpoint、实际提示、shown-doc 全集、温度与重试仍未知。不能将论文中的模型日期直接补入这些答卷的运行身份。

SN 名为 `alce100-all-100` 的服务器批次预期使用这份题单，但本轮没有其 manifest、case IDs 和 scorer identity，尚未完成与 SN 的机器配对准入。可以并列描述汇总值；只有核实服务器身份后，才能称为已确认的同题同评分比较。按现有汇总，SN 相对外部最高 STR-EM 38.200 为 +6.233 分，相对最高 STR-HIT 11.000 为 +2.000 分；两个最高值来自不同配置。不据此宣称统计显著或算法归因。

STR-EM 是每题消歧短答案组的字符串覆盖比例，STR-HIT 是整题所有组均覆盖的比例；都不等于引用正确率。SN 此次没有提供 citation precision/recall；外部答卷缺完整 shown-doc 身份，本项目也未恢复共同引用评分。

## 扩展参考：论文的较大评测范围

下表与 SN 的 100 题不是相同范围，不用于排高低。ALCE 原论文 §2 写每任务抽 1,000 题，而固定普通 ASQA 发布文件实数为 948；这里保留论文规模与发布实数的差别，不静默把原文全改成 948。

| 发表工作／配置 | STR-EM ↑ | STR-HIT ↑ | 范围与输入 | 定位 |
| --- | ---: | ---: | --- | --- |
| ALCE：ChatGPT VANILLA，5 段 | 40.4 | 未报告 | 论文 ASQA 范围；GTR | [原论文表4](https://aclanthology.org/2023.emnlp-main.398.pdf#page=6) |
| ALCE：ChatGPT SUMM，10 段 | 43.3 | 未报告 | 同上，候选摘要压缩 | 同表 |
| ALCE：GPT-4 VANILLA，5 段 | 41.3 | 未报告 | 同上 | 同表 |
| ALCE：GPT-4 VANILLA，20 段 | 44.4 | 未报告 | 同上，更多上下文 | 同表 |
| Self-RAG 7B | 30.0 | 未报告 | ALCE-ASQA；训练反思 token 的 Llama2；作者 GTR top-5 | [ICLR 2024 正式版表2](https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf#page=8) |
| Self-RAG 13B | 31.7 | 未报告 | 同上 | 同表 |
| MAIN-RAG，Mistral-7B | 35.7 | 未报告 | ALCE-ASQA，本文未明确题数；Contriever 最多20段再过滤 | [ACL 2025 表1、§4.1/4.3](https://aclanthology.org/2025.acl-long.131.pdf#page=7) |
| MAIN-RAG，Llama3-8B | 39.2 | 未报告 | 同上；无需任务微调 | 同表 |
| Adaptive-Note 论文内 Vanilla，GPT-3.5 | 43.4 | 18.2 | 948题；GTR-XXL，top-5；不要求引用 | [作者论文 v1 表1](https://arxiv.org/html/2410.08821v1#S5) |
| Adaptive-Note，GPT-3.5 | 47.3 | 22.0 | 948题；多轮检索及笔记；不要求引用 | 同表 |
| Adaptive-Note，Qwen2-7B | 46.9 | 21.5 | 同上，模型不同 | 同表 |

ALCE 论文 §5 的 ChatGPT 为 `gpt-3.5-turbo-0301`、GPT-4 为 `gpt-4-0613`。Self-RAG 原文表2里 35.7 / 37.0 是 ROUGE，74.3 / 71.6 是 MAUVE，不能错填成 STR-HIT。MAIN-RAG 表1中 42.0 也是 ROUGE，70.6 是 MAUVE。

Self-RAG 正式版仍为 30.0/31.7；InstructRAG 后续论文中的 Self-RAG-13B 31.4 属于不同来源，不据此更改原论文成绩。Self-RAG 正式版明确 ALCE 数据/指标，但没有单列主实验 N；MAIN-RAG 的测试题数也未明确，不能将本表每一行统称为已核实的 full 948。

Adaptive-Note §Experimental Setup 明确默认 `gpt-3.5-turbo-0125`、GTR-XXL 检索 Wikipedia、zero-shot、移除 ALCE 引用生成要求，默认每轮 top-5、CI=3、IU=1、RP=15；因此它是答案覆盖参考，不能当成完整 ALCE 引用任务的同条件胜负。其论文内标为 Self-RAG 的基线是 LangChain 下的作者适配，不是原 Self-RAG 7B/13B；没有混用两个来源的同名行。

## 本轮核查范围

原始 ALCE 论文、官方仓库、Self-RAG 作者论文、MAIN-RAG 正式论文和 Adaptive-Note 作者论文均已联网读取；搜索结果的博客与聚合榜单只用于寻找一手来源。没有下载大型数据、调用生成模型、运行 judge 或访问服务器。此表是与当前结果相关的文献调研，不声称覆盖全部新论文或建立截至当日的 SOTA 排名。
