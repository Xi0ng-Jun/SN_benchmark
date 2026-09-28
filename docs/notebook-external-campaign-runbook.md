# 外部方法比较 campaign：服务器执行手册

本手册把[外部比较计划](superpowers/plans/2026-09-25-external-benchmark-comparison.md)的服务器阶段收敛成可执行文件。机器可读入口是 [`notebook-external-campaign-v1.json`](../configs/notebook-external-campaign-v1.json) 和 [`notebook-external-execution-plan-v1.jsonl`](../configs/notebook-external-execution-plan-v1.jsonl)。它们是执行模板，不是已经完成的服务器实验；`<...>` 占位符必须在服务器上由实际路径、哈希和身份替换并写入 campaign 目录。

## 启动前冻结

在 `${CAMPAIGN_ROOT}` 下建立以下目录，并把当前 evaluator checkout 复制或只读挂载进去：

```text
${CAMPAIGN_ROOT}/
  candidate-manifest.json
  experiment-manifest.json
  execution-plan.jsonl
  execution-status.jsonl
  bundles/
  scorers/
  sources/
  runs/
  submissions/
  scores/
  comparisons/
```

先复制候选登记、campaign 范围和执行题单三个模板，再补写实际值：bundle `manifest.json` 的 SHA256、公开数据 URL/revision、官方 scorer commit、Python/依赖 lock、SN checkout SHA、模型和 tokenizer 快照、模型服务身份、采样参数、重试规则，以及每个运行的 `run_dir`。提交冻结前必须能从 manifest 找到每个计划行的输入、方法和输出目录；不从 shell 历史推断。当前工作树以 `6423475` 为基础但含未提交修改，服务器必须记录最终 checkout 或完整 patch digest。

启动模型前先执行 campaign 预检。它只检查登记表、执行计划、scope 和 smoke 题单；提供 `--bundle SUITE=PATH` 时还会核对题单确实存在于 frozen bundle：

```bash
${PYTHON} scripts/validate_external_campaign.py \
  --registry configs/notebook-external-method-registry-v1.json \
  --campaign configs/notebook-external-campaign-v1.json \
  --execution-plan configs/notebook-external-execution-plan-v1.jsonl \
  --root /path/to/evaluator \
  --bundle qasper=/eval/bundles/qasper-v3 \
  --bundle multihop_rag=/eval/bundles/multihop-rag-full-v3 \
  --bundle alce.asqa=/eval/bundles/alce-asqa-v3 \
  --bundle alce.qampari=/eval/bundles/alce-qampari-v3 \
  --bundle alce.eli5=/eval/bundles/alce-eli5-v3 \
  --bundle qmsum=/eval/bundles/qmsum-v3 \
  --bundle hotpotqa=/eval/bundles/hotpotqa-distractor-validation-v3 \
  --output "$CAMPAIGN_ROOT/preflight.json"
```

预检失败时不能开始 full；变更 bundle 或题单应创建新的 campaign 版本。

`smoke-*.txt` 是基于当前冻结 bundle 的确定性题单。服务器重新准备 bundle 后，逐行核对题单仍存在且 bundle hash 相同；任何 ID 或 hash 不同都要新建 campaign 版本，不能静默替换。

## 执行顺序

先执行每个候选的 smoke 行，再进入同一候选的 full 行。每个 job 单独目录保存 `command.json`、原始日志、环境身份和 `execution-status.jsonl`；失败重试必须创建新的 attempt，记录父 attempt 和失败原因。低分不重跑。

1. 准备并审计五套 frozen bundle，抓取固定官方 scorer，核对预期题量：QASPER 1,451、MultiHop 2,556（检索分母 2,255）、ALCE ASQA/QAMPARI/ELI5 为 948/1,000/1,000、QMSum 281、HotpotQA 7,405。
2. 运行 SN 和 reference smoke。检查请求不含 answer、evidence、citation、类型、level、null 标签；检查 chunk/turn 顺序、资料边界、状态、超时和错误保留。
3. 只有 smoke 通过才扩大 full。SN 按 bundle partition 逐个运行；不要把成功 partition 数当作题量，最终以 export 的 case IDs 对账。
4. 从所有有效 partition 导出统一 submission，再用固定官方 scorer 评分。MultiHop chunk 必须有 `sn-multihop-chunk-selected-passages-v1` snapshot；reasoning 的检索指标保持 pending。QASPER Evidence F1、HotpotQA Supporting/Joint、ALCE citation 指标都遵守各自显式映射完整性门槛。
5. 比较器只接受相同 bundle、scope、scorer identity 和共同有效 case IDs。生成 `comparison/report.json` 与 `report.md`，附状态计数、实际分母、缺失原因、题型/会议分组和不确定性状态。

## 通用命令骨架

以下命令是计划文件中的模板，不能直接复制运行。先把变量写入不入 Git 的 `campaign.env`，并在 `experiment-manifest.json` 保存变量对应的实际身份（密钥值本身不保存）。

```bash
set -euo pipefail
PYTHON=/path/to/evaluator/.venv/bin/python
export PYTHONPATH=/path/to/evaluator/src

# 固定 scorer；若来源已缓存，也必须校验 manifest/hash
$PYTHON scripts/benchmark_protocol.py fetch-sources \
  --output "$CAMPAIGN_ROOT/scorers"

# 一个 SN partition；完整运行需对每个 partition 建独立 run-dir
$PYTHON scripts/run_notebook_benchmarks.py \
  --bundle "$BUNDLE" --partition-id "$PARTITION_ID" --mode chunk \
  --request-revision notebook-request-v3 \
  --project-root "$PROJECT_ROOT" --model-config "$SN_MODEL_CONFIG" \
  --run-dir "$CAMPAIGN_ROOT/runs/$JOB_ID/$PARTITION_ID" \
  --case-id-file "$CASE_FILE"       # smoke 时使用；full 时省略

# 导出、评分、比较必须使用新目录
$PYTHON scripts/benchmark_protocol.py export-sn \
  --bundle "$BUNDLE" --runs "$CAMPAIGN_ROOT/runs/$JOB_ID" \
  --output "$CAMPAIGN_ROOT/submissions/$JOB_ID"
$PYTHON scripts/benchmark_protocol.py score \
  --bundle "$BUNDLE" \
  --submission "$CAMPAIGN_ROOT/submissions/$JOB_ID/submission.json" \
  --sources "$CAMPAIGN_ROOT/scorers" \
  --output "$CAMPAIGN_ROOT/scores/$JOB_ID"
```

QMSum 的 `score` 必须显式加冻结的 `--rouge-home` 和 `PERL5LIB`；ALCE 先用 answer-only 模式验收文本路径，只有 shown-doc 映射和固定模型依赖都完整时才使用 full 模式；QASPER Evidence、HotpotQA Supporting/Joint 不因上下文覆盖自动解除 pending。

## 服务器回传的最低工件

每个 job 至少回传：

- `command.json`、实际环境/模型/scorer 身份及 SHA256；
- 原始输出、产品状态和完整请求/资料审计（脱敏）；
- `submission.json`、`scores.json`、逐题 case ID、每个指标的实际分母；
- 失败/缺失/clarification/no_answer 的状态计数和 retry parent；
- smoke gate 报告与 full gate 报告。

收到这些工件前，只能说“入口已准备”或“smoke/full 正在服务器执行”，不能说候选方法已经复现，也不能声称 SN 超过任何外部方法。`published-reference`、`recomputed-subset`、`controlled-rerun` 必须在报告中保持三条独立轨道。
