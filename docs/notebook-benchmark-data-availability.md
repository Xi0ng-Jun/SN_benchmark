# Notebook 类应用公开基准：数据与评分资源可获取性

核查日期：2026-09-17。对象为 QASPER、ALCE、MultiHop-RAG、QMSum、LAB 和 ResearchQA。

## 核查结论与证据边界

QASPER、ALCE、MultiHop-RAG、QMSum 都已公开任务所需的数据资源，可以作为 SN 下一阶段的接入候选。LAB 也有可访问的预处理数据包。ResearchQA 的题目、标注和评测代码已公开，但其加载方式、论文全文获取与 PDF 依赖需要先处理。

本轮只读取官方论文、说明、代码、文件目录和元数据，并对部分数据地址发送 HTTP HEAD 请求。HEAD 不读取文件正文。没有下载数据集、论文 PDF、模型权重或压缩包，没有解包、运行 loader、执行评分、调用 SN 或修改生产代码。因此，下文的规模来自发布说明，不能当作本地完整性审计结果；本机访问情况也不保证公司服务器的网络情况。

| 基准 | 公开原文/候选资料 | 公开标准标注 | 划分与发布规模 | 评分资源 | 当前判断 |
| --- | --- | --- | --- | --- | --- |
| QASPER v0.3 | 论文标题、摘要、按章节组织的全文段落；另有图表信息 | 原问题、多位标注者的答案、证据段落/高亮句、无法回答标志 | train / dev / test；说明为 1,585 篇论文、5,049 问 | 独立官方 `scripts/evaluator.py`：Answer F1、Evidence F1 | 文本路线资料齐备，优先接入 |
| ALCE | ASQA、QAMPARI、ELI5 的预检索候选片段包，含 top-100 候选及 oracle 重排变体 | 按子集提供答案、短答案及别名、参考长答案或 claims 等评分输入 | 三个子任务的发布包；本轮未解包计数 | 官方 `eval.py`；引用评分依赖 AutoAIS/NLI 模型 | 资料和 scorer 均有，需适配 SN 引用与准备评分模型 |
| MultiHop-RAG | 独立文章库 `corpus.json`，含正文及来源元数据 | `MultiHopRAG.json` 中的问题、答案、题型、证据列表 | 2,556 问；HF corpus 页面为 609 篇文章；两个 config 均展示为 train | 官方检索、问答评分脚本 | 可复用已有项目基础，先补多文档证据集合支持 |
| QMSum | 会议完整文本，按说话人和发言组织 | 查询、人工参考摘要；特定查询附相关发言范围 | train / val / test；232 场会议、1,808 对查询与摘要 | 发布 ROUGE 结果和模型入口；主数据仓库未提供独立 scorer | 数据齐备，评分实现需另行固定 |
| LAB | 大学数据仓库发布的 `data.zip`，将六类任务整理为 Intertext Graph 格式 | 各任务原有标签及归因评测材料，标注来源随任务而异 | 数据仓库版本 4；包大小 1,410,519,294 bytes | 任务评分与归因评分代码 | 发布包可访问；与 QASPER 等任务重叠，按需采用 |
| ResearchQA | 表中提供论文 PDF 地址、论文身份、部分来源片段 | 答案、分章节候选证据、judge rubric、部分题型的拒答标志 | 论文报告 494 篇、6,211 问；当前 HF 全量加载状态异常，未实测计数 | OpenPaper 的 `server/evals/run_benchmark.py` 等 | 保留候选；不能描述为已具备无障碍文本接入 |

## 1. QASPER：可以直接取得原文、答案、证据和 scorer

