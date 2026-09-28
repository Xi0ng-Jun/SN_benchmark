# Notebook Benchmark 选型审计

更新：2026-09-25。本文记录当前五套 benchmark 的社区定位、遗漏项和 HotpotQA 纳入决定。它描述的是 Silicon Notebook 的比较范围，不把不同任务拼成一个总分。

## 结论

QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 组成了一个面向 Silicon Notebook 能力的互补套件，但不是所有 RAG 论文共同采用的固定五件套：

- **QASPER** 是成熟的科研论文长文档问答任务，包含 1,585 篇论文和 5,049 个问题，适合论文阅读、长上下文问答和证据定位。[官方论文](https://aclanthology.org/2021.naacl-main.365/)
- **QMSum** 是成熟的查询驱动会议摘要任务，适合会议和长对话摘要；它不是通用 RAG 论文最常用的答案问答主基准。[官方论文](https://aclanthology.org/2021.naacl-main.472/)
- **ALCE** 是引用感知答案生成的重要 benchmark，包含 ASQA、QAMPARI、ELI5，并分别评估答案、流畅性和引用质量。[论文](https://aclanthology.org/2023.emnlp-main.398/)、[官方仓库](https://github.com/princeton-nlp/ALCE)
- **MultiHop-RAG** 与 SN 的跨文档检索和多跳能力高度匹配，但发布较新；不能把它和 HotpotQA 一样表述成历史最久、跨论文最稳定的多跳标准。[论文](https://arxiv.org/abs/2401.15391)、[官方仓库](https://github.com/yixuantt/MultiHop-RAG)

因此，当前五套适合作为 SN 的**能力覆盖套件**。如果要声称“与通用 RAG 论文中的主流方法直接比较”，至少还需要一个历史更成熟的多跳 QA 基准和一个独立检索基准。

## 候选 benchmark 与适用条件

| 候选 | 社区定位 | 对 SN 的作用 | 当前决定 |
| --- | --- | --- | --- |
| HotpotQA | 约 113K 个 Wikipedia 多跳问答，提供句子级 supporting facts；有 distractor/fullwiki 设置、官方 evaluator 和 Codalab 入口 | 给 MultiHop-RAG 增加历史更长、baseline 更多的多跳对照 | **纳入第一版扩展** |
| MuSiQue | 约 25K 个 2–4 hop 问题，构造时减少单跳捷径，强调真正的组合推理 | 检验多跳链条是否真的被使用 | 后续；若论文主张组合式推理再加入 |
| 2WikiMultiHopQA | 结构化 Wikidata 与 Wikipedia 文本、多种推理题型和证据路径 | 增加结构化/比较型多跳覆盖 | 后续；不与 HotpotQA 同时作为第一批必跑 |
| BEIR | 18 个异构信息检索数据集，评价 BM25、dense、late interaction、reranker 等检索器 | 单独验证 SN retriever，避免只看最终答案 | 作为检索专项，建议受控子集 |
| MTEB | embedding/检索模型的公开 leaderboard | 比较 embedding 模型，不等于完整 RAG 答案评测 | 仅在比较 embedding 时使用 |
| Natural Questions | 真实搜索查询，含 long answer、short answer 和 null answer | 通用开放域 QA 对照 | 只有在论文声称通用 QA 时加入 |
| TriviaQA | 大规模开放域 QA 与独立证据文档 | 通用开放域检索与问答 | 后续，成本高于当前主套件 |
| CRAG | 4,409 个现实问题，包含动态事实、长尾实体和 mock API；曾用于 KDD Cup 2024 | 现实 web/knowledge-base RAG 专项 | 后续专项，不加入当前主套件 |
| SCROLLS | 长文本摘要、问答和 NLI 套件，其中已经包含 QASPER、QMSum | 长文本统一外部参考 | 不重复跑整套；保留原始任务评分 |
| LongBench/LongBench v2 | 多文档 QA、摘要、代码和长对话；v2 上下文可到 2M 词 | 长上下文泛化参考 | 只有在主张长上下文泛化时加入 |
| CiteBench | 科学论文 citation text 生成 | 评估“写论文引用句” | 不是 ALCE 的替代 |
| FActScore | 长文本原子事实支持率 | 事实性诊断 | 不是 citation benchmark 替代 |

官方入口：[HotpotQA 主页](https://hotpotqa.github.io/)、[HotpotQA 论文](https://aclanthology.org/D18-1259/)、[MuSiQue 论文](https://aclanthology.org/2022.tacl-1.31/)、[2WikiMultiHopQA 论文](https://aclanthology.org/2020.coling-main.580/)、[BEIR 论文](https://arxiv.org/abs/2104.08663)、[Natural Questions](https://research.google/pubs/natural-questions-a-benchmark-for-question-answering-research/)、[CRAG 论文](https://papers.neurips.cc/paper_files/paper/2024/hash/1435d2d0fca85a84d83ddcb754f58c29-Abstract-Datasets_and_Benchmarks_Track.html)、[SCROLLS 论文](https://aclanthology.org/2022.emnlp-main.823/)、[LongBench 仓库](https://github.com/THUDM/LongBench)、[CiteBench 仓库](https://github.com/UKPLab/citebench)。

## HotpotQA 纳入边界

HotpotQA 官方区分两种设置：

1. **Distractor**：每题直接给出 10 个 Wikipedia 段落，其中含支持段落和干扰段落；本地可完整复核答案与 supporting facts。
2. **Fullwiki**：使用处理后的 Wikipedia 语料和独立检索；官方 fullwiki dev 文件中的段落是作者检索器返回的 top-10，并不保证包含 gold 段落。完整 fullwiki 还需要冻结官方 processed Wikipedia 和检索设置。

第一版扩展采用 `distractor/validation`，因为它有公开答案和 supporting facts，能够先验证数据适配、无 gold 请求、官方评分和受控答案比较。它评价的是“在官方十段输入中的多跳阅读和证据选择”，不能写成 fullwiki 开放域检索结果。

当前实现身份：

- suite：`hotpotqa`；
- source setting：`distractor`；
- split：`validation`；
- 数据许可：CC BY-SA 4.0；代码许可：Apache-2.0；
- 输入单位：题目对应的全部上下文段落，段落内部保留句子单位；
- 生成端不可见：answer、type、level、supporting facts；
- 官方答案指标：Answer EM/F1、precision、recall；
- supporting-fact 指标：Supporting Fact EM/F1、precision、recall；
- 联合指标：Joint EM/F1、precision、recall；
- SN 尚无可审计句子级 supporting-fact 预测时，只报告答案指标，其余指标为 `pending`，不把最终上下文覆盖率冒充 supporting-fact F1。

## 比较原则

同一个 benchmark 的数字只有在 setting、split、可见资料、检索范围、模型、提示、预算和 scorer 都对齐时才进入 paired comparison。HotpotQA distractor 的结果不能和 fullwiki 结果混排；HotpotQA 的答案 F1 也不能和 MultiHop-RAG 的弱词交集准确率合成总分。

当前推荐的最小扩展为：保留 QASPER、MultiHop-RAG、ALCE、QMSum，并纳入 HotpotQA；若要证明 retriever 泛化，再单独增加 BEIR 受控子集。不要因为 benchmark 数量增加而制作跨套件平均分。
