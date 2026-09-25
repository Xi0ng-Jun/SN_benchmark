# MultiHop-RAG 与 ALCE 真实数据验收

核实日期：2026-09-24。用户开启代理后授权在本机下载这两套官方数据并检查实现。本次补上真实完整数据与官方评分脚本的验收；没有运行新的 SN/LLM 生成，也没有运行 AutoAIS、QA 模型或 MAUVE。正式模型实验仍在服务器执行。

这些检查分别对应哪些标准、框架如何执行、哪些结论仍不能作出，见[Benchmark 标准与实现符合性](notebook-benchmark-standards-and-conformance.md)。

## 下载身份与范围

使用固定官方版本。命令行原先没有自动使用 Windows 代理；通过已启用的本机代理下载成功，未改变系统代理配置。数据、隔离依赖和验收工件都位于当前工作树被 Git 忽略的 `var/benchmark-protocol-validation/`。

| 文件 | 固定发布版本 | 字节数 | 校验 |
| --- | --- | ---: | --- |
| MultiHopRAG.json | `71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82` | 5,171,312 | Git blob 与官方固定文件树一致；SHA256见下 |
| corpus.json | 同上 | 6,785,567 | Git blob 与官方固定文件树一致；SHA256见下 |
| ALCE-data.tar | `334fa2e7dd32040c3fef931a123c4be1a81e91a0` | 451,297,280 | SHA256与官方LFS记录完全一致 |

SHA256：

```text
MultiHopRAG.json  03cfb4926461f868684903aadc8024447bdda5bb3f6804741424cce338515bff
corpus.json       20b61b5ab84de84a927420c5d265b7ec8d859ae49980699958a787ade9e4d28f
ALCE-data.tar     eda837bf659a91b3648dc6e7ab6b17197664d93593857e8fdf3800b6aa6a98f0
```

