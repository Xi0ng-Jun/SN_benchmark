# QMSum 外部发表成绩与比较口径核查

日期：2026-10-14。用途：为论文结果表补充可引用的外部成绩；本次只读调研，没有运行模型、重评分或核实服务器。每个外部数值回到原论文或作者资料，不要求为了引用发表成绩先取得答卷或重跑。本文中的“未知”限制比较主张，不否定发表参考本身。

## 1. SN 的当前参照与原版任务范围

| 结果 | ROUGE-1 F1 | ROUGE-2 F1 | ROUGE-L F1 | 范围与证据状态 |
| --- | ---: | ---: | ---: | --- |
| SN chunk，用户提供的服务器汇总 | 27.746 | 9.039 | 24.247 | 用户转述 n=281；本机没有读取该批答卷、manifest、模型或评分 stdout |
| Socratic SegEnc 公开答卷，仓库历史重评分 | 38.955 | 13.960 | 33.942 | 281 题、35 会议；本轮回读 2026-09-28 的本地历史评分工件，未重跑或验收服务器副本 |

全篇分数按百分制表示：SN 原转述值为 0.27746 / 0.09039 / 0.24247。历史公开答卷来源、逐行映射与评分证据见[外部结果 §4](../notebook-external-results-2026-09-28.md#4-qmsum281-条-socratic-segenc-公开预测按公开代码顺序映射原运行清单缺失)。本轮[本地回读审计](2026-10-14-local-score-audit.json)记录原 `scores.json` SHA256 `c8f013b77846bf102ab35c6377be7b70bfe0e8aff9da9807dab8d249be5fee21`、三项分母均为 281 与 `qmsum-author-rouge155-hmnet-seg-v1` profile。公开代码顺序映射没有恢复作者原生成清单，不能自动证明同生成输入。

原版任务是“会议全文 + query → query-focused summary”，含 general 和 specific 两类查询。项目冻结的[官方 test 文件](https://github.com/Yale-LILY/QMSum/blob/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e/data/ALL/jsonl/test.jsonl)在仓库既有全量核查中为 **35 场会议、281 查询（37 general、244 specific）**，原文件 SHA256 为 `6bcd428211260ad2efae3af76cbaf6a7f5ae4bb5e1e59c45a4b8e89539cb9208`。本次读回[符合性 §7](../notebook-benchmark-standards-and-conformance.md#7-qmsum查询驱动的会议摘要)的核查记录，没有重新下载计数。

[QMSum 原论文](https://aclanthology.org/2021.naacl-main.472.pdf#page=5) Table 1 则印为 train/valid/test = 1257/272/**279**，总计 1808。后续 [Vig et al. 2022](https://aclanthology.org/2022.findings-naacl.109.pdf#page=13)附录明确写 1257/272/**281**，却仍沿用总计 1808。不能依据论文旧统计删掉当前两道题；也不能把历史表中所有方法的实际评分分母都补成 281。

## 2. 原版查询摘要任务：九行发表参考

下列行均来自研究原版 QMSum 查询摘要任务的论文，使用模型预测片段或完整会议生成；不是把测试 gold spans 直接喂给生成器的 oracle 行。表中“训练标注”与“测试 gold 输入”必须区分。它们可组成发表参考面板，但不能宣称同模型、同预算或同一冻结 scorer 的配对实验。

| 方法 / 年份 | R-1 | R-2 | R-L | 输入、模型与训练 | split / 分母披露 | 原始证据与评分口径 |
| --- | ---: | ---: | ---: | --- | --- | --- |
| Locator + PGNet / 2021 | 31.37 | 8.47 | 27.08 | 学习式 ranking Locator 预测相关 turns → pointer-generator；监督训练 | 原论文 test，统计报 279 | [原论文 Table 3，PGNet∗](https://aclanthology.org/2021.naacl-main.472.pdf#page=7)；ROUGE F1；三次运行均值 |
| Locator + BART / 2021 | 31.74 | 8.53 | 28.21 | 同一预测 Locator → **BART-base**；gold spans 是 Locator 训练标注 | 原论文 test，统计报 279 | [原论文 Table 3，BART∗](https://aclanthology.org/2021.naacl-main.472.pdf#page=7)；ROUGE F1；三次运行均值 |
| Locator + HMNet / 2021 | 32.29 | 8.67 | 28.17 | 预测 Locator → 分层会议摘要模型，含跨域预训练 | 原论文 test，统计报 279 | [原论文 Table 3，HMNet∗](https://aclanthology.org/2021.naacl-main.472.pdf#page=7)；不是同表 HMNet† 的 gold-input |
| SummN，QMSum-All / 2022 | 34.03 | 9.28 | 29.48 | 全会议，多阶段 split → coarse summaries → final；各阶段 BART-large-cnn 分别训练 | 原版 QMSum 测试成绩；该表未写评分 N，仓库历史记录其公开答卷为 279 行 | [原论文 Table 2](https://aclanthology.org/2022.acl-long.112.pdf#page=6)；§4.1 明示 pyrouge、按句切分后计算 R-L |
| DYLE / 2022 | 34.42 | 9.71 | 30.10 | RoBERTa-base extractor + BART-large generator 联合训练，测试用预测 top-25 snippets；训练使用 extractive-oracle loss | 原版 QMSum test；该表未披露实际评分 N | [原论文 Table 3、§4.3、Table 7](https://aclanthology.org/2022.acl-long.118.pdf#page=6)；作者仓库列 pyrouge 0.1.3，精确调用参数未核实 |
| RELREG-W / 2022 | 36.45 | 12.81 | 32.28 | ROUGE relevance 回归检索 → BART-large；“-W”为 WikiSum 额外预微调 | Table 5 明确 test；附录明确 281 | [原论文 Table 5](https://aclanthology.org/2022.findings-naacl.109.pdf#page=7)；§3.4 使用 SummEval，五次运行均值 |
| SegEnc-W / 2022 | 37.80 | 13.43 | 33.38 | 全会议分段独立编码、decoder 联合注意；BART-large + WikiSum 预微调 | 同上，281 test | [原论文 Table 5](https://aclanthology.org/2022.findings-naacl.109.pdf#page=7)；SummEval，五次运行均值 |
| Socratic SegEnc，Books3 30M / 2023 | 38.06 | 13.74 | 33.51 | BART-large SegEnc；Books3 问答式预训练，再在 QMSum 微调；不是 gold-span 测试输入 | 原版 QMSum 结果；正文沿用总计 1808，表未列实际评分 N | [原论文 Table 1](https://aclanthology.org/2023.acl-long.713.pdf#page=6)；§4.4 标“standard Rouge”，§6 为五次微调运行均值 |
| LTRSum / 2024 | 38.82 | 14.11 | 34.45 | SegEnc 扩展，406M；共享 decoder 做摘要 + learning-to-rank；训练借助 span relevance 标签 | 原版 QMSum；§5.2 报 test=279，未核实其实际答卷 N | [原论文 Table 1(a)](https://aclanthology.org/2024.emnlp-main.838.pdf#page=6)；§5.4 继承 Vig 代码，ROUGE 实际版本/参数未明确 |

同表中的输入选择是方法的一部分。例如原始 Locator 取约全文 1/6，PGNet/BART 再截至 2048 tokens；Vig 的 retrieval 模型和 SegEnc 的可见长度不同。全会议作为源材料并不保证每个方法把所有 token 都送入生成器。论文训练与 SN 的模型、提示和输出长度也不同，不能从端到端分差直接归因于检索质量。

### 关键证据定位与容易误引的数值

- **原始 Locator。** 原论文 §5.1 写 base 预训练模型、PGNet/BART 的 2048-token 截断及三次均值；Table 3 的 `∗` 是预测 Locator，`†` 才是 gold spans。后续 DYLE Table 3 引用的 **BART-large 32.16 / 8.01 / 27.72** 来自其他后续研究，不能替换成原论文 BART∗ 的数字或把两者当同配置。
- **DYLE。** [论文 Table 8](https://aclanthology.org/2022.acl-long.118.pdf#page=9)另有 Oracle **39.80 / 14.74 / 36.06**，按 gold summary 构造测试输入；本文采用 Table 3 的预测 extractor。训练中使用 oracle loss 不等于测试使用 oracle。[作者 README](https://github.com/Yale-LILY/DYLE#dependency)确实列 `pyrouge==0.1.3`，同时列 `rouge==1.0.0`；仅凭依赖列表不能证明历史评分命令与本仓库完全相等。
- **SummN。** [Table 2](https://aclanthology.org/2022.acl-long.112.pdf#page=6)同时印 QMSum-All 与 QMSum-Gold。Gold **40.20 / 15.32 / 35.62** 不进入完整资料端到端面板；该论文明确说明 R-L 的句切分，不能误取 AMI/ICSI 未切句的带星号 R-L。
- **Vig / SegEnc。** [§3.4 与附录](https://aclanthology.org/2022.findings-naacl.109.pdf#page=5)明确 SummEval、4-beam decoding、五次均值；[Table 5](https://aclanthology.org/2022.findings-naacl.109.pdf#page=7)的 SegEnc **37.05 / 13.03 / 32.62** 与 SegEnc-W 是两条配置。Socratic 后续表转引 SegEnc 的 R2 为 13.04，引用 Vig 应保留其原表 13.03。
- **Socratic。** 38.06 是论文五次均值；[作者 HF 发布的 `test.predictions.rouge`](https://huggingface.co/Salesforce/socratic-pretraining-qmsum/blob/d127cbc54b974a58e8bd75935f2863d136e0bc3d/test.predictions.rouge)历史报告 **38.48 / 13.90 / 33.62**，仓库记录其采用 Stanza + SummEval；本仓库对公开单文件的 Perl 重评分是 **38.955 / 13.960 / 33.942**。本次联网核实论文表，HF 文件条件与重评分数值沿用既有来源审计，不当作本次重新读取了 HF 文件。三组数字表示不同汇总/答卷/评分条件，不能任选高值冠以论文均值。
- **LTRSum。** [§5.2–5.4 与 Table 1](https://aclanthology.org/2024.emnlp-main.838.pdf#page=3)给出 279、406M 和继承代码；对测试完整评分分母、参数及 seed 聚合未取得充分证据。训练 relevance 标签与人评展示 gold spans 不能被误描述为 Table 1 的测试生成 oracle；也不能凭继承代码就认证同一 scorer。

## 3. LLM 检索与长上下文：明确独立口径的参考

原版完整测试上的现代 LLM 结果可以引用，但 ROUGE 实现必须显式列出。2026 年工作目前是 arXiv 预印本，以下数字不视为已由同行评审确认的主结果，也没有由本项目重评分。

| 工作 / 配置 | 范围 | R-1 | R-2 | R-L | R-Lsum | 关键条件与证据 |
| --- | --- | ---: | ---: | ---: | ---: | --- |
| Retrieved-Span Training，promoted / 2026 | 原版全 test，281 / 35 会议 | 35.41 | 12.28 | 24.63 | 31.36 | [§6.1 Table 1、§3.3](https://arxiv.org/html/2609.25028#S6.SS1)；LFM2.5-1.2B QLoRA + 33M cross-encoder，测试预测片段 2000 words；Python rouge-score 0.1.2、stemming |
| 同一工作，LFM2.5-1.2B zero-shot，located spans | 同上 | 30.12 | 6.70 | 19.29 | 25.04 | [Table 1 / §7.4](https://arxiv.org/html/2609.25028#S7.SS4)；相同预测检索片段，无任务微调；该工作内部受控参考 |
| 同一工作，GPT-5.6 Luna zero-shot | 同上 | 32.43 | 7.89 | 19.05 | 27.49 | [Table 1、§5.1](https://arxiv.org/html/2609.25028#S5.SS1)；全文、512输出token预算；2026-07-27浮动服务别名，禁 reasoning，vendor 默认采样 |

该预印本分别命名 R-L 与 R-Lsum，因此不能把其中 24.63 或 31.36 任意选择一个塞入“官方 ROUGE-L”列。作者也声明测试生成用预测 spans、训练使用 gold spans；这可作为监督学习方法背景，不能当成 SN 零样本的同条件比较。上述三行的版本、模型别名和分数来自论文自述，未额外验证服务厂商模型身份。其 scorer 偏移实验仅针对特定 Socratic 发布预测，不能推广成其他论文评分等价证明。

### 范围不同的 LLM 研究与排除理由

| 工作 / 配置 | 已发表值（R1 / R2 / RL） | 实际范围 / 输入 | 放置方式 |
| --- | --- | --- | --- |
| ChatGPT zero-shot / 2023 | 28.34 / 8.74 / 17.81 | [论文 §3、Table 2](https://arxiv.org/pdf/2302.08081#page=3)：随机 100 test 例、手工 ChatGPT 网页操作；完整会议版本 | subset 参考；不宣称 281 全量，API模型版本和 scorer 实现未明确 |
| ChatGPT，QMSum(Golden) / 2023 | 36.83 / 12.78 / 24.23 | 同论文，100 例；输入人工 gold spans | oracle/subset；不进入端到端主面板 |
| GoR + LLaMA-2-7B-chat / ACL 2025 | 34.5 / 7.8 / 19.8 | [论文 §3.1、Table 1、附录 A](https://aclanthology.org/2025.acl-long.1159.pdf#page=5)：只用 general queries；图检索6×256-token chunks，Mixtral生成合成记录 | general-only RAG 参考；split/查询数量和 scorer 精确版本仍需确认 |
| GoR 论文内 BM25 / Full Context | 32.1 / 6.1 / 18.4；33.1 / 6.8 / 19.4 | 同一 general-only 条件；“Full Context”超上下文窗口时有连续片段采样 | 可解释该论文内部对照；不能作为完整 QMSum 的 BM25 或全文基线 |

另外排除：**LongBench QMSum 的 200 例**与其 `rouge_score`，**SCROLLS / ZeroSCROLLS**版本和 validation 成绩、只评 whole-meeting 的 35 例研究、ExplainMeetSum 的新增证据标签/数据排除版。它们可研究长上下文或会议摘要，但不是本文冻结的 37 general + 244 specific 测试范围。例：[MASFormer](https://aclanthology.org/2023.findings-emnlp.183.pdf#page=6)虽附录列 281 test，实际 §4.2.1 说明在 SCROLLS **validation** 报成绩，不能只看到数据统计就把 Table 6 搬入原版 test 表。

## 4. “官方 ROUGE”在这里的准确含义

QMSum 原仓库没有一份随 benchmark 发放的完整、版本冻结、含分句与预处理的统一 scorer。作者[评分澄清 issue #5](https://github.com/Yale-LILY/QMSum/issues/5#issuecomment-890003212)在项目既有审计中指定 pyrouge 并指向 [MatchSum metrics.py](https://github.com/maszhongming/MatchSum/blob/c7754245a454d0ba3535db0e4cc1a13b3d35680d/metrics.py) 的 Perl 调用。本次网页抓取只能看到提问，未显示该作者回复；这部分依据为既有审计和本地 scorer 中的来源标记，不能报告为“本次重新在线读到了作者回复”。

本项目准确可复查的口径是 `qmsum-author-rouge155-hmnet-seg-v1`：[实现](../../src/rag_eval/qmsum_official.py)执行 Perl ROUGE-1.5.5，参数 `-c 95 -r 1000 -n 2 -m -a`，额外 `-d` 用于逐题审计；双方同用固定 HMNet regex 分句，以原 stdout `Average_F` 作主总分。HMNet 分句是显式复现选择，不是作者原始实验唯一强制分句。其批量 bootstrap 汇总也不能随意替换成逐题 F 的简单均值。

因此建议表名写成“QMSum 原版任务的发表参考（各方法原文协议）”，把本项目同 scorer 成绩另设小面板。只要披露原文条件即可引用九行论文成绩，不要求重新评所有论文；尚未认证评分一致的行不应改标“与 SN 完全同官方协议”。ROUGE 评的是文本重合，不能由分数差推导摘要事实性、覆盖或检索因果。

## 5. 已核查范围与仍未知项

本次联网读原论文 PDF/HTML，核对上述表号、数字、任务与 gold-input 标签；读取仓库文档和 scorer 源码核对当前协议。未下载权重，未请求模型，未变动评测代码，未重新执行 Perl。

仍未知：SN 转述分数的实际 scorer 与 manifest；原论文 279 与当前 281 的逐题历史差异；DYLE/SummN/Socratic/LTRSum 各历史评分实际参与 ID；各论文的完整句切分、tokenization、Perl参数与版本；部分作者链接抓取不到的原源码与评分回复。已有分数照实保留，这些未知不应以“standard ROUGE”四个字补齐。

可直接采用的解释是：SN 的服务器转述分数已具备初步参照，监督训练的原版论文方法约处于 R1 31–39 区间；SN 的 R2 与部分早期方法相近，但 R1/RL 较低。该描述是跨条件发表参考，不是显著性或方法排名结论；本项目对公开 Socratic 答卷的历史同 scorer 重评可提供更接近评分条件的独立参照，仍须披露输入映射与生成模型差异。
