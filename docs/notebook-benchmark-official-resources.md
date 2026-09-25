# QASPER、MultiHop-RAG、ALCE、QMSum 官方资料手册

资料检索：2026-09-23；完整文件与评分证据更新：2026-09-24。文档状态：外部来源参考，供后续整理、论文阅读与实验设计使用。返回[文档导航](README.md)。

本页最初整理网页资料；随后已验证完整QASPER/QMSum文件、Perl评分校准与一题真实SN/BM25实验。2026-09-24又经代理下载并校验MultiHop/ALCE固定完整文件，完成真实数据及文本评分核查；ALCE大型评分模型尚未运行。下文区分**论文报告规模**、**发布页面记录单位**与**本项目实测口径**；链接状态是核查时观察，实际证据见[实验计划](notebook-benchmark-experiment-plan.md)和[MultiHop/ALCE验收](notebook-benchmark-real-data-validation.md)。

“官方”指论文作者或发布机构提供的资源；SCROLLS、ZeroSCROLLS、LongBench 是另一些研究团队建立的衍生评测套件。它们自己的仓库可以是官方仓库，但不能因此把其中的 QASPER/QMSum 版本称为原作者原始发布。

## 1. 总览与快速入口

| Benchmark | 主要任务 | 原始论文 | 作者 GitHub | 作者/发布机构 Hugging Face | 榜单情况 |
| --- | --- | --- | --- | --- | --- |
| QASPER | 基于一篇科研论文问答，并定位证据 | [NAACL 2021](https://aclanthology.org/2021.naacl-main.365/) | [allenai/qasper-led-baseline](https://github.com/allenai/qasper-led-baseline) | [allenai/qasper](https://huggingface.co/datasets/allenai/qasper) | 数据卡链接历史 Papers with Code；本次不能作为有效排名入口，详见 §2.4 |
| MultiHop-RAG | 跨新闻文档的检索与多跳问答 | [arXiv:2401.15391](https://arxiv.org/abs/2401.15391)，作者仓库标明 COLM 2024 | [yixuantt/MultiHop-RAG](https://github.com/yixuantt/MultiHop-RAG) | [yixuantt/MultiHopRAG](https://huggingface.co/datasets/yixuantt/MultiHopRAG) | 在已查作者入口中未找到独立官方提交榜单；论文提供结果表 |
| ALCE | 带引用生成：流畅性、答案正确性和引用支持 | [EMNLP 2023](https://aclanthology.org/2023.emnlp-main.398/) | [princeton-nlp/ALCE](https://github.com/princeton-nlp/ALCE) | [princeton-nlp/ALCE-data](https://huggingface.co/datasets/princeton-nlp/ALCE-data) | 在已查作者入口中未找到独立官方提交榜单；按三个子任务阅读论文结果 |
| QMSum | 根据用户查询总结一场长会议的相关内容 | [NAACL 2021](https://aclanthology.org/2021.naacl-main.472/) | [Yale-LILY/QMSum](https://github.com/Yale-LILY/QMSum) | 未从原论文和作者仓库确认原作者维护的独立 HF 数据集；衍生版本见 §5.4 | 作者仓库提供实验结果；SCROLLS 等为衍生套件，旧网站存在访问异常 |

| 比较项 | QASPER | MultiHop-RAG | ALCE | QMSum |
| --- | --- | --- | --- | --- |
| 资料单位 | 论文全文 | 完整新闻文章库 | 检索语料；发布包提供每题候选片段 | 完整会议转录 |
| 期望回答 | 抽取、自由文本、Yes/No、不可回答 | 实体、比较/时间判断、信息不足 | ASQA 长答案、QAMPARI 列表、ELI5 解释 | 针对查询的摘要 |
| 主要证据标注 | 段落、图表及高亮文本 | 多文档 evidence/fact | 参考答案/别名/claims；引用支持由评测模型判断 | specific 查询的相关 turn 范围 |
| 论文/发布说明规模 | 1,585 篇，5,049 问 | 609 篇，2,556 问 | 论文称每个子任务从 dev 取 1,000 例；文件实数需另核 | 232 场，1,808 对查询摘要 |
| 语言 | 英语 | 英语 | 英语 | 英语 |
| 主要评测入口 | Answer F1、Evidence F1 | 检索排名指标、QA 脚本成功比例 | 子任务正确性、MAUVE、引用 precision/recall | ROUGE-1/2/L |

表中规模与任务分别依据 [QASPER 论文](https://aclanthology.org/2021.naacl-main.365.pdf)、[MultiHop-RAG 论文](https://arxiv.org/pdf/2401.15391)、[ALCE 论文 §2](https://aclanthology.org/2023.emnlp-main.398.pdf)、[QMSum 论文表 1](https://aclanthology.org/2021.naacl-main.472.pdf)。各套任务和评分单位不同，不据此计算跨 benchmark 总分。

## 2. QASPER

### 2.1 论文与官方资源

论文：**A Dataset of Information-Seeking Questions and Answers Anchored in Research Papers**。作者：Pradeep Dasigi、Kyle Lo、Iz Beltagy、Arman Cohan、Noah A. Smith、Matt Gardner。NAACL 2021，4599–4610 页。书目信息以 [ACL Anthology](https://aclanthology.org/2021.naacl-main.365/) 为准。

| 资源 | 地址 | 用途与归属 |
| --- | --- | --- |
| 正式论文、引用导出 | [ACL Anthology](https://aclanthology.org/2021.naacl-main.365/) | 正式会议版本及 BibTeX |
| 论文 PDF | [NAACL PDF](https://aclanthology.org/2021.naacl-main.365.pdf) | 数据构建、任务定义、基线和误差分析 |
| 预印本 | [arXiv:2105.03011](https://arxiv.org/abs/2105.03011) | 预印本版本记录 |
| DOI | [10.18653/v1/2021.naacl-main.365](https://doi.org/10.18653/v1/2021.naacl-main.365) | 持久书目标识 |
| 原项目入口 | [allenai.org/data/qasper](https://allenai.org/data/qasper) | 本次重定向到 Ai2 的 HF 数据页 |
| HF 数据卡与预览 | [allenai/qasper](https://huggingface.co/datasets/allenai/qasper) | Ai2 命名空间；schema、许可、数据预览 |
| HF 文件列表 | [Files](https://huggingface.co/datasets/allenai/qasper/tree/main) | loader、版本及元数据 |
| 发布 loader | [qasper.py](https://huggingface.co/datasets/allenai/qasper/blob/main/qasper.py) | 原始 v0.3 包地址和字段转换 |
| 规模与下载校验元数据 | [dataset_infos.json](https://huggingface.co/datasets/allenai/qasper/blob/main/dataset_infos.json) | 论文行数、源文件大小和发布方 checksum |
| 官方基线 | [qasper-led-baseline](https://github.com/allenai/qasper-led-baseline) | LED 训练配置、证据选择基线 |
| 独立官方评分脚本 | [scripts/evaluator.py，固定提交](https://github.com/allenai/qasper-led-baseline/blob/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/scripts/evaluator.py) | Answer F1、Evidence F1；无需训练 LED 即可阅读/复用 |
| 论文视频 | [ACL 托管视频](https://aclanthology.org/2021.naacl-main.365.mp4) | 论文页面提供的报告入口，本次未播放 |

原始包由发布 loader 指向：[train/dev v0.3](https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz)、[test + evaluator v0.3](https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz)。test包已实际下载，SHA256为 `72a52a41193e2838b8074f80ac074b94f956b84886c36a61c58a7df4171bdd72`，实测416篇/1451题。旧式HF loader存在不保证任意新版datasets都能直接执行。

### 2.2 任务、数据与标注

问题由 NLP 从业者只读论文标题和摘要后提出，另一批标注者阅读全文并回答、提供证据。正文来自 S2ORC；目标是阅读论文时的信息需求。答案包含抽取片段、自由文本、Yes/No 和 Unanswerable。[数据卡](https://huggingface.co/datasets/allenai/qasper)

| 划分 | 发布元数据中的论文数 | 论文报告的问题数 |
| --- | ---: | ---: |
| train | 888 | 2,593 |
| dev / validation | 281 | 1,005 |
| test | 416 | 1,451 |
| 合计 | 1,585 | 5,049 |

论文数来自 [HF v0.3 元数据](https://huggingface.co/datasets/allenai/qasper/blob/main/dataset_infos.json)，问题数来自[论文 §4.1](https://aclanthology.org/2021.naacl-main.365.pdf)。这是两种计数单位和来源，不能拿 HF 的 416 行当成 416 道题，也不能把论文数字当成本项目筛选后的题量。

关键结构由 [loader schema](https://huggingface.co/datasets/allenai/qasper/blob/main/qasper.py) 给出：

| 字段 | 含义 |
| --- | --- |
| `id`、`title`、`abstract` | 论文身份、标题、摘要 |
| `full_text` | 章节名称及段落 |
| `qas.question_id`、`question` | 题目身份和原始问题 |
| `answers[].answer` | 多位标注者的答案，包含 `unanswerable`、`extractive_spans`、`free_form_answer`、`yes_no` |
| `evidence`、`highlighted_evidence` | 段落/图表证据与细粒度高亮文本 |
| `figures_and_tables` | 图表 caption 与文件信息 |

图表证据有 `FLOAT SELECTED` 标记；有文字正文不等于所有题都能只靠文字作答。HF 展示结构与原始 JSON 的 list/dict 包装也可能不同，数据适配应注明读取的具体发布格式。[数据卡字段说明](https://huggingface.co/datasets/allenai/qasper)

### 2.3 官方评分与基线

- **Answer F1**：小写、去标点/冠词并规范空白后计算 token F1；多参考取最大值。
- **Evidence F1**：比较预测证据与标注证据；支持多参考。它评价证据选择，不能直接等同于引用语义支持。
- 官方evaluator把缺失预测计零；新submission保留missing/error状态，正式比较拒绝不完整生成。原SN诊断null单独保留，不能混用分母。
- `--text_evidence_only` 过滤图表证据项，不自动排除依赖图表才能回答的问题。

以上来自[固定版本 evaluator](https://github.com/allenai/qasper-led-baseline/blob/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/scripts/evaluator.py)。[官方基线 README](https://github.com/allenai/qasper-led-baseline) 提供 LED、证据 scaffold、有/无证据监督及短上下文配置；本项目不是复现 LED 训练。

固定[作者reader](https://github.com/allenai/qasper-led-baseline/blob/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/qasper_baselines/dataset_reader.py)对FLOAT只统计，不实际删题；其full_text仅章节标题与段落。项目v3保留全题并加入公开摘要/caption，明确标为输入变体，不把它当论文LED表格复现。

### 2.4 Leaderboard、许可与使用边界

[数据卡](https://huggingface.co/datasets/allenai/qasper) 将 Papers with Code 列为 leaderboard。它是被数据卡引用的第三方平台，不能据此推定为作者运营的提交服务器。本次访问[旧数据集地址](https://paperswithcode.com/dataset/qasper)跳到 HF 数据卡，[旧 QA 榜单](https://paperswithcode.com/sota/question-answering-on-qasper)跳到 HF Trending Papers；均未得到可核实的 QASPER 排名。数据卡中“active leaderboard”是残留说明，本手册不沿用为当前事实。

可从论文实验表、官方基线和 §6 的衍生套件继续查结果，但须记录全文/短上下文、是否训练、Answer/Evidence 指标和划分。不能把 LongBench 的子集结果直接放入原版 QASPER 排名。

数据卡标注 **CC BY 4.0**；基线代码仓库为 **Apache-2.0**，分别见[数据卡](https://huggingface.co/datasets/allenai/qasper)与[代码 LICENSE](https://github.com/allenai/qasper-led-baseline/blob/main/LICENSE)。

## 3. MultiHop-RAG

### 3.1 论文与官方资源

论文：**MultiHop-RAG: Benchmarking Retrieval-Augmented Generation for Multi-Hop Queries**。作者：Yixuan Tang、Yi Yang，香港科技大学。作者仓库标明已被 COLM 2024 接收；README 的描述性标题也使用 *A Dataset for Evaluating Retrieval-Augmented Generation Across Documents*，引用时采用论文正式标题。[论文](https://arxiv.org/abs/2401.15391)、[作者仓库](https://github.com/yixuantt/MultiHop-RAG)

| 资源 | 地址 | 用途与归属 |
| --- | --- | --- |
| 论文与版本 | [arXiv:2401.15391](https://arxiv.org/abs/2401.15391) | 任务定义、构建流程、检索/生成实验 |
| 论文 PDF | [arXiv PDF](https://arxiv.org/pdf/2401.15391) | 表 2/3 的规模、表 5/6 的基线 |
| COLM 会议页 | [OpenReview](https://openreview.net/forum?id=t4eB3zYWBK) | 本次返回浏览器验证页，未读取评审内容；论文可由 arXiv 阅读 |
| 官方仓库 | [yixuantt/MultiHop-RAG](https://github.com/yixuantt/MultiHop-RAG) | 检索、问答、数据构建示例 |
| 作者 HF 数据集 | [yixuantt/MultiHopRAG](https://huggingface.co/datasets/yixuantt/MultiHopRAG) | GitHub 直接指向的作者发布 |
| 题目文件 | [MultiHopRAG.json](https://huggingface.co/datasets/yixuantt/MultiHopRAG/blob/main/MultiHopRAG.json) | 问题、答案、题型和 evidence |
| 语料文件 | [corpus.json](https://huggingface.co/datasets/yixuantt/MultiHopRAG/blob/main/corpus.json) | 文章正文和元数据 |
| 语料预览 | [corpus/train](https://huggingface.co/datasets/yixuantt/MultiHopRAG/viewer/corpus/train) | 609 行，以文章为单位 |
| 检索评测代码 | [retrieval_evaluate.py，固定提交](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/retrieval_evaluate.py) | Hits@4/10、MAP@10、MRR@10 |
| 问答评测代码 | [qa_evaluate.py，固定提交](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/qa_evaluate.py) | 实际答案提取与成功判定规则 |
| 数据构建流程 | [pipeline/](https://github.com/yixuantt/MultiHop-RAG/tree/main/pipeline) | 作者公开的部分构建代码 |

### 3.2 任务、数据与标注

语料为英语新闻文章，包含标题、正文、来源、URL、作者及时间等信息；可回答题通常需要联合 2–4 篇文章的证据，部分问题直接涉及来源或时间。`null_query` 是信息不足题，不应套用“每题都有 2–4 篇支持证据”的概括。[论文 §3](https://arxiv.org/pdf/2401.15391)、[HF 数据预览](https://huggingface.co/datasets/yixuantt/MultiHopRAG)

| 题型 | 论文数量 | 主要要求 |
| --- | ---: | --- |
| `inference_query` | 816 | 综合不同事实定位实体或结论 |
| `comparison_query` | 856 | 比较不同文章中的信息 |
| `temporal_query` | 583 | 判断事件时间关系 |
| `null_query` | 301 | 识别现有资料无法回答 |
| 合计 | 2,556 | 语料包含 609 篇文章 |

数量来自[论文表 2/3](https://arxiv.org/pdf/2401.15391)。构建流程使用 GPT-4 生成 claim、连接实体/主题和问题，结合自动验证及人工抽查，不应写成全部人工逐题编写的 gold。

2026-09-24完整下载实测为2556题、609篇语料、6084条证据，四种题型数量与上表一致；全部保留。真实metadata、gold隔离及原始官方QA/检索脚本的对照见[验收记录](notebook-benchmark-real-data-validation.md)。

HF 的两个 config 是 `MultiHopRAG` 和 `corpus`，发布预览均为 `train`。这说明文件组织方式，不证明存在彼此独立的官方 train/dev/test 任务划分。[作者数据卡](https://huggingface.co/datasets/yixuantt/MultiHopRAG/blob/main/README.md)

关键字段：题目侧 `query`、`answer`、`question_type`、`evidence_list`；语料侧 `title`、`body` 和来源元数据。evidence 中的 `fact` 用于评分与定位，不能只导入这些 gold 片段来代表完整语料检索任务。[题目页](https://huggingface.co/datasets/yixuantt/MultiHopRAG)、[语料页](https://huggingface.co/datasets/yixuantt/MultiHopRAG/viewer/corpus/train)

### 3.3 官方评分与解释限制

检索脚本跳过 `null_query`，基于返回文本与 gold fact 的匹配输出 Hits@4、Hits@10、MAP@10、MRR@10。比较复现结果时应沿用固定代码，并说明检索的 chunk 划分和排名范围，不能只凭指标同名认定实现相同。[检索脚本](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/retrieval_evaluate.py)

**当前官方 QA 脚本的成功条件较宽松**：若存在固定格式的答案句则先提取，否则取整个输出；对预测和 gold 小写、按空白分词，只要词集合有任何交集就记成功。脚本输出的 precision、recall、F1、accuracy 都返回该成功比例，不能理解为四种独立指标或标准 token F1。[问答脚本](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/qa_evaluate.py)

因此应区分“论文里的 accuracy”“某一固定提交的 QA 脚本”“另加的严格答案评分”。不能未经核实就认定当前 `main` 的脚本与 2024 年实验完全相同。论文也区分 retrieved chunk 与 ground-truth chunk 两种输入条件；后一种结果不能当作端到端检索系统成绩。[论文 §4 与表 6](https://arxiv.org/pdf/2401.15391)

### 3.4 Leaderboard 与许可

在本次检查的作者仓库、HF 数据卡和论文入口中，未找到独立的官方在线提交榜单或明确的提交协议。可查论文检索/生成结果表及仓库示例；其他团队自建的 “MultiHop-RAG leaderboard” 不能自动视为作者统一验收的排名。

作者 [README](https://github.com/yixuantt/MultiHop-RAG#license) 和 [HF 数据卡](https://huggingface.co/datasets/yixuantt/MultiHopRAG/blob/main/README.md) 标注 **ODC-BY**。该标注不应改写成 MIT/Apache 代码许可，也不等于所有新闻原文都脱离其原有来源条款。

## 4. ALCE

### 4.1 论文与官方资源

论文：**Enabling Large Language Models to Generate Text with Citations**。作者：Tianyu Gao、Howard Yen、Jiatong Yu、Danqi Chen。EMNLP 2023，6465–6488 页。ALCE 是 **Automatic LLMs’ Citation Evaluation**，包含 ASQA、QAMPARI、ELI5 三个子任务。[ACL Anthology](https://aclanthology.org/2023.emnlp-main.398/)

| 资源 | 地址 | 用途与归属 |
| --- | --- | --- |
| 正式论文、引用导出 | [ACL Anthology](https://aclanthology.org/2023.emnlp-main.398/) | 正式会议版本及 BibTeX |
| 论文 PDF | [EMNLP PDF](https://aclanthology.org/2023.emnlp-main.398.pdf) | 三维评测定义、子任务和实验表 |
| 预印本 | [arXiv:2305.14627](https://arxiv.org/abs/2305.14627) | 预印本版本记录 |
| DOI | [10.18653/v1/2023.emnlp-main.398](https://doi.org/10.18653/v1/2023.emnlp-main.398) | 持久书目标识 |
| 官方仓库/项目说明 | [princeton-nlp/ALCE](https://github.com/princeton-nlp/ALCE) | 数据、基线、检索、生成和评分入口 |
| 官方 HF 发布 | [princeton-nlp/ALCE-data](https://huggingface.co/datasets/princeton-nlp/ALCE-data) | 作者组织发布的数据包，不是标准逐行 viewer 的替代说明 |
| 数据包文件页 | [ALCE-data.tar](https://huggingface.co/datasets/princeton-nlp/ALCE-data/blob/main/ALCE-data.tar) | 约 451 MB，页面提供 SHA256 |
| 作者下载脚本 | [download_data.sh](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/download_data.sh) | 证明官方 GitHub 与 HF 包之间的对应关系 |
| 官方评分 | [eval.py，固定提交](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py) | 答案、引用、MAUVE、QA/NLI 指标 |
| 基线配置与提示词 | [configs/](https://github.com/princeton-nlp/ALCE/tree/main/configs)、[prompts/](https://github.com/princeton-nlp/ALCE/tree/main/prompts) | 阅读模型输入及实验条件 |
| 检索实现 | [retrieval.py](https://github.com/princeton-nlp/ALCE/blob/main/retrieval.py) | BM25/GTR 等资料检索入口 |
| 人工评价材料 | [human_eval/](https://github.com/princeton-nlp/ALCE/tree/main/human_eval) | 论文人评与分析，非每题唯一正确引用集合 |
| 引用判定模型 | [google/t5_xxl_true_nli_mixture](https://huggingface.co/google/t5_xxl_true_nli_mixture) | AutoAIS 使用的 NLI 模型 |
| ASQA QA 指标模型 | [gaotianyu1350/roberta-large-squad](https://huggingface.co/gaotianyu1350/roberta-large-squad) | 在生成答案上做 QA 的辅助指标 |
| 论文视频 | [ACL 托管视频](https://aclanthology.org/2023.emnlp-main.398.mp4) | 论文页面提供的报告入口，本次未播放 |

HF固定版本记录给出的数据包SHA256为 `eda837bf659a91b3648dc6e7ab6b17197664d93593857e8fdf3800b6aa6a98f0`。2026-09-24已完整下载451297280字节并重新计算，完全匹配；8个JSON分别记录文件身份。[固定文件详情](https://huggingface.co/datasets/princeton-nlp/ALCE-data/blob/334fa2e7dd32040c3fef931a123c4be1a81e91a0/ALCE-data.tar)、[本地验证](notebook-benchmark-real-data-validation.md)

本次 HF 首页没有完整数据卡，viewer 报 `SplitsNotFoundError`，错误信息涉及不同子任务字段无法合并。文件页仍列有发布包；预览失败不等于数据包不可得，也不应据此承诺直接 `load_dataset` 能正确加载所有子任务。[HF 页面状态](https://huggingface.co/datasets/princeton-nlp/ALCE-data)

### 4.2 三个子任务及各自原始来源

| 子任务 | 回答形式 | ALCE 采用的检索资料 | 上游原始论文与作者资源 |
| --- | --- | --- | --- |
| ASQA | 长答案，覆盖歧义问题的多个解释 | Wikipedia，2018-12-20 快照 | [ASQA: Factoid Questions Meet Long-Form Answers](https://arxiv.org/abs/2204.06092)；[Google Research 项目说明](https://github.com/google-research/language/tree/master/language/asqa) |
| QAMPARI | 来自不同段落的多个实体答案列表 | 同一 Wikipedia 快照 | [QAMPARI 原论文](https://arxiv.org/abs/2205.12665)；[自述为官方的作者仓库](https://github.com/samsam3232/qampari) |
| ELI5 | 对开放问题给出较长解释 | Sphere：经过过滤的 Common Crawl 语料 | [ELI5: Long Form Question Answering，ACL 2019](https://aclanthology.org/P19-1346/)；[facebookresearch/ELI5](https://github.com/facebookresearch/ELI5) |

ALCE论文§2称从每个上游数据集的development set选取1000例，并说明不提供引用监督训练数据。2026-09-24发布包实测则为ASQA 948题、QAMPARI 1000题、ELI5 1000题，ASQA差异原样记录、不补齐或删题。普通ASQA/QAMPARI每题100候选；ELI5为31～100，总89462候选槽位。[ALCE论文](https://aclanthology.org/2023.emnlp-main.398.pdf)、[发布包验收](notebook-benchmark-real-data-validation.md)

作者发布包包括 ASQA/QAMPARI 的 DPR/GTR top-100 候选，以及 ELI5 的 BM25 top-100 候选；另有 oracle 重排版本。普通 top-100、oracle top-5、最终送给生成模型的 top-k 是三种不同概念。上游 ASQA/QAMPARI/ELI5 的完整规模也不能当作 ALCE 子集规模。[官方数据说明](https://github.com/princeton-nlp/ALCE#data)

关键结构：`question`、候选 `docs`（含 `title`、`text`）、系统 `output`；ASQA 另有 `qa_pairs.short_answers` 与长答案标注，QAMPARI 有答案及别名列表，ELI5 有用于正确性评价的 `claims`。评分器对所需字段有不同要求。[官方 eval.py](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py)

### 4.3 官方评测方法

| 维度 | 方法 | 解释 |
| --- | --- | --- |
| 流畅性 | MAUVE，主要用于 ASQA/ELI5 | 分布层面的文本质量诊断，不是事实正确率 |
| ASQA 正确性 | EM recall / STR-EM | 检查各组短答案别名是否出现在生成答案中，衡量覆盖 |
| QAMPARI 正确性 | 列表 precision、recall、F1，以及 recall@5 变体 | 答案别名匹配；ALCE 对召回采用最多 5 个正确答案的封顶口径 |
| ELI5 正确性 | Claim recall | NLI 判断生成回答覆盖多少参考 claims |
| 引用召回 | Citation recall | 回答单元是否得到其所引用资料的支持 |
| 引用精确率 | Citation precision | 引用是否支持回答或对联合支持有必要性，识别无关/多余引用 |

方法来自[论文 §3](https://aclanthology.org/2023.emnlp-main.398.pdf)和[评分实现](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py)。引用 NLI、ELI5 claims、QA-based 和 MAUVE 指标都有各自模型/计算依赖；不能只看引用编号存在就称为“引用正确”。

固定CLI把输出截到首行，每个回答单元最多取3个引用。新官方submission评分遵循这些规则；旧SN多行诊断入口保留自身身份。AutoAIS在ASQA/ELI5会跳过无句子的空输出，新报告另存实际参与题目与分母，不能把引用分母冒称全题。[官方CLI](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py)、[当前实验计划](notebook-benchmark-experiment-plan.md)

### 4.4 Leaderboard、论文基线与许可

在本次检查的论文、官方仓库和 HF 包说明中，未找到作者运营的独立在线提交榜单。论文表 4/5/6 分别报告 ASQA、QAMPARI、ELI5；基线还区分 VANILLA、SUMM、SNIPPET、INLINESEARCH、CLOSEDBOOK、POSTCITE 等条件。比较时同时记录生成模型、检索器、候选预算、是否 oracle、是否事后补引用及评分模型。[论文实验部分](https://aclanthology.org/2023.emnlp-main.398.pdf)

代码仓库为 **MIT**，[LICENSE](https://github.com/princeton-nlp/ALCE/blob/main/LICENSE) 可查。HF 数据包页面没有提供足以概括所有子任务和原文的统一许可说明；ASQA、QAMPARI、ELI5、Wikipedia、Sphere/Common Crawl 的来源条款需分别保留，不能把仓库 MIT 自动套给所有材料。

## 5. QMSum

### 5.1 论文与官方资源

论文：**QMSum: A New Benchmark for Query-based Multi-domain Meeting Summarization**。作者：Ming Zhong、Da Yin、Tao Yu、Ahmad Zaidi、Mutethia Mutuma、Rahul Jha、Ahmed Hassan Awadallah、Asli Celikyilmaz、Yang Liu、Xipeng Qiu、Dragomir Radev。NAACL 2021，5905–5921 页。[ACL Anthology](https://aclanthology.org/2021.naacl-main.472/)

| 资源 | 地址 | 用途与归属 |
| --- | --- | --- |
| 正式论文、引用导出 | [ACL Anthology](https://aclanthology.org/2021.naacl-main.472/) | 正式会议版本及 BibTeX |
| 论文 PDF | [NAACL PDF](https://aclanthology.org/2021.naacl-main.472.pdf) | 数据构建、领域、标注流程、locate-then-summarize |
| 预印本 | [arXiv:2104.05938](https://arxiv.org/abs/2104.05938) | 预印本版本记录 |
| DOI | [10.18653/v1/2021.naacl-main.472](https://doi.org/10.18653/v1/2021.naacl-main.472) | 持久书目标识 |
| 官方仓库 | [Yale-LILY/QMSum](https://github.com/Yale-LILY/QMSum) | 原始发布与字段说明，优先入口 |
| 官方数据目录 | [data/ALL/jsonl](https://github.com/Yale-LILY/QMSum/tree/main/data/ALL/jsonl) | `train.jsonl`、`val.jsonl`、`test.jsonl`，一行一场会议 |
| 分领域数据 | [data/](https://github.com/Yale-LILY/QMSum/tree/main/data) | Academic、Product、Committee 和 ALL |
| 数据转换说明 | [data_process.ipynb](https://github.com/Yale-LILY/QMSum/blob/main/data_process.ipynb) | 给 seq2seq 基线整理数据 |
| 官方 Locator 输出 | [extracted_span/](https://github.com/Yale-LILY/QMSum/tree/main/extracted_span) | 作者模型检出的 span，不应混称人工 gold |
| 已发布模型输出 | [model_output/](https://github.com/Yale-LILY/QMSum/tree/main/model_output) | README 说明为 HMNet、golden input 条件 |
| 统计与实验图 | [figures/](https://github.com/Yale-LILY/QMSum/tree/main/figures) | 原始统计和基线表 |
| HF 论文索引 | [HF Papers:2104.05938](https://huggingface.co/papers/2104.05938) | 论文发现页，**不是官方 HF 数据集** |

基线代码由作者 README 指向 [Locator](https://github.com/maszhongming/Effective_Extractive_Summarization)、[Pointer-Generator](https://github.com/abisee/pointer-generator)、[HMNet](https://github.com/microsoft/HMNet) 和 BART 实现；这是作者引用的模型实现集合，不是主数据仓库自带的一键完整评测器。[官方模型说明](https://github.com/Yale-LILY/QMSum#models)

### 5.2 任务、划分与原始结构

任务输入为会议全文和一个查询，输出针对查询的人工摘要式回答。三个领域包括产品设计会议（AMI）、学术会议（ICSI）、议会委员会会议。论文报告 232 场会议、1,808 对查询摘要，表 1 的 train/valid/test 查询摘要对分别是 **1,257 / 272 / 279**。[论文表 1](https://aclanthology.org/2021.naacl-main.472.pdf)

**版本与数量差异保留原口径。** 已重新下载固定commit的test，实测35场/281题（37 general、244 specific），SHA256为 `6bcd428211260ad2efae3af76cbaf6a7f5ae4bb5e1e59c45a4b8e89539cb9208`。不删题凑论文279，也不把281写成原论文统计。[固定test](https://github.com/Yale-LILY/QMSum/blob/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e/data/ALL/jsonl/test.jsonl)

| 字段 | 含义与限制 |
| --- | --- |
| `meeting_transcripts` | 按顺序保存 `speaker` 和 `content` 的完整转录 |
| `general_query_list` | 针对整场会议的查询和参考 `answer` |
| `specific_query_list` | 针对局部议题/人物/决策的查询、参考 `answer` 和相关范围 |
| `relevant_text_span` | turn 索引范围，可有多段；不是一份检索算法输出 |
| `topic_list` | 主题与相关 turn 范围 |

作者 README 明确：general query 对应整场会议，没有 specific query 那样的相关文本区间。因而不能声称每道 QMSum 题都有局部 gold evidence。[官方 schema](https://github.com/Yale-LILY/QMSum#dataset)

### 5.3 官方评价、结果与实现边界

论文报告 **ROUGE-1、ROUGE-2、ROUGE-L**，并比较直接摘要和 locate-then-summarize 等设置。ROUGE 反映与人工摘要的词面重合，不能单独证明所有事实正确、证据充分或引用准确。[论文实验部分](https://aclanthology.org/2021.naacl-main.472.pdf)

作者仓库提供 HMNet 的部分摘要输出，注明使用 **golden input**，报告 36.51 / 11.41 / 31.60（R-1/R-2/R-L）。这是该模型、输入和历史评分设置的结果，不能当作完整会议端到端系统或当前 SOTA 的通用比较线。[官方 Model Outputs](https://github.com/Yale-LILY/QMSum#model-outputs)

作者在[issue #5](https://github.com/Yale-LILY/QMSum/issues/5#issuecomment-890003212)明确指定pyrouge 0.1.3并指向[MatchSum评分代码](https://github.com/maszhongming/MatchSum/blob/c7754245a454d0ba3535db0e4cc1a13b3d35680d/metrics.py#L179)：Perl ROUGE参数为 `-c 95 -r 1000 -n 2 -m -a`。新评分使用Perl并明确HMNet regex分句；旧Python rouge-score保留为SN诊断。279份公开答卷在同序条件下，独立pyrouge SEE与当前SPL均得36.464/11.374/31.558，未严格复现公布数字；Perl的Average_F是bootstrap均值，不能重算逐题平均。[验证细节](notebook-benchmark-experiment-plan.md)

### 5.4 Hugging Face、Leaderboard 与许可

本次从原论文和官方 README 未确认原作者维护的独立 QMSum HF 数据集。检索得到的重打包、清洗或长文本套件版本，应先检查作者归属、原始 ID、划分、是否拼接/截断以及证据字段是否保留。

优先保留有自身作者说明的衍生资源：[tau/scrolls](https://huggingface.co/datasets/tau/scrolls)、[tau/zero_scrolls](https://huggingface.co/datasets/tau/zero_scrolls)、[zai-org/LongBench](https://huggingface.co/datasets/zai-org/LongBench)。它们适合研究各自套件，但不是本项目读取原始 `meeting_transcripts` / 查询列表的直接替代品。

未在 QMSum 作者仓库中确认独立在线提交榜单。论文与仓库实验表是原始结果来源；衍生套件的 leaderboard 状态见下一节。仓库 [LICENSE](https://github.com/Yale-LILY/QMSum/blob/main/LICENSE) 为 **MIT**；原始 AMI/ICSI/议会材料仍需保留来源信息。

## 6. 衍生版本与榜单访问状态

| 资源 | 与本次四套 benchmark 的关系 | 可用参考入口 | 本次核查与边界 |
| --- | --- | --- | --- |
| SCROLLS | 将 QASPER、QMSum 等统一为长文本 text-to-text 任务 | [作者 GitHub](https://github.com/tau-nlp/scrolls)、[HF](https://huggingface.co/datasets/tau/scrolls) | 属于 SCROLLS 团队的正式发布；不是原版任务所有证据字段/指标的替代 |
| ZeroSCROLLS | 包含 QASPER、QMSum 的 zero-shot 长文本套件 | [作者 HF](https://huggingface.co/datasets/tau/zero_scrolls) | 按其自己的任务格式、抽样和评价协议解读 |
| LongBench v1 | 包含 QASPER、QMSum 的长上下文评测任务 | [作者 GitHub](https://github.com/THUDM/LongBench)、[当前 HF](https://huggingface.co/datasets/zai-org/LongBench) | 原 HF `THUDM/LongBench` 本次重定向到 `zai-org/LongBench`；仓库同时介绍 v2，不能混用版本 |
| QASPER 数据卡中的 Papers with Code 链接 | 原数据卡引用的第三方聚合入口 | 见 §2.4 | 数据集链接跳 HF 数据卡，QA 榜单链接跳 HF Trending Papers；未取得实际排名 |
| 历史 SCROLLS 网站 | 官方材料曾提供的榜单/提交入口 | 优先从上述 GitHub/HF 回溯 | `www.scrolls-benchmark.com/leaderboard` 本次重定向至无关站点，不作为可用榜单链接推荐 |
| 历史 ZeroSCROLLS 网站 | 相关套件历史入口 | 优先使用其 HF 数据卡 | 本次访问 `www.zero.scrolls-benchmark.com` 未能取得页面，未验证在线提交功能 |

本次没有向任何榜单提交结果、登录账户或验证提交流程。对 MultiHop-RAG、ALCE、QMSum 的“未找到独立官方榜单”，含义仅是**已检查的论文、作者仓库和数据卡未提供可确认入口**，不是证明网上不存在任何相关榜单。

## 7. 与 Silicon Notebook 当前评测的对应关系

本节为项目事实与解释，不是 benchmark 发布方要求。新官方评分入口为 [benchmark_official.py](../src/rag_eval/benchmark_official.py)；[notebook_scoring.py](../src/rag_eval/notebook_scoring.py) 保留 Notebook 诊断，[notebook_alce.py](../src/rag_eval/notebook_alce.py) 同时提供引用转换。逐项规则、公式、代码证据和未完成项见[标准与实现符合性](notebook-benchmark-standards-and-conformance.md)，执行入口见[当前实验计划](notebook-benchmark-experiment-plan.md)。

| Benchmark | SN 当前资料组织 | SN 当前主要分数 | 与原始定义的关键差异 |
| --- | --- | --- | --- |
| QASPER | v3一篇一个分区；标题、摘要、正文、caption；不按gold删题 | 官方Answer F1；显式预测才有Evidence F1 | 公共输入变体不同于原LED reader；最终上下文覆盖仅SN诊断 |
| MultiHop-RAG | 完整 corpus 的独立分区，gold 留在评分侧 | 官方弱匹配答案分、上下文 fact 覆盖 | fact 覆盖不是 Hits/MAP/MRR；正文答案与宽松词匹配要结合案例解释 |
| ALCE | 每题完整候选，区分task/retriever/ordinary-oracle | 字符串指标及显式官方CLI模型批评分 | 不重检全Wikipedia/Sphere；只认实际显示的引用，保留官方预处理和各指标实际分母 |
| QMSum | 一场完整会议一个分区，区分general/specific | 新官方轨道Perl ROUGE-1/2/L；turn覆盖仅诊断 | 明确分句与题序，不声称复现历史279题表格 |

四套官方任务本身均不定义本项目的 DeepEval Agent 总轨迹或组件协议。`sn-deepeval-native-v1` 中的 Faithfulness、AnswerRelevancy、TaskCompletion 等是另行声明的诊断维度，不能改名为这四套 benchmark 的官方指标。[原生 Agent 协议](native-agent-evaluation.md)

## 8. 版本记录与后续整理入口

本次通过 GitHub commit API 核实以下仓库 `main` 的提交身份。固定链接用于重查本次读到的代码，**不表示这些提交均为原论文实验时的版本**，也不表示自动更新项目依赖。

| 仓库 | 本次读取的 main 提交 | 建议追踪的位置 |
| --- | --- | --- |
| QASPER LED baseline | [afd0fb96bf78ce8cd8157639c6f6a6995e4f9089](https://github.com/allenai/qasper-led-baseline/commit/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089) | `scripts/evaluator.py`、训练配置；原数据另记 v0.3 和包哈希 |
| MultiHop-RAG | [c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8](https://github.com/yixuantt/MultiHop-RAG/commit/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8) | `qa_evaluate.py`、`retrieval_evaluate.py`；HF 两份 JSON 另记版本 |
| ALCE | [246c476a4edfc564266b7346b6e29ef4861ae937](https://github.com/princeton-nlp/ALCE/commit/246c476a4edfc564266b7346b6e29ef4861ae937) | `eval.py`、`download_data.sh`、模型身份、候选包 |
| QMSum | [83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e](https://github.com/Yale-LILY/QMSum/commit/83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e) | `data/ALL/jsonl/`、README、基线输出 |

2026-09-24已完成MultiHop HF revision `71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82` 与ALCE `334fa2e7dd32040c3fef931a123c4be1a81e91a0` 的完整下载及校验；此前超时/重置属于历史记录，不再是当前阻塞。冻结时保存 **dataset repo + revision + 文件名 + 实测SHA256 + 展开实数**；完整文件、适配与评分对照证据见[真实数据验收](notebook-benchmark-real-data-validation.md)。

引用时，QASPER/ALCE/QMSum 可直接使用各自 ACL 页面提供的 BibTeX；MultiHop-RAG 可从作者 README 的 Citation 获取并按所引用的预印本/会议版本填写。采用 ALCE 子任务或衍生套件时，再补引对应上游数据集/套件论文；不只引用 DeepEval 框架。

当前数据文件版本、实际题数、排除情况和 scorer/SN 适配差异已进入验收记录及[标准说明](notebook-benchmark-standards-and-conformance.md)；仍待补齐真实模型全量执行和可比的论文基线配置。网页资料不能代替这些执行证据。[历史资源可获取性核查](notebook-benchmark-data-availability.md)、[当前实验计划](notebook-benchmark-experiment-plan.md)