源文件：[MultiHop固定发布](https://huggingface.co/datasets/yixuantt/MultiHopRAG/tree/71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82)、[ALCE固定发布](https://huggingface.co/datasets/princeton-nlp/ALCE-data/tree/334fa2e7dd32040c3fef931a123c4be1a81e91a0)。ALCE首次传输在约320MB处断开，续传后才校验完整大小及哈希；没有用部分文件做验收。压缩包解包前检查了路径和成员类型，8个JSON分别记录哈希。

## MultiHop-RAG

完整适配为 **2556题、609篇文章、6084条证据、1个完整语料分区，0排除**。题型为816 inference、856 comparison、583 temporal、301 null，与发布文件一致。

逐字段核对了标题、URL、来源、日期、作者、类别和正文。64篇作者为null、4篇作者为空文本，均保持缺失状态，不编造元数据。6084条证据与对应正文匹配。对所有题目的答案、题型和证据进行变更，生成侧的全部v3请求和公共文档保持不变。

检索验收从原始corpus独立构造4976个chunk，与项目2556题共20448个top-8排名逐项比较，包括分数、文档、原文位置及文本，全部一致。相同排名分别送入[固定官方完整检索脚本](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/retrieval_evaluate.py)和项目评分入口；2255个非null问题的4项指标对齐官方输出精度，301个null问题明确不进入检索分母。该BM25是本项目控制组，未复现论文的dense/hybrid检索器。

QA另构造事前固定 `index % 8` 的全题校准答卷，覆盖空输出、无词交集、正确答案、答案提取命中/不命中及多行反例。2556题逐题与[固定原函数及完整CLI](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/qa_evaluate.py)一致；959题为0、1597题为1，每种题型均覆盖正负例。这些构造答案包含标准答案，只能校准评分器，不能作为SN或其他方法成绩。

官方QA的“词集合有任何交集即成功”和非标准MAP均保留原口径并明确命名；“与官方脚本一致”不代表这些指标等于严格事实正确率或通用数学定义。

## ALCE

包内有5个普通候选文件和3个oracle重排文件；实际计数如下，不能以论文概述或`top100`文件名代替实测。

| 普通文件 | 题数 | 每题候选数 | 总候选槽位 | 同题重复的标题/正文槽位 |
| --- | ---: | --- | ---: | ---: |
| ASQA GTR | 948 | 100 | 94800 | 228 |
| ASQA DPR | 948 | 100 | 94800 | 260 |
| QAMPARI GTR | 1000 | 100 | 100000 | 289 |
| QAMPARI DPR | 1000 | 100 | 100000 | 340 |
| ELI5 BM25 | 1000 | 31～100 | 89462 | 0 |

五个普通文件均以 `max_documents=100` 完整prepare/load，未排除题目。相同标题/正文可共享材料存储，但原候选顺序、重复槽位及引用编号完整保留。QAMPARI每个变体各有5个空答案别名，按原始标注保留，不删题或擅改答案。

对五个普通文件的4896条记录逐字段修改answer/annotations/qa_pairs/answers/claims，并修改候选的score/has_answer/summary/extraction等辅助字段，检查这些变化不影响生成请求和公开正文，全部通过。candidate-topk、BM25、full-context共完成29376次变形提示相等比较，验证候选顺序与引用映射；预算设为足够容纳完整原文，仅验证输入，不调用模型。479062个候选位置与1117个同题重复位置全部保留。

三个oracle文件均每题5候选，来自相应普通候选池，但其顺序/选择与普通前5不同的题数分别为ASQA831、QAMPARI859、ELI5571。显式标记为oracle的数据被普通对照方法入口拒绝。来源真实性仍要由原文件/哈希核实，不能把oracle文件改写标签后称为普通检索条件。

评分校准采用三条主要普通轨道ASQA GTR、QAMPARI GTR、ELI5 BM25的全部2948题，固定8种输出模式，覆盖空答、首行截断、结束标记、错误/多重引用、列表和普通文本：

- ASQA的STR-EM/STR-HIT逐题及整体共1898项对照，与固定原始CLI一致。
- QAMPARI的5项文本指标逐题及整体共5005项对照，与固定原始CLI一致。
- 三个任务全部输出预处理与原始CLI一致。ELI5默认文本适配没有可直接比较的claim语义指标，仍标记pending，不能因预处理通过就说ELI5完整评分已通过。
- 原始CLI使用真实NumPy、NLTK和ROUGE执行；仅对未使用的torch/transformers导入提供“调用即失败”的占位，未生成任何模型分数。原始CLI的ASQA/ELI5 ROUGE运行成功，但默认轻量评分入口仍将它留在完整ALCE评分环境中，不把校准值注入正式结果。
- 用真实NLTK和固定AutoAIS函数跟踪实际进入聚合的题目，三套校准答卷的引用分母分别为711/1000/750，与桥接层记录的题目ID逐项一致。NLI边界仅用控制流探针替代，返回数值丢弃；这验证空答、分句、引用预处理和分母，**没有验证NLI判断或引用语义分数**。

该校准使用[固定ALCE源代码](https://github.com/princeton-nlp/ALCE/tree/246c476a4edfc564266b7346b6e29ef4861ae937)，不是对ALCE原论文全部实验条件的复现。

## 证据位置和继续执行

以下路径相对本工作树的 `var/benchmark-protocol-validation/`，不随Git分发；机器可读报告、原始stdout/stderr和校准脚本保留在本机：

| 证据 | 路径 |
| --- | --- |
| 下载清单、哈希与解包文件身份 | `vpn-acquisition-20260924/` |
| MultiHop完整适配、独立排名与官方检索 | `multihop-acceptance-20260924/run-v2/acceptance-report.json` |
| MultiHop正负混合QA校准 | `multihop-acceptance-20260924/qa-mixed-v2/mixed-calibration-report.json` |
| ALCE清点、完整适配、逐题隔离与oracle检查 | `alce-data-acceptance-20260924/report.json`及各变体的`case-checks.jsonl` |
| ALCE原始CLI与项目文本评分对照 | `alce-scoring-acceptance-20260924/{asqa,qampari,eli5}/report.json` |
| ALCE真实分句/官方函数的聚合范围 | `alce-scoring-acceptance-20260924/citation-scope-report.json` |

轻量依赖安装到单独的 `alce-lightweight-deps-20260924/`，未写入共享`.venv`；包版本和77个分句资源文件哈希记录在校准报告。独立完整Python回归仍使用原环境及真实Perl路径，2026-09-24结果为 **657 passed、0 skipped**。

本轮没有发现需要再修改评测源代码的新缺陷；这是对上一轮修正增加的真实数据证据。仍待服务器验证的是新SN生成、ALCE完整AutoAIS/QA/MAUVE环境和模型评分，以及同条件外部方法比较。操作入口见[当前实验指南](notebook-benchmark-experiment-plan.md)，不从这里的校准答卷推导方法排名。
