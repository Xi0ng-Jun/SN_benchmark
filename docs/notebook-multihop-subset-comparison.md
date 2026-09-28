# MultiHop 146 题：外部答卷与 SN 配对执行

**历史子集入口。** 2026-09-28 后续已取得完整 2,556 题的 GPT-4/PaLM 答卷及排名并完成官方重评分，后续主比较改用[完整外部结果入口](notebook-external-results-2026-09-28.md)。本文及 146 题输入包保留供链路回放，不再代表外部来源的最大可用范围。

更新：2026-09-28。本文准备服务器上的第一次真实外部方法配对；本机没有执行新的 SN/LLM 生成，也未连接服务器。历史来源审计见[外部方法审计](notebook-external-method-audit-2026-09-26.md)。

## 比较什么

比较 SN chunk/reasoning 与 Multi-Meta-RAG 的已公开 GPT-4/Voyage-02 答卷，在**同一 146 题、同一冻结评分数据、同一官方 QA scorer**下的端到端答案表现。SN 使用完整 609 篇资料库；外部方法的生成输入来自作者保存的检索上下文和脚本，唯一 query 映射不等于核验了作者索引的完整语料哈希。公开答卷可以直接重评分，不必先重写 Multi-Meta-RAG。若要进一步说明差异来自检索算法，就必须另外控制生成模型、语料、提示、预算等条件重跑；这里的系统差值不支持该因果结论。

| 项目 | 冻结条件 |
| --- | --- |
| 数据 | MultiHop HF revision `71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82`，`notebook-data-v3` |
| bundle | `5f5104cf2032430aa9fccf2421268e2328328f78b7e4dadf6cb7fb27f86a5346`，2556 题、609 篇文章 |
| 子集来源 | 传输截断前缀中的全部 146 条完整对象，按唯一 query 映射；不是随机抽样，也不是按分数选择 |
| 题型 | inference 50、comparison 45、temporal 31、null 20 |
| 外部方法 | 固定提交 `e77e4638cbae16fa7a63f291e73230d5bb356081`，GPT-4-0613、Voyage-02、bge-reranker-large |
| 外部类别 | `recomputed-subset`；SN 新运行登记 `controlled-rerun`，此标签不表示两方法模型/预算已匹配 |
| 唯一共同主指标 | `upstream_weak_match_accuracy`，固定官方弱词交集规则，分母 146 |
| 统计 | 当前全部题共享一个 `group_id`；cluster bootstrap CI 为 unavailable，不作显著性结论 |

外部重评分基准是 97/146 = `0.6643835616438356`；这是**外部单方成绩**。生成 SN 同题答卷并通过比较门禁后才能报告差值。检索 Hits/MAP/MRR 仍 pending，不能用 SN 最终上下文覆盖率替代检索排名。

## 输入包与代码

本地已准备输入目录 `var/external-comparison/multimeta-146-inputs-20260928/` 及同名 `.tar.gz`；它们在 Git 忽略目录中，**仅 git pull 不会取得这些文件**。需将压缩包单独复制到服务器独立评测目录。包内只包含公开数据、外部答卷及来源证据、固定评分源码和题单，没有私有模型配置。

```text
bundle/           完整 frozen bundle，包括评分侧 gold；运行器只向模型投影公开资料
external/         audited predictions、case map、method、source audit 和观察字段
evidence/         原始截断文件、已恢复完整对象、作者代码及许可
scorers/          固定官方 MultiHop QA/检索脚本
case-ids.txt      按冻结顺序列出的 146 个 ID，每行一项
scope.json        输入文件 SHA256、bundle 身份、范围和结果解释限制
```

代码内的[输入锁定表](../configs/comparison-scopes/multimeta-e77e4638-146.json)应与包内 `scope.json` 完全一致。不要手写 `0..145` 来替代已审计的映射；以后若取得完整作者文件，应创建新的 scope，不覆盖这份来源记录。

服务器需要包含本轮 `--case-id-file` 改动的评测 checkout，及已具备 SN 依赖的 Python 和隔离 SN checkout。评测改动已固定在 `feat/benchmark-protocol-correctness` 的 `ba19831`；服务器仍需记录实际 checkout，不得用本地 SHA 代替服务器事实。同步代码时保留服务器自有修复；固定用于实际生成的代码与配置，运行器会保存源码快照和配置身份。

以下命令从该评测 checkout 根目录执行。将变量改成服务器实际路径；`PAIR_OUTPUT` 必须是本次专用的新目录，且与 `PAIR_INPUT`、SN checkout、模型配置分离。SN 的 TOML 和 reference 的 JSON 是不同配置接口。

```bash
set -euo pipefail
BENCH_PYTHON=/path/to/eval-venv/bin/python
PAIR_INPUT=/eval/inputs/multimeta-146-inputs-20260928
PAIR_OUTPUT=/eval/campaigns/multimeta-146-attempt-1
SN_PROJECT=/path/to/isolated-sn-checkout
SN_MODEL_CONFIG=/private/model-services.toml
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
```

