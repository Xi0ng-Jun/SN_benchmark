# 外部方法来源审计（2026-09-26）

本文记录本地已经取得的 QMSum 和 ALCE 外部资料，判断它们能否作为 Silicon Notebook 的同题外部方法对照。本文只记录可由当前 checkout 和 `var/` 产物直接核实的事实；文件存在不等于已经运行了方法。

**2026-09-28 后续进展：** 已取得并重评分 Multi-Meta-RAG 两模型完整 2,556 题及排名；ALCE `human_eval` 中的八份真实答卷已导入，ASQA 文本指标及四方法描述性比较已生成。见[最新结果与可复现入口](notebook-external-results-2026-09-28.md)。下文“没有 ALCE 答卷”和 MultiHop 146 题限制是当时的来源检查结论，已被新来源补充；QMSum gold-input 排除结论保持有效。

## 判定规则

一个资料只有同时满足以下条件，才能进入同条件方法比较：

1. 每个输出能映射到当前 frozen bundle 的唯一 `case_id`，范围和顺序可复查；
2. 模型可见输入、检索条件、上下文权限、预算和提示身份已核实；
3. 使用当前协议固定的官方 scorer，且逐题状态和分母完整；
4. 输出属于 ordinary 条件。gold span、oracle reranking、校准 fixture 和论文汇总分只能作为参考或校准，不能冒充 ordinary 方法结果。

问题、标签和 scorer 已核对，但生成条件仍有缺项时，可以报告公开答卷的同题描述性分差，并显式列出缺项；不能升级为同条件比较或算法归因。最新报告按这一层级区分结果，不把缺失来源条件填成 verified。

## QMSum：HMNet 公开答卷

### 已核实的来源

本地资料位于 `var/qmsum-official-calibration/`，主要身份如下：

