# HotpotQA 外部结果调研：dev distractor 与 SN 的比较范围

核查日期：2026-10-14。本文只查论文、作者资源和本地协议记录，没有运行模型、下载完整新数据集、登录服务器或重新评分。数值统一为百分制；`—` 表示该来源未提供此指标，不能补零。论文发表成绩是 `published-reference`，并非本项目复现结果。

## 1. 先固定 SN 的比较口径

用户转述 SN 使用 **HotpotQA distractor validation，n=7,405**，Answer EM/F1 为 **29.98 / 48.11**，Joint F1 为 **18.68**。本次没有读取对应服务器答卷、模型配置、分母账本或 supporting-fact 投影回放，因此这些数字标为**服务器转述**，不能升级为本机核实结果。Joint F1 尤其仍需核实显式 supporting-fact 预测、实际题单与独立官方评分工件。

官方 dev 的 7,405 题与 test-distractor 的 7,405 题是不同 split，不能因题数相同合并。原论文表 1 明确 dev/test 划分，表 4 同时列出了各 split 成绩。distractor 的候选池为每题两篇支持段落及八篇干扰段落；模型可在池内选择或截断上下文，这属于方法差异，不能把仅提供 gold paragraphs 的 oracle 行当普通 distractor 行。[原论文 §3、表 1/4](https://aclanthology.org/D18-1259.pdf)

本项目采用固定 HF revision `1908d6afbbead072334abe2965f91bd2709910ab` 的 distractor validation，历史完整验收为 7,405 题、73,700 段落，保留原 sentence IDs、49 个空白句位和一个越界 gold tuple；实际公共池并非每题都恰好十段。本文将“十段候选”理解为标准任务设置，不假造每题精确段数。[本地全量输入验收](../notebook-external-results-2026-09-28.md#5-hotpotqa-全量协议校准与-kg2rag-受控入口)、[实现符合性 §6](../notebook-benchmark-standards-and-conformance.md#6-hotpotqa-distractor-多跳问答与-supporting-facts)

### “排除 37 个重复”的本地含义

[2026-10-08 服务器导出指令](../server-upgrade-running-export-prompt.md)开头及步骤 6 记录的是**重复 case/run 的导出选择**：保留全部原始 run，核查身份、状态、输出哈希和排除理由，建立显式选择清单，最终 case ID 集合应覆盖 frozen bundle 的全部 7,405 道计划题、每题唯一。[2026-10-09 交接](../session-handoff-2026-10-09.md)也采用这个描述。

因此当前有证据支持的含义是“重复执行/导出记录去重”，**不是删掉 37 道官方题，也不是重复 title 的上下文清洗**。不应把正式分母自行改为 7,368，也不应推断原始 run 总数一定为 7,442。37 条记录实际如何选择、覆盖是否完整仍属服务器未核实事实。

另一个不同问题是 KG2RAG 跨题 title-only 缓存：本地历史核查为 66,581 个唯一标题中有 54 个标题对应不同句子列表。这与 37 个重复 run 不同，不应合并叙述。[缓存核查记录](../notebook-external-results-2026-09-28.md#kg2rag-接入为何需要适配)

## 2. 最接近当前设置的论文结果

下表只取作者论文明确标注 **dev + distractor** 的非 oracle 行。`7,405*` 表示论文使用标准完整 dev 协议、未声明抽样，题数由 HotpotQA 原论文的 dev 定义确认；本轮没有取得各作者的实际 scored-ID 清单或证明其数据文件字节与 SN 固定 HF 包一致。它们可以提供同任务发表参考，不能称为同题配对实验或严格控制模型/预算的胜负排名。

| 方法与年份 | split / n | 输入与训练条件 | Answer EM | Answer F1 | Supporting F1 | Joint F1 | 原始出处 |
| --- | --- | --- | ---: | ---: | ---: | ---: | --- |
| Yang 等 HotpotQA baseline，2018 | dev distractor / 7,405 | 公共候选段落；词/字符编码、注意力与 RNN；HotpotQA 联合监督训练 | 44.44 | 58.28 | 66.66 | 40.86 | [原论文表 4，PDF 第 8 页](https://aclanthology.org/D18-1259.pdf) |
| QFE，2019 | dev distractor / 7,405* | 候选段落拼接，长输入可截断；预训练词向量 + RNN，联合训练答案和顺序证据抽取；非 BERT | 53.7 | 68.7 | 84.7 | 60.6 | [作者论文表 4，PDF 第 5 页](https://aclanthology.org/P19-1225.pdf) |
| DFGN，2019；2-layer 原模型 | dev distractor / 7,405* | 候选段落经学习式筛选；BERT-base 维度编码 + 实体图，HotpotQA 监督训练 | 55.42 | 69.23 | — | — | [作者论文表 2，PDF 第 7 页](https://aclanthology.org/P19-1617.pdf) |
| SAE，2020 | dev distractor / 7,405* | 在每题候选池内选 top-2 段；BERT-base-uncased；选择器及答案/证据模块监督训练 | 61.32 | 74.81 | 85.27 | 66.45 | [作者论文表 1 Dev 区，PDF 第 6 页](https://arxiv.org/pdf/1911.00484) |
| SAE-large，2020 | dev distractor / 7,405* | 同 SAE 的候选池与选择流程；RoBERTa-large；有监督 | 67.70 | 80.75 | 87.38 | 72.75 | [作者论文表 1 Dev 区，PDF 第 6 页](https://arxiv.org/pdf/1911.00484) |
| QUARK，2020 | dev distractor / 7,405* | 每题候选池内句子筛选、答案抽取、答案条件证据重选；BERT-large-cased + whole-word masking + SQuAD 后再 HotpotQA 微调；reader 限 512 wordpieces | 67.75 | 81.21 | 86.97 | 72.26 | [作者论文表 1，PDF 第 4 页](https://aclanthology.org/2020.emnlp-main.711.pdf) |
| HGN，RoBERTa-large，2020 | dev distractor / 7,405* | RoBERTa-large 段落 ranker，在候选池内建分层图；图中段落/句子/实体分别有 4/40/60 上限；联合监督训练 | — | 82.22 | 88.58 | 74.37 | [作者论文表 5，PDF 第 8 页](https://aclanthology.org/2020.emnlp-main.710.pdf) |
| HGN，ALBERT-xxlarge-v2，2020 | dev distractor / 7,405* | 同分层图框架，替换 context encoder；有监督 | — | 83.46 | 89.2 | 75.79 | [作者论文表 5，PDF 第 8 页](https://aclanthology.org/2020.emnlp-main.710.pdf) |
| **SN：用户转述** | **validation distractor / 7,405（转述）** | 每题 context 分区，SN 自行检索/引用；模型、提示、预算、源码和 run 清单本轮未知 | **29.98** | **48.11** | **未提供** | **18.68** | 用户提供；尚无本次读取的原始工件 |

上表保留原文精度，例如 QFE 为一位小数、HGN ALBERT Supporting F1 为 89.2，不补造更多有效位。Answer EM/F1、Supporting F1、Joint F1 是不同指标；Joint F1 由每题答案与 supporting precision/recall 组合后再汇总，不能用两个汇总 F1 相乘补算。

### 容易误抄的行

- **DFGN**：原论文表 1 是 private test，不能抄到 dev 栏。原论文的 dev 表 2 只给 Answer 55.42 / 69.23；SAE 论文表 1 对 DFGN 的 dev 转引为 55.66 / 69.34、Supporting F1 82.24、Joint F1 59.86，HGN 表 5 又转引为 69.38 / 82.23 / 59.89。不能把这些版本的 Answer 和 Joint 拼成“同一次原作者运行”。如需含 Joint 的 DFGN 行，可以明确写“SAE 论文转引的 DFGN dev”，而非本表原论文行。
- **HGN**：作者主表 1 为 distractor test；这里取表 5 的 dev encoder 对比。QUARK 论文转引 HGN 的 dev F1 79.69 / Joint 71.45 及 RoBERTa 81.00 / 73.01，与 HGN 正式论文表 5 数字不同；采用 HGN 作者最终表格，并保留版本差异，不替不同论文静默统一数字。
- **SAE-oracle**：原论文明确给 gold documents，排除于普通 distractor 比较。SAE/SAE-large 在预测 top-2 段落上评测，训练模块使用 gold paragraphs 属于监督训练条件，不能误称推理时给了 gold。
- **QFE**：表 2 是历史 test leaderboard；dev 是表 4。题均有两篇支持段落不等于预测 evidence 必须只有两句。

## 3. LLM / RAG 结果：有研究价值，但需要另标条件

### KG2RAG：发表参考，可列于 RAG 表，不能当已核实严格匹配基线

[NAACL 2025 作者论文表 1](https://aclanthology.org/2025.naacl-long.449.pdf)明确区分 Hotpot-Dist、Hotpot-Full 和 Shuffle 变体。下表只取 **Hotpot-Dist 的 response quality F1**，统一转为百分制，不拿论文表 2 的 retrieval F1 冒充官方 Supporting F1，也不伪造 Joint。三行是同一篇作者控制实验中的结果，而非重新核实各框架原论文成绩。

| 作者实验中的方法 | Answer F1 | Answer EM / Joint F1 | 模型及输入 | 来源 |
| --- | ---: | --- | --- | --- |
| Semantic RAG | 61.7 | 未报 | LLaMA3-8B；mxbai-embed-large；每题 distractor 候选；k=10 | [表 1，PDF 第 6 页](https://aclanthology.org/2025.naacl-long.449.pdf) |
| Semantic RAG + rerank | 65.2 | 未报 | 同上，增加 bge-reranker-large | [表 1，PDF 第 6 页](https://aclanthology.org/2025.naacl-long.449.pdf) |
| KG2RAG | 66.3 | 未报 | 同生成模型/embedding/reranker；KG 扩展与组织；k=10 | [表 1，PDF 第 6 页](https://aclanthology.org/2025.naacl-long.449.pdf) |

论文 §3.1 未给实际评分题数/明确 split 名称；**不能仅凭 Hotpot-Dist 标签自行写“官方 dev 全 7,405 题已核实”**。作者发布的数据副本在本地历史验收中与冻结 dev 的全部 7,405 行和顺序一致，这是数据身份的证据，仍不直接证明论文每一行成绩的实际分母。[作者仓库](https://github.com/nju-websoft/KG2RAG)、[本地身份核查](../notebook-external-results-2026-09-28.md#完整真实输入与原生答卷)

另外，本地对作者固定源码 `7d626c77b7af30b55aa3f960cde755b9549a0616` 的历史核查发现 QA few-shot prompt 直接含四道本次 validation 题及答案，KG 提取也含相关评测事实；跨题同名异文缓存亦有问题。因此保留“原作者设置的发表参考”即可。本仓已设计移除这些示例并绑定完整内容的 controlled/adapted runner，但尚无服务器模型结果，不能拿适配准备工作声称复现了 66.3。[原始提示/适配边界](../notebook-external-results-2026-09-28.md#kg2rag-接入为何需要适配)

论文的 **Hotpot-Full 仅使用 66,581 篇汇聚文档**，是作者自建更大检索池，不应写为在官方 processed Wikipedia 全语料上的 fullwiki 结果。本文不把该列 63.1 加入 SN distractor 主表。

### Agent 与强 reader 的边界参考

| 方法 | 报告数值（%） | split / n / 资料范围 | 为什么与 SN 主表分开 | 一手出处 |
| --- | --- | --- | --- | --- |
| ReAct，GPT-3 text-davinci-002 | Answer EM 30.8；F1/Joint 未报 | 随机 validation 子集 500；Wikipedia Search/Lookup API；greedy decoding | 小样本 + 在线开放检索，不是每题固定完整 distractor 池 | [ReAct 论文附录 A.1 表 5](https://arxiv.org/pdf/2210.03629) |
| Beam Retrieval + supervised reader，beam size 2 | Answer EM/F1 72.69 / 85.04；Joint F1 77.54 | distractor **blind test**；官方 test 7,405；DeBERTa-large retriever、DeBERTa-xxlarge reader | 同 setting、不同 split；不是 SN dev 的排名对手 | [NAACL 2024 论文表 5](https://aclanthology.org/anthology-files/pdf/naacl/2024.naacl-long.96.pdf)、[官方 test 榜单 Joint](https://hotpotqa.github.io/) |
| Beam Retrieval + GPT-3.5-turbo-16k / LongChat-13b-16k | 作者图 3 给 Answer F1；本次不从图估读数字 | §4.1 明确每数据集抽 500 题；distractor 池，比较全候选与 BR 选中段落 | 500 子集；few-shot；模型与监督 reader 不同 | [作者论文 §4.1/4.2、图 3](https://aclanthology.org/anthology-files/pdf/naacl/2024.naacl-long.96.pdf) |
| LongBench v1 HotpotQA | 本次不抄分 | 200 题；LongBench 自有输入与 token-F1 口径 | 衍生题单/上下文设置，不能贴成官方完整 dev 成绩 | [作者任务定义](https://github.com/THUDM/LongBench/blob/main/LongBench/task.md)、[作者 v1 结果表](https://github.com/THUDM/LongBench/blob/main/LongBench/README.md) |

ReAct 原论文表 1 的 PaLM-540B ReAct 为 27.4 EM、ReAct→CoT-SC 为 35.1，而上表 30.8 来自附录明确的 500 题 GPT-3 实验。作者 README 的 500 题行又写 GPT-3 30.4、PaLM 29.4；这些是不同报告实例，不能把主表、附录和 README 视为一个 n=500 运行，更不能把所有 ReAct 成绩机械标成同一题单。[作者 README](https://github.com/ysymyth/ReAct)

另发现作者自报的 [Evidence-Based RAG 项目](https://github.com/Ajinkya-Nagarkar/evidence-based-rag-multihop-qa) 声明完整 dev 7,405、零样本、Gemma4-31B，给 EM/F1/Joint F1 **46.8 / 59.5 / 43.2**。本轮只核实 README 存在该声明，未取得其完整 scorer 配置、预测题单或确认检索是否限每题 distractor（仓库有 `index_cache_global`）；没有找到正式论文。故只作为待查的作者自报线索，不纳入已核实输入协议的主比较表，不据此宣称外部 LLM 在严格相同设置下的胜负。

## 4. 目前能作出的比较结论

按用户转述数字，SN 的 Answer F1 比最基础的同轨道原论文 dev baseline 低 **10.17 个百分点**（48.11 vs 58.28），Joint F1 低 **22.18 个百分点**（18.68 vs 40.86）；这可以表达为“相对公开参考仍有差距”，前提是明确服务器转述且不当作统计显著性结论。上表更强的监督 reader 也说明该任务有较高可达表现，但训练数据、模型大小、reader 上限、提示和计算预算不同，不能将分差直接归因到 SN 的检索、KG 或多跳算法。

要使 SN 行成为可审计正式成绩，后续需要读取其冻结 7,405-ID 范围、重复 run 的显式选择规则、失败/缺失分母、模型/源码/提示/预算身份、Answer 官方评分以及 Supporting/Joint 的显式预测和回放工件。引用外部论文汇总成绩本身不要求重新跑对方或取得逐题答卷；配对统计和模块归因才需要更强控制证据。

本次核查输出仅此调研文档；原始代码、官方数据包、实验配置和运行记录均未修改。
