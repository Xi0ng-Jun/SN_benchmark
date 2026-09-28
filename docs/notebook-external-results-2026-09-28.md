# 外部方法比较：公开答卷重评分与受控运行入口

核实日期：2026-09-28。本页记录真实公开答卷在本项目固定评分协议下的结果与复现入口。已有一份 5 题 QASPER SN/BM25 smoke 配对工件，以及 MultiHop smoke 的成本与外部内容过滤诊断；这些历史 smoke 结果不代表完整 benchmark 成绩，也不等同于当前服务器 full run。

**当前已有两份完整 MultiHop 答卷、八份 ALCE 样本答卷、一份完整 QMSum Socratic SegEnc 答卷，以及 MultiHop 两方法和 ASQA 四方法的同题比较报告。QASPER 已有 5 题 SN/BM25 smoke 比较；其它 benchmark 的 SN 全量比较仍未完成。**

旧的 [MultiHop 146 题入口](notebook-multihop-subset-comparison.md)保留为历史链路验收。本次取得完整文件后，后续 MultiHop 主比较默认使用完整 2,556 题，不再受下载截断范围限制。

## 1. 比较的含义

这里已经完成的是：固定外部模型的原始答案 → 唯一问题映射 → 使用冻结标签和固定官方脚本重评分 → 在相同题目上计算差值。无需先重新训练或重写外部方法。

题目与 scorer 相同，可以支持**公开答卷的描述性比较**。这并不证明生成时语料、模型、提示或预算相同。只有进一步核实并控制这些条件，才能支持同条件方法比较或算法归因。本次缺失条件明确保留；尤其不能把 ALCE 的样本答卷报告当作完整官方榜单、受控重跑或 SN 排名。

现有机器标签 `recomputed-subset` 表示“公开逐题答卷重评分”这一来源类别，保留以兼容已有工件；范围以 `submission.scope` 和实际 case IDs 为准。本次 MultiHop 是 `full`，ALCE 是 `subset`。`full` 指覆盖冻结 bundle，不能据此宣称复现了论文所有实验。

## 2. Multi-Meta-RAG：完整 2,556 题

### 来源与身份