## 1. 离线预检与外部答卷重评分

先解包到独立位置，然后运行以下检查。它不调用模型。题单解析拒绝空文件、空行、重复和含空白的 ID；成员归属由现有 bundle/partition 选择器验证，执行顺序采用 bundle 的规范顺序，完整 corpus 不变。题单可以与命令行 `--case-id`（导入/导出为 `--case-ids`）组合，但合并后的重复或越界依然报错。

```bash
"$BENCH_PYTHON" - "$PAIR_INPUT" <<'PY'
import json
import hashlib
import sys
from pathlib import Path
from rag_eval.notebook_bundle import load_bundle, partition_bundle
from rag_eval.notebook_runner import load_case_ids_file, selected_cases
from rag_eval.starter_protocol import fingerprint

root = Path(sys.argv[1])
lock = json.loads(Path('configs/comparison-scopes/multimeta-e77e4638-146.json').read_text())
if json.loads((root / 'scope.json').read_text()) != lock:
    raise ValueError('Input package differs from the committed scope lock')
for name, expected in lock['files'].items():
    if hashlib.sha256((root / name).read_bytes()).hexdigest() != expected:
        raise ValueError('Input checksum differs: ' + name)
bundle = load_bundle(root / 'bundle')
if fingerprint(bundle['manifest']) != lock['bundle_id']:
    raise ValueError('Wrong bundle identity')
ids = load_case_ids_file(root / lock['case_id_file'])
public = partition_bundle(bundle, lock['partition_id'], request_revision='notebook-request-v3')
cases = selected_cases(bundle, public, ids)
if len(cases) != lock['case_count'] or [c['case_id'] for c in cases] != ids:
    raise ValueError('Wrong case scope or order')
if len(public['documents']) != lock['document_count']:
    raise ValueError('Incomplete public corpus')
print(f'Inputs verified: {len(cases)} cases; {len(public["documents"])} public documents')
PY

"$BENCH_PYTHON" scripts/benchmark_protocol.py import-external \
  --bundle "$PAIR_INPUT/bundle" --source "$PAIR_INPUT/external" \
  --method "$PAIR_INPUT/external/method.json" --case-map "$PAIR_INPUT/external/case-map.json" \
  --case-id-file "$PAIR_INPUT/case-ids.txt" --output "$PAIR_OUTPUT/external-submission"

"$BENCH_PYTHON" scripts/benchmark_protocol.py score \
  --bundle "$PAIR_INPUT/bundle" --submission "$PAIR_OUTPUT/external-submission/submission.json" \
  --sources "$PAIR_INPUT/scorers" --output "$PAIR_OUTPUT/external-score"

"$BENCH_PYTHON" - "$PAIR_OUTPUT/external-score/scores.json" <<'PY'
import json, math, sys
score = json.load(open(sys.argv[1]))
metric = 'upstream_weak_match_accuracy'
if (not score['coverage']['generation_complete'] or len(score['case_ids']) != 146
        or score['metric_denominators'][metric] != 146
        or not math.isclose(score['metrics'][metric], 97 / 146, abs_tol=1e-12)):
    raise ValueError('External QA calibration differs; inspect inputs/scorer before SN generation')
print('External QA verified: 97/146; retrieval metrics remain pending')
PY
```

跨机器评分时使用同一个解释器、源码和依赖环境重新评分双方。不要直接将本地旧 `scored-v2/scores.json` 与服务器新评分混用：比较器会核对 bridge、Python、依赖和官方源码身份。重评分不重新生成外部答案。

## 2. 服务器运行 SN 并导出同一范围

下面会调用模型、导入完整资料库并建立隔离 runtime。先完成上面的无模型预检，再在服务器执行。两个 mode 各用独立新目录，不能混到一个 submission 中。

```bash
PAIR_PARTITION=$("$BENCH_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1]))["partition_id"])' "$PAIR_INPUT/scope.json")
for SN_MODE in chunk reasoning; do
  "$BENCH_PYTHON" scripts/run_notebook_benchmarks.py \
    --bundle "$PAIR_INPUT/bundle" --partition-id "$PAIR_PARTITION" \
    --mode "$SN_MODE" --request-revision notebook-request-v3 \
    --project-root "$SN_PROJECT" --model-config "$SN_MODEL_CONFIG" \
    --case-id-file "$PAIR_INPUT/case-ids.txt" --run-dir "$PAIR_OUTPUT/sn-$SN_MODE-run"

  "$BENCH_PYTHON" scripts/benchmark_protocol.py export-sn \
    --bundle "$PAIR_INPUT/bundle" --runs "$PAIR_OUTPUT/sn-$SN_MODE-run" \
    --case-id-file "$PAIR_INPUT/case-ids.txt" --output "$PAIR_OUTPUT/sn-$SN_MODE-submission"

  "$BENCH_PYTHON" scripts/benchmark_protocol.py score \
    --bundle "$PAIR_INPUT/bundle" --submission "$PAIR_OUTPUT/sn-$SN_MODE-submission/submission.json" \
    --sources "$PAIR_INPUT/scorers" --output "$PAIR_OUTPUT/sn-$SN_MODE-score"
done
```

