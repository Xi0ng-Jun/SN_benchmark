# Notebook 评测的独立重评分

文档状态：当前实现说明。重评分创建独立批次并保持原 run 只读。


独立重评分把一次评测拆成两条成本不同的路径：

1. **生成实验**调用 SN 或 BM25 对照，保存回答、实际上下文、引用对象、状态和模型事件。
2. **评分批次**读取已保存的回答和冻结 gold，运行确定性 scorer 或明确配置的 Judge，写入新的派生 run。

重评分不会创建 notebook、导入资料、建立索引、检索、调用 SN Ask，也不会调用生成模型。LLM Judge 仍然会产生 Judge 模型调用；这属于评分批次成本，不是重新问答。

## 为什么这样拆

如果 ROUGE 依赖缺失、评分器有 bug、需要增加一个上下文诊断指标，直接修改原实验的 `scores.jsonl` 会丢失“当时使用了什么评分规则”的证据。让模型重新回答又会引入第二次生成随机性，无法判断差异来自评分规则还是回答变化。

因此原运行目录始终保持只读，派生目录保存：

- `base-manifest.json`、`base-planned.jsonl`、`base-scores.jsonl`：来源运行的快照；
- 原 `input/`、`product-bundle.json`、`outputs.jsonl` 和模型事件的副本；
- 新的 `planned.jsonl`、`scores.jsonl`、`manifest.json` 和 `state.json`；
- `manifest.scoring_batch`：来源运行、来源文件哈希、评分器选择、题目选择和评分批次版本。

派生 run 使用新的 `run_id`、`protocol_id` 和 `pairing_id`。它可以单独加载、审计、进入 Dashboard；配置族会与生成 run 分开，避免把同一批回答误计为第二次 SN 问答。

## 命令

默认只补算来源 run 中缺失、`error` 或 `unscored` 的评分项：

```bash
python scripts/rescore_notebook_run.py \
  --source-run /eval/runs/qmsum-chunk-meeting-001 \
  --output /eval/runs/qmsum-chunk-meeting-001-score-v2
```

指定指标和题目时，选项会进一步缩小范围：

```bash
python scripts/rescore_notebook_run.py \
  --source-run /eval/runs/qmsum-chunk-meeting-001 \
  --output /eval/runs/qmsum-chunk-meeting-001-context-v1 \
  --metric product.notebook.qmsum_context_nonempty_turn_recall_v2 \
  --case-id qmsum:meeting-001:specific:0
```

若要在选定范围内重新计算成功分数，必须显式使用 `--all`：

```bash
python scripts/rescore_notebook_run.py \
  --source-run /eval/runs/qmsum-chunk-meeting-001 \
  --output /eval/runs/qmsum-chunk-meeting-001-rouge-v2 \
  --metric product.notebook.qmsum_rouge1_f1_body_v1 \
  --metric product.notebook.qmsum_rouge2_f1_body_v1 \
  --metric product.notebook.qmsum_rougeL_f1_body_v1 \
  --all
```

输出目录必须是新的目录，并且不能位于来源 run 内。重复运行需要使用另一个输出目录；命令没有隐式续跑。

## 评分选择规则

| 调用方式 | 重新计算的单元 |
| --- | --- |
| 不提供筛选 | 所有缺失、`error`、`unscored` 单元 |
| `--metric` / `--case-id` | 先按指定指标/题目过滤，再默认只算缺失、错误或未评分单元 |
| 同时使用 `--all` | 过滤后的所有单元，包括已有成功分数 |

未被选中的旧成功分数会复制到派生 ledger，并重新绑定到新的评分批次身份。没有成功回答的题仍会记录 `unscored`，不会用 0 填充。评分过程中发生中断时，已经写入 `scores.jsonl` 的单项分数保留，`state.json` 标记为 `interrupted`。

## 适用范围与边界

当前入口支持 `sn-notebook-benchmarks-v1` 的 SN 运行和 `sn-notebook-baseline-v1` 的 QMSum BM25 运行。它要求原 run 能通过现有完整性校验：输入 bundle、计划、回答和协议身份必须未被篡改。评分器必须能从保存的 `product_record` 得到所需输入；原运行没有保存的上下文、引用映射或轨迹不能由重评分补造。

修改 scorer 公式、正文清洗规则、依赖版本或 Judge 配置时，应创建新的评分批次并保留这些身份。只有评分批次身份和输入完全匹配的结果才适合做同题比较。不能把不同评分版本的分数拼成一个均值，也不能把评分批次当作新的问答实验。

## 与 Dashboard 的关系

可把来源 run 和派生评分 run 一起传给 Dashboard 查看，但它们会被不同配置族隔离。条目详情可以看到来源运行与评分批次身份；汇总中的计划题数、评分覆盖率和缺失原因仍然保留。需要比较不同系统回答时，应选择各自同一评分批次，而不是把一个系统的旧分数和另一个系统的新分数混合。

本地验证覆盖：原目录不被修改、来源回答篡改会在创建输出前被拒绝、指定指标/题目筛选、成功分数默认不重复计算、`--all` 强制重算、评分中断保留已写分数，以及全量阻断网络回归。真实服务器 run 的重评分仍需在服务器上执行。

现有生成 run 的 manifest 没有在生成完成后预先提交 `outputs.jsonl` 哈希；重评分会记录重评分开始时看到的来源文件哈希，并依靠现有 bundle、prompt、上下文和协议校验发现结构性篡改。若需要对“答案文字本身”提供生成时点的不可变证明，后续应在生成 run 结束时增加输出 ledger 哈希，再由 loader 强制核验；这属于生成协议增强，不由本次重评分入口回填历史 run。
