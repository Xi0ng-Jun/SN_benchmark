# 已有 SN 结果与外部工作对照

调研日期：2026-10-14。本文对应用户已返回的 ALCE-ASQA100、QASPER、QMSum 和 HotpotQA 四批服务器结果。所有表格统一为 **0–100，越高越好**；例如 0.31469 显示为 31.469。不构造跨 benchmark 总分。

2026-10-15 更新 ASQA：补充近期正式论文，澄清 100 题答卷是 ALCE 2023 一篇论文的四配置；详见[ASQA 对照选型复核](research/2026-10-15-asqa-comparison-selection.md)。

SN 分数与产物路径来自用户转述，尚未读取本轮服务器原始工件。论文结果来自一手来源；公开答卷历史重评分单列，并在本轮回读本地 scores。不同模型、训练、提示、输入预算允许作为系统水平参考，不能把差值直接归因于 SN 的检索或某个模块。引用发表汇总值不要求重跑外部模型。

## 1. 当前 SN 结果和对照选择

| 任务 | SN 已报范围 | SN 指标（百分制） | 优先外部对照 | 比较边界 |
| --- | --- | --- | --- | --- |
| ALCE-ASQA | 固定100题，100/100 success | STR-EM 44.433；STR-HIT 13.000 | 作者 human_eval 的4份公开答卷 | 最接近同题同评分；SN case IDs / scorer 尚待工件核实 |
| QASPER | test，1,451题 | Answer F1 31.469；Evidence F1 47.451 | Visconde；监督式全文QA作为另一组 | 公开摘要/caption、text-only证据口径和训练条件须披露 |
| QMSum | test，281题 | R1 27.746；R2 9.039；RL 24.247 | Socratic公开答卷同profile重评分；原版test论文 | 281/279差异、ROUGE实现及多次均值不可混同 |
| HotpotQA | distractor validation，7,405题 | Answer EM 29.98；F1 48.11；Joint F1 18.68 | 原版dev distractor监督方法；LLM子集另列 | fullwiki/test/随机子集分开，不减成7,368题 |

服务器相对产物路径：ALCE `scores/alce100-all-100`、`submissions/alce100-all-100`；QASPER `scores/qasper-sn`；QMSum `scores/qmsum-sn`；HotpotQA `scores/hotpot-sn`、`submissions/hotpot-sn`（用户报告415MB）。这些路径是来源线索，不是本机已验证的文件链接。

HotpotQA 的37条排除按[已有交接记录](server-upgrade-running-export-prompt.md)指向重复case/run选择，预期最终仍覆盖冻结的7,405个唯一case；本轮没有重新审核其选择依据，不能声称删除了37道独立标准题。

## 2. ALCE-ASQA：固定100题的文本正确性

| 方法 | N | STR-EM ↑ | STR-HIT ↑ | 证据 |
| --- | ---: | ---: | ---: | --- |
| **SN chunk** | 100 | **44.433** | **13.000** | 服务器转述 |
| GPT-3.5 VANILLA | 100 | 35.300 | 9.000 | 作者公开答卷，本项目重评分 |
| GPT-3.5 interactive summary | 100 | 38.200 | 9.000 | 同上 |
| GPT-3.5 sample4 RERANK | 100 | 36.550 | 11.000 | 同上 |
| Vicuna-13B | 100 | 26.467 | 5.000 | 同上 |

