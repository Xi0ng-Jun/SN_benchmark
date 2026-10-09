# Benchmark 实验结果表与填报协议

日期：2026-10-09。本文覆盖 QASPER、MultiHop-RAG、ALCE、QMSum 和 HotpotQA。
本文保存当前计划的结果表结构。
本文不记录新的服务器成绩。
本次服务器任务仅根据已跑出的结果填报，不负责选择或安排实验。

实验目标是获得可追溯的答案、证据、官方分数和比较结论。
下列表格是这些证据的论文呈现形式。
每个有效数值都必须有可核实来源。
发表参考值回到其原论文或作者结果页；本项目新成绩回到实际运行与评分工件。

已有发表成绩是有效的论文比较证据。
它们不要求逐题答卷、统一重评分或重跑外部模型。
同轨结果可标明来源后并列；协议不同或关键条件未知时分组展示。
本模板中的 BM25 行不是所有比较的必做前提。
是否需要受控实验取决于论文主张，不能用候选行是否填满判断证据是否充分。
榜单、后续论文实例和 LongBench 衍生版本差异见[官方资料手册](notebook-benchmark-official-resources.md)。

## 1. 执行范围与阅读方式

当前执行范围以 [CURRENT_STATE.md](../CURRENT_STATE.md) 为准。
当前范围为五套 SN chunk，以及 MultiHop-RAG SN reasoning。
外部方法和 reference 暂缓。
用户后续对服务器的明确任务可以改变某项授权范围。
实验选择由用户与主 agent 确定。
本次填报不要求服务器判断下一项实验或恢复任何缺失来源。

| 方法组 | 本文中的位置 | 当前执行含义 |
| --- | --- | --- |
| 五套 SN chunk | 主结果行 | 当前 SN-only 范围；ALCE 按三任务分别报告 |
| MultiHop SN reasoning | QA 结果行 | 当前范围；没有单一检索排名契约 |
| BM25 controls | 保留比较行 | 登记计划中，当前暂缓；已有专项授权另记 |
| QASPER LAB、ALCE VANILLA、Hotpot KG2RAG | 保留候选行 | 受控入口已登记；服务器验收或 full 范围尚需明确 |
| Multi-Meta-RAG、ALCE human_eval、QMSum Socratic | 公开答卷行 | 已记录历史重评分；服务器工件须独立核实 |
| 论文或作者汇总值 | 独立参考面板 | 可直接引用；保留 setting、分母和条件差异 |

模板的行可以保留为空。
空位不是失败，也不是执行指令。
smoke 结果另存，不填入 full 行。
不得把候选方法的 smoke 验收当作全量比较。

下列表中：

- `待填` 表示尚无本轮经核验的数值。
- `pending` 表示缺少指标所需观测或评分依赖。
- `error` 表示评分失败，必须附错误来源。
- `unavailable` 表示无法取得所需数据或观测。
- `N/A` 表示该结果不适用。
- `暂缓` 表示该方法当前不执行。

生成状态另用 `success`、`no_answer`、`clarification`、`error`、`missing`。
两种状态不能混用。
有效的零分写 `0`，并保留评分来源。
缺分不填零。

## 2. 表 1：数据与实验协议

**Table 1. Benchmark datasets, evaluation scopes, and protocol identities.**

| Benchmark / task | Setting / split | 材料单元 | Full cases | 特殊指标分母 | 输入条件 |
| --- | --- | ---: | ---: | --- | --- |
| QASPER | v0.3 test | 416 篇论文 | 1,451 | 答案 1,451；证据需完整投影 | 标题、摘要、正文、公开 caption；区别于 LED full_text reader |
| MultiHop-RAG | frozen public collection | 609 篇文章 | 2,556 | QA 2,556；检索 2,255，排除 301 null | 完整 corpus；生成不见题型或 gold facts |
| ALCE-ASQA | ordinary GTR release | 每题完整候选集 | 948 | 每项指标另记 eligible IDs | 保留原候选编号和顺序 |
| ALCE-QAMPARI | ordinary GTR release | 每题完整候选集 | 1,000 | 每项指标另记 eligible IDs | 同上 |
| ALCE-ELI5 | ordinary BM25 release | 每题完整候选集 | 1,000 | 每项指标另记 eligible IDs | 同上 |
| QMSum | frozen test | 35 场会议 | 281 | 37 general、244 specific | 完整会议与 query；不使用 gold spans |
| HotpotQA | distractor validation | 73,700 段落 | 7,405 | 答案 7,405；SF/Joint 需完整显式预测 | 每题完整 context；不与 fullwiki/test 混报 |

