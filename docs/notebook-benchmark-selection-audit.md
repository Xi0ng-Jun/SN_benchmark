# Notebook Benchmark 选型审计

更新：2026-10-15。本文记录当前五套 benchmark 的社区定位、遗漏项和 HotpotQA 纳入决定，并补充近期 RAG/检索论文中常见的外部 benchmark、协议和榜单状态。它描述的是 Silicon Notebook 的比较范围，不把不同任务拼成一个总分。

## 结论

QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 组成了一个面向 Silicon Notebook 能力的互补套件，但不是所有 RAG 论文共同采用的固定五件套：

- **QASPER** 是成熟的科研论文长文档问答任务，包含 1,585 篇论文和 5,049 个问题，适合论文阅读、长上下文问答和证据定位。[官方论文](https://aclanthology.org/2021.naacl-main.365/)
- **QMSum** 是成熟的查询驱动会议摘要任务，适合会议和长对话摘要；它不是通用 RAG 论文最常用的答案问答主基准。[官方论文](https://aclanthology.org/2021.naacl-main.472/)
- **ALCE** 是引用感知答案生成的重要 benchmark，包含 ASQA、QAMPARI、ELI5，并分别评估答案、流畅性和引用质量。[论文](https://aclanthology.org/2023.emnlp-main.398/)、[官方仓库](https://github.com/princeton-nlp/ALCE)
- **MultiHop-RAG** 与 SN 的跨文档检索和多跳能力高度匹配，但发布较新；不能把它和 HotpotQA 一样表述成历史最久、跨论文最稳定的多跳标准。[论文](https://arxiv.org/abs/2401.15391)、[官方仓库](https://github.com/yixuantt/MultiHop-RAG)

因此，当前五套适合作为 SN 的**能力覆盖套件**。它们不能单独支持“SN 的 retriever 在跨域检索上达到主流水平”这一主张，因为没有一套是独立的、跨域的检索 leaderboard。反过来，检索 leaderboard 也不能替代答案、引用或摘要评测。近期调研没有发现一个被所有 RAG 论文共同遵守的端到端 benchmark；论文通常按研究问题选择检索、长上下文、开放域 QA 或动态 Web 轨道。

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

## 近期社区采用的外部 benchmark 审计（2026-10-15）

这里把“采用度”和“协议/榜单质量”分开判断：一个 benchmark 被很多论文引用，不代表这些论文使用了相同的 split、上下文、检索范围和 scorer；有 leaderboard 也不代表它是端到端 RAG 榜单。以下只把官方资料、官方代码或官方竞赛页面作为协议依据。

| Benchmark | 评测层次与明确协议 | 榜单状态 | 社区采用判断 | 与当前五套的关系和建议 |
| --- | --- | --- | --- | --- |
| **MTEB Retrieval** | embedding、retriever 和 reranker 的任务集合；由 `mteb` toolkit 运行，常用 `nDCG@10`，任务元数据和训练/测试污染信息公开 | [官方 leaderboard](https://leaderboard.mteb.org/) 持续维护，覆盖大量模型和任务 | **检索模型事实上的主流比较入口之一**；近期 embedding/RAG 论文常按 MTEB 或其 retrieval 子集报告 | 不评生成答案或引用。建议作为独立 **retrieval track** 的模型参考，不并入五套端到端平均分 |
| **BEIR** | 原论文为 18 个异构、zero-shot IR 数据集；输入是 corpus/query/qrels，标准指标包括 `nDCG@10`、MAP、Recall、Precision；官方库提供统一 evaluator | [BEIR 代码与 evaluator](https://github.com/beir-cellar/beir) 仍是主要复现入口；旧 EvalAI/Google Sheet 榜单部分已标 deprecated，2024 资源论文提供参考实现和官方 leaderboard 资源 | **经典 IR/RAG 检索对照**，但不是完整 RAG 答案榜 | 当前五套没有公开跨域 qrels 检索轨道。若只选一组，建议固定 3–5 个与论文语料相近的 BEIR 子集并披露子集，不把子集均值写成完整 BEIR 成绩 |
| **BRIGHT** | 1,385 个需要多步推理才能找到相关文档的真实查询，分短/长文档轨道，平均 `nDCG@10`；支持原始查询或 LLM 推理后的查询 | [作者 leaderboard](https://brightbenchmark.github.io/) 有近期提交；同时进入 [MTEB task 页面](https://leaderboard.mteb.org/tasks/BrightTheoremQATheoremsRetrieval) | **较新的推理密集检索专项**，采用度低于 BEIR/MTEB，但与多跳 RAG 的检索瓶颈更贴近 | 可作为 MultiHop-RAG/HotpotQA 之外的检索诊断；不作为通用 RAG 主榜单 |
| **AIR-Bench** | 2024.05 版本含 69 个数据集、9 个领域、13 种语言；提供 dev/test、统一打包和检索/重排提交格式 | [官方 HF leaderboard](https://huggingface.co/spaces/AIR-Bench/leaderboard) 可提交 test 结果 | **正在形成的异构检索基准**，协议清楚但历史积累和采用度仍低于 BEIR/MTEB | 若 SN 需要多语言或领域泛化，可作为替代/补充；当前英语科研 Notebook 目标下优先级低 |
| **CRAG** | 4,409 个 QA，五领域、八类问题；Task 1 给网页，Task 2 提供 mock KG/API，Task 3 为 50 个网页加 API 的端到端 RAG。官方自动评测将答案分为 perfect/acceptable/missing/incorrect，并计 correct=+1、missing=0、incorrect=-1 | [Meta KDD Cup 2024 页面](https://www.aicrowd.com/challenges/meta-comprehensive-rag-benchmark-kdd-cup-2024) 有三任务 leaderboard 和最终获奖结果；比赛已结束，提交不是持续开放榜 | **目前最像“完整 RAG 系统”竞赛协议的公开候选**，但采用主要来自该挑战及其方案论文，不应称为所有 RAG 论文的统一标准 | 如果 SN 要主张动态 Web、长尾事实或 KG/API 访问，建议做独立外部轨道。当前主 suite 不加入：数据与 mock API、版本和 CC BY-NC 许可会显著增加复现成本 |
| **KILT** | 统一 2019-08-01 Wikipedia snapshot，覆盖 NQ、TriviaQA、HotpotQA、ELI5、FEVER 等知识密集任务；同时报告答案、provenance retrieval 和 provenance-gated KILT-EM/F1 | [EvalAI KILT leaderboard](https://eval.ai/web/challenges/challenge-page/689/leaderboard/1905) 可查历史结果，但页面显示测试周期已于 2026-01-31 结束；[官方 repo](https://github.com/facebookresearch/KILT) 已于 2023-10-31 archive | **历史影响力大、协议完整**，但不是现在活跃的 RAG 榜单；与当前 HotpotQA 有任务重叠 | 适合引用历史 published-reference 或需要 Wikipedia provenance 时复现；不建议作为当前第一优先级新增套件 |
| **LongBench v1/v2** | v1 统一 21 个长文本任务、自动评分，含 QASPER、QMSum、HotpotQA 等；v2 为 503 道选择题，8k–2M 词上下文，答案为 A/B/C/D | [v1 结果表/代码](https://github.com/THUDM/LongBench/tree/main/LongBench) 与 [v2 leaderboard](https://longbench2.github.io/) 均可查 | **长上下文论文和 RAG-vs-full-context 比较中常见**，但它是 long-context benchmark，不是检索器或引用榜单 | 适合作为“SN 与长上下文模型如何比较”的外部轨道；QASPER/QMSum/HotpotQA 同名任务必须保留 LongBench 版本、题数和 scorer，不能并入原版分数 |
| **SCROLLS / ZeroSCROLLS** | 长文档 QA、摘要和 NLI 的统一文本格式；可提交 `Task,ID,Prediction` CSV，按任务 scorer 评测 | 官方 getting-started 页面仍描述提交流程，但截至本次核查其 leaderboard URL 已重定向到无关站点，无法把当前页面当作可信榜单 | 曾是长上下文论文常用外部参考，当前榜单可用性不足 | 不重复跑整套；只在论文明确使用 SCROLLS/ZeroSCROLLS 时作为衍生协议引用，并标明版本和题集 |

### 这些结果如何解释“大家都会采用”

- **检索层没有一个唯一标准，但有明显分工。** MTEB/BEIR 主要回答“embedding 或 retriever 能否找到相关文档”；BRIGHT 测试需要推理的查询；AIR-Bench 强调多领域、多语言。它们不能直接推出最终回答质量。
- **端到端 RAG 也没有统一榜单。** CRAG 的挑战赛协议最完整，但它是动态 Web/KG 场景且比赛已结束；KILT 的 provenance 设计很完整，但依赖固定 Wikipedia 快照并已归档。两者都不能代表私有 Notebook 语料。
- **LongBench 是长上下文外部比较，不是 RAG 官方协议。** 它复用当前五套中的同名任务并改变题量、输入模板或 scorer；例如 LongBench QASPER 只有衍生题集且没有官方 Evidence F1。因此它只能支持长上下文比较，不能替换当前 QASPER 的答案/证据评测。
- **新近专门 benchmark 尚未形成“多数工作遵守”的标准。** MIRAGE（2025，7,560 个实例和 37,800 条检索池）、MEMERAG（2025，多语言自动评测器 meta-evaluation）和 T²-RAGBench（2026，文本与表格）都有清晰论文协议，但目前没有与 MTEB/BEIR/LongBench 同等稳定的中央榜单，宜作为研究线索或诊断集，而不是主比较轨道。

检索层采用度的一个可核验信号是近期 embedding 论文同时报告 MTEB、MTEB-Retrieval/BEIR 及其扩展，并直接使用 `mteb` evaluator；例如 [COLM 2025 的 embedding 评测](https://openreview.net/pdf?id=NC6G1KCxlt)明确列出 MTEB-Retrieval/BEIR。CRAG 的采用度则主要由其官方挑战规模支撑：官方结果页记录超过 2,000 名参与者和 5,500 多次提交，并分别给出三项任务的获奖榜单；这支持“有可比较的历史竞赛结果”，不支持“所有 RAG 论文的默认标准”。

### 对 SN 的建议（不改变当前五套代码范围）

1. **最低成本补齐可比性：建立独立 retrieval track。** 优先使用 MTEB Retrieval 或固定、可复现的 BEIR 子集，保存 corpus/query/qrels、检索 top-k、指标版本和模型身份；该轨道与 QASPER 等答案套件分开报告。
2. **需要完整 RAG 外部结果时：选择 CRAG 作为专项，而不是替代五套。** 只有在研究主张包含动态事实、Web/KG/API 或长尾实体时才值得承担其数据、mock API 和许可证成本。
3. **需要和长上下文模型比较时：使用 LongBench v1/v2 的原生结果或受控复现。** 必须把 LongBench 版本、题数、输入模板和 scorer 写入结果身份，不能把它的 QASPER/QMSum/HotpotQA 数字填进原版表。
4. **暂不把 BRIGHT、AIR-Bench、MIRAGE、MEMERAG、T²-RAGBench 加入主 suite。** 它们分别适合推理密集检索、多语言检索、组件诊断、judge meta-evaluation 和文本/表格场景；当前没有足够的统一采用度来替代五套的领域覆盖。

因此，当前五套并不是因为“漏掉了一个所有 RAG 论文都必须跑的 benchmark”而选错。更准确的表述是：五套负责 SN 的论文阅读、跨文档、多跳、引用和摘要能力；若论文还要声称检索器泛化或动态 Web RAG，则应加一条独立、明确标注的外部轨道。任何新增 benchmark 仍需另行决定，本文更新不改变仓库已确定的五套代码范围。

## 短期结果表推荐（2026-10-15）

如果目标是交付包含近期外部方法的论文结果表，首选 **ALCE-ASQA 的 ordinary 948 题范围**。已有 human_eval 100 题可以先交付样本表，但目前确认可用的外部答卷只有 ALCE（EMNLP 2023）一篇论文的四种配置，不能称为许多近期方法。详细核查与选型见[2026-10-15 ASQA 对照复核](research/2026-10-15-asqa-comparison-selection.md)。本次不新增 benchmark 代码或派发实验。

| 字段 | 短期冻结值 |
| --- | --- |
| 任务 | ALCE-ASQA；QAMPARI/ELI5 暂不合并为一个总分 |
| 检索资料 | ordinary GTR release；保留官方候选编号和顺序 |
| 当前样本表 | 已有 100 个公共 case IDs；四份 ALCE 答卷使用同一固定官方字符串评分函数；SN 的题单和 scorer 尚待服务器工件核对，不称已执行完整 `eval.py` |
| 论文主表目标 | ordinary ASQA 948 题；优先 InstructRAG、Think&Cite 的 `published-reference`；SN full 尚无已验收数值，不能用 100 题值代填 |
| 指标 | `STR-EM`、`STR-HIT`；引用 precision/recall 另列，不能用字符串指标替代 |
| 结果身份 | 样本表和 full 分开；保留模型、训练、检索段数、题数和引用要求；未知题数的方法单独列参考 |

选择 ALCE-ASQA 的原因是：官方仓库同时提供 ASQA 数据、GTR top-100 检索结果、baseline 配置和 `eval.py`，并明确评估 correctness、fluency 和 citation quality；这比从论文中拼接不同 QASPER split 或 HotpotQA fullwiki/distractor 数字更适合当前交付。官方仓库还保留了 human-evaluation 目录和可直接评分的公开答卷。[ALCE 官方仓库](https://github.com/princeton-nlp/ALCE)、[ALCE 论文](https://aclanthology.org/2023.emnlp-main.398/)

近期核心对照优先选 **InstructRAG（ICLR 2025）和 Think&Cite（ACL 2025 主会）**：两篇均明确 948 题并报告 ALCE correctness/citation 指标；可加 Cooperative CoRAG（Findings ACL 2026）作为更新补充。Self-RAG 保留为经典参照；MAIN-RAG 未明确该表测试题数，Adaptive-Note 移除了引用要求，不把它们笼统写成“已确认同范围完整 ALCE”。[InstructRAG 正式论文](https://proceedings.iclr.cc/paper_files/paper/2025/file/cdce0de17dc3cf97019a35baa65aebd0-Paper-Conference.pdf)、[Think&Cite](https://aclanthology.org/2025.acl-long.490/)、[Cooperative CoRAG](https://aclanthology.org/2026.findings-acl.2157/)。

引用这些发表结果不要求重跑外部方法。但若 SN 只有 100 题，不能据此判断它相对 948 题方法的高低或“大致位置”。应先复用已有 SN full 工件；不存在时才考虑补齐 SN 全量。若要求零新增生成，则交付现有 100 题样本表，近期成绩只作背景。本轮在核查的作者仓库中未找到可直接提取这 100 题的近期方法测试预测，不能承诺免重跑获得其子集分数。

**HotpotQA distractor** 是第二选择：官方 EM/F1、supporting-fact 和 Joint 指标成熟，适合补充一般多跳 QA；但当前 SN 是 validation/distractor，外部近期 RAG 论文经常只报 Answer F1、未说明实际分母，且 fullwiki、distractor、LongBench 衍生设置混在一起。因此它适合作为第二面板，暂不作为短期唯一主比较轨道。

**不建议短期改选 CRAG、MTEB 或 LongBench。** CRAG 的端到端协议很有价值，但历史 leaderboard 已结束、mock API 和版本成本较高；MTEB/BEIR 只评检索，无法填充 SN 的答案结果表；LongBench 的同名 QASPER/QMSum/HotpotQA 是衍生题集，不能替代原版 scorer。短期应先交付 ALCE-ASQA 面板，再决定是否增加独立检索或动态 Web 轨道。

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

论文级系统比较应明确任务、测试范围、资料来源与评分口径，并披露模型、训练、提示及预算差异；引用发表汇总值不要求所有系统使用相同模型或重跑。逐题配对另需核对 case IDs、scorer 和指标分母；若要将差值归因于某个模块，则需控制其余条件。HotpotQA distractor 的结果不能和 fullwiki 结果混排；HotpotQA 的答案 F1 也不能和 MultiHop-RAG 的弱词交集准确率合成总分。

当前推荐的最小扩展为：保留 QASPER、MultiHop-RAG、ALCE、QMSum，并纳入 HotpotQA；若要证明 retriever 泛化，再单独增加 BEIR 受控子集。不要因为 benchmark 数量增加而制作跨套件平均分。
