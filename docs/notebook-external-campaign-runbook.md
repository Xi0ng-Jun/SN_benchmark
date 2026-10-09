# 外部方法比较 campaign：服务器执行手册

本手册把[外部比较计划](superpowers/plans/2026-09-25-external-benchmark-comparison.md)的服务器阶段收敛成可执行文件。机器可读入口是 [`notebook-external-campaign-v1.json`](../configs/notebook-external-campaign-v1.json) 和 [`notebook-external-execution-plan-v1.jsonl`](../configs/notebook-external-execution-plan-v1.jsonl)。它们是执行模板，不是已经完成的服务器实验；`<...>` 占位符必须在服务器上由实际路径、哈希和身份替换并写入 campaign 目录。

2026-10-08 操作边界：当前执行仍为 **SN-only**，外部/reference/BM25 候选暂缓。以下外部方法登记是保留的后续能力，不能直接执行完整候选计划；当前先按 [RUNBOOK](../RUNBOOK.md) 过滤 SN 任务。共享输入、bounded 单 run 报告、canonical export 与角色回传已实现，服务器实测尚未在本机核验；详见[结果存储与导出](result-storage-and-export.md)。

## 启动前冻结

在 `${CAMPAIGN_ROOT}` 下建立以下目录，并把当前 evaluator checkout 复制或只读挂载进去：

```text
${CAMPAIGN_ROOT}/
  candidate-manifest.json
  experiment-manifest.json
  execution-plan.jsonl
  execution-status.jsonl
  bundles/
  artifacts/
  scorers/
  sources/
  runs/                         # 每个 run 是直接子目录
  submissions/
  official-scores/
  comparisons/
```

先复制候选登记、campaign 范围和执行题单三个模板，再补写实际值：bundle `manifest.json` 的 SHA256、公开数据 URL/revision、官方 scorer commit、Python/依赖 lock、SN checkout SHA、模型和 tokenizer 快照、模型服务身份、采样参数、重试规则，以及每个运行的 `run_dir`。提交冻结前必须能从 manifest 找到每个计划行的输入、方法和输出目录；不从 shell 历史推断。历史评测协议提交 `ba198311aed53020558280ac1ce34a5a4701b930` 不包含本次共享存储改造；服务器必须在 `experiment-manifest.json` 记录实际已含当前实现的 checkout SHA。

### 模型服务与比较轨道

每个方法的 manifest 必须分别记录 `answer_generation`、`retrieval_components` 和 `evaluation_models`。前者是产生最终答案的模型/服务，后者包括 embedding、reranker、意图模型等方法组件，最后一类是官方评分器调用的模型。评分模型必须按 benchmark scorer 固定，不能把它当作某个方法的生成模型。

SN 与项目 BM25/full-context 的 primary controlled comparison 使用相同的解析后 `answer_generation` 身份：模型或服务版本、tokenizer、request revision、temperature/top-p/max-tokens、超时、重试和随机性规则都要一致。`SN_MODEL_CONFIG` 和 `REFERENCE_MODEL_CONFIG` 只是两个 CLI 配置入口；它们可以分开存放，但 manifest 必须保存解析后的身份和配置哈希，并通过 `model_alignment=verified`。SN 自己的额外模型组件仍然照实记录；若回答模型或关键预算不一致，必须写成 `mixed-model-end-to-end`，不得解释为单独的 retrieval advantage。

作者方法的受控重跑保留作者要求的模型/embedding/reranker；替换模型或改提示后只能登记为 `adapted`/`controlled`。已发布答卷保留原始模型身份，不能替换成服务器上的 SN 模型，也不能与同模型受控结果混成一条成绩。当前登记表中的已知身份包括：Multi-Meta-RAG 使用 `gpt-4-0613` 或 Vertex `text-bison@001`，检索组件为 Voyage-02 与 `BAAI/bge-reranker-large`；ALCE human sample 使用 `gpt-35-turbo` 或 `vicuna-13b`；QMSum Socratic 关联 BART-large；QASPER LAB 使用 `lmsys/longchat-7b-v1.5-32k`；HotpotQA KG2RAG 计划使用 Llama3:8b、`mxbai-embed-large` 和 BGE reranker。精确 checkpoint、运行参数缺失的条目继续标记为 unknown，不能补猜。

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
ARTIFACT_ROOT="$CAMPAIGN_ROOT/artifacts"

# 一次完整安装已冻结 bundle，不改写原目录
$PYTHON scripts/prepare_notebook_benchmarks.py \
  --install-bundle "$BUNDLE" --artifact-root "$ARTIFACT_ROOT"

# 安装输出的 bundle/index ID 先写入冻结 campaign 配置；ARTIFACT_INDEX_ID 从该配置取得