表 1 的配套协议记录按方法保存以下字段：

| Row ID | Bundle ID / raw hash | Scope / case-ID hash | Evaluator / SN SHA | Generator / tokenizer | Prompt / sampling / budget | Scorer / dependencies | 重要适配差异 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 每个正式结果行 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

full 和 subset 是独立的范围属性。
结果类别另用 `published-reference`、`recomputed-subset`、`controlled-rerun`。
机器标签 `recomputed-subset` 也可表示 full 公开答卷重评分。
full 覆盖不等于原论文复现。

## 3. 表 2：QASPER 答案与证据

**Table 2. Answer and evidence performance on QASPER v0.3 test under the declared input profiles.**

| 方法 / Row ID | 类别 | 范围 / N | Answer F1 ↑ | Evidence F1 ↑ | 计划状态 |
| --- | --- | --- | --- | --- | --- |
| SN chunk / `sn.qasper.chunk` | controlled-rerun | full / 1,451 | 待填 | 待填 / pending | 当前范围 |
| BM25 control / `qasper.bm25.full_text.control` | controlled-rerun | full / 1,451 | 待填 | 待填 / pending | 暂缓 |
| LAB LongChat citation / `qasper.lab.longchat.citation.control` | controlled-rerun | 实际验收范围待填 | 待填 | 待填 / pending | 候选；full 不由验收模板自动授权 |

Answer F1 使用固定官方多参考评分。
Evidence F1 使用显式预测的原始段落。
任一范围内题目存在证据映射错误时，整批 Evidence F1 保留 pending。
不得只对映射成功题计算正式证据成绩。
上下文覆盖放入诊断，不填到 Evidence F1。
LED 论文值可另列发表参考面板；输入差异须披露。

## 4. 表 3：MultiHop-RAG 答案与检索

**Table 3a. End-to-end QA performance on the complete MultiHop-RAG collection.**

| 方法 / Row ID | 类别 | QA weak-match accuracy ↑ | QA N | 计划状态 |
| --- | --- | --- | ---: | --- |
| SN chunk / `sn.multihop_rag.chunk` | controlled-rerun | 待填 | 2,556 | 当前范围 |
| SN reasoning / `sn.multihop_rag.reasoning` | controlled-rerun | 待填 | 2,556 | 当前范围 |
| BM25 control / `multihop.author_bm25.qa` | controlled-rerun | 待填 | 2,556 | 暂缓 |
| Multi-Meta-RAG GPT-4 / `multihop.multimeta.gpt4_voyage02` | recomputed-subset | 0.6060250391† | 2,556 | 历史公开答卷 |
| Multi-Meta-RAG PaLM / `multihop.multimeta.palm_voyage02` | recomputed-subset | 0.6075899844† | 2,556 | 历史公开答卷 |

**Table 3b. Official retrieval metrics on the non-null MultiHop-RAG questions.**

| 方法 | Hits@4 ↑ | Hits@10 ↑ | MAP@10 ↑ | MRR@10 ↑ | Retrieval N |
| --- | --- | --- | --- | --- | ---: |
| SN chunk | 待填 | 待填 | 待填 | 待填 | 2,255 |
| BM25 control | 待填 | 待填 | 待填 | 待填 | 2,255 |
| Multi-Meta-RAG shared retrieval† | 0.7920177384 | 0.9042128603 | 0.3388160642 | 0.6747622567 | 2,255 |
| SN reasoning | pending | pending | pending | pending | N/A：无单一排名契约 |

Multi-Meta-RAG 的两份 QA 答卷使用相同的公开检索结果。
检索面板只列一次该排名，避免暗示两次独立实验。
QA 指标是作者的弱 token-intersection match。
MAP 使用固定作者公式，不能改成教科书公式后沿用同一标签。
检索必须来自真实有序输出，不能从答案或 gold 反推。