| 项目 | 身份 |
| --- | --- |
| QMSum 数据源 | `test.jsonl`，35 场会议、281 条 query，SHA256 `6bcd428211260ad2efae3af76cbaf6a7f5ae4bb5e1e59c45a4b8e89539cb9208` |
| HMNet 输出 | `refs.txt` 与 `preds.txt`，各 279 行；SHA256 分别为 `32ff266e626e8492ca10e9ddbef046f32621d78f385f59b9c768f368384cd467` 和 `c2c4ed2cc5be7eb7ea1ee843f05570c18a2562f6908de64c661b4a8d354a3fd9` |
| 代码归档 | `hmnet.tar.gz`，SHA256 `251dd69acd5ef52fe155043e373f4269c7ee90762328b6cec82e4c521aff94bc`；归档目录名含 HMNet commit `416966c63e3cb7a57dc59b4ce8fa76f11fd048be` |
| 作者来源 | [QMSum issue #5](https://github.com/Yale-LILY/QMSum/issues/5#issuecomment-890003212)，本地校准报告记录为 `author_source` |
| 评分实现 | QMSum commit `83d7768c1f2b4dfeb091385d3dc7e239b8e5bb7e`、pyrouge commit `08e9cc35d713f718a05b02bf3bb2e29947d436ce`；HMNet 内置 ROUGE-1.5.5 与作者确认的 Perl 参数 |

`verified-calibration/hmnet_regex/inputs.json` 的每个对象都具有 `case_id`、`prediction`、`reference` 三个字段，case ID 从 `hmnet-gold-input:0` 到 `hmnet-gold-input:278`。`verified-calibration/report.json` 将来源明确记录为 `published_input: HMNet with gold relevant spans`，并记录官方论文表中报告的 36.51/11.41/31.60（ROUGE-1/2/L）。因此本次审计按 gold relevant spans 的 oracle-input 条件登记；它不能当作只给会议全文的普通端到端运行。

归档中的 HMNet README 和 `Evaluation/ROUGEEval.py` 也显示作者评测流程使用 HMNet 的预处理/分句和 ROUGE-1.5.5。当前本地校准调用的命令身份为：

```text
ROUGE-1.5.5.pl -c 95 -r 1000 -n 2 -m -d -a <settings.xml>
```

### 映射和评分结果

当前脚本以 ASCII 字母数字小写归一化摘要文本，只作身份诊断，不参与评分：

| 检查 | 结果 |
| --- | ---: |
| 当前 frozen test query | 281 |
| HMNet 公开输出 | 279 |
| 唯一匹配 | 273 |
| 未匹配的公开行 | 6（行 81--85、129） |
| 未匹配的当前 query | 8（`qmsum:5:general:0`、`qmsum:5:specific:0..5` 共 7 条，另有 `qmsum:16:specific:2`） |

本地独立 Perl 重跑的 HMNet gold-input 校准分数为：

| 预处理/文件顺序 | ROUGE-1 F | ROUGE-2 F | ROUGE-L F |
| --- | ---: | ---: | ---: |
| HMNet sentence regex | 36.464 | 11.374 | 31.558 |
| 仅换为换行分句 | 36.464 | 11.374 | 22.498 |
| 同序 `SEE/SPL` 文件 | 36.464 | 11.374 | 31.558 |
| MatchSum 文件名排序诊断 | 36.515 | 11.421 | 31.599 |
| 论文报告值 | 36.51 | 11.41 | 31.60 |

文件名排序会改变 bootstrap 的样本映射，因此最后一行诊断接近论文值不能证明输入和官方运行已被严格复现。`report.json` 的 `strict_published_score_reproduction` 为 `false`。

### 准入结论

HMNet 资料的最终分类是：

- `published-reference`、`calibration-only`、`oracle-input`；
- 可用于验证 QMSum ROUGE-1.5.5 封装、分句和文件顺序的校准链路；
- 不能导入为当前 281 条 test 的普通 `recomputed-subset`，不能据此给 SN 排名；
- 273 条文本匹配也不能消除 gold span 条件和 279/281 范围差异。

因此登记表中的 `qmsum.author_model_output` 保持不进入 paired comparison；当前不需要为它编写预测导入 adapter。

## ALCE：普通候选资料与 oracle 资料

### 已核实的来源

本地归档为 `var/benchmark-protocol-validation/vpn-acquisition-20260924/ALCE-data.tar`，归档 SHA256 为 `eda837bf659a91b3648dc6e7ab6b17197664d93593857e8fdf3800b6aa6a98f0`。固定身份为：

- ALCE 官方代码 commit `246c476a4edfc564266b7346b6e29ef4861ae937`；
- ALCE 数据 revision `334fa2e7dd32040c3fef931a123c4be1a81e91a0`；
- 本地验收报告：`var/benchmark-protocol-validation/alce-data-acceptance-20260924/report.json`；
- 官方 `eval.py` SHA256 `e0f63bf865cacc64d7390fc62669c82b2653b1628f40051de0efdf5518064091`，`utils.py` SHA256 `92e6900b5350f7da4dc179f3d9f498f73d976377a412e8c244db8339ad7976b9`。

归档包含 5 个 ordinary 候选文件和 3 个显式 oracle 文件：

| 条件 | 文件 | 题数 | 字节数 | SHA256 |
| --- | --- | ---: | ---: | --- |
| ordinary | `asqa_eval_dpr_top100.json` | 948 | 83,625,528 | `221b4a7fc074346096cf6298319feb635256ec12c5cdf10aae528402ee39c252` |
| ordinary | `asqa_eval_gtr_top100.json` | 948 | 84,762,794 | `d72737ce7d46629d3504fc508f29ec0c270e0ddd5cff3fad69625d2c0e4be35b` |
| ordinary | `qampari_eval_dpr_top100.json` | 1000 | 85,769,271 | `a10a96fb19593ead32dbc53758f829c6040ea2f5b3eccca5dba8da10f96f9412` |
| ordinary | `qampari_eval_gtr_top100.json` | 1000 | 87,241,220 | `42b8ae06faae183d55be6768011c456a927daddb36def447bc92a66a9cfa38ac` |
| ordinary | `eli5_eval_bm25_top100.json` | 1000 | 83,469,180 | `ae5f0d4333668b19390eb0c878def2a673971aa9190d5c02a30a87a6a867ca1e` |
| oracle | `asqa_eval_gtr_top100_reranked_oracle.json` | 948 | 10,350,811 | `aaab90bee9b0d3e53050326b4c4d05077a929046996ce4a9cfe9bab1dc9ee75` |
| oracle | `qampari_eval_gtr_top100_reranked_oracle.json` | 1000 | 8,434,999 | `88f618efefe448779126ef2fa1d28c50bb12eb4e841951c85a9b3e3c6b5ac092` |
| oracle | `eli5_eval_bm25_top100_reranked_oracle.json` | 1000 | 7,631,357 | `dfc96da193a183b769842b0d2efa2d795efb9edcc9328180663c9134d18f422a` |

普通文件的每条记录保存问题、gold answer/claims、候选 `docs` 及原始候选顺序；ASQA/QAMPARI/ELI5 的字段分别由官方任务 schema 验收。`docs` 是给未来方法使用的候选资料，不是该方法已经生成的答卷。归档没有独立的模型预测文件、模型 checkpoint 运行日志或逐题生成配置。`answer` 和 `claims` 是数据标注字段，不能当作 VANILLA/RERANK/Self-RAG 的预测。

带 `reranked_oracle` 的 3 个文件是 gold/oracle 重排条件，和 ordinary 候选条件分开保存。验收过程保留重复候选、空别名和候选位置；不会把 oracle 文件替换普通文件，也不会由文件名推断模型已经运行。

### 已完成和未完成的检查

三个任务的真实数据验收均通过：ASQA 948、QAMPARI 1000、ELI5 1000，普通候选顺序和候选重复均保留；每个任务的官方文本指标控制流与本地适配预处理对齐。验收报告明确记录：

- 没有运行生成模型；
- 没有运行 AutoAIS、QA 或 MAUVE 模型评分；
- 没有运行 SN ingestion/server 实验；
- 没有复现或排序论文方法。

因此已有的文本分数属于 scorer/data calibration，不能解释为外部方法的预测分数。ALCE 的 `rougeLsum` 等文本验收不解除引用、claims NLI、QA 和 MAUVE 的 pending 状态。

### 准入结论

- ordinary 5 个文件可以作为未来服务器 controlled rerun 的固定候选输入，方法本身仍需运行官方模型或获得可审计的逐题预测；
- oracle 3 个文件只能登记为单独的 oracle setting，不能与 ordinary SN 或 ordinary 外部方法混比；
- 当前没有可导入的 ALCE 外部模型答卷，因此不提升为 `recomputed-subset`，也不产生 ALCE 外部方法排名。

## 登记表变更

`configs/notebook-external-method-registry-v1.json` 只提升有直接证据支持的字段：

| method_id | 当前状态 |
| --- | --- |
| `qmsum.author_model_output` | `published-reference` / `calibration-only` / `oracle-input`；279 输出、273 唯一文本匹配，拒绝 ordinary paired comparison |
| `alce.vanilla.ordinary` | 保持 `controlled-rerun`；ordinary 候选数据已核实，但没有模型输出 |
| `alce.rerank.ordinary.reference` | 保持 `published-reference`；没有可审计的 row-level 输出和完整条件 |

其他 scope 和方法状态不因本次文件审计自动改变。所有结论都必须以登记表的 method、scope、input variant 和 scorer 身份为准，不能只按 benchmark 名称比较。

## 下一步

服务器阶段需要先固定模型、提示、预算和运行环境，再生成 SN 与 controlled reference 的完整答卷；若获取外部逐题输出，必须先建立唯一 case mapping、输入权限审计和官方 scorer replay。只有这些检查通过，才可以调用比较器进入 paired comparison。当前无需为这些来源新增 scorer adapter，也不能把本地 calibration 产物接入 Dashboard 作为历史 run。

## Multi-Meta-RAG：首个可重评分的外部逐题答卷

**历史子集快照，已由完整范围结果替代：** 以下 146 题、97/146 和检索指标 pending 记录仅对应当时截断下载的工件。2026-09-28 已取得两模型各 2,556 题及真实排名；当前主结果与检索分母 2,255 见[最新结果 §2](notebook-external-results-2026-09-28.md#2-multi-meta-rag完整-2556-题)。保留本节用于旧工件回放，不代表当前下载或执行限制。

### 来源与条件

在 Multi-Meta-RAG 提交 `e77e4638cbae16fa7a63f291e73230d5bb356081` 中，作者提供了 `qa_output/gpt-4-voyage-02-filtering.json` 和生成脚本 `qa_gpt.py`。脚本明确使用 `gpt-4-0613`、temperature `0.1`，从 `voyage-02_256_32_with_filtering.json` 的检索列表取前 6 条资料生成答案；该仓库的检索代码记录 Voyage-02 embedding、`BAAI/bge-reranker-large` 和 256/32 chunk 设置。每行同时保存 `query`、prompt、`model_answer`、`gold_answer` 和 `question_type`。`gold_answer` 是作者输出文件中的评测观察字段，不是导入的预测文本，也不在保存的 generation prompt 中作为答案输入；导入审计保留它以便核对来源。

原始答卷通过本地网络传输时只取得了一个有 146 条完整 JSON 对象的前缀，最后一条之后的 JSON 字符串被截断。该前缀文件 SHA256 为 `d0556faa296ff675db386d5068104cce7c00cba01d44f788370a385a247c2e3a`；我们把这个事实写入 `source.json`，没有把未取得的记录当作失败或零分。146 条记录的题目文本在 frozen MultiHop bundle 中均唯一匹配：题型计数为 inference 50、comparison 45、temporal 31、null 20；没有未知、重复或歧义映射。映射文件和归一化预测保存在 `var/external-comparison/multimeta-e77e4638/audited-source/`，原始前缀文件和 SHA256 也保留在同一目录树中。

### 统一评分结果

通过 `benchmark_protocol.py import-external`，以显式 case map 建立 `recomputed-subset` submission；随后使用固定 MultiHop QA scorer 重评分。146/146 行状态为 `success`，`upstream_weak_match_accuracy = 0.6643835616438356`，分母为 146。由于该截取的答卷没有导入 ordered retrieval list，`Hits@10`、`Hits@4`、`MAP@10` 和 `MRR@10` 保持 pending；没有用最终上下文或 gold evidence 补造检索排名。

按 frozen case 的 `question_type` 做描述性拆分（不是重新选择题目，也不作显著性检验）：inference 49/50 (`0.98`)、comparison 21/45 (`0.466667`)、temporal 7/31 (`0.225806`)、null 20/20 (`1.0`)。这些分组数字来自同一 146 题 submission，不能外推到其余 2410 条题。

这已经是一个真实外部方法的可审计逐题重评分结果，但仍不能写成“MultiHop 全量结果”或“SN 已超过该方法”。只有生成同一 146 个 case ID 的 SN 或受控 reference submission，并通过相同 bundle/profile/scorer 检查后，才可以调用比较器进行 paired comparison。登记表新增 `multihop.multimeta.gpt4_voyage02`，准入类别为 `recomputed-subset`；它不会覆盖原作者论文总分，也不解除其未取得的检索指标。

2026-09-28 已将该题单、完整 bundle、外部来源证据和固定 scorer 打包，输入哈希锁定在 `configs/comparison-scopes/multimeta-e77e4638-146.json`。新目录的真实离线导入/重评分仍为 97/146，完整资料保持 609 篇；未新增 SN 或模型执行。服务器命令及比较限制见[146 题配对执行](notebook-multihop-subset-comparison.md)。当前 MultiHop corpus 共用一个 `group_id`，所以既有 cluster bootstrap 在该 scope 上不可计算 CI；不临时更改采样单位来制造显著性结果。
