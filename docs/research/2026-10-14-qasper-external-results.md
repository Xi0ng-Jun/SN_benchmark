# QASPER 外部发表结果核查（2026-10-14）

本文提供可以回到原论文和作者代码核查的比较资料。外部数值均为 **published-reference（作者报告值）**，本次没有重跑模型或重新评分。SN 的 `n=1451, Answer F1=0.31469, Evidence F1=0.47451` 来自**用户服务器转述**；本次未读取该服务器答卷、评分工件或配置，不称本机核实。

## 研究范围与数据身份

目标是原始 QASPER 的论文问答／证据选择，不把 LongBench、SCROLLS、ZeroSCROLLS 的同名子任务成绩合入原版排名。检索覆盖原始 LED、对比学习、监督检索、GPT-3 RAG、LongChat 微调和后续 LLM／树检索论文；下表优先保留原版 test 的结果，有输入或范围差异时直接注明。

本项目固定数据是 **v0.3 test，416 篇论文／1451 道题，全文资料条件**，生成不见 gold。作者 [HF 元数据](https://huggingface.co/datasets/allenai/qasper/blob/main/dataset_infos.json) 明列 `version_str=0.3.0`、test `num_examples=416`（单位是论文）；[原论文 §4.1，p.4603](https://aclanthology.org/2021.naacl-main.365.pdf#page=5) 列 test 1451 个问题。仓内完整文件核查与 hash 见 [标准与符合性 §3／附录](../notebook-benchmark-standards-and-conformance.md)。本次沿用已有文件核查，不重新下载数据。

本项目的全文公开输入变体包含标题、摘要、正文和 caption。原 LED reader 的 `full_text` 仅章节标题和正文段落，因此“全文”不意味着模型看到的字符串完全相同。评测 profile 为 `qasper-v03-full-text-answer-v1`，固定官方 evaluator 使用 `text_evidence_only=True`；SN Evidence F1 是最终引用经声明的 chunk→原段落投影产生的预测集合。外部的段落分类器或检索器不是同一证据产生机制。

## 原版 test 的主要结果

为便于阅读，以下统一用 **0–100 F1 点**；SN 对应 **31.469 / 47.451**。`—` 是未提供可用于该行的指标，不是 0。数值精度保留论文原样；`≈` 表示从论文一位小数基线与增量相加，不能当作精确重新评分。

| 方法／配置 | Split、输入与训练条件 | Answer F1 | Evidence F1 | 同行关系与原始出处 |
|---|---|---:|---:|---|
| SN（用户服务器转述） | v0.3 test；n=1451；本项目完整公共输入；模型、预算和服务器实际工件待核对 | 31.469 | 47.451 | 同一服务器报告，非本机核实；本次用户提供 |
| LED-base，Q+Full Text，无 evidence scaffold | 原始 QASPER test；正文输入，最多16K tokens；QASPER 监督训练 | 32.80 | — | [Dasigi et al. 2021 Table 2，p.4605](https://aclanthology.org/2021.naacl-main.365.pdf#page=7)，`Q+Full Text` 行 |
| LED-base，Q+Full Text，with evidence scaffold | 原始 QASPER test；联合答案生成与段落证据监督；原论文范围限制见下文 | 33.63 | 29.85† | Answer：[Table 2](https://aclanthology.org/2021.naacl-main.365.pdf#page=7)；Evidence：[Table 3](https://aclanthology.org/2021.naacl-main.365.pdf#page=7)。同模型家族，但来自两张表，未核实共同 checkpoint；†不可宣称逐题同一答卷 |
| LED-large，证据选择 | 原始 QASPER test；长文段落分类、监督训练 | — | 39.37 | [Dasigi et al. 2021 Table 3](https://aclanthology.org/2021.naacl-main.365.pdf#page=7)。不能配 LED-base 的答案分；作者称 large 在答案任务更差 |
| LED-base + LQE（question-evidence contrastive loss） | 原始 QASPER test；复现 LED 并追加监督对比损失；只在可回答且有证据的训练实例上追加该损失；版本和最终分母未列 | ≈33.5 | ≈30.6 | [Caciularu et al. 2022 Table 2，p.2876](https://aclanthology.org/2022.naacl-main.207.pdf#page=5)：test Overall 32.8 + 0.7；Evidence 29.9 + 0.7；同一表同配置。完整 test 身份仍有未知项 |
| Visconde：monoT5-3B + GPT-3 text-davinci-002 | 作者复现入口为 **v0.3 test**；逐论文所有段落 rerank，取 top5；不做 QASPER query decomposition 或 BM25；few-shot CoT，目标题不提供 gold evidence | **49.1** | **38.5** | [Pereira et al.，ECIR 2023／arXiv v1 Table 1，PDF p.6](https://arxiv.org/pdf/2212.09656#page=6)，Visconde 同一行；[作者 README QASPER](https://github.com/neuralmind-ai/visconde#qasper) 明确官方 evaluator 和 `--text_evidence_only` |
| CGSN（SciBERT selector）+ LED evidence-trained reader | **v0.2 test**，不是 v0.3 复评分；selector 读完整论文并预测段落；reader 在训练集 gold evidence + QA 上训练，**test 输入是预测 evidence** | **39.44** | **53.98** | [Nie et al. 2022 Table 2，p.5042](https://aclanthology.org/2022.emnlp-main.336.pdf#page=7)，CGSN (SciBERT) 同一行；[作者 README](https://github.com/JerrryNie/CGSN#qasper) 明示 v0.2 和 test 预测证据流水线 |
| LongChat-7B-v1.5-32k，单步 LongBench prompt，zero-shot | **原版完整 test 1451**；全文 QA；借用提示模板，不是 LongBench 200题子集；无 QASPER 微调 | **28.81** | — | [Hilgert et al. 2024 Table 12，PDF p.17](https://aclanthology.org/2024.customnlp4u-1.17.pdf#page=17)；题量见 Table 1 |
| LongChat-7B-v1.5-32k，同单步 prompt，QLoRA 5 epochs | **原版完整 test 1451**；训练来自 QASPER train，过滤含图表证据、冲突答案和超过8K训练文本；test 不给 gold evidence | **55.20** | — | [Hilgert et al. 2024 Table 12](https://aclanthology.org/2024.customnlp4u-1.17.pdf#page=17)，5 epochs 列；不是 Table 7 的 ZeroSCROLLS 500题结果 |
| LongChat-7B-v1.5-32k，extract-then-answer，QLoRA 5 epochs | **原版 full test 1451**；先生成证据文本再回答；与前一行的提示／训练目标不同 | — | **42.57‡** | [Hilgert et al. 2024 Table 13，PDF p.18](https://aclanthology.org/2024.customnlp4u-1.17.pdf#page=18)，Test evidence F1；没有同配置完整 test Answer F1。‡证据文本→段落集合解析和 scorer 开关未充分披露，视为作者名为 Evidence F1 的参考值 |

这张表有多个可核查方法和配置，但不构成统一排行榜。最接近本项目完整 v0.3、官方文本评分、答案／证据同时报告的 RAG 参考是 Visconde。监督方法 CGSN 和全文 LLM LongChat 是有价值的不同条件参考；它们应与训练、版本或评分未知项一起展示，不能因为有差异而把发表成绩全部删掉。

## 表格定位与可复查证据

只摘录定位所需的短文本，不复制整张论文表。

| 来源 | 定位／短摘录 | 本次确认的事实 |
|---|---|---|
| [QASPER 原论文](https://aclanthology.org/2021.naacl-main.365/) | §4.1；Table 2、Table 3，印刷 p.4603、4605；`Q+Full Text w/ scaff.` | Answer 33.63 与 Evidence 29.85 分别位于不同表；large 的 39.37 是证据分 |
| [固定作者 LED reader](https://github.com/allenai/qasper-led-baseline/blob/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/qasper_baselines/dataset_reader.py) | `_extract_answer_and_evidence`、`_get_paragraphs_from_article` | FLOAT 分支只加统计；full_text 取章节名／段落；当前固定 reader 未在此删图表题。不能据此证明发表运行使用完全相同版本 |
| [LED 作者 README](https://github.com/allenai/qasper-led-baseline) | Experiments：with/without scaffold、shorter contexts、LED-large | 支持 scaffold、gold-evidence／短输入等独立配置；large 的 QA 分不能用 base 替代 |
| [Long Context QA via Supervised Contrastive Learning](https://aclanthology.org/2022.naacl-main.207/) | Table 2；`+ LQE (∆)`；§4.1、Appendix B.1 | 同时报告 Answer 与 Evidence 改变量；33.5 / 30.6 是保留一位小数的加法推导 |
| [Visconde 论文](https://arxiv.org/abs/2212.09656) | §3、§4.2、Table 1；Qasper `Visconde` 行 | GPT-3 few-shot；QASPER 用 monoT5 对本篇段落排序取5条；同一行 49.1 / 38.5 |
| [Visconde 作者复现代码入口](https://github.com/neuralmind-ai/visconde#qasper) | `--gold data/qasper-test-v0.3.json --text_evidence_only` | split 和评分开关可核查；正文表格本身未写 split，不能只引用表格就声称文件级已对齐 |
| [CGSN 论文](https://aclanthology.org/2022.emnlp-main.336/) | Table 2；§4.2；Appendix C；`CGSN (SciBERT)` | 同一系统行 39.44 / 53.98；gold evidence 是 reader 的训练监督，推理阶段先预测 evidence |
| [CGSN 作者 README](https://github.com/JerrryNie/CGSN) | Data Preparation／Qasper；`version 0.2 of Qasper` | 用 v0.2 test；dev 选 epoch-2、test 用该 checkpoint 的证据预测；不能改标 v0.3 |
| [Hilgert et al. 2024](https://aclanthology.org/2024.customnlp4u-1.17/) | §4.1、Table 1、Table 12、Table 13；`test 1,451` | 完整 test 与 ZeroSCROLLS 子集分开报告；Table12 单步答案 55.20，Table13 两步证据42.57，不属于同一配置 |

## 已核查但不合入主要原版 test 比较的结果

| 工作／配置 | 已核实数字（0–100） | 不能与 SN 1451题直接同轨排序的原因／出处 |
|---|---:|---|
| RAPTOR + GPT-4；同论文 BM25 + GPT-4、DPR + GPT-4 | F1-Match **55.7 / 50.2 / 53.0** | [ICLR 2024 论文 Table 3](https://arxiv.org/html/2401.18059v1#S4)；从全文构建树并检索原文／GPT-3.5摘要，QA上下文预算2000 tokens。论文只列 QASPER 总规模5049题，未明确此表 split、题数、v0.3或官方 evaluator；无 Evidence F1。不能补写成1451题test。可作为带范围未知标识的树检索参考 |
| LAB / GPT-4 citation、post-hoc；GPT-3.5 citation | QASPER “EM”（Exact match F1）**68 / 65 / 51** | [Buchmann et al. 2024 Table 1、Table 3，§4.2](https://aclanthology.org/2024.emnlp-main.463.pdf#page=6)；Table1确有test1451，三例 few-shot，输入最大16K。QASPER证据列是 **ATT attributable score**，citation 76、post-hoc68，不是官方 Evidence F1。答案指标沿用匹配F1体系，但本次未核实 scorer代码与本项目固定脚本等价，保留作者指标名 |
| DRC：DiT+pdfminer.six，ELECTRA_CE-ft，UnifiedQA-ft | Answer F1 **40.16** | [论文 Table 2、§2、§4.1](https://arxiv.org/html/2210.01959v3#S3)；从原PDF提取文本，但明确剔除 unanswerable，且描述使用 UnifiedQA tokenizer；分母不是完整1451。Table6 ELECTRA_CE-ft Evidence36.12是另一个证据分类实验，不能直接配40.16成同一端到端答卷 |
| AttenWalker，监督 LED+synthetic pretraining | Overall F1 **27.08** | [ACL Findings 2023 Table 1／§4](https://aclanthology.org/2023.findings-acl.862.pdf#page=6)；只保留 extractive/abstractive，丢弃 yes/no、unanswerable；“Overall”不是原版全部题型 |
| LongChat / ZeroSCROLLS、gold-evidence dev-short | 不移入主表 | [Hilgert et al. Table7、Table14](https://aclanthology.org/2024.customnlp4u-1.17.pdf) 分别为500题衍生test和990题dev-short gold-evidence；论文Table12/13已另提供完整test资料 |
| LongBench v1 Qasper、SCROLLS／LongT5／CoLT5 聚合榜单 | 不移入主表 | 衍生输入、题目集合或评分发布格式不同；RAPTOR Table5转引的53.1／53.9不能绕过原来源协议核查 |
| 第三方 QASPER evidence leaderboard、博客与聚合站 | 不作为数值依据 | 聚合表可作为线索，本报告的关键数值来自原论文和作者代码；不把失效 Papers with Code 地址当作者官方在线榜单 |

## 可比性限制与尚未知项

1. **发表参考不等于受控消融。** SN 对 Visconde 的表面差值为 Answer −17.631点、Evidence +8.951点；对 CGSN 为 Answer −7.971点、Evidence −6.529点。它们只描述报告值，不能证明 SN 某个模块优劣、统计显著性或整体胜负。模型、监督训练、提示、token预算及证据输出政策均不同。
2. **LED 的发表分母存在论文／当前代码差异。** 原论文 §5 字面写忽略需要图表的题（约13%）；固定作者 reader 的 FLOAT 分支却只计数、没有跳题。论文 test split 的1451题不自动证明 Table2/3实际评分分母也是1451。本报告不虚构该缺失信息，也不为比较静默过滤 SN 题目。
3. **版本没有自动等价。** CGSN明确v0.2；LED原论文、LQE和Hilgert没有提供本项目v0.3原文件hash／case-ID对账。原版任务身份支持发表参考，但不能说已经达到逐题配对。
4. **Answer／Evidence 配对必须保留配置身份。** CGSN与Visconde各有同一论文行；LED-base跨表，checkpoint未知；LongChat55.20和42.57提示／目标不同；RAPTOR无证据分；LAB的ATT不是Evidence F1。禁止拼接各实验最高分。
5. **LongChat评分细节仍待作者代码确认。** 论文提官方脚本与多参考取最大，但没有给此完整test表的脚本commit、text-only开关、生成证据原文映射政策。42.57应显示脚注，不能宣称已验证与SN官方set-F1完全一致。论文test表没有注明删test题；Table1明列1451，训练过滤与dev-short过滤另列。
6. **RAPTOR范围未知未被其他论文转引消除。** 此次直接查原论文Table3与数据说明，未找到QASPER表对应split/题数或官方scorer入口；不因后续论文称SOTA而补齐身份。
7. **服务器SN工件未知。** 尚未核查模型标识、配置与源码、全量scope/case IDs、状态分母、Answer/Evidence是否同一score批次、引用投影成功率及官方命令。文档整理不将这些未知改为完成。

本次交付是可继续补核的文献资料：已核查论文数值、表号与主要输入／训练差异，未复现外部训练、未调用模型、未更新运行或服务器状态。