† 数字来自 [2026-09-28 外部结果记录](notebook-external-results-2026-09-28.md)。
这些数字不是本次服务器复核结论。
正式汇编时须记录原文件、导入映射和评分工件身份。

## 5. 表 4：ALCE 三任务与公共样本

**Table 4. Task-specific correctness, fluency, and citation metrics on ALCE.**

### 表 4a：ASQA full，948 题

宽表分成两个面板。两个面板使用相同 Row ID。

| 方法 | 类别 / 状态 | STR-EM ↑ | STR-HIT ↑ | QA-EM ↑ | QA-F1 ↑ | QA-Hit ↑ | Scope N |
| --- | --- | --- | --- | --- | --- | --- | ---: |
| SN chunk / `sn.alce.chunk`（ASQA） | controlled-rerun / 当前范围 | 待填 | 待填 | 待填 / pending | 待填 / pending | 待填 / pending | 948 |
| VANILLA / `alce.vanilla.ordinary`（ASQA） | controlled-rerun / 暂缓 | 待填 | 待填 | 待填 | 待填 | 待填 | 948 |

| 方法 | ROUGE-Lsum ↑ | MAUVE ↑ | Citation precision ↑ | Citation recall ↑ | 指标分母记录 |
| --- | --- | --- | --- | --- | --- |
| SN chunk（ASQA） | 待填 / pending | 待填 / pending | 待填 / pending | 待填 / pending | 每项 N 与 eligible-ID hash |
| VANILLA（ASQA） | 待填 | 待填 | 待填 | 待填 | 同上 |

### 表 4b：QAMPARI full，1,000 题

| 方法 | 类别 / 状态 | Precision ↑ | Recall ↑ | Recall@5 ↑ | F1 ↑ | F1@5 ↑ | Citation P ↑ | Citation R ↑ |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SN chunk（QAMPARI） | controlled-rerun / 当前范围 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 / pending | 待填 / pending |
| VANILLA（QAMPARI） | controlled-rerun / 暂缓 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 |

### 表 4c：ELI5 full，1,000 题

| 方法 | 类别 / 状态 | Claims-NLI recall ↑ | ROUGE-Lsum ↑ | MAUVE ↑ | Citation P ↑ | Citation R ↑ |
| --- | --- | --- | --- | --- | --- | --- |
| SN chunk（ELI5） | controlled-rerun / 当前范围 | 待填 / pending | 待填 / pending | 待填 / pending | 待填 / pending | 待填 / pending |
| VANILLA（ELI5） | controlled-rerun / 暂缓 | 待填 | 待填 | 待填 | 待填 | 待填 |

三个 full 任务不能平均为一个 ALCE 总分。
QAMPARI 的列表指标不能简写为 list accuracy。
ELI5 的主要 correctness 是 claims-NLI recall，ROUGE 是辅助词面指标。

### 表 4d：ASQA human_eval 固定 100 题

**Table 4d. Text correctness on the fixed public ASQA human-evaluation sample.**

| 方法配置 | 类别 | Scope N | STR-EM ↑ | STR-HIT ↑ | Citation P/R |
| --- | --- | ---: | --- | --- | --- |
| SN chunk（同一固定题单） | controlled-rerun | 100 | 待填 | 待填 | 与外部共同指标 unavailable |
| GPT-3.5 VANILLA | recomputed-subset | 100 | 0.353000† | 0.090000† | unavailable |
| GPT-3.5 interactive summary | recomputed-subset | 100 | 0.382000† | 0.090000† | unavailable |
| GPT-3.5 sample4 RERANK | recomputed-subset | 100 | 0.365500† | 0.110000† | unavailable |
| Vicuna-13B | recomputed-subset | 100 | 0.2646666667† | 0.050000† | unavailable |

SN 100 题比较使用已导入来源的固定 case IDs，不自行抽样。
如果 full SN 工件包含这 100 题且身份兼容，使用已有输出导出新 subset。
不得仅为生成这张表重新问答。
已有完整逐题 STR-EM/STR-HIT 时，可按固定题单汇总已有分数。
账本标明该数值由已有逐题分数汇编。
MAUVE、ROUGE 等批量结果不能从 full 总分拆出 subset 值。
表 4d 与 full 表 4a 分开。
空答案保留在 100 的文本分母中。