来源：[官方数据仓库](https://huggingface.co/datasets/allenai/qasper)、[发布 loader](https://huggingface.co/datasets/allenai/qasper/blob/main/qasper.py)、[版本元数据](https://huggingface.co/datasets/allenai/qasper/blob/main/dataset_infos.json)。

官方 loader 指向两个原始包：

| 文件 | 内容 | 本轮 HEAD 结果 |
| --- | --- | --- |
| [qasper-train-dev-v0.3.tgz](https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-train-dev-v0.3.tgz) | train、dev JSON | HTTP 200，application/gzip，10,835,856 bytes |
| [qasper-test-and-evaluator-v0.3.tgz](https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz) | test JSON 及 evaluator | HTTP 200，application/gzip，3,865,061 bytes |

HF 元数据中 train / validation / test 的行数为 888 / 281 / 416，单位是论文，一行内包含多道题。不能直接将行数当作题数。

[官方 evaluator](https://github.com/allenai/qasper-led-baseline/blob/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/scripts/evaluator.py) 可独立阅读和复用，不需要训练官方 LED 基线。答案使用规范化后的 token F1，证据使用段落匹配 F1，并支持多个参考标注。证据匹配需要将 SN 的 chunk/引用映射到原始段落，不能直接用 SN 的 chunk ID 与 gold 文本比较。

首阶段可使用结构化文本，无需重新下载论文 PDF 做 OCR。需要区分依赖正文与依赖图表的题目；脚本的 `--text_evidence_only` 只过滤证据中的图表项，不会自动证明题目仅靠文字可答，也不会自动排除图表题。文本子集的纳入规则和排除数量应单独报告。

发布 loader 是旧式 Hugging Face 数据脚本，不能仅凭一个 `load_dataset` 示例承诺与任意新版依赖兼容；未来可以读取固定版本原始 JSON。数据卡标注 CC BY 4.0；代码仓库为 Apache-2.0。

## 2. ALCE：候选资料与评分代码均已发布

来源：[官方仓库](https://github.com/princeton-nlp/ALCE)、[官方获取脚本](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/download_data.sh)、[数据文件页](https://huggingface.co/datasets/princeton-nlp/ALCE-data/blob/main/ALCE-data.tar)。

- 官方获取脚本使用 `princeton-nlp/ALCE-data` 的 `ALCE-data.tar`，文件页显示约 451 MB。
- 文件页发布 SHA256：`eda837bf659a91b3648dc6e7ab6b17197664d93593857e8fdf3800b6aa6a98f0`。这是发布方提供的值，本轮没有自行下载核对。
- README 说明包内有 ASQA/QAMPARI 的 DPR/GTR top-100 和 ELI5 的 BM25 top-100 检索结果，也包含 oracle 重排版本。普通候选与 oracle 候选是不同实验条件，不能混用。
- [官方 scorer](https://github.com/princeton-nlp/ALCE/blob/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py) 按子集读取不同标准答案结构：ASQA 的短答案/参考长答案、QAMPARI 的答案集合及别名、ELI5 的 claims 等。

这些预检索资料足以设计固定候选范围内的 SN 评测，不必先重建 Wikipedia/Sphere 全库。这样得到的是候选资料范围内的系统成绩，不能声称复现了全库检索。

引用分数使用模型判断被引用资料是否支持回答中的句子，不是仅检查引用编号存在，也不是默认调用 GEval。默认引用模型为公开的 [google/t5_xxl_true_nli_mixture](https://huggingface.co/google/t5_xxl_true_nli_mixture)；ASQA 的 QA-based 指标另用 [gaotianyu1350/roberta-large-squad](https://huggingface.co/gaotianyu1350/roberta-large-squad)。模型权重页面已确认存在，本轮未下载或运行。启用 MAUVE 等指标还有各自依赖。

ALCE 不要求每道题都只有唯一的正确引用组合。官方 `human_eval` 是特定模型输出的人审材料，不能视为所有题的唯一 gold 引用。接入 SN 时应保留“回答句子—SN 引用—原候选片段”的对应关系，不能事后替 SN 补上正确引用再计为系统原生表现。

代码仓库标注 MIT；三类原始数据与语料的许可需分别记录，不能把代码许可概括成所有原文的许可。

## 3. MultiHop-RAG：题目与完整文章库分别可得

来源：[作者数据卡](https://huggingface.co/datasets/yixuantt/MultiHopRAG/blob/main/README.md)、[官方代码](https://github.com/yixuantt/MultiHop-RAG)。

| 资源 | 内容 | 发布页 |
| --- | --- | --- |
| `MultiHopRAG.json` | `query`、`answer`、`question_type`、`evidence_list` | [约 5.17 MB](https://huggingface.co/datasets/yixuantt/MultiHopRAG/blob/main/MultiHopRAG.json) |
| `corpus.json` | 文章 `body`、标题、URL、作者、时间、来源等 | [约 6.79 MB](https://huggingface.co/datasets/yixuantt/MultiHopRAG/blob/main/corpus.json)；[609 行 corpus 视图](https://huggingface.co/datasets/yixuantt/MultiHopRAG/viewer/corpus/train) |

这里有已保存的文章正文，不需要依赖逐篇实时爬取新闻网页。HF config 名分别为 `MultiHopRAG` 和 `corpus`；页面上的 train 是发布容器名称，不能据此虚构官方独立 test 划分。

[官方检索脚本](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/retrieval_evaluate.py) 给出 Hits@4、Hits@10、MAP@10、MRR@10，并跳过 `null_query`。[当前官方问答脚本](https://github.com/yixuantt/MultiHop-RAG/blob/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/qa_evaluate.py) 使用小写后词集合是否有交集作为成功判定，其输出的 precision/recall/F1/accuracy 都来自同一成功比例；不能把这些名字解释成严格答案 EM 或标准 token F1。未来复用时应固定版本并原样说明公式，补充诊断指标时独立命名。

本项目已有历史 loader 和受限候选库实验记录，但不代表当前十套公开 benchmark 的分区执行器已支持该任务。接入前要支持一题绑定多份证据文档，并让整个证据集合处于可访问的 notebook 范围。证据列表留在评测侧，SN 导入公开文章正文。

数据卡标注 ODC-BY。

## 4. QMSum：会议文本与人工摘要齐备

来源：[官方 README](https://github.com/Yale-LILY/QMSum)、[原论文](https://aclanthology.org/2021.naacl-main.472/)。

官方数据路径为 `data/ALL/jsonl/{train,val,test}.jsonl`。本轮对三个原始文件的 HEAD 请求均返回 HTTP 200，长度分别为 11,940,700、2,714,748、2,746,108 bytes。它们是文本，不需要音频转写或 OCR。

字段包括 `meeting_transcripts`、`general_query_list`、`specific_query_list`，查询带人工 `answer`。特定查询有 `relevant_text_span`，可包含多段发言；一般查询针对整场会议，没有同样的局部证据范围标注。两类查询不能都宣称具备细粒度 gold evidence。

官方报告 ROUGE-1/2/L 并链接基线实现；本轮检查主仓库完整文件树，发现数据处理 notebook，但没有独立评分脚本。因而“数据可得”已确认，“一键复用独立官方 scorer”尚不能承诺。未来应固定 ROUGE 实现、分词/预处理及参数，DeepEval 的语义指标作为另行声明的诊断，不能冒充原论文 ROUGE。

主仓库 LICENSE 为 MIT；保留会议来源信息。

## 5. LAB：公开预处理包可访问，包含重叠任务

来源：[官方仓库](https://github.com/UKPLab/emnlp2024-attribute-or-abstain)、[大学数据仓库版本 4](https://tudatalib.ulb.tu-darmstadt.de/handle/tudatalib/4276.4)。

发布记录列出 QASPER、Natural Questions、Evidence Inference、WiCE、ContractNLI、GovReport，统一采用 Intertext Graph 格式。不能把它们都当作全新独立数据，也不能在与 QASPER 合并汇总时重复计题。

本轮读取到官方 `data.zip` 的[文件元数据](https://tudatalib.ulb.tu-darmstadt.de/server/api/core/bitstreams/f3f94e39-f71c-4de8-9aea-8f796a3bd420)：大小 **1,410,519,294 bytes**；对元数据返回的 [content 地址](https://tudatalib.ulb.tu-darmstadt.de/server/api/core/bitstreams/f3f94e39-f71c-4de8-9aea-8f796a3bd420/content) 发送 HEAD，得到 HTTP 200、`application/zip`。未下载或检查包内文件完整性。网页的 `/bitstreams/.../download` 本身返回 HTML，不能把这个响应误当 ZIP 文件。

官方 README 的开头指向版本 4，而后面的旧示例仍指向版本 2；应明确使用哪个发布版本。GitHub 提供任务和归因 scorer；GovReport 的部分证据使用 BM25 自动构造，不能统一称为人工 gold evidence。发布页标注 CC BY 4.0（另有说明的内容除外），代码为 Apache-2.0。

## 6. ResearchQA：题目与代码可得，全文路线仍有具体缺口

来源：[数据卡](https://huggingface.co/datasets/khoj-ai/ResearchQA/blob/main/README.md)、[数据页面](https://huggingface.co/datasets/khoj-ai/ResearchQA)、[作者评测说明](https://github.com/khoj-ai/openpaper/blob/master/server/evals/README.md)。

已确认公开字段包括 `paper_s3_url`、`question`、`expected_answer`、`expected_references`、`judge_rubric`，以及部分题型的 `expected_refusal`。来源片段不是整篇论文正文。官方代码位于 **master** 分支的 `server/evals/`，包含 `run_benchmark.py` 等；本轮通过 GitHub 文件树确认。

发现的具体问题：

1. HF 页面显示 `DatasetGenerationError / CastError`，原因是不同记录的列不一致。这是公开 viewer 的状态，不等于所有获取方式均失败，也不能据此承诺默认 `load_dataset` 可直接成功。
2. 页面示例 PDF 的虚拟主机地址在本机严格 TLS 检查中出现主机名不匹配。同一 S3 对象使用标准 path-style 地址后 HEAD 成功：`https://s3.us-east-1.amazonaws.com/assets.openpaper.ai/op-evals/benchmark/W3140854437.pdf`，HTTP 200、`application/pdf`、7,613,176 bytes。没有关闭证书验证；这只验证了一个公开示例，不能外推全部论文。
3. 作者代码主要围绕 OpenPaper 和原始 PDF 基线组织。用于 SN 时需适配系统调用及引用结构；若坚持当前纯文本范围，还需确定可靠的全文文本来源。
4. 发布题目包含模型生成标注。数据卡标注 CC BY-NC 4.0，并明确引用原文段落保留原出版者条款；这些应作为来源元信息记录。

因此它可以留作后续候选，本轮不将其列入最先实施的四套。

## 建议下一步

先围绕 **QASPER、ALCE、MultiHop-RAG、QMSum** 设计 SN 数据适配。LAB 可按需求复用具体任务和归因方法；ResearchQA 待全文获取与数据加载方式确定后再安排。

标准题目、答案和已有证据优先复用发布方版本，不要求重新人工编题或增加“先审核官方 benchmark 才能评分”的门槛。仍需做通常的数据与接口校验：固定版本、保留原始 ID、区分全文与 gold、记录缺失字段、完整保留多文档证据集合；这些是接入正确性的检查。

后续服务器实际获取时再核对文件哈希、条目数、证据映射及 scorer 依赖。本轮仅形成来源与可用性记录，不改变已运行实验的配置、选题协议或历史成绩。