外部答案来自 [ALCE 作者 human_eval](https://github.com/princeton-nlp/ALCE/tree/246c476a4edfc564266b7346b6e29ef4861ae937/human_eval)。本轮回读四份评分文件，确认共用100题与相同scorer；空答案仍在分母。SN与它们的身份核对尚未完成，因此当前是有明确同题意图的汇总对照，不是已完成的配对统计。

这四行是同一篇 2023 论文的配置，不代表四篇近期方法；默认 scorer 执行固定官方字符串指标函数，未完成共同的引用模型评分。若论文目标是与近期方法比较，优先使用 948 题 full 范围；不能把不同范围的汇总值用于估计 SN 的排名。

按已报数字，SN比外部最高STR-EM多6.233分，比最高STR-HIT多2分；两项外部最高值来自不同配置。只说明这个样本上的字符串答案覆盖，不证明引用支持、完整事实正确率、显著性或同预算算法优势。

下列较大范围结果另作背景，**不与SN的100题排序**：

| 工作／配置 | STR-EM ↑ | STR-HIT ↑ | 关键范围差异 |
| --- | ---: | ---: | --- |
| ALCE ChatGPT VANILLA，5段 | 40.4 | 未报告 | 论文主实验；非固定100题 |
| ALCE GPT-4 VANILLA，20段 | 44.4 | 未报告 | 同上，更长输入 |
| Self-RAG 7B / 13B | 30.0 / 31.7 | 未报告 | ALCE-ASQA，专门训练 |
| InstructRAG-ICL / FT，Llama-3-Instruct-8B | 44.7 / 47.6 | 未报告 | ICLR 2025；948题，GTR top-5；ICL不微调、FT有ASQA训练 |
| Think&Cite，Llama-3.1-8B-Instruct / GPT-4o | 45.2 / 50.1 | 未报告 | ACL 2025；948题，GTR top-100内树搜索；带引用任务 |
| Cooperative CoRAG，Llama-3-Instruct-8B | 45.8 | 未报告 | Findings ACL 2026；948题，联合训练、推理选3段；可选补充 |
| MAIN-RAG Mistral-7B / Llama3-8B | 35.7 / 39.2 | 未报告 | ALCE-ASQA，多Agent过滤；正文未明确题数 |
| Adaptive-Note，GPT-3.5 | 47.3 | 22.0 | 948题；多轮检索；移除了引用要求 |

来源：[ALCE表4](https://aclanthology.org/2023.emnlp-main.398.pdf#page=6)、[Self-RAG正式版表2](https://proceedings.iclr.cc/paper_files/paper/2024/file/25f7be9694d7b32d5cc670927b8091e1-Paper-Conference.pdf#page=8)、[InstructRAG表2/3](https://proceedings.iclr.cc/paper_files/paper/2025/file/cdce0de17dc3cf97019a35baa65aebd0-Paper-Conference.pdf)、[Think&Cite表1/附录A](https://aclanthology.org/2025.acl-long.490.pdf)、[Cooperative CoRAG表1/2](https://aclanthology.org/2026.findings-acl.2157.pdf)、[MAIN-RAG表1](https://aclanthology.org/2025.acl-long.131.pdf#page=7)、[Adaptive-Note表1](https://arxiv.org/html/2410.08821v1)。ALCE原文称抽样1,000题，固定ASQA普通发布文件实数948；未将两者静默视为完全相同范围。旧方法详细输入见 [ALCE核查记录](research/2026-10-14-alce-external-results.md)；近期方法的引用P/R、选型和答卷可用性见[2026-10-15复核](research/2026-10-15-asqa-comparison-selection.md)。

## 3. QASPER：答案和证据分开看

| 方法／配置 | Answer F1 ↑ | Evidence F1 ↑ | 评测范围及输入／训练差异 | 来源 |
| --- | ---: | ---: | --- | --- |
| **SN chunk** | **31.469** | **47.451** | v0.3 test，1,451题；公开全文变体；服务器转述 | 用户汇总 |
| Visconde，monoT5-3B + GPT-3 | 49.1 | 38.5 | v0.3 test；篇内段落排序top-5，few-shot CoT；官方文本证据评分 | [原论文表1](https://arxiv.org/pdf/2212.09656#page=6)、[作者复现入口](https://github.com/neuralmind-ai/visconde#qasper) |
| LED-base，全文，无证据scaffold | 32.80 | — | 原版test；监督训练；正文最多16K；发表分母有下述未知 | [原论文表2](https://aclanthology.org/2021.naacl-main.365.pdf#page=7) |
| LED-base，全文，有证据scaffold | 33.63 | — | 同上，追加证据监督；此行不拼接另一表的证据分 | 同表 |
| CGSN（SciBERT）+ LED reader | 39.44 | 53.98 | **v0.2** test；训练reader用gold evidence，测试用预测evidence | [原论文表2](https://aclanthology.org/2022.emnlp-main.336.pdf#page=7)、[作者版本说明](https://github.com/JerrryNie/CGSN#qasper) |
| LongChat-7B-v1.5-32k，zero-shot | 28.81 | — | 原版完整test 1,451；全文，借用LongBench提示但不是其200题子集 | [论文表12](https://aclanthology.org/2024.customnlp4u-1.17.pdf#page=17) |
| 同LongChat，QLoRA 5 epochs | 55.20 | — | 同完整test；QASPER监督微调，训练过滤另行披露 | 同表 |

`—`表示该行不提供已核实的对应成绩，不是零。Visconde是本次最接近的官方v0.3 RAG对照：相对它，SN的报告值为Answer −17.631分、Evidence +8.951分。应表述为“答案与证据指标呈不同方向”，不能仅凭证据分推导答案可靠性更强。

SN输入包括公开摘要和caption，原LED reader主要读正文；原LED论文还写忽略需图表的题，而固定当前reader没有对应跳题，这使原论文实际评分分母仍需保留未知。CGSN训练时使用gold是监督条件，**不能误写为测试时oracle**。LongChat的另一种两步extract-then-answer配置有Evidence 42.57，但不是55.20那一行，不拼成一个双指标结果。

LAB、RAPTOR等另有参考成绩：LAB的证据指标是attribution，不是Evidence F1；RAPTOR论文55.7没有明确这一表的split／题数。未将它们塞进上表排名。完整取舍、跨表配对说明与来源见 [QASPER核查记录](research/2026-10-14-qasper-external-results.md)。

## 4. QMSum：优先同评分答卷，再看发表区间

### 4.1 当前281题范围的已保存评分

| 方法 | N | ROUGE-1 ↑ | ROUGE-2 ↑ | ROUGE-L ↑ | 来源 |
| --- | ---: | ---: | ---: | ---: | --- |
| **SN chunk** | 281 | **27.746** | **9.039** | **24.247** | 服务器转述 |
| Socratic SegEnc 公开答卷 | 281 | 38.955 | 13.960 | 33.942 | 本项目固定Perl profile历史重评分，本轮回读 |

Socratic的三项分母和文件hash已记录于[回读账本](research/2026-10-14-local-score-audit.json)，来源与逐行映射见[公开答卷记录](notebook-external-results-2026-09-28.md#4-qmsum281-条-socratic-segenc-公开预测按公开代码顺序映射原运行清单缺失)。这不是Socratic原论文五次运行均值。按已报汇总，SN分别低11.209／4.921／9.695分；SN scorer身份与题单尚待服务器工件对账，公开预测的原生成manifest也未恢复。

### 4.2 原版QMSum任务的发表参考

| 工作／配置 | ROUGE-1 ↑ | ROUGE-2 ↑ | ROUGE-L ↑ | 测试范围、方法与差异 | 原始来源 |
| --- | ---: | ---: | ---: | --- | --- |
| Locator + PGNet | 31.37 | 8.47 | 27.08 | 原论文test统计279；预测相关turns；监督训练 | [QMSum表3](https://aclanthology.org/2021.naacl-main.472.pdf#page=7) |
| Locator + BART-base | 31.74 | 8.53 | 28.21 | 同上；输入预测片段，非gold spans | 同表 |
| Locator + HMNet | 32.29 | 8.67 | 28.17 | 同上；分层会议摘要模型 | 同表 |
| SummN，QMSum-All | 34.03 | 9.28 | 29.48 | 多阶段全文摘要；历史公开答卷279行；pyrouge | [SummN表2](https://aclanthology.org/2022.acl-long.112.pdf#page=6) |
| DYLE | 34.42 | 9.71 | 30.10 | RoBERTa extractor + BART generator；test实际N未报 | [DYLE表3](https://aclanthology.org/2022.acl-long.118.pdf#page=6) |
| RELREG-W | 36.45 | 12.81 | 32.28 | 281 test；检索+生成；WikiSum预微调；SummEval | [Vig等表5](https://aclanthology.org/2022.findings-naacl.109.pdf#page=7) |
| SegEnc-W | 37.80 | 13.43 | 33.38 | 281 test；全文分段编码；WikiSum预微调；SummEval | 同表 |
| Socratic SegEnc | 38.06 | 13.74 | 33.51 | Books3预训练+QMSum微调；五次运行均值 | [Socratic表1](https://aclanthology.org/2023.acl-long.713.pdf#page=6) |
| LTRSum | 38.82 | 14.11 | 34.45 | SegEnc+联合排序训练；正文报279；scorer细节未完整披露 | [LTRSum表1](https://aclanthology.org/2024.emnlp-main.838.pdf#page=6) |

原版论文方法的R1约31–39，SN当前27.746较低；R2与部分早期方法较接近。这里只描述跨条件报告值。原版数据统计279与当前281、句切分、ROUGE实现、多次运行平均都有差异，不能对所有行统称“完全同scorer”。当前固定Perl profile采用作者推荐方向加明确HMNet分句选择，不等于历史论文每份环境都已复现。

SummN-Gold、HMNet gold-input、DYLE Oracle不进此表。ChatGPT的100例研究、GoR的general-only任务、LongBench的200例不能冒充281全量；现代LLM及不同ROUGE实现的参考放在[QMSum核查记录](research/2026-10-14-qmsum-external-results.md)。

## 5. HotpotQA：只从distractor dev表取主对照

下面是标准dev distractor发表参考，所有外部方法均有监督训练。标准dev为7,405题；除原数据定义外，本轮未取得各论文逐ID评分清单，不声称已和SN机器配对。`—`是原来源未报或没有给可用于该行的分数。

| 方法／配置 | Answer EM ↑ | Answer F1 ↑ | Supporting F1 ↑ | Joint F1 ↑ | 原始dev来源 |
| --- | ---: | ---: | ---: | ---: | --- |
| **SN chunk** | **29.98** | **48.11** | 未提供 | **18.68** | 服务器转述，7,405题 |
| HotpotQA原始baseline | 44.44 | 58.28 | 66.66 | 40.86 | [Yang等表4](https://aclanthology.org/D18-1259.pdf#page=8) |
| QFE | 53.7 | 68.7 | 84.7 | 60.6 | [QFE表4](https://aclanthology.org/P19-1225.pdf#page=5) |
| DFGN，2-layer | 55.42 | 69.23 | — | — | [DFGN表2](https://aclanthology.org/P19-1617.pdf#page=7) |
| SAE，BERT-base | 61.32 | 74.81 | 85.27 | 66.45 | [SAE表1 Dev区](https://arxiv.org/pdf/1911.00484#page=6) |
| SAE-large，RoBERTa-large | 67.70 | 80.75 | 87.38 | 72.75 | 同表 |
| QUARK | 67.75 | 81.21 | 86.97 | 72.26 | [QUARK表1](https://aclanthology.org/2020.emnlp-main.711.pdf#page=4) |
| HGN，RoBERTa-large | — | 82.22 | 88.58 | 74.37 | [HGN表5](https://aclanthology.org/2020.emnlp-main.710.pdf#page=8) |
| HGN，ALBERT-xxlarge-v2 | — | 83.46 | 89.2 | 75.79 | 同表 |

最基础参照下，SN的Answer F1低10.17分、Joint F1低22.18分。值得优先检查答案形式、真实检索结果及句级证据预测，但汇总分不能确定是哪一步导致差距。Joint F1不等于两个汇总F1相乘；缺Supporting F1时不能由当前两列反推它。

现代LLM/RAG另有下列参考。三行来自KG2RAG论文内部对照，生成模型均为LLaMA3-8B；列的是答案F1，不能当Supporting或Joint：

| 原作者Hotpot-Dist实验 | Answer F1 ↑ | 关键差异 |
| --- | ---: | --- |
| Semantic RAG | 61.7 | mxbai-embed-large，k=10 |
| Semantic RAG + rerank | 65.2 | 追加bge-reranker-large |
| KG2RAG | 66.3 | KG扩展与组织；同模型/embedding/reranker |

来源：[KG2RAG表1](https://aclanthology.org/2025.naacl-long.449.pdf#page=6)。论文未明确实际评分N/split；作者数据副本的历史核查能对齐dev，但不能直接证明论文分母。仓内历史核查还发现作者提示含4道本次dev题及答案，因此保留原作者发表参考，不能声称是无评测示例重叠的严格匹配基线。其“Hotpot-Full”也不是官方全Wikipedia fullwiki。

ReAct/Wikipedia API的500题、Beam Retrieval的blind test、官方test榜单和LongBench不混入主表。作者原文DFGN/HGN与后续论文转引有版本数字差异，已回到原作者dev表，未拼接。各方法训练、上下文上限、排除原因见 [HotpotQA核查记录](research/2026-10-14-hotpotqa-external-results.md)。

## 6. 如何使用这些表

1. **现在即可用作研究汇报的四组结果。** 保留SN的“服务器转述”脚注；ALCE100和QMSum公开答卷重评分面板优先；QASPER优先Visconde；HotpotQA保留dev监督参考与LLM发表参考两个面板。
2. **论文主表不必等待重跑外部方法。** 使用发表值和原来源即可，但标题／脚注声明不同训练、模型、split、输入或评分条件。不能给所有方法加统一scorer标签，也不宣称SOTA或显著优越。
3. **需要补的是SN已有工件身份。** 回读对应scores、submission、case IDs和模型／预算manifest；ALCE核对固定100题，Hotpot核对37条重复选择及最终7,405题。无需为了引用表格重跑生成，也无需回传415MB完整答卷才能开始核对汇总与身份。
4. **具体差异归因留给逐题证据。** QASPER高证据/低答案、Hotpot低Joint、QMSum低ROUGE都是排查线索；不能由表格直接证明检索失败、证据投影故障或生成器较差。

## 7. 核查记录

本轮联网检查原论文、作者仓库与数据说明，并独立回读4份ALCE、1份QMSum的历史评分文件。保存5份文件的哈希、题单哈希、分母与scorer identity于[本地账本](research/2026-10-14-local-score-audit.json)。未调用生成模型、judge、训练或重评分，也未连接服务器。本调研选择与现有四套结果相关且有协议解释价值的工作，不声称文献穷尽或截至当日的最高成绩。

- [ALCE详细来源与扩展表](research/2026-10-14-alce-external-results.md)
- [QASPER详细来源与排除项](research/2026-10-14-qasper-external-results.md)
- [QMSum详细来源、ROUGE差异与LLM参考](research/2026-10-14-qmsum-external-results.md)
- [HotpotQA详细来源、dev/test差异与RAG参考](research/2026-10-14-hotpotqa-external-results.md)