### 表 4e：ELI5 human_eval 固定 100 题

| 方法配置 | 类别 | Scope N | Claims-NLI recall ↑ | ROUGE-Lsum ↑ | MAUVE ↑ | Citation P/R |
| --- | --- | ---: | --- | --- | --- | --- |
| SN chunk（同一固定题单） | controlled-rerun | 100 | 待填 / pending | 待填 / pending | 待填 / pending | 与外部共同指标 unavailable |
| GPT-3.5 VANILLA | recomputed-subset | 100 | pending | pending | pending | unavailable |
| GPT-3.5 interactive summary | recomputed-subset | 100 | pending | pending | pending | unavailable |
| GPT-3.5 sample4 RERANK | recomputed-subset | 100 | pending | pending | pending | unavailable |
| Vicuna-13B | recomputed-subset | 100 | pending | pending | pending | unavailable |

表 4d/4e 是公共样本比较目标，不自动授权新的 subset 生成或模型评分。
公开 human_eval 缺完整生成时文档编号映射。
不能补造 citation mapping，也不能填零引用分。
当前没有 QAMPARI 公开逐题样本对应表。

† ASQA 数字来自 [外部结果 §3](notebook-external-results-2026-09-28.md)。
这些是历史轻量文本评分值。
ALCE 评分模式必须单独记录：

| 模式 | 可报告内容 | 填表要求 |
| --- | --- | --- |
| 默认轻量 `score` | ASQA STR-EM/STR-HIT、QAMPARI 五项列表指标；ELI5 语义分 pending | 不要求真实评分模型；模式身份保留 |
| `--alce-answer-only` | 原版非引用模型批评分；ASQA 包含 QA/ROUGE/MAUVE，ELI5 包含 claims-NLI/ROUGE/MAUVE | 仍需真实依赖；两侧同模式重评 |
| `--alce-full` | 完整答案与 AutoAIS 引用评分 | 需要真实模型、分句资源、完整 shown-doc 映射 |

不能把 `--alce-answer-only` 理解为仅 STR-EM/STR-HIT。
不能把历史轻量 scorer hash 与新模型评分 hash 直接配对。
各方法的 citation eligible IDs 可能不同。
此时可以分别保留官方分数，但当前正式配对入口拒绝该指标比较。
MAUVE 等批量指标不虚构逐题值。

## 6. 表 5：QMSum 查询驱动摘要

**Table 5. Query-focused summarization performance on the frozen 281-query QMSum test collection.**

| 方法 / Row ID | 类别 | ROUGE-1 F1 ↑ | ROUGE-2 F1 ↑ | ROUGE-L F1 ↑ | N | 计划状态 |
| --- | --- | --- | --- | --- | ---: | --- |
| SN chunk / `sn.qmsum.chunk` | controlled-rerun | 待填 | 待填 | 待填 | 281 | 当前范围；用户转述生成完成，待工件验收 |
| BM25 / `qmsum.bm25.control` | controlled-rerun | 待填 | 待填 | 待填 | 281 | 暂缓；已有专项授权另记 |
| Socratic SegEnc / `qmsum.socratic.segenc.author_test` | recomputed-subset | 0.38955† | 0.13960† | 0.33942† | 281 | 历史公开答卷 |

使用固定 Perl ROUGE-1.5.5、参数和 HMNet regex 分句。
主表读取官方 `Average_F`，不以逐题均值覆盖。
Socratic 映射来自公开代码顺序，原始 generation manifest 未恢复。
模型、输入和预算差异须披露。
当前 281 题不能删成论文历史 279 题。
HMNet gold-input 仅作独立 oracle/calibration 参考。
SummN 的 279 行未取得可靠映射，原 SegEnc checkpoint 的可用性仅有历史受阻记录。
两者不列为已完成的 281 题对照。

† 数字来自 [外部结果 §4](notebook-external-results-2026-09-28.md)。
本机未核实本轮服务器输出。

## 7. 表 6：HotpotQA 答案、Supporting Fact 与 Joint