作者仓库：[Multi-Meta-RAG 固定提交](https://github.com/mxpoliakov/Multi-Meta-RAG/tree/e77e4638cbae16fa7a63f291e73230d5bb356081)。文件同时核对完整 JSON、Git tree 中的字节数与 Git blob SHA1，并记录以下 SHA256：

| 文件 | 字节数 | SHA256 |
| --- | ---: | --- |
| `qa_output/gpt-4-voyage-02-filtering.json` | 19,059,054 | `f4ad29c607867e4ef73a3f9d5016559ee9bbcdb0331c3a05cb2948d0cf82812c` |
| `qa_output/google-palm-voyage-02-filtering.json` | 19,114,305 | `732232e2b5fcb2d4af163b3cd9de14ee3f2815d6000e1034ff5702a6882dcea0` |
| `output/voyage-02_256_32_with_filtering.json` | 34,246,706 | `3ebd57ddf6e2ea29f61d45a8e25514bbf91ae74f8c95bf020bd539b9559774eb` |

有效下载位于 `var/external-comparison/multimeta-full-e77e4638-20260928/proxy-downloads/`，获取记录为 `acquisition.json`。目录上层失败的 `.part` 文件不作为输入。

同目录的 `source-evidence/` 保存两种生成脚本、作者 QA 评分脚本、检索/建库脚本、README、许可证和 Git tree；全部代码文件对照固定 tree 的 Git blob 校验，`manifest.json` 保存哈希。

冻结数据为 HF revision `71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82`，bundle ID 为 `5f5104cf2032430aa9fccf2421268e2328328f78b7e4dadf6cb7fb27f86a5346`。本地路径：`var/benchmark-protocol-validation/multihop-acceptance-20260924/run-v2/bundle-v3`，含 2,556 题和完整 609 篇文档。

### 实际核对

两份 QA 文件与检索文件各有 2,556 个唯一 query，均按问题原文与 frozen case 一一匹配，不依赖行号碰巧相同。两种模型每题的 `gold_answer`、`question_type` 及检索文件的 gold facts 与冻结标注完全一致；这些只用于来源核对，官方评分仍从 bundle 取 gold。

每题保存的 prompt 都严格等于作者前缀 + query + 检索列表前六段，段落以 `--------------` 连接。没有用 gold 修复提示或补造检索内容。检索列表的长度为：10 段 2,402 题、9 段 6 题、8 段 9 题、7 段 2 题、0 段 137 题。原始顺序、重复项和空列表全部保留。

公开检索文件与作者脚本支持方法身份核验，但不包含可与我们完整 corpus 对照的作者索引哈希。因此“作者完整索引与 frozen corpus 相同”仍未证明。`multihop-published-ranking-v1` 明确保存这一限制，不把源文件哈希当作语料同一性的证明。

| 条件 | GPT-4 | PaLM |
| --- | --- | --- |
| 作者脚本 | `qa_gpt.py` | `qa_google.py` |
| 模型 | `gpt-4-0613` | Vertex `text-bison@001` |
| temperature | 0.1 | 0.1 |
| 生成上下文 | 同一检索结果的前六段 | 同左 |
| 输出 token 上限 | 脚本未显式设置 | 12 |
| 提示前缀 | `Below is a question…` | `You will be provided with questions…` |
| 空答案 | 0 | 30，记作 `no_answer`，原因未知，保留在分母 |

检索代码使用 `voyage-2` 与 `BAAI/bge-reranker-large`、metadata filtering、256/32 chunks。文件名中的 `voyage-02` 与代码模型名分别记录；不能把该 chunk/tokenizer 设置当作本项目 BM25 的同等配置。

### 重评分结果

固定官方版本为 `yixuantt/MultiHop-RAG@c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8`。原版 `qa_evaluate.py` 和 `retrieval_evaluate.py` 的 CLI 也直接运行在完整作者文件上，打印结果与 bridge 一致，日志保存于 `original-cli-validation/`。

| 指标 | GPT-4 | PaLM | 实际分母 |
| --- | ---: | ---: | ---: |
| upstream QA weak-match accuracy | 0.6060250391（1,549/2,556） | 0.6075899844（1,553/2,556） | 2,556 |
| Hits@4 | 0.7920177384 | 同左 | 2,255 |
| Hits@10 | 0.9042128603 | 同左 | 2,255 |
| MAP@10 | 0.3388160642 | 同左 | 2,255 |
| MRR@10 | 0.6747622567 | 同左 | 2,255 |

检索排除 301 道 `null_query`；两模型使用同一排名，因此检索得分相同。QA 为官方的弱匹配准确率，不能标成严格 EM、token F1 或人工正确率。作者 Multi-Meta-RAG 的 `evaluate_qa.py` 使用另一套双向 substring 规则，本表不是作者论文数字的复现。

两份公开答卷的 QA 总差值仅为 PaLM − GPT-4 = 4/2,556，但题型表现差异较大：

| 题型 | 题数 | GPT-4 正确数 | PaLM 正确数 |
| --- | ---: | ---: | ---: |
| inference | 816 | 776 | 751 |
| comparison | 856 | 327 | 462 |
| temporal | 583 | 149 | 265 |
| null | 301 | 297 | 75 |

这是官方指标下的观察，不是错误原因诊断。当前 MultiHop bundle 只有一个 `group_id`，现有 cluster bootstrap 明确返回 `fewer_than_two_groups`；没有改采样单位来生成显著性结论。成本、调用次数和时延没有来源观测，保持 unavailable。

### 可复现入口

新增 [`import_multimeta_predictions.py`](../scripts/import_multimeta_predictions.py) 对固定完整来源校验哈希、问题、标注及实际 prompt，保留原文件，生成显式 case map 后调用统一导入器。以下仅执行离线导入和评分；使用新的输出目录：

```bash
set -euo pipefail
MULTIHOP_BUNDLE=var/benchmark-protocol-validation/multihop-acceptance-20260924/run-v2/bundle-v3
MULTIMETA_SOURCE=var/external-comparison/multimeta-full-e77e4638-20260928/proxy-downloads
MULTIHOP_SCORERS=var/benchmark-protocol-validation/multihop-acceptance-20260924/run-v2/official-sources
REPLAY_DIR=var/external-comparison/multimeta-full-replay

for METHOD in gpt4 palm; do
  .venv/bin/python scripts/import_multimeta_predictions.py \
    --bundle "$MULTIHOP_BUNDLE" --source "$MULTIMETA_SOURCE" \
    --model "$METHOD" --output "$REPLAY_DIR/$METHOD"
  .venv/bin/python scripts/benchmark_protocol.py score \
    --bundle "$MULTIHOP_BUNDLE" --submission "$REPLAY_DIR/$METHOD/submission/submission.json" \
    --sources "$MULTIHOP_SCORERS" --output "$REPLAY_DIR/$METHOD-scored"
done

.venv/bin/python scripts/compare_benchmark_submissions.py \
  --bundle "$MULTIHOP_BUNDLE" \
  --entry "$REPLAY_DIR/gpt4/submission/submission.json" "$REPLAY_DIR/gpt4-scored/scores.json" \
  --entry "$REPLAY_DIR/palm/submission/submission.json" "$REPLAY_DIR/palm-scored/scores.json" \
  --output "$REPLAY_DIR/comparison"
```

实际本轮工件根目录为 `var/external-comparison/multimeta-full-e77e4638-20260928/`：`gpt4/`、`palm/`、`gpt4-scored/`、`palm-scored/` 和 `comparison/report.{json,md}`。输入身份见 [`multimeta-e77e4638-full.json`](../configs/comparison-scopes/multimeta-e77e4638-full.json)。

## 3. ALCE：两任务、四配置、各 100 题

### 真实答案来源

[ALCE 固定提交](https://github.com/princeton-nlp/ALCE/tree/246c476a4edfc564266b7346b6e29ef4861ae937) 的 [`human_eval`](https://github.com/princeton-nlp/ALCE/tree/246c476a4edfc564266b7346b6e29ef4861ae937/human_eval) 包含 ASQA 和 ELI5 的真实模型答案。该目录与先前核实的普通候选资料文件不同。

- 导入来源：`human_eval_citations_completed.json`，SHA256 `cfed9293752413d7c7631f36524dd4ee9ef58b209cdf9c63f6fc1e280b43cca6`。
- 同目录还存在 `human_eval_utility_completed.json`，SHA256 `1d84b8a96a4ff29dc0678b35e33ea535e9f22d2313a212e41c4ce06d7a58e5ff`；本次没有将它的人工标签导入为自动指标。
- 每个任务四个方法：GPT-3.5 VANILLA、interactive、sample4 RERANK 和 Vicuna-13B。准确方法 key 保存于 method metadata，不能用一个“ChatGPT”标签覆盖配置差异。
- 每个方法字典包含 100 道问题和 `overall_results`。聚合项显式排除，不作为第 101 道题。
- 原作者问题 ID 与当前 sample ID 不同；全部通过**原文问题唯一匹配**，保留原 ID 和显式映射。每任务的四方法确实覆盖同一 100 题。
- 该发布没有 QAMPARI 答卷。ASQA 100/948、ELI5 100/1,000 是作者选出的人工评测样本，不能外推到各自全量。

### 已重评分的文本指标

ASQA 使用固定原 `eval.py` 与 `utils.py` 的处理：首行截断、特殊终止标记清理、引用清理，以及 `compute_str_em`。全部保留空答案和 100 题分母。`str_em` 是短答案覆盖指标，`str_hit` 要求该题所有短答案组均被覆盖，不是普通单答案 EM。

| ASQA 方法（作者标签） | str_em | str_hit | 原始空答案 / 分母 |
| --- | ---: | ---: | ---: |
| GPT-3.5 VANILLA | 0.353000 | 0.090000 | 1 / 100 |
| GPT-3.5 interactive summary | 0.382000 | 0.090000 | 1 / 100 |
| GPT-3.5 sample4 RERANK | 0.365500 | 0.110000 | 2 / 100 |
| Vicuna-13B | 0.264667 | 0.050000 | 0 / 100 |

ASQA 四方法的描述性配对报告已生成。生成条件并未全部恢复，不能据此声称某算法在相同计算预算下更优。

ELI5 四方法各导入 100 题，空答案依次为 5、3、9、1。已保存预处理工件，但 `metrics={}`：ELI5 没有在此轻量路径上可冒充正确性的词面指标，claims NLI、MAUVE 等仍待真实模型评分。

### 引用与模型评分边界

原始文件的每个已记录 citation 含 title/text，可以核对候选身份。本地检查发现所有已记录 ASQA 引用及 ELI5 三种普通配置引用均能匹配候选；ELI5 interactive 有 5 条明确的 `invalid` 引用。interactive 的数字引用并不总等于当前普通候选位置，不能直接套用 `[n] → 第 n 篇候选`。

人工评测文件只保存每句前三个引用，没有完整原始 `docs` 列表，也没有完整生成提示、实际模型 checkpoint、温度及重试记录。只凭文件名或部分正确引用，不补造这些事实。

本次全部八方法统一使用 `citation_mapping_status=unavailable` 的文本导入：保留原答案和原引用，评分输入不虚构显示过的资料列表，人工/旧自动标签只在不可变原文件中保留。`--alce-full` 对这种 submission 明确拒绝，防止把“没有映射观测”误报为零引用质量。

新增 `--alce-answer-only` 入口执行同一固定原版 `eval.py`，只关闭其 `--citations` 开关。ASQA 保留 str_em/str_hit、QA、MAUVE、ROUGE-Lsum；ELI5 保留 claims-NLI、MAUVE、ROUGE-Lsum。ASQA 无需仅用于引用的 AutoAIS 模型，ELI5 的 claims-NLI 仍需要官方 `google/t5_xxl_true_nli_mixture`。两种模式互斥，scorer 身份记录模式、实际模型、依赖及参数；做比较时两侧必须选择同一模式和依赖。引用指标保持 pending，不补零；模型批次指标不伪造逐题分数或置信区间。代码/子进程链路已验证，真实模型评分尚未执行。

服务器准备好原评分依赖后，在原 `score` 命令上添加以下参数即可，无须重做生成：

```bash
--alce-answer-only --alce-python /path/to/alce-env/bin/python \
--alce-hf-cache /path/to/pinned-hf-cache --alce-nltk-data /path/to/pinned-nltk-data
```

### 可复现入口

新增 [`import_alce_human_predictions.py`](../scripts/import_alce_human_predictions.py)，一个 ASQA/ELI5 bundle 一次导入该任务的四方法。实际来源已分别复制到本轮 `asqa/raw/` 和 `eli5/raw/`，不依赖 `/tmp` checkout 的存续。

本轮 ALCE 工件根目录的 `source-evidence/` 还保存固定 README、human_eval 说明、普通 turbo 配置及许可证；它们经固定 Git checkout 字节核对。配置示例不能替代某个历史答卷的完整运行身份。

```bash
set -euo pipefail
ALCE_BUNDLE=var/benchmark-protocol-validation/alce-data-acceptance-20260924/asqa-gtr/bundle
ALCE_SOURCE=var/external-comparison/alce-human-246c476-20260928/asqa/raw/human_eval_citations_completed.json
ALCE_SCORERS=var/external-comparison/alce-human-246c476-20260928/official-sources
ALCE_REPLAY=var/external-comparison/alce-human-asqa-replay

.venv/bin/python scripts/import_alce_human_predictions.py \
  --bundle "$ALCE_BUNDLE" --source "$ALCE_SOURCE" --output "$ALCE_REPLAY"
for METHOD in vanilla interactive rerank vicuna; do
  .venv/bin/python scripts/benchmark_protocol.py score \
    --bundle "$ALCE_BUNDLE" --submission "$ALCE_REPLAY/$METHOD/submission/submission.json" \
    --sources "$ALCE_SCORERS" --output "$ALCE_REPLAY/$METHOD/scored"
done

.venv/bin/python scripts/compare_benchmark_submissions.py \
  --bundle "$ALCE_BUNDLE" \
  --entry "$ALCE_REPLAY/vanilla/submission/submission.json" "$ALCE_REPLAY/vanilla/scored/scores.json" \
  --entry "$ALCE_REPLAY/interactive/submission/submission.json" "$ALCE_REPLAY/interactive/scored/scores.json" \
  --entry "$ALCE_REPLAY/rerank/submission/submission.json" "$ALCE_REPLAY/rerank/scored/scores.json" \
  --entry "$ALCE_REPLAY/vicuna/submission/submission.json" "$ALCE_REPLAY/vicuna/scored/scores.json" \
  --output "$ALCE_REPLAY/comparison"
```

ELI5 将 bundle 换为 `eli5-bm25/bundle` 并使用新目录；可完成导入和 pending 评分工件，不宣称已有 ELI5 正确性得分。每方法的 `normalized/case-ids.txt` 可供未来 SN 生成使用，同一任务只需执行一次这组 100 题。

实际工件位于 `var/external-comparison/alce-human-246c476-20260928/{asqa,eli5}/{vanilla,interactive,rerank,vicuna}/`，包含原生来源、normalized、submission 和 scored；ASQA 比较为 `asqa/comparison/report.{json,md}`。工件在 Git 忽略的 `var/`，迁移机器须连同数据/来源另行搬运。

## 4. QMSum：Socratic SegEnc 完整 281 条测试答案

### 来源、逐行映射及比较条件

方法来源为 ACL 2023 [Socratic Pretraining](https://aclanthology.org/2023.acl-long.713/) 的作者 [Salesforce 仓库](https://github.com/salesforce/socratic-pretraining/tree/d0de964b4c26746c11f634f0c1162aa80c27baa5)。实际答卷来自作者 HF 账号的 `Salesforce/socratic-pretraining-qmsum`，revision `d127cbc54b974a58e8bd75935f2863d136e0bc3d`，文件 `test.predictions`，125,085 字节，SHA256 `8cdbbb9e2b1a6bbd8f99aa7d59b7314d3a3659914c4902b627756bbe7d69247e`。没有下载模型权重或在本机生成答案。

需区分名称：作者 README 链接的 `Salesforce/qmsum-socratic-books-30M` 在本次核查 revision `66ee0a9fe1a1594e860ad6803374c9977bcb6aea` 只有模型卡，没有权重。实际导入来源是上述包含预测、配置及权重条目的另一仓库；不因为名称相近就把二者当作同一已验证运行。

测试集为已冻结的 QMSum commit `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`，原文件 SHA256 `6bcd428211260ad2efae3af76cbaf6a7f5ae4bb5e1e59c45a4b8e89539cb9208`；35 场会议、281 题，bundle ID `ee4df555e934b415032b25ece0fdfbd006965853f2fee803822bcc6fc24beac4`。

原预测文件无问题 ID。映射依据是作者明确引用的 `query-focused-sum@8666f5023cb4932037d2b907fba9373d325d1bef` 的 `prep_qmsum.py`：按原会议文件顺序，每会议先 general、再 specific，各保留 query 顺序。`convert_qmsum.py` 按该顺序输出；Socratic 的 `MultiEncoderDataset` 按文件读取，`train.py` 按预测顺序逐行保存。导入器从冻结 raw-data 独立重建该顺序，并核对全部问题/参考答案与 frozen case；不会搜索高分排列或根据预测内容重排。第一条为第 0 场会议 general query，最后一条为第 34 场会议第 6 个 specific query。

**这是“公开代码顺序映射”，不是“恢复了作者原始运行清单”。** 原文件没有 query ID、实际输入 hash、命令行或每条生成上下文。该限制写入 method 和每题 mapping；目前支持按此公开顺序约定的描述性重评分，不宣称相同输入预算、论文表格复现或算法归因。若后续发现作者实际输入顺序与公开流程不同，必须撤销该映射并重评。

公开流程使用完整会议的预处理文本：去除 `{...}`、空 utterance，规范 AMI/LCD/PMS/TV 缩写，加 `speaker: `，连接全篇；不按 gold relevant spans 选取。示例配置为 32 个重叠 chunk、source length 512、generation max length 512、seed 1；这是代码条件，不能冒充该历史预测的实际覆盖配置。Gold reference 仅用于映射核对和评分，不进入 normalized prediction。

### 实际重评分

原发布附带的 `test.predictions.rouge` 报告 38.48 / 13.90 / 33.62，采用 Stanza + SummEval。这些数字仅为来源观测，不直接导入我们的得分。本次对原预测使用与 SN 一致的固定 Perl ROUGE-1.5.5 + HMNet regex 分句 profile 重新执行：

| 指标 | 本次得分（0–1） | 百分制 | 分母 |
| --- | ---: | ---: | ---: |
| ROUGE-1 F1 | 0.38955 | 38.955 | 281 |
| ROUGE-2 F1 | 0.13960 | 13.960 | 281 |
| ROUGE-L F1 | 0.33942 | 33.942 | 281 |

281 条均有非空答案，0 missing/error。空缺不被丢弃。分句/词元处理不同可造成与作者文件的数值差异；本项目得分不写成论文成绩。Perl 的汇总估计和逐题均值也保持区分。尚无完整 SN 答卷，因此没有制造 QMSum 的 SN 配对报告。

工件根目录 `var/external-comparison/qmsum-socratic-d127cbc5-20260928/`：

- `imported/raw/test.predictions`：原始发布字节。
- `imported/normalized/`：逐题预测、显式 case map、method、来源、281 题题单。
- `imported/submission/submission.json`：统一答卷。
- `scored/scores.json`、`scored/rouge/`：真实 Perl 输出、逐题分数、分母及依赖。
- `source-evidence/`：固定作者代码、预处理脚本、HF 元信息、原得分旁证及哈希清单。

复现入口（输出必须是新目录；Perl 依赖位置使用实际本地安装）：

```bash
QMSUM_BUNDLE=var/benchmark-protocol-validation/bundles/qmsum-v3
QMSUM_SOURCE=var/external-comparison/remaining-methods-20260928/socratic-test.predictions
QMSUM_REPLAY=var/external-comparison/qmsum-socratic-replay

.venv/bin/python scripts/import_qmsum_socratic_predictions.py \
  --bundle "$QMSUM_BUNDLE" --source "$QMSUM_SOURCE" --output "$QMSUM_REPLAY/imported"
.venv/bin/python scripts/benchmark_protocol.py score \
  --bundle "$QMSUM_BUNDLE" --submission "$QMSUM_REPLAY/imported/submission/submission.json" \
  --sources var/external-comparison/alce-human-246c476-20260928/official-sources \
  --rouge-home /path/to/ROUGE-1.5.5 --output "$QMSUM_REPLAY/scored"
```

本机真实执行复用了既有校准的 `rouge_home` 与 `PERL5LIB`，见 `scored/rouge/manifest.json`；没有重新安装系统依赖。

## 5. HotpotQA 全量协议校准与 KG2RAG 受控入口

### 完整真实输入与原生答卷

已取得 [HF 固定版本](https://huggingface.co/datasets/hotpotqa/hotpot_qa/tree/1908d6afbbead072334abe2965f91bd2709910ab) 的 `distractor/validation-00000-of-00001.parquet`（27,452,575 bytes，SHA256 `c20b638ca82b21d04fe12e14ff417ad05153d4d215a65de54497fca4e972f7c6`）。转换为官方 JSON 形状后，与 KG2RAG 作者仓库的数据副本全部行及顺序一致。

完整 bundle 位于 `var/benchmark-protocol-validation/hotpot-full-20260928/bundle`，ID 为 `1136533bc2a9a4e6f52092df7d3e0e9ff21c8ce9b35dc637336eb6735b740699`，含 7,405 题/73,700 段落。35 个空字符串和 14 个纯空白字符串原样保留，不挤掉后续句子编号。题 `5ae61bfd5542992663a4f261` 的 gold 包含 `["Jimmy Butler (basketball)",902]`，对应 context 只有 5 句；该 tuple 原样参与官方评分，另记为未映射，不修标注、不删题。

`import_hotpot_predictions.py` 接受官方 `{"answer": {qid: text}, "sp": {qid: [[title, sent_id]]}}`，通过精确原问题 ID 导入。method 必须声明 `configuration.benchmark_setting=distractor`；格式本身不证明生成条件。未知/重复预测 tuple 保留，缺少 `sp` 不补成空列表，缺少答案仍为 missing。实际外部 Hotpot 答卷尚未取得。

### 评分验证范围

在全量数据上构造明确标注的合成扰动：每第 11 题改错答案、每第 7 题少报一个 supporting fact、每第 13 题增加不存在的 supporting tuple。与未修改的官方 `hotpot_evaluate_v1.py` CLI 对照，12 项指标最大绝对差为 `1.7763568394002505e-14`，分母均为 7,405。文件 SHA256 为 `d35fc91a6db21d791dbdda11daf3856e9359f5701d54e3eefba20d88fecc02c0`；运行依赖 `ujson==5.11.0` 安装在本次隔离目录。证据为同目录 `validation.json`、`original-cli.stdout.txt` 和 `calibration-scored/`。

这验证真实输入下的评分一致性，**不是任何模型成绩**。SN supporting-fact projection 后续已实现，见下文；真实模型执行与服务器回放仍 pending。

### KG2RAG 接入为何需要适配

固定作者代码为 [nju-websoft/KG2RAG@7d626c77](https://github.com/nju-websoft/KG2RAG/tree/7d626c77b7af30b55aa3f960cde755b9549a0616)。本轮真实输入核查发现：

- QA prompt 直接包含当前 validation 的四道题和答案：`5a8b57f25542995d1e6f1371`、`5a8c7595554299585d9e36b6`、`5a87ab905542996e4f3088c1`、`5a7be2595542997c3ec972ac`。KG 提取提示也使用其中相关事实作示例。
- 66,581 个唯一标题中，54 个标题对应多个不同句子列表；原来的 title-only KG cache 会把某题的句子图用于另一份内容。不能凭标题相同认定 context 相同。
- 原 query 异常转成空答案，无法区分模型拒答与执行失败；且 reranker 写死 `device=3`，而所声明的 FlagEmbedding 1.3.4 实际接收 `devices`，原参数不能有效控制设备。

[`run_kg2rag.py`](../scripts/run_kg2rag.py) 复用固定作者的向量检索、KG 扩展、图过滤和生成代码，集中作以下适配：QA 使用已冻结的前四条 train 示例，并检查与完整 validation 的 ID/问题不重叠；KG 提取移除 validation 示例，保留结构格式要求和作者 parser；缓存绑定完整 title+sentences 且只在同一 run 内共享；异常写入 error，逐题刷新事件；使用正确的 `devices` 参数传递显式设备选项。实际 reranker 文件哈希和相关包版本进入方法身份；Ollama 名称目前仍是配置 tag，尚未观测服务端 digest，这个限制单独记录。生成侧文件只包含 `_id/question/context`，没有答案、supporting labels 或题型。训练示例来自 HF train rows API 的哈希快照，该 API 不提供固定 revision，本项目不虚构其版本号。

因此方法名必须带 adapted/controlled 标识；**不是原论文成绩复现**。KG 提取提示变化会影响图质量，QA 示例变化也可能影响得分，应作为条件差异报告。完整模型/权重与依赖的服务器执行尚未验收。

已实际准备完整 7,405 题的无 gold 输入及适配源码：`var/external-comparison/kg2rag-controlled-20260928/prepared-final/`，未导入模型依赖或调用生成模型。准备命令：

```bash
.venv/bin/python scripts/run_kg2rag.py \
  --bundle var/benchmark-protocol-validation/hotpot-full-20260928/bundle \
  --upstream /path/to/KG2RAG-7d626c77b7af30b55aa3f960cde755b9549a0616 \
  --output /fresh/path/kg2rag-prepared --prepare-only
```

服务器执行时使用新的 output，去掉 `--prepare-only`，增加 `--method METHOD.json --reranker /local/path/bge-reranker-large`，必要时传 `--model-name`、`--embed-model-name`、`--reranker-device`、`--top-k`、`--case-id-file`。method 使用现有 submission 的六字段结构，`kind=reference`、`citation_style=none`、`configuration.comparison_category=controlled-rerun`，如实填写模型身份。`submission.json` 直接进入现有 score/compare；`events.jsonl`、`predictions.json` 和 run manifest 保留错误与实际范围。本地已逐行核对全部公共输入与官方 context 完全一致；使用替身依赖执行适配后的真实 `process_sample` 和提取函数，验证正常答案、句子编号、错误保留及 `devices` 参数。该验证没有运行真实 LlamaIndex/FlagEmbedding 或模型，记录为 `plumbing-validation.json`。准备结果不是模型验收，不支持现在就填入成绩表。

### SN supporting-fact 投影

新增 [`hotpot_evidence.py`](../src/rag_eval/hotpot_evidence.py) 接入 `notebook_runner`、SN 导出和官方评分。它只读取最终答案的实际 citation anchor、作者运行时观察到的 source chunk/element、公开文本偏移和冻结 sentence units；不读取 gold supporting facts，也不把检索命中或最终上下文覆盖当作预测。一个引用覆盖多个可见句子时全部作为预测，交给官方集合 precision/recall 扣除误选；未知引用写入确定的无效 tuple。缺失 capture、来源不唯一或偏移无法还原时保留答案，Supporting Fact/Joint 不报部分分数。

真实 HotpotQA distractor bundle 的 73,700 个公共段落经过项目实际 parser/chunker：92,312 个 chunk 的可见源区间逐一与独立句子区间计算一致，92,312/92,312 通过；49 个全量空白句位仍保留原编号。另有 79 个文档含 81 个 parser 不产生的句位：54 个是 `<ref>`、`<br>` 等纯 HTML 标记，27 个是数字或星号被解释为空列表标记，不能被 SN 观测，报告中保留为不可观测，不推断其支持关系。产物为 `var/benchmark-protocol-validation/hotpot-evidence-20260928/corpus-report.json`，只含合成引用，不是模型成绩。

正式 SN 运行会在生成后保存 `hotpot_evidence` 快照；`export-sn` 和 `prepare_inputs` 重新验证快照身份后才允许 supporting-fact 指标。本轮 7 项定向测试和独立审查已完成，全量回归 760 passed、2 skipped。当前真实服务器 run 尚未回放，因此 HotpotQA 的 SN Supporting/Joint 仍 pending；Answer EM/F1/Precision/Recall 可先独立比较。

## 6. QASPER：LAB LongChat citation 受控方法

方法来源为 [Attribute or Abstain（EMNLP 2024）](https://aclanthology.org/2024.emnlp-main.463/)，固定作者代码 [UKPLab/emnlp2024-attribute-or-abstain@6c663ed6](https://github.com/UKPLab/emnlp2024-attribute-or-abstain/tree/6c663ed61521dd0032ca41af3dc503e7c2e13dac)。作者 `experiments/templates/citation_runs.txt` 明确包含 QASPER + `longchat-7b-v1.5-32k`。本项目新增 [`run_lab_qasper.py`](../scripts/run_lab_qasper.py)，复用作者 `CausalLMForExtractionModel` 的输入准备、生成、答案及引用解析；不使用作者评分 callback，不把 LAB 论文表格视为本项目成绩。

### 真实数据与可比范围

通过公开 ZIP 的字节范围，仅提取 QASPER 两个成员，并校验 ZIP CRC32、长度和 SHA256，没有下载完整 1.41GB 包：

| 成员 | 篇数 | SHA256 |
| --- | ---: | --- |
| `QASPER-ITG/deep-test.jsonl` | 416 | `0b5d84987791e9da68aa96605407cc887d777407d25a6a06ebd464600471ab66` |
| `QASPER-ITG/deep-train.jsonl` | 888 | `94cbc6cae8996ee7cda609a8ede286c5234610d7bb7b88b40dc75e358b03fab8` |

测试集的全部论文 ID、1,451 个问题及完整标注与现有 QASPER v0.3 bundle 一致。每篇 ITG 的 `p` 节点，严格按顺序等于原 abstract + full_text 段落去掉首尾空白后的内容；caption 节点也按顺序对应原 caption。全体段落/caption 中有 1,894 个节点发生了首尾空白修剪。证据回映使用顺序和原字符串，避免重复/空白段落造成文本匹配歧义；独立复核覆盖 20,637 个 `p` 节点。

输入准备保留文章标题、结构、节点 ID、顺序和正文，移除 `document.meta.qas`、节点 `is_evidence_for` 及测试 `free_text_answer/answer_type/extraction_nodes`。训练侧只保留作者预定的三个演示实例及其训练标签，与测试数据分开：原扁平化训练索引 **11、0、17**（每个 answer annotation 一个实例，不是每道题一个实例），传给模型时映射成 **0、1、2**。不按测试得分选示例。

### 原方法设置及明确差异

- 作者 LongChat 配置实际输入上限是 **16,000 tokens**，不能因模型名含 32k 改成 32k；输出上限 **100 tokens**、bf16、greedy、seed 635191、三个训练示例。
- 保留作者 citation 的 `answer_and_segments`、`node_id`、`text` 格式，以及其 task/prompt、示例裁剪和 tokenizer 截断。明确选择单题 Hugging Face 路径，防止安装 vLLM 后作者配置自动改引擎和批量大小。
- 原 QASPER prompt 的 node types 包含 title/abstract/headings/paragraphs，**不含 figure captions**；SN 的完整公开材料可包含 captions。因此这是各系统声明条件下的比较，不是完全相同生成输入的算法归因。
- 作者 parser 将 `unknown` 等关键词映射成 `unanswerable`，并清空证据；适配器把这个非空答案保留为 success，由官方 QASPER F1 判分。空输出、执行失败和未执行仍区分。
- 作者 parser 在截断前的完整节点映射中解析引用、去重，忽略未知编号；该行为保持不变，原始生成和解析后节点都保存。结构节点引用转为确定的无效证据项，段落/caption 引用回到原始字符串；不把完整上下文当预测证据，不事后补正确引用。
- NLTK 分句在加载权重之前预检；避免整批模型生成后才因缺分句资源失败。模型文件、依赖、分句资源、提示/代码和演示身份均记录，实际 token IDs 和解码输入逐题保存。

输出统一为现有 submission，再走固定 QASPER 官方 scorer。**这不是原论文得分复现**：作者的 LAB metrics/输入选择、这里的官方评分和服务器实际运行条件必须分别记录。当前没有真实 LongChat 答卷或 SN 对比数字。

### 本地验证及执行入口

完整准备结果：`var/external-comparison/lab-qasper-citation-20260928/prepared-final/`。70 个作者代码/配置文件逐一验证了 Git blob 和锁定 SHA256；`source-acquisition.json` 保存数据获取记录。

`offline-validation.json` 记录实际加载原版 Intertext Graph（固定 commit `2516bd20a7153e825b89206dac0ebeb7d1ab7302`）、Hydra/OmegaConf 配置、作者数据类和解析函数的验证：416 篇去标签前后渲染相同，1,451 个无 gold 实例可加载，三个演示与原作者创建函数相同，实际 parser 的不可回答和未知引用行为已确认。为避开本地 Torch/模型依赖，数据类按原 AST 独立加载；这不等同于完整模型栈已安装或运行。`validate_offline.py` 留存了验证代码。本轮没有模型或 tokenizer 权重下载。

```bash
.venv/bin/python scripts/run_lab_qasper.py \
  --bundle var/benchmark-protocol-validation/bundles/qasper-v3 \
  --upstream /path/to/emnlp2024-attribute-or-abstain-6c663ed61521dd0032ca41af3dc503e7c2e13dac \
  --test-itg var/external-comparison/remaining-methods-20260928/lab-qasper-deep-test.jsonl \
  --train-itg var/external-comparison/remaining-methods-20260928/lab-qasper-deep-train.jsonl \
  --output /fresh/path/lab-qasper-prepared --prepare-only
```

服务器使用新的 output，移除 `--prepare-only` 并提供 `--model-path /local/longchat-snapshot --method METHOD.json --nltk-data /local/nltk_data`。method 的 `kind=reference`、`citation_style=none`、`configuration.comparison_category=controlled-rerun`，其余字段如实填写；必要时加显式 `--case-id-file`。模型必须是本地 snapshot，HF 强制离线。`events.jsonl` 逐题保存错误及答案，`submission.json` 接入现有 score/compare；尚未验收真实模型依赖、tokenizer、CUDA 和生成。

## 7. ALCE：完整答案与引用的 VANILLA 受控方法

公开 human-evaluation 答案没有完整 shown-doc 列表，不能据此恢复引用评分。新增 [`run_alce_vanilla.py`](../scripts/run_alce_vanilla.py) 从头运行作者 VANILLA 方法，生成时同步保存实际输入与引用映射；不修改或补造旧样本的文档列表。

`var/external-comparison/alce-vanilla-20260928/*/author-main-validation-final/result/` 中现有的 JSON 只来自 `validate_offline.py` 的 synthetic offline validation，字段里的 `output` 明确标记为 synthetic；它们用于校准输入格式，不能作为 Llama-2 模型答卷导入或评分。真实 VANILLA 结果必须在服务器提供实际模型快照后重新生成。

复用 [ALCE@246c476 原代码](https://github.com/princeton-nlp/ALCE/tree/246c476a4edfc564266b7346b6e29ef4861ae937) 的 `make_doc_prompt/make_demo` 和 `LLM.generate` HF 分支。八个源码、prompt、YAML 文件逐一对照固定 Git tree 的 blob，SHA256 固定在 [`alce-vanilla-source-lock.json`](../configs/alce-vanilla-source-lock.json)。仅加载这些定义，不启动旧 API、交互检索或作者主程序的 gold 日志。

| 任务 | 固定普通候选 | 完整题数 | 实际送入生成模型 |
| --- | --- | ---: | --- |
| ASQA | GTR | 948 | 原顺序前 5 篇完整 passage |
| ELI5 | BM25 | 1,000 | 同上 |
| QAMPARI | GTR | 1,000 | 同上 |

按作者 `llama2_shot2_ndoc5_*_default.yaml`：Llama-2-70b-chat-hf 配置、2-shot、NumPy seed 42 抽出示例索引 `[1, 3]`、temperature 1.0、top_p 0.95、最多 300 新 tokens、总预算 4,096。作者的四个候选示例均与本任务完整评测题的规范化问题无重合。生成请求只有问题和前五篇文档的 title/text，gold、辅助答案和候选中的标注字段不进入请求。保持原版普通文本提示，不额外套 chat template 或本项目 reference 的 JSON 回答要求。

每题保存 prompt、实际 input token IDs、所见文档、原始生成、清理后的答案、耗时和状态；`citation_index_to_document_id` 将 `[1]` 至 `[5]` 对应到原始 candidate ID。评分桥接仍保留完整候选编号：例如模型引用未见过的第六篇，转换为官方候选范围外的无效引用，不因该候选在数据包中存在就算有效。`submission.json` 进入现有官方 score/compare；`native-output.json` 和逐行刷新的 `events.jsonl` 保留原始观测。

**明确适配与限制：** 本入口使用本地模型快照、fp16 和自动设备放置，不复用作者按 GPU 空闲显存预留的加载策略；增加 Torch seed 42（原版只固定 NumPy）；剩余输出预算按包含 BOS 的实际输入 token 数计算，修正作者 `tokenize()` 估计少算开头标记的问题。不截断长提示，预算耗尽标为 error；模型确实生成空文本才是 no_answer，异常单独记录类型和阶段。模型文件哈希、依赖版本和以上差异进入方法身份。使用其他 Llama 快照时必须在 method 中如实更名，不能沿用原模型成绩标签。本入口不支持旧 OpenAI API 分支；这是声明条件下的受控重跑，不是原论文分数复现。

完整准备工件为 `var/external-comparison/alce-vanilla-20260928/{asqa,eli5,qampari}/prepared-final/`。`validate_offline.py` 实际执行固定作者 `main`，只替换生成模型为无模型的固定输出，对全部 **2,948** 题确认 prompt 和 shown-doc 顺序/内容完全一致；另执行原 HF generation 分支，使用替身 tokenizer/model 核对生成参数、停止符及输出清理。结果在 `offline-validation-final.json`。这没有执行真实 Torch/Transformers/权重/tokenizer，也没有产生模型成绩。独立只读审查发现的 BOS 预算问题已修复，4 项定向测试通过，无剩余重要发现。

```bash
.venv/bin/python scripts/run_alce_vanilla.py \
  --bundle var/benchmark-protocol-validation/alce-data-acceptance-20260924/asqa-gtr/bundle \
  --upstream /path/to/ALCE-246c476a4edfc564266b7346b6e29ef4861ae937 \
  --output /fresh/path/alce-asqa-prepared --prepare-only
```

ELI5/QAMPARI 分别改用 `eli5-bm25` / `qampari-gtr` bundle。服务器正式执行使用新 output，去掉 `--prepare-only`，增加 `--model-path /local/model-snapshot --method METHOD.json`；可用 `--case-id-file` 选择明确子集。method 沿用六字段结构，例如下面的声明；实际模型快照若不同，先更正名称与 `declared_model`，运行时再补文件身份：

```json
{
  "name": "alce-vanilla-llama2-70b-hf-control",
  "kind": "reference",
  "citation_style": "numeric",
  "model_identity": {"declared_model": "meta-llama/Llama-2-70b-chat-hf"},
  "input_policy": "ordinary-candidates-first-five; author-two-shot",
  "configuration": {"comparison_category": "controlled-rerun"}
}
```

后续使用固定 `benchmark_protocol.py score --alce-full`，两侧采用同一 scorer 环境。当前新增的是可生成真实引用答卷的入口；AutoAIS、ASQA QA/MAUVE、ELI5 claims 等真实模型评分及 SN 对比仍待服务器执行。旧八份 human 样本仍维持 answer-only 限制。

## 8. 其余候选的可用性核查

本次固定来源与访问证据均保存在 `var/external-comparison/remaining-methods-20260928/`，包括仓库 archive、commit/API 元信息及哈希。下载链接写在 README 中不等于实际可用。

| 候选 | 2026-09-28 核实事实 | 当前处理 |
| --- | --- | --- |
| QASPER LED | `allenai/qasper-led-baseline@afd0fb96bf78ce8cd8157639c6f6a6995e4f9089` 有训练代码，releases 为空，未取得训练好的模型/完整逐题输出；predictor 读取 `predicted_evidence`，model 没有写出此字段，且答案生成在 `answer is not None` 分支内 | 保留 published-reference；不能直接宣称无 gold 推理入口可用，也不能以基础 LED 权重冒充 QASPER 微调基线。继续查有真实输出的论文方法 |
| 原 SegEnc | `salesforce/query-focused-sum@8666f502…` 的 run-1 Google Storage 模型链接 HEAD/GET 均 403 `AccessDenied` | 保留受阻候选；不下载或伪造权重，已取得的 Socratic 答卷作为独立方法 |
| SummN | `psunlpgroup/Summ-N@05ea309fce2d18d940a7022a6acde050aa71463f` 的官方 Drive 可取得 `QMSum_full.hypo`；279 行，SHA256 `92b3090f96ae212f8d0e9a9f5cf9c3b2bdba497b84648dd8416101b2ec4a0f26`；当前 loader 的 `load()` 首行直接抛 `NotImplementedError` | 保存来源，不强行按行映射到 281 题；尚无题级配对资格。`QMSum_gold.hypo` 明确属于另一条件 |
| HGN | `yuwfan/HGN@c95f1dcc5ab81d4b6cf0f43a7618317c6d0042dc` 的 Azure 包返回 409“Public access is not permitted”；`eval_model` 在同一 dev gold 上搜索 support threshold 并按 joint_f1 选最好结果 | 不能称为预冻结阈值的盲评；需可用 checkpoint 加独立校准/显式调参声明，或换候选 |
| SAE | 官方 README 的 Google Drive checkpoint 文件页返回 404 | 未取得 checkpoint，不报已接通 |
| IRCoT | 作者 README 的主实验是开放检索，依赖 Wikipedia 索引与自己发布的 dev/test 子集 | 不静默替换当前 HotpotQA distractor validation 轨道 |
| LAB（QASPER） | 固定作者代码 `6c663ed61521dd0032ca41af3dc503e7c2e13dac`；大学数据接口可访问，按字节范围检查 1.41GB ZIP 的全部 15,834 个目录项，results 目录为空，results.csv 是无 hash/分数的模板；同 bundle 仅 code.zip/data.zip | 未取得已发布的 QASPER 答卷；已另行准备 LongChat citation 受控入口，见 §6。未下载整个包；证据 `lab-acquisition-audit.json` |
| KG2RAG | 官方 `nju-websoft/KG2RAG@7d626c77b7af30b55aa3f960cde755b9549a0616` 提供 distractor 代码和原始数据；没有已生成预测，KG 需按公开 context 生成，默认 Llama3:8b/mxbai/BGE reranker | 受控入口与完整公共输入准备已完成，适配差异见 §5；真实模型执行未完成 |

## 案例复核与解释

新增 `review_benchmark_comparison.py` 从有效 comparison 生成确定性复核包，保留原答案、参考分组、实际证据、分数与空白人工记录。已为 MultiHop 两方法生成 45 题、ALCE 四方法生成 72 个配对项/23 题；完整题型统计显示 MultiHop 总分仅差 4 题，但 758 题的官方命中不同。具体字符串匹配与语义区别、命令和限制见[案例复核文档](notebook-comparison-case-review-2026-09-28.md)。这些不是 SN 成绩或独立人工结论。

后续已把题型统计接入同一入口：报告独立列出全部合格题目的两侧逐题均值、分差、实际分母和四种方向计数，不从抽样案例估计总体，也不替代原 batch 分数。新工件位于上述两来源目录的 `case-review-task-summary/`；原 comparison、抽样选择和案例内容未改。MultiHop 四个题型的计数与此前独立分析相同，ALCE 六组方法对均使用冻结的 100 题。

## 9. 2026-09-28 真实 smoke 验收

### QASPER：SN chunk 与 BM25 同题比较

在服务已就绪的本地服务器上，SN chunk 对 smoke 题单的 5 个 QASPER case 分别运行了独立 attempt。每个请求使用 `notebook-request-v3`，不携带答案或证据 gold 字段；随后从五个 run 导出严格 5 题 subset submission，并使用固定的 QASPER LED evaluator `afd0fb96bf78ce8cd8157639c6f6a6995e4f9089`（SHA256 `781aba7cd8e524bef4f0a1b4bf3504e5b02cb1d8d5bf32a8f0a89dfa83e86bfe`）评分。

| 方法 | 成功/计划 | answer F1 | evidence F1 | 分母 |
| --- | ---: | ---: | ---: | ---: |
| SN chunk | 5/5 | 0.474510 | 0.400000 | 5 |
| BM25 reference | 5/5 | 0.400000 | 0.400000 | 5 |

比较报告位于 `var/external-comparison/campaign-20260928/qasper-smoke-comparison/report.{json,md}`。两边使用同一 bundle、同一 case IDs 和同一 scorer，故这是一份有效的 5 题配对 smoke；它不能外推到 1,451 题 test 集，也不能替代服务器 full run。SN subset submission 和评分分别位于 `qasper-sn-smoke-subset-submission/`、`qasper-sn-smoke-subset-score/`。之前的 `qasper-sn-smoke-score/` 是 1/1,451 的 full-scope 诊断，不能与本表混用。

### MultiHop：准备成本和内容过滤边界

MultiHop frozen bundle 含 2,556 题和 609 篇资料。SN smoke 即使只选 4 题，也必须在隔离 run 中导入完整资料集；本次观察到导入速度约为每 5 秒 6--7 篇，估计本地 smoke 需要较长时间，因而停止了该 attempt。目录 `var/external-comparison/campaign-20260928/multihop-sn-smoke/` 保留 `interrupted` 状态，不能作为成绩。

BM25 reference 已生成 4 题 submission，但 Qwen 服务对 `multihop_rag:3` 的输入持续返回 `data_inspection_failed`，重试 attempt 仍相同。其余 3 题成功，故评分 coverage 为 3/4、`generation_complete=false`；该结果不能进入正式比较。失败原因属于当前模型服务的内容过滤响应，不能静默改题、删题或把 error 当作 no-answer。待服务器使用可处理该题集的模型服务后，应以同一 4 题题单重新执行 SN 与 BM25，再比较。

## 10. 后续工作与未完成项

1. MultiHop 的外部主范围已具备全量 QA 与检索得分；SN chunk run 现在会保存同一 bundle 的 native selection snapshot，并可在服务器同一版本 scorer 下重放官方 Hits/MAP/MRR。只有 snapshot 完整覆盖全部非 null 题时才进入主表；reasoning run 没有单一排名，不能把最终上下文 coverage 放入外部 Hits/MAP/MRR 表。
2. ALCE 可先在同一 100 题上比较 ASQA 文本结果；仅答案模型评分入口已实现；新 VANILLA 受控入口可保存所见文档和引用映射，覆盖三任务完整 2,948 题。真实生成和模型评分仍未执行；旧公开样本继续限制为 answer-only。
3. QMSum Socratic 的 281 题导入和真实 Perl 重评分已完成；在公开顺序约定及其限制下，等待同题 SN 输出。QASPER 已有 LAB LongChat citation、HotpotQA 已有适配后的 KG2RAG 运行入口；两者真实依赖与模型执行仍待服务器验收；QMSum HMNet gold-input 维持校准用途。
4. 正式 SN 比较、完整五套结论与算法收益归因仍未完成。现有结果不支持“SN 超过外部方法”。

本次验证包含完整真实文件导入、固定官方脚本重评分、原版 MultiHop CLI 对照、两份实际配对报告和代码回归；QASPER smoke 已调用当前 Qwen 服务并保留原始运行日志，未改动 Dashboard。新增 KG2RAG/LAB/ALCE VANILLA 执行模式未来会调用模型，只有 `--prepare-only` 保证不导入或启动它们。最新回归计数见[评测状态](evaluation-status.md)。