# 固定 scorer；若来源已缓存，也必须校验 manifest/hash
$PYTHON scripts/benchmark_protocol.py fetch-sources \
  --output "$CAMPAIGN_ROOT/scorers"

# 一个 SN partition；完整运行需对每个 partition 建独立 run-dir
$PYTHON scripts/run_notebook_benchmarks.py \
  --bundle "$BUNDLE" --artifact-root "$ARTIFACT_ROOT" --partition-id "$PARTITION_ID" --mode chunk \
  --artifact-index-id "$ARTIFACT_INDEX_ID" \
  --request-revision notebook-request-v3 \
  --project-root "$PROJECT_ROOT" --model-config "$SN_MODEL_CONFIG" \
  --run-dir "$CAMPAIGN_ROOT/runs/$JOB_ID--$PARTITION_ID--attempt-1" \
  --case-id-file "$CASE_FILE"       # smoke 时使用；full 时省略

# 导出、评分、比较必须使用新目录
mapfile -t RUN_DIRS < "$CAMPAIGN_ROOT/$JOB_ID.run-dirs.txt"
$PYTHON scripts/benchmark_protocol.py export-sn \
  --bundle "$BUNDLE" --runs "${RUN_DIRS[@]}" \
  --output "$CAMPAIGN_ROOT/submissions/$JOB_ID"
$PYTHON scripts/benchmark_protocol.py score \
  --bundle "$BUNDLE" \
  --submission "$CAMPAIGN_ROOT/submissions/$JOB_ID/submission.json" \
  --sources "$CAMPAIGN_ROOT/scorers" \
  --output "$CAMPAIGN_ROOT/official-scores/$JOB_ID"
```

QMSum 的 `score` 必须显式加冻结的 `--rouge-home` 和 `PERL5LIB`；ALCE 先用 answer-only 模式验收文本路径，只有 shown-doc 映射和固定模型依赖都完整时才使用 full 模式；QASPER Evidence、HotpotQA Supporting/Joint 不因上下文覆盖自动解除 pending。

`$JOB_ID.run-dirs.txt` 每行一个实际 run-dir，由执行记录生成，不能用共同父目录替代；smoke export 另加 `--case-id-file "$SMOKE_CASE_FILE"`，full 按完整 bundle 对账。自动单 run 报告只校验已消费 capsule，正式 export 使用 canonical 全验证及 compact scoring projection。QASPER/Hotpot 完整 response/captures、MultiHop ranking、ALCE 映射和所有失败状态保留；outputs 始终是完整审计来源。

每个 benchmark/task 的预期 `ARTIFACT_INDEX_ID` 取自受信任 installer 的 `Shared partition index` 输出并写入冻结配置，和 `Shared bundle` ID 一起审计。不要每次从可变 pointer 读取预期 ID；所有 shared run CLI/API 要同时传 root 与 index pin，pointer 或 bundle/index 对象集合替换则拒绝，审查后另建 campaign。

## 服务器回传的最低工件

按角色生成新包，输出放 campaign 外；回传 archive 和相邻 receipt，不把整个 campaign/runtime tar：

```bash
$PYTHON scripts/package_benchmark_results.py --campaign "$CAMPAIGN_ROOT" \
  --mode results --output "/path/to/deliveries/$CAMPAIGN_ID-results.tar.gz"
$PYTHON scripts/package_benchmark_results.py --campaign "$CAMPAIGN_ROOT" \
  --mode review --output "/path/to/deliveries/$CAMPAIGN_ID-review.tar.gz"
```

results 带 compact 答卷、官方成绩/审计、scope/coverage/来源身份和报告；review 另带完整 outputs、必要 Agent 工件/映射及去重共享输入/源码。runtime、配置和原始日志仍在服务器，不回传也不删除；旧 QASPER 恢复等数据库依赖继续保留。记录 inventory 分类字节、安装/导出/打包耗时、RSS 和完整覆盖；实际收益由服务器同范围实测决定。解包后调用 `result_package.validate_package` 验证。

每个 job 至少回传：

- `command.json`、实际环境/模型/scorer 身份及 SHA256；
- 原始输出、产品状态和完整请求/资料审计（脱敏）；
- `submission.json`、`scores.json`、逐题 case ID、每个指标的实际分母；
- 失败/缺失/clarification/no_answer 的状态计数和 retry parent；
- smoke gate 报告与 full gate 报告。

收到这些工件前，只能说“入口已准备”或根据实际运行证据说“smoke/full 正在服务器执行”，不能说候选方法已经复现，也不能声称 SN 超过任何外部方法。报告分别使用“发表参考值”（`published-reference`）、“公开答卷重评分”（`recomputed-subset`）和“受控重跑”（`controlled-rerun`）三条轨道；机器标签保留，重评分范围单列为 full/subset。参考值逐项注明模型/检查点、split/setting、输入条件、scorer/版本与分母，来源缺项标未知。