**Table 6. Answer and sentence-level supporting-fact performance on HotpotQA distractor validation.**

### 表 6a：答案，7,405 题

| 方法 / Row ID | 类别 / 状态 | EM ↑ | F1 ↑ | Precision ↑ | Recall ↑ | N |
| --- | --- | --- | --- | --- | --- | ---: |
| SN chunk / `sn.hotpotqa.chunk` | controlled-rerun / 当前范围 | 待填 | 待填 | 待填 | 待填 | 7,405 |
| BM25 / `hotpotqa.bm25.distractor_control` | controlled-rerun / 暂缓 | 待填 | 待填 | 待填 | 待填 | 实际范围待填 |
| KG2RAG adapted / `hotpotqa.kg2rag.adapted.control` | controlled-rerun / 候选 | 待填 | 待填 | 待填 | 待填 | 实际验收范围待填 |

### 表 6b：Supporting Fact

| 方法 | SF EM ↑ | SF F1 ↑ | SF Precision ↑ | SF Recall ↑ | N / 投影状态 |
| --- | --- | --- | --- | --- | --- |
| SN chunk | 待填 / pending | 待填 / pending | 待填 / pending | 待填 / pending | 待填 |
| BM25 | 待填 | 待填 | 待填 | 待填 | 待填 |
| KG2RAG adapted | 待填 | 待填 | 待填 | 待填 | 待填 |

### 表 6c：Joint

| 方法 | Joint EM ↑ | Joint F1 ↑ | Joint Precision ↑ | Joint Recall ↑ | N / 投影状态 |
| --- | --- | --- | --- | --- | --- |
| SN chunk | 待填 / pending | 待填 / pending | 待填 / pending | 待填 / pending | 待填 |
| BM25 | 待填 | 待填 | 待填 | 待填 | 待填 |
| KG2RAG adapted | 待填 | 待填 | 待填 | 待填 | 待填 |

三个面板合计 12 项官方指标。
SF/Joint 依赖显式 `[title, sentence_id]` 预测及完整观测。
映射错误时保留答案，整批 SF/Joint 为 pending。
最终 context coverage 不是 supporting-fact prediction。
KG2RAG 的提示、缓存与错误处理适配须明确披露。
其登记验收 job 不等于已授权全量 7,405 题。
fullwiki/test 论文或 leaderboard 值放到独立发表参考面板。

## 8. 表 7：方法间配对差异与不确定性

**Table 7. Paired differences under matched case scopes and scorer identities.**

| Benchmark / scope | A | B | Metric | Official A | Official B | Official Δ(A−B) | Mean per-case Δ | Cluster 95% CI | Eligible N / groups | Gate / 差异声明 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QASPER full | SN chunk | BM25 | Answer F1；证据满足时另行 | 待填 | 待填 | 待填 | 待填 | 待填 | 1,451 / 实际 groups | 暂缓对照；待 gate |
| MultiHop full | SN chunk | SN reasoning | QA weak-match | 待填 | 待填 | 待填 | 待填 | unavailable：当前只有 1 group | 2,556 / 1 | 当前模式比较；不等于模块消融 |
| MultiHop full | SN chunk / reasoning | BM25，各模式独立行 | QA weak-match | 待填 | 待填 | 待填 | 待填 | unavailable：当前只有 1 group | 2,556 / 1 | 对照暂缓；待 gate |
| MultiHop full | SN chunk / reasoning | Multi-Meta-RAG，各配置独立行 | QA weak-match | 待填 | 历史值需核验 | 待填 | 待填 | unavailable：当前只有 1 group | 2,556 / 1 | 公开答卷描述性比较 |
| MultiHop non-null | SN chunk | BM25 / Multi-Meta，各方法独立行 | Hits@4/10、MAP/MRR，各指标独立行 | 待填 | 待填 | 待填 | 待填 | unavailable：当前只有 1 group | 2,255 / 1 | 待 gate |
| ALCE ASQA100 | SN chunk | 四公开配置，各配置独立行 | STR-EM/STR-HIT，各指标独立行 | 待填 | 历史值需核验 | 待填 | 待填 | 根据实际 group | 100 / 待填 | 同轻量 scorer；题单固定 |
| ALCE full / task | SN chunk | VANILLA | 共同有效指标，各指标独立行 | 待填 | 待填 | 待填 | 待填 / N/A | 待填 / unavailable | 各指标实际 N | 暂缓；模式和 eligible IDs 相同 |
| ALCE ELI5 100 | SN chunk | 四公开配置，各配置独立行 | 非引用共同指标 | 待填 | pending | 待填 | 待填 / N/A | 待填 / unavailable | 100 / 待填 | 真实模型评分待验收 |
| QMSum full | SN chunk | BM25 / Socratic，各方法独立行 | ROUGE-1/2/L，各指标独立行 | 待填 | 待填 | 待填 | 待填 | 待填 | 281 / 35 | 同 Perl profile；公开映射限制披露 |
| Hotpot distractor | SN chunk | BM25 / KG2RAG，各方法独立行 | 共同有效指标，各指标独立行 | 待填 | 待填 | 待填 | 待填 | 待填 | 实际 N / groups | 对照暂缓或验收范围待定 |