若运行退出非零，先检查保存的 `state.json`、`outputs.jsonl` 和报告。生成成功而旧诊断失败，可以在核对后单独执行 export/官方 score；生成 missing/error 则保留不完整状态，不能删掉失败题换分母。当前 CLI 没有隐式续跑；任何恢复或新尝试应明确记录所选 run，不能用“挑最好一次”形成成绩。

如需首次服务器 smoke，可另选题并使用独立目录，仅检查运行能力；正式 146 题仍使用上述固定范围和预先确定的配置，不能依据这 146 题成绩调参后把结果当未见测试。

## 3. 生成真正的配对报告

```bash
"$BENCH_PYTHON" scripts/compare_benchmark_submissions.py \
  --bundle "$PAIR_INPUT/bundle" \
  --entry "$PAIR_OUTPUT/external-submission/submission.json" "$PAIR_OUTPUT/external-score/scores.json" \
  --entry "$PAIR_OUTPUT/sn-chunk-submission/submission.json" "$PAIR_OUTPUT/sn-chunk-score/scores.json" \
  --entry "$PAIR_OUTPUT/sn-reasoning-submission/submission.json" "$PAIR_OUTPUT/sn-reasoning-score/scores.json" \
  --output "$PAIR_OUTPUT/comparison"
```

验收看 `comparison/report.json` 和 `report.md`：每方法 146 题、相同 bundle/profile/scorer、完整生成、QA 实际分母 146、逐题差值及配置差异。报告方向为 `right minus left`；上述顺序的外部→SN 差值就是 SN 减外部。多方法报告还会产生 reasoning 减 chunk。

本 scope 的 group 数为 1，预期 `uncertainty.computed=false` 和 `fewer_than_two_groups`。这是现有统计口径的限制，不能为了得到 CI 临时把题号当独立 group。传输前缀也不是全量任务的代表性随机样本；可以解释这些题上的错误案例，不能外推“全量领先/SOTA”。

## 可选：同题 BM25 控制组

已有 `run_benchmark_reference.py` 可执行本项目 BM25；它不是 Multi-Meta-RAG 的重实现，也不是原论文 dense/reranker 方法。先固定实际 `tested` 模型和预算，再运行。下列 chunk 使用项目的非空白词跨度规则，不能因为同为 256/32 就视为作者 tokenizer 相同。

```bash
REFERENCE_MODEL_CONFIG=/private/reference-model.json
"$BENCH_PYTHON" scripts/run_benchmark_reference.py \
  --bundle "$PAIR_INPUT/bundle" --project-root "$SN_PROJECT" \
  --model-config "$REFERENCE_MODEL_CONFIG" --strategy bm25 \
  --top-k 10 --max-context-chars 16000 --chunk-window 256 --chunk-overlap 32 \
  --case-id-file "$PAIR_INPUT/case-ids.txt" --run-dir "$PAIR_OUTPUT/bm25-run"

"$BENCH_PYTHON" scripts/benchmark_protocol.py score \
  --bundle "$PAIR_INPUT/bundle" --submission "$PAIR_OUTPUT/bm25-run/submission.json" \
  --sources "$PAIR_INPUT/scorers" --output "$PAIR_OUTPUT/bm25-score"
```

比较时追加该 submission/scores 的 `--entry` 并使用新的 comparison 目录。BM25 独有检索分不能填到其他方法名下；共同主指标仍只取各方法均有、且参与 case IDs 相同的指标。

## 本地验收与交付边界

本轮用真实 bundle 和外部答卷重新走离线导入、评分、身份与范围验证；新 CLI 的回归还覆盖文件选择不会默认为全量、资料库完整保留，以及缺失答卷不能从分母消失。测试中的 SN 和模型边界使用 fixture，不能作为真实成绩。

实际命令 `.venv/bin/python -m pytest -q`：729 passed、2 skipped（需要额外 native judge / Perl ROUGE 环境）。本文无模型命令块已直接执行，重评分仍为 97/146；其余 shell 命令块完成语法检查，服务器生成尚未执行。

尚待服务器执行的是 SN 146 题生成、同环境官方评分和最终配对报告。本地未生成 SN 答案，也没有伪造配对结果。整个五套 benchmark 的正式全量比较、其他外部方法的复现和 ALCE 大型模型指标仍按[总计划](notebook-benchmark-experiment-plan.md)推进。