表 7 是各 benchmark 内的比较目录，不是跨 benchmark 总排名。
正式输出时把每个 method pair 和 metric 展开为单独一行。
配对准入检查 case IDs、scorer 身份、eligible IDs 和 missing/error。
同样的分母数字不证明题目相同。
blocked 比较记录原因，不绕过比较器。

`Official Δ` 是两个原始汇总分的差。
`Mean per-case Δ` 是真实逐题差值的等权均值。
二者在 QMSum 等批量汇总中可能不同。
表格统一展示 A−B，现有比较器原生输出 `right minus left`。
读取报告时须按方法 ID 核对方向。
若需要反向，差值取负，区间 `[l, u]` 转为 `[-u, -l]`。
现有 cluster bootstrap CI 对应逐题差值均值，不能贴到官方批量差上。
没有逐题分数的 MAUVE 等指标只保留批量差；CI 写 unavailable。
自然 group 少于两个时不计算 cluster CI。
不得改采样单位来制造区间或显著性。
现有 CI 属于探索性不确定性报告，不是显著性检验。

回答模型、输入、提示或预算不同的比较标为端到端描述性比较。
严格模块归因需要固定其他关键因素。
本轮不新增 chunk-size、overlap、reranker 消融，不扩展跨套件 reasoning。

## 9. 表 8：运行完整性、延迟与资源观测

**Table 8a. Generation outcomes and observation completeness.**

| Benchmark / task / method / scope | Expected N | Saved rows | Success | No-answer | Clarification | Error | Missing | Evidence/ranking mapping errors | Official scoring state |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 每个正式结果行 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 | 待填 / N/A | valid / pending / error |

五个生成状态的数量必须与冻结范围对账。
证据映射错误另计，不能等同于没有引用。
error/missing 不从分母中隐去。
它们会阻止当前正式配对，仍须保留运行事实。
no_answer/clarification 使用固定官方空答规则，不改为成功答案。

**Table 8b. Observed runtime and provider usage under declared measurement boundaries.**

| Benchmark / task / method / scope | 计时边界 | 延迟观测 N / scope N | 延迟汇总（单位） | Usage coverage | Calls | Prompt tokens | Completion tokens | Monetary cost |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 每个存在观测的结果行 | 待填 | 待填 | 从报告读取 | 待填 | 待填 / unavailable | 待填 / unavailable | 待填 / unavailable | unavailable，除非有独立已验证价格协议 |

不将缺少 token 日志解释为零成本。
分别保留 SN provider 观测和 reference 的实际覆盖。
计时边界不同的结果不能解释为单个检索器的速度差。
初始化、存储或 RSS 的已授权观测可另附工程表，不替代算法成绩。
普通 reasoning 不自动产生专门 Agent judge 结果表。

### 附表：已有分数的分组分析与发表参考

**Appendix A. Descriptive breakdowns derived from saved per-case metrics.**

| Benchmark / task | 分组 | N | Method | Metric | 已存逐题分数的均值 | 来源 / 限制 |
| --- | --- | --- | --- | --- | --- | --- |
| MultiHop-RAG | inference / comparison / temporal / null，各组独立行 | 816 / 856 / 583 / 301 | 实际方法 | QA weak-match | 待填 | 保留全部对应题目 |
| QMSum | general / specific，各组独立行 | 37 / 244 | 实际方法 | ROUGE-1/2/L，各指标独立行 | 待填 | 逐题诊断均值；不是新 Perl Average_F |
| HotpotQA | bridge / comparison，或原 level | 由固定题单统计 | 实际方法 | 已有有效指标 | 待填 / unavailable | 分组元数据只在分析侧使用 |

附表只汇总已有逐题指标，不新增评分或实验。
不能把没有逐题值的批量指标拆成分组均值。
范围不完整时记录状态，不只统计成功题。

**Appendix B. Published reference results with explicit protocol differences.**

| Benchmark / method | Published metrics / scale | Split / setting | Reported N | Model / input | Scorer | 差异或未知项 | 文献 / 页码 / 表号 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 每个已有可核实发表参考 | 按原文填写 | 按原文填写 | 原文未提供则 unknown | 待填 | 待填 / unknown | 待填 | 待填 |

发表参考可以移到对应 benchmark 主表的独立面板，不强制只放附录。
它不要求逐题重评分，也不进入本项目的正式配对表。
不能从本项目冻结题数替原论文补写历史分母。
本次仅使用已有文献记录，不安排新的外部方法实验。

## 10. 每个数值的来源账本与交付

论文表简化字段，私有账本保留完整来源。
建议在 campaign 的新报告目录保存 `result-tables.md`、`result-ledger.json` 和 `result-gaps.md`。
这些是本次填报约定，不是声称代码已有自动表格生成器。

本项目新生成或重评分结果的账本至少保存：

| 字段组 | 必需内容 |
| --- | --- |
| 行身份 | table ID、row ID、suite/task、method ID、结果类别、full/subset |
| 范围 | expected N、bundle ID、case-ID 清单或 hash；运行任务记录存在时引用 |
| 原始事实 | 实际 run/attempt 目录、outputs hash、生成状态数；原答卷来源 hash |
| 提交与评分 | submission 路径/hash、scores 路径/hash、scorer 模式/hash、依赖身份 |
| 指标 | 原字段名、值、尺度、分母、eligible-ID hash、metric state、batch-only 标记 |
| 比较 | comparison 路径/hash、准入状态、pair 方向、group 数、差值类型、CI 对象 |
| 可比条件 | 模型、提示、token/上下文预算、输入组成、适配策略和已知缺项 |

发表参考使用附表 B 的来源字段。
不要求原论文提供本项目的 bundle、submission、scorer hash 或逐题答卷。
未披露的历史字段写 unknown，并限制相应比较结论。

展示层可以统一改为百分制，必须标明尺度。
原 scores 文件的 0–1 数值保持不变。
未知项写原因和解除条件。
历史公开值核实来源后可引用，不要求全部重跑外部模型。
若服务器缺 Git 忽略的来源工件，报告缺失；Git pull 不会恢复这些文件。
本次不下载或重建来源，不重跑 scorer。
若仅有本机文档中的历史数字，标为“文档引用，服务器工件未核验”。
它可以保留为历史参考，不能冒充本轮服务器新成绩。

服务器按 [填报指令](server-benchmark-result-tables-prompt.md) 操作。
验收结果须包含已填表、来源账本、空缺列表和可校验的私有回传包。
本机无法直接访问服务器；由用户转发指令和回传工件。

## 11. 事实来源

- [当前执行范围](../CURRENT_STATE.md)
- [实验协议与历史候选](notebook-benchmark-experiment-plan.md)
- [精确指标与符合性](notebook-benchmark-standards-and-conformance.md)
- [外部答卷和既有重评分](notebook-external-results-2026-09-28.md)
- [方法登记](../configs/notebook-external-method-registry-v1.json)
- [Campaign 候选模板](../configs/notebook-external-campaign-v1.json)
- [执行 job 模板](../configs/notebook-external-execution-plan-v1.jsonl)
- [共享存储与回传边界](result-storage-and-export.md)
- [Wiki：如何解释结果](benchmark-wiki/05-results-and-paper.md)
