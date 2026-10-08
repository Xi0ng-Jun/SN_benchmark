# Silicon Notebook 评测项目执行指南

核实日期：2026-09-29。工作树：`feat/benchmark-protocol-correctness`；删除前 HEAD 为 `86addcf421d627f62553204275b506b4d8bc022b`，本文已同步本轮尚未提交的清理。每次执行记录实际 SHA 与工作区状态。

本文面向 **benchmark-deepeval 评测仓库**，覆盖安装、启动、测试、构建与服务器交付、配置和失效指引。它不是 Silicon Notebook 产品前后端的部署手册。命令以 Bash、评测仓库根目录为工作目录；`/path/to/...`、`<...>` 必须替换成实际值。

**当前执行边界：本机只做代码、文档与离线检查；实际 SN、生成模型、judge、ALCE 模型评分均在服务器进行。当前服务器任务只评 SN，外部方法和 BM25/reference 暂不运行。** 以下服务器命令是执行说明，不代表已在本次整理中运行或通过验收。

**维护范围只保留 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA。** 其他旧 benchmark 专属内容已从当前工作树清理，实际删除见 [DELETION_LOG](DELETION_LOG.md)。不要新开旧任务或以旧测试兼容作为现行要求。五套内的共用 runtime、Agent、Dashboard 和比较方法保留，见 [TRIAGE](TRIAGE.md)。

## 1. 先确认目录、版本和任务

本机当前实现位于：

```text
/home/wabiwabi/silicon-notebook/benchmark-deepeval/.worktrees/benchmark-protocol-correctness
```

2026-09-29 本轮有尚未提交的删除、迁移与文档改动。父目录的 `main` 位于 `e022c60`，有另一批未提交文档，不应直接切到父目录照抄命令。未在本次任务中 fetch 或查询远程，也未读取服务器进程或实验工件。

进入实际 checkout 后先检查：

```bash
pwd
git status --short --branch
git rev-parse HEAD
git worktree list
```

| 要做什么 | 入口 | 是否调用模型 |
| --- | --- | --- |
| 冻结已经取得的本地数据 | `scripts/prepare_notebook_benchmarks.py` | 否，也不下载 |
| SN 按分区生成回答，保存运行期诊断 | `scripts/run_notebook_benchmarks.py` | 是，服务器执行 |
| 导出 SN 答卷 | `scripts/benchmark_protocol.py export-sn` | 否 |
| 官方评分 | `scripts/benchmark_protocol.py score` | QASPER/MultiHop/Hotpot 文本评分及 QMSum Perl 不调用模型；ALCE 显式模型模式会调用 |
| 原生 Agent 组件／轨迹评测 | `scripts/run_notebook_agent.py` | 是，SN 加独立 judge |
| 已保存组件补评 | `scripts/score_native_components.py` | 只调用 judge |
| 从已有 run 构建 Dashboard | `scripts/build_experiment_dashboard.py` | 否 |
| 外部方法、参考方法、比较 | 见[外部结果入口](docs/notebook-external-results-2026-09-28.md)和[比较手册](docs/notebook-external-campaign-runbook.md) | 按入口区分；当前 SN-only 任务不执行 |

当前主流程是：**固定源码与环境 → 核验 frozen bundles／scorers → SN-only 预检 → smoke → full → 导出答卷 → 官方评分 → 对账与回传**。详细评分契约见[标准与实现符合性](docs/notebook-benchmark-standards-and-conformance.md)。

## 2. 怎么安装依赖

### 2.1 评测工具与本地开发

Python 要求来自 [`pyproject.toml`](pyproject.toml)：`>=3.11`。基础包没有必装运行依赖；可选依赖为 `dev`（pytest）、`deepeval`（固定 **4.2.2**）、`notebook`（`rouge-score==0.1.2`）。只生成已有工件的 Dashboard 不需要 DeepEval。

在新的开发环境中：

```bash
python3 --version
python3 -m venv .venv
export EVAL_PYTHON="$PWD/.venv/bin/python"
"$EVAL_PYTHON" -m pip install -e '.[dev,deepeval,notebook]'
"$EVAL_PYTHON" -m pip check
```

已有 `.venv` 时先检查其实际位置，不覆盖重建。本机当前工作树的 `.venv` 是指向父仓库环境的符号链接，修改依赖会影响其他使用该环境的工作树。

`notebook` extra 用于现有 Python ROUGE 诊断；**它不能替代 QMSum 正式的 Perl ROUGE**。只安装 `.[dev,deepeval]` 可能通过部分测试，却在实际 QMSum 运行期诊断中得到缺包错误。

仓库没有覆盖全部运行依赖的统一 lock 文件；`pip install -e` 不等于重建历史环境。用于正式实验时还要保存 Python 版本、依赖清单、SN 提交和 scorer／模型身份。

### 2.2 服务器 SN 运行环境

普通 Notebook runner 在当前 Python 进程导入 SN 的业务模块，因此同一个解释器还须具备所选 SN checkout 的后端依赖。在**专用评测环境**中安装，`PROJECT_ROOT` 指向包含 `backend/` 的 SN checkout：

```bash
export PROJECT_ROOT=/path/to/reviewed-sn-evaluation-checkout
"$EVAL_PYTHON" -m pip install -r "$PROJECT_ROOT/backend/requirements.txt"
"$EVAL_PYTHON" -m pip install -e '.[dev,deepeval,notebook]'
"$EVAL_PYTHON" -m pip check
```

SN 后端的编译依赖、模型组件及服务要求以该 **SN 提交自身**的安装说明为准。不能因 evaluator 安装成功就认为 SN、embedding、reranker 或 CUDA 已可用。产品 tracked 文件必须与已审阅提交一致；runner 会保存源码身份并拒绝产品 tracked 修改。

Agent 评测另外要求 SN 实现 `sn-deepeval-native-v1` 观测契约。检查[成对补丁说明](integrations/silicon-notebook/README.md)；当前增量补丁依赖旧观测基础，不能直接对任意全新 SN clone 应用。已具备契约时不重复应用。

### 2.3 官方评分器与额外资源

| 路径 | 必须准备 | 注意 |
| --- | --- | --- |
| QASPER、MultiHop-RAG、HotpotQA | 冻结 scorer 源码及 manifest／哈希；NumPy 等相应 Python 数值依赖 | 用固定源码和本仓库适配器，不能仅根据指标同名替换算法 |
| QMSum | Perl、完整 ROUGE-1.5.5（含 `data/`）、所需 Perl 模块 | CLI 必须显式传 `--rouge-home`；`PERL5LIB` 指向实际本地模块位置 |
| ALCE 普通 `score` | 冻结源码、答卷、适配器依赖 | 只产生已实现的轻量文本指标；其余在 `pending_metrics`，不是完整 ALCE 分数 |
| ALCE `--alce-answer-only`／`--alce-full` | 独立评分 Python、官方脚本依赖、固定 HF 缓存和 NLTK 分句资源 | 按任务会执行 QA、MAUVE、Claims NLI／AutoAIS；answer-only 不表示“无模型” |

ALCE 的模型常量见 [`alce_official_cli.py`](src/rag_eval/alce_official_cli.py)：AutoAIS/ELI5 Claims 使用 `google/t5_xxl_true_nli_mixture`，ASQA QA 使用 `gaotianyu1350/roberta-large-squad`，MAUVE 使用 `gpt2-large`。实际所需组合由 task 和评分模式决定。缓存必须具有可解析的 commit、配置和权重；不能仅提供一个同名空目录或换用较小模型。

`--alce-python` 可指向专门的评分环境；它需要固定原版 `eval.py`／`utils.py` 所导入的依赖及兼容的 Torch／Transformers、NLTK、ROUGE、MAUVE 等环境。本仓库尚无一条已在服务器验收的完整 ALCE 安装命令或通用 GPU 镜像；上传 Python 包也不能证明这部分齐备。评分桥接强制离线，缺权重或资源会失败，不自动下载。

### 2.4 使用已准备的上传包

本机交付目录为 `/home/wabiwabi/silicon-notebook/server-upload-20260928-v2/`，其中包含三份归档、manifest、校验文件和 `SERVER-AGENT-PROMPT.md`，不属于 Git 仓库内容。归档不含模型权重或凭据。

下面仅在服务器使用，`SERVER_ROOT` 及解包目标应为专用的新目录；已有环境不得直接覆盖。校验失败即停止，不继续解包或执行：

```bash
export UPLOAD=/path/to/upload
export SERVER_ROOT=/path/to/new-sn-benchmark-assets
export EVAL_ROOT="$PWD"

# 校验文件内是相对路径，必须在 UPLOAD 目录中执行。
(cd "$UPLOAD" && sha256sum -c ARCHIVE-SHA256SUMS)
mkdir -p "$SERVER_ROOT"
tar -xzf "$UPLOAD/sn-benchmark-assets-v1.tar.gz" -C "$SERVER_ROOT"
tar -xzf "$UPLOAD/sn-python-metadata-v1.tar.gz" -C "$SERVER_ROOT"
(cd "$SERVER_ROOT/assets" && sha256sum -c SHA256SUMS)
(cd "$SERVER_ROOT/python" && sha256sum -c SHA256SUMS)
```

Python 环境二选一：

- **兼容的二进制环境**：确认系统、架构、Python ABI 兼容且目标 `.venv` 不存在，再解压 `sn-eval-venv-v1.tar.xz` 到 evaluator 根目录。使用 `.venv/bin/python` 检查实际解释器，不依赖归档内 console-script 的旧绝对路径。可搬运性尚须服务器验证。
- **服务器重建**：创建独立 venv，使用 `python/requirements-freeze.txt` 安装，然后按 §2.2 核对所选 SN 的依赖。freeze 是本机环境快照，不是 wheelhouse；重建仍可能需要包源访问或另行提供 wheel 文件。

```bash
# 选择重建时执行；不要与二进制解包混用到同一个环境。
python3 -m venv /path/to/new-server-eval-venv
export EVAL_PYTHON=/path/to/new-server-eval-venv/bin/python
"$EVAL_PYTHON" -m pip install -r "$SERVER_ROOT/python/requirements-freeze.txt"
"$EVAL_PYTHON" -m pip install -e '.[dev,deepeval,notebook]'
"$EVAL_PYTHON" -m pip check
```

## 3. 怎么启动和执行

### 3.1 项目没有必须常驻的 evaluator 服务

本项目通过 CLI 执行任务。SN runner 直接调用所选 SN checkout 的 Python 业务实现，并创建独立数据库、notebook、存储与索引；**无需先启动 SN 前端或 HTTP 后端**。SN 配置引用的模型服务仍须可用。

快速核对入口，不调用模型：

```bash
"$EVAL_PYTHON" scripts/run_notebook_benchmarks.py --help
"$EVAL_PYTHON" scripts/benchmark_protocol.py score --help
"$EVAL_PYTHON" scripts/run_notebook_agent.py --help
"$EVAL_PYTHON" scripts/build_experiment_dashboard.py --help
```

### 3.2 服务器准备 frozen bundles

已上传的 bundle 不必重复下载或 prepare。新来源需要先按[官方资料](docs/notebook-benchmark-official-resources.md)核实数据身份，再用 prepare 冻结**本地**原始数据。示例：

```bash
"$EVAL_PYTHON" scripts/prepare_notebook_benchmarks.py \
  --suite qasper --raw /path/to/qasper-test-v0.3.json \
  --source /path/to/qasper-source.json \
  --adaptation-revision notebook-data-v3 \
  --output /path/to/new-qasper-v3
```

`source.json` 要求真实的 dataset、split、revision、source_url、license；ALCE 另需 task/retriever/variant，HotpotQA 要求 distractor。MultiHop 要另传 `--corpus`，目前公开 scope 的 split 为 train，不能改名为独立 test。`--max-documents` 是资料容量，不是限题参数。bundle 内文件与 manifest 不能手工修改来通过校验。

当前上传 bundle 位于 `$SERVER_ROOT/assets/bundles/`：

| 目录名 | 完整题量 | 当前 SN 方法 |
| --- | ---: | --- |
| `qasper-v3` | 1,451 | chunk |
| `multihop-rag-full-v3` | 2,556 | chunk、reasoning；检索评分另有 2,255 个非 null 样本 |
| `alce-asqa-v3` | 948 | chunk |
| `alce-qampari-v3` | 1,000 | chunk |
| `alce-eli5-v3` | 1,000 | chunk |
| `qmsum-v3` | 281 | chunk |
| `hotpotqa-distractor-validation-v3` | 7,405 | chunk |

### 3.3 先生成 SN-only 计划，再预检

完整 external campaign 模板包含外部方法，**不能直接当作当前任务清单执行**。下面只生成文件与预检，不启动实验：

```bash
export EVAL_ROOT="$PWD"
export CAMPAIGN_ROOT=/path/to/new-campaign-sn-only
mkdir -p "$CAMPAIGN_ROOT"
"$EVAL_PYTHON" - "$EVAL_ROOT" "$CAMPAIGN_ROOT" <<'PY'
import json
from pathlib import Path
import sys
root, out = map(Path, sys.argv[1:])
registry = json.loads((root / 'configs/notebook-external-method-registry-v1.json').read_text())
campaign = json.loads((root / 'configs/notebook-external-campaign-v1.json').read_text())
plan = [json.loads(line) for line in (root / 'configs/notebook-external-execution-plan-v1.jsonl').read_text().splitlines() if line.strip()]
sn_ids = {m['method_id'] for m in registry['methods'] if m['method_id'].startswith('sn.')}
campaign['candidates'] = [c for c in campaign['candidates'] if c['method_id'] in sn_ids]
plan = [row for row in plan if row['method_id'] in sn_ids]
for name, data in [('registry.json', registry), ('campaign-sn-only.json', campaign)]:
    with (out / name).open('x') as f:
        f.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
with (out / 'execution-plan-sn-only.jsonl').open('x') as f:
    f.write(''.join(json.dumps(row, ensure_ascii=False) + '\n' for row in plan))
print('SN methods:', len(sn_ids), 'template jobs:', len(plan))
PY

"$EVAL_PYTHON" scripts/validate_external_campaign.py \
  --registry "$CAMPAIGN_ROOT/registry.json" \
  --campaign "$CAMPAIGN_ROOT/campaign-sn-only.json" \
  --execution-plan "$CAMPAIGN_ROOT/execution-plan-sn-only.jsonl" \
  --root "$EVAL_ROOT" \
  --bundle qasper="$SERVER_ROOT/assets/bundles/qasper-v3" \
  --bundle multihop_rag="$SERVER_ROOT/assets/bundles/multihop-rag-full-v3" \
  --bundle alce.asqa="$SERVER_ROOT/assets/bundles/alce-asqa-v3" \
  --bundle alce.qampari="$SERVER_ROOT/assets/bundles/alce-qampari-v3" \
  --bundle alce.eli5="$SERVER_ROOT/assets/bundles/alce-eli5-v3" \
  --bundle qmsum="$SERVER_ROOT/assets/bundles/qmsum-v3" \
  --bundle hotpotqa="$SERVER_ROOT/assets/bundles/hotpotqa-distractor-validation-v3" \
  --output "$CAMPAIGN_ROOT/preflight.json"
```

本代码基线预期 **6 个 SN method、12 条 smoke/full 模板行**。ALCE 还要展开三个 task，SN 每个任务再展开各 partition；12 不是最终进程数或运行目录数。执行计划 JSONL 是任务模板，不是会自动调度全部实验的程序。

预检应为 `valid: true` 且无 errors。它校验登记、题单和所提供 bundle 的题目范围／数量，**不验证模型可达性、GPU、完整 scorer 环境或模型配置对齐**。后者仍需专门检查和 smoke 验收；bundle 的完整内容校验由加载路径执行。

### 3.4 运行一个分区；通过 smoke 后再扩 full

先填写 `BUNDLE`、`PARTITION_ID`、`SN_MODEL_CONFIG`、`RUN_DIR`。分区来自该 bundle 的 `partitions.jsonl`，题目来自 `cases.jsonl`。下面为服务器 smoke 示例：

```bash
"$EVAL_PYTHON" scripts/run_notebook_benchmarks.py \
  --bundle "$BUNDLE" --partition-id "$PARTITION_ID" \
  --mode chunk --request-revision notebook-request-v3 \
  --project-root "$PROJECT_ROOT" --model-config "$SN_MODEL_CONFIG" \
  --case-id-file "$PARTITION_CASE_FILE" \
  --run-dir "$RUN_DIR"
```

`PARTITION_CASE_FILE` 必须只包含当前分区的 smoke case IDs，每行一题、无空白或重复。全 suite 的 smoke 题单可能跨分区，需先与各分区取交集，空交集不启动；**不能把完整 suite 题单原样传给每个分区**。ALCE 的同一 smoke 题单还应分别与 ASQA/QAMPARI/ELI5 bundle 对齐。限题不会删减该分区完整资料。

每个 partition × mode × attempt 使用新目录、新 CLI 进程。当前 runner **不支持原目录隐式续跑**，目录存在即拒绝。重试建新 attempt 并记录原因和父 attempt，不能按低分选择性重跑。

smoke 核验请求无 gold、资料范围正确、输出及失败状态完整、证据映射可回放，并验证对应官方评分路径。通过后按计划枚举全量 partitions，省略 `--case-id-file`。MultiHop reasoning 使用 `--mode reasoning` 和不同的新目录；其他方法保持本轮清单中的 mode。

每个运行会保存 manifest、状态、题单、`outputs.jsonl`、运行期 `scores.jsonl` 和产品工件。**runner 的诊断分不等于统一答卷的官方成绩**；后续导出和官方评分仍须执行。

### 3.5 导出答卷，再做官方评分

`export-sn --runs` 接受一组**真实 run 目录**，每个目录直接包含该次运行的 manifest 等工件，不会扫描它们的共同父目录。

由执行记录生成 `$CAMPAIGN_ROOT/$JOB_ID.run-dirs.txt`，每行一个绝对 run 路径，覆盖该 job 的全部目标分区。同一题的重试不能和原 attempt 同时纳入；chunk/reasoning、不同配置、smoke/full 分别导出。Bash 示例：

```bash
mapfile -t RUN_DIRS < "$CAMPAIGN_ROOT/$JOB_ID.run-dirs.txt"
"$EVAL_PYTHON" scripts/benchmark_protocol.py export-sn \
  --bundle "$BUNDLE" --runs "${RUN_DIRS[@]}" \
  --output "$CAMPAIGN_ROOT/submissions/$JOB_ID"
```

这是 **full scope** 的示例；smoke 导出另加 `--case-id-file "$SMOKE_CASE_FILE"` 声明对应 bundle 的完整 smoke 范围。省略 scope 时按全 bundle 对账，未提供的题会留下 missing，不能把缺失的全量导出当作成功的 smoke 或正式完整成绩。

普通官方评分（QASPER/MultiHop/Hotpot 或 ALCE 轻量文本路径）：

```bash
export SCORERS="$SERVER_ROOT/assets/scorers/fetched-official-scorers"
"$EVAL_PYTHON" scripts/benchmark_protocol.py score \
  --bundle "$BUNDLE" \
  --submission "$CAMPAIGN_ROOT/submissions/$JOB_ID/submission.json" \
  --sources "$SCORERS" --output "$CAMPAIGN_ROOT/scores/$JOB_ID"
```

QMSum 必须改用含显式 ROUGE 路径的命令：

```bash
export ROUGE_HOME="$SERVER_ROOT/assets/qmsum-rouge/ROUGE-1.5.5"
export PERL5LIB="$ROUGE_HOME${PERL5LIB:+:$PERL5LIB}"
"$EVAL_PYTHON" scripts/benchmark_protocol.py score \
  --bundle "$BUNDLE" \
  --submission "$CAMPAIGN_ROOT/submissions/$JOB_ID/submission.json" \
  --sources "$SCORERS" --rouge-home "$ROUGE_HOME" \
  --output "$CAMPAIGN_ROOT/scores/$JOB_ID-perl"
```

上传包之外的 Perl 模块布局须按实际位置设置 `PERL5LIB`。单独 export `ROUGE_HOME` 不会让 `score` 自动读取它。

ALCE 完整模型评分在已准备 §2.3 环境后执行：

```bash
"$EVAL_PYTHON" scripts/benchmark_protocol.py score \
  --bundle "$BUNDLE" \
  --submission "$CAMPAIGN_ROOT/submissions/$JOB_ID/submission.json" \
  --sources "$SCORERS" --alce-full \
  --alce-python "$ALCE_PYTHON" --alce-hf-cache "$ALCE_HF_CACHE" \
  --alce-nltk-data "$ALCE_NLTK_DATA" \
  --output "$CAMPAIGN_ROOT/scores/$JOB_ID-alce-full"
```

只评答案时将 `--alce-full` 换成 `--alce-answer-only`，另用新输出目录；二者互斥，引用分在 answer-only 保持 pending。full 还要求完整 shown-doc／引用映射。

检查 `scores.json` 的 coverage、各指标实际分母和 `pending_metrics`。QASPER Evidence、Hotpot Supporting Fact/Joint 依赖显式完整投影；MultiHop chunk 依赖实际排名快照，reasoning 无单一排名契约，不能补造检索分。错误、缺失、不适用分别保留；适用指标中的真实零分仍是零分。

### 3.6 原生 Agent 评测：单独安排的服务器任务

当前 SN-only campaign 使用普通 Notebook runner，**不会自动产生专门的 Agent judge 分数**。reasoning 是业务路径；`--trajectory` 才开启该路径的完整执行轨迹评分。下面是另行安排 Agent 任务时的入口，不并入当前 campaign 自动运行：

```bash
"$EVAL_PYTHON" scripts/run_notebook_agent.py \
  --bundle "$BUNDLE" --partition-id "$PARTITION_ID" \
  --mode reasoning --request-revision notebook-request-v3 \
  --project-root "$PROJECT_ROOT" --model-config "$SN_MODEL_CONFIG" \
  --judge-config "$JUDGE_CONFIG" --case-id "$CASE_ID" \
  --trajectory --run-dir "$AGENT_RUN_DIR"
```

不加 `--trajectory` 默认评 contextual_relevancy、faithfulness、answer_relevancy；加上后另选取 task_completion、step_efficiency、plan_quality、plan_adherence。计划类指标要求真实显式计划，不适用时记 N/A。可用重复 `--metric` 缩小范围，重复 `--case-id` 选题；**该 CLI 没有 `--case-id-file`**。

SN TOML 控制产品模型，judge JSON 控制评审模型，二者独立。答案／组件先保存，judge 失败不应抹掉回答。组件补评入口及必填参数用 `scripts/score_native_components.py --help` 查看，具体步骤见[评分恢复](docs/native-scoring-recovery.md)。补评需要新的 output，只支持三个组件指标，不能从普通日志补造完整 Agent 原生轨迹。

### 3.7 查看 Dashboard

只读已有 run，可在本机执行；当前服务器 SN-only prompt 不包含这个步骤，回传后按需生成。

```bash
"$EVAL_PYTHON" scripts/build_experiment_dashboard.py \
  /path/to/actual-run-1 /path/to/actual-run-2 \
  --output /path/to/new-report
```

也可改用 `--runs-root /path/to/runs` 自动发现（不能同时传显式 run）。输出目录必须是新的，且与输入 run 不互相包含。打开 `dashboard.html` 即可，无需 npm、HTTP 服务或 DeepEval。分发时复制**整个报告目录**，包括 `details/`；它是生成时快照，不会随原 run 自动更新。

## 4. 怎么跑测试

下列命令检查五套及共用 Agent／Dashboard／runtime 职责。退役专属测试已删除，混合测试的有效性质已迁移到五套或中性 fixture；剩余兼容债务见 [TRIAGE](TRIAGE.md)。

从当前 evaluator 根目录使用 Python 模块入口，避免 pytest console-script 的导入路径差异：

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=YES DEEPEVAL_DISABLE_DOTENV=1 CONFIDENT_TRACE_FLUSH=0 \
  "$EVAL_PYTHON" -m pytest -q -rs

node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs
```

Node 仅用于 Dashboard JS 测试，本仓库没有 npm 安装／打包流程。核实时本机版本为 Python 3.13.15、Node v24.20.0；这是已观察环境，不是额外声明的最低版本。

| 测试层 | 额外前提 | 跳过意味着什么 |
| --- | --- | --- |
| 大部分 Python 离线测试 | `dev`；Agent/SDK 覆盖还需要 `deepeval==4.2.2` | 没装 SDK 时部分测试会 skip，并非完整回归 |
| 真实本地 Perl ROUGE 测试 | `QMSUM_ROUGE_HOME` 指向完整 ROUGE，及必要 `PERL5LIB` | 默认可跳过；这个变量是测试开关，区别于 CLI `--rouge-home` |
| SN 观测跨仓库契约 | `SN_EVALUATION_SOURCE` 指向配对 SN checkout | 不在默认 `tests/` 搜索路径，需显式执行 |
| Dashboard 浏览器交互 | 现有 Playwright/Chromium，可用 `PLAYWRIGHT_MODULE`、`CHROMIUM_EXECUTABLE` 指定 | 不是 Node 纯逻辑测试的一部分；步骤见[Dashboard 文档](docs/experiment-dashboard.md) |

显式观测契约检查：

```bash
DEEPEVAL_TELEMETRY_OPT_OUT=YES DEEPEVAL_DISABLE_DOTENV=1 CONFIDENT_TRACE_FLUSH=0 \
  SN_EVALUATION_SOURCE="$PROJECT_ROOT" PYTHONPATH=.:src \
  "$EVAL_PYTHON" -m pytest integrations/silicon-notebook/test_native_agent_contract.py -q
```

以上为代码／契约测试，不能代替服务器 smoke/full。若需要在回归中额外阻断网络，使用[Notebook 文档中的离线测试包装](docs/notebook-benchmarks.md)并审查子进程边界；该 Python socket 包装不是操作系统级网络隔离。

## 5. 怎么构建和部署

### 5.1 evaluator 代码

常规运行使用源码 checkout，无额外构建步骤。仓库未提供 Dockerfile、Compose、Makefile 或 `.github/workflows` 部署流水线；不要把 SN 产品的 npm／后端部署命令套用到 evaluator。

旧 benchmark 的 systemd service／timer 模板及对应自动化入口已删除；当前没有五套的常驻调度器。服务器既有部署未核实、未启停。整体入口和调用关系见[当前架构](ARCHITECTURE_CURRENT.md)。

需要 Python 分发包时，基于现有 setuptools 构建配置可使用标准构建工具：

```bash
"$EVAL_PYTHON" -m pip install build
"$EVAL_PYTHON" -m build
```

预期输出在 `dist/`，构建可能访问依赖源。本次没有安装 build 或执行打包。wheel 主要提供 `rag_eval` 包、Dashboard 资源及 `rag-eval-deepeval` console entry point；**当前 Notebook 主流程依赖仓库的 `scripts/`、`configs/` 和配对资源，不能只交付一个 wheel 就视为完整部署**。

### 5.2 服务器交付

在新的服务器 checkout 固定经审阅的 evaluator SHA，避免更新正在实验的工作树。例如：

```bash
git clone --branch feat/benchmark-protocol-correctness \
  git@github.com:Xi0ng-Jun/SN_benchmark.git /path/to/new-evaluator
cd /path/to/new-evaluator
git checkout --detach '<reviewed-evaluator-sha>'
git status --short --branch
git rev-parse HEAD
```

SSH 访问条件由服务器提供。不要把浮动分支名或本文基线误当作永远正确的执行版本。准备专用 Python、bundle/scorer 和 SN checkout 后，按 §3 执行；无需部署 evaluator Web 服务、修改生产 SN 或启用定时任务。

在 `experiment-manifest.json` 保存实际 evaluator/SN SHA、Python/依赖身份、bundle/scorer 哈希、生成模型、embedding/reranker 等组件模型、评分模型、采样与预算参数。已有代码会保存多种运行身份，但 campaign 总账和逐 job 命令／状态仍须由执行者维护；预检不会自动补齐它们。

回传至少包括：计划及预检、命令与状态、原始逐题输出、submission、官方 scores、逐指标分母、错误／缺失／pending 计数、证据投影与检索快照、smoke/full 验收结论。私有 runtime 含模型配置副本、数据库和原始日志，不应整体公开上传；提取脱敏结果和必要身份。

## 6. 需要哪些环境变量、配置、外部服务

| 名称／配置 | 来源与用途 |
| --- | --- |
| `EVAL_PYTHON`、`EVAL_ROOT`、`SERVER_ROOT`、`CAMPAIGN_ROOT` | 本文的 shell 路径变量，不是统一自动发现协议；全部指向实际专用目录 |
| `PROJECT_ROOT`／`--project-root` | 所选 SN 源码 checkout；直接导入业务模块 |
| `SN_MODEL_CONFIG`／`--model-config` | 产品模型服务 TOML；省略时读取 `project/.local/model-services.toml`。runner 将其复制至隔离 runtime 并记录哈希 |
| SN `.env` | 产品 Settings 仍读取所选 checkout 的 `.env`；runtime 隔离项会覆盖相应路径和开关。不能假定所有配置都来自 TOML |
| `JUDGE_CONFIG`／`--judge-config` | Agent／补评使用的显式 JSON，格式见 [`model-roles.example.json`](configs/model-roles.example.json)，仅需要 judge 角色 |
| `STARTER_JUDGE_BASE_URL`、`STARTER_JUDGE_API_KEY` | 上述示例 JSON 引用的变量名，可改成自有名字；实际端点与凭据由服务器注入。model_id 和 parameters 在 JSON 中显式填写 |
| `ROUGE_HOME` | 本文供 `--rouge-home` 传参的 shell 变量；不是 score 的自动读取配置 |
| `PERL5LIB` | QMSum Perl 评分依赖搜索路径，按实际分发包设置 |
| `ALCE_PYTHON`、`ALCE_HF_CACHE`、`ALCE_NLTK_DATA` | 分别传给 ALCE CLI；桥接设置离线 HF 和 NLTK 路径，模型权重须提前准备 |
| `SN_EVALUATION_SOURCE`、`QMSUM_ROUGE_HOME` | 不同离线测试的选择开关，见 §4，互不替代 |
| DeepEval 本地开关 | Agent 入口设置 `DEEPEVAL_TELEMETRY_OPT_OUT`、`DEEPEVAL_DISABLE_DOTENV` 等，并清除当前进程的 `CONFIDENT_API_KEY`；无需 Confident AI 云账号 |

SN 的实际 chat/LLM、embedding、reranker 服务及其认证／本地路径必须与所选配置一致；普通 Notebook runner 不要求独立 judge，Agent 评测才需要。官方 ALCE 评分模型不是 SN 的生成模型，三类模型不能混为一份身份。

runtime 自动隔离数据库、存储、缓存和日志，并关闭 profile、检索经验注入、reasoning memory、generated-question index、chunk KG overlay、自动 KG 等功能。设置的唯一入口见 [`runtime_environment.configure_environment`](src/rag_eval/runtime_environment.py)；不要靠额外 export 改成生产路径。这也限定了实验结论：本轮不能证明这些被关闭模块的贡献。

根目录旧配置示例与旧数据集 CLI 已删除。当前分别使用 SN TOML、显式模型角色 JSON 和 campaign 登记配置；不存在自动覆盖所有路径的统一配置文件。

## 7. 哪些步骤已失效或不能直接照用

下表记录不能直接照用的指引。退役专属源码、受跟踪工件和文档已清理；仓外上传包和服务器工件未改动，下次交付仍须与所选 evaluator 版本核对。

| 旧指引／容易误用的步骤 | 现状与处理 |
| --- | --- |
| 继续运行五套之外的旧任务或启用旧 public timer | 专属入口、配置与仓内调度模板已删除；实际删除项见 DELETION_LOG |
| 在父仓库 `main` 固定绝对目录执行所有命令 | 当前协议实现尚在本工作树；先核实 checkout 和 SHA，不能按目录名推断功能齐全 |
| Agent 文档称默认 request-v2，或照抄旧 server tracing prompt 的 v2 | `run_notebook_agent.py` 与普通 Notebook CLI 默认均为 v3。v1/v2 是显式历史兼容路径；正式 SN 答卷导出要求 v3。Python API 某些默认仍是 v1，不可推广 CLI 默认 |
| 上传 prompt 中从 evaluator cwd 执行 `sha256sum -c "$UPLOAD/ARCHIVE-SHA256SUMS"` | 清单记录相对文件名，cwd 错误会找不到归档；改为 `(cd "$UPLOAD" && sha256sum -c ARCHIVE-SHA256SUMS)` |
| 将 `$CAMPAIGN_ROOT/runs/$JOB_ID` 父目录直接传给 `export-sn --runs` | 不递归发现 run；按 §3.5 显式提供真实目录列表 |
| QMSum 只 export `ROUGE_HOME`，随后使用通用 score 命令 | score 不读该环境变量，缺 `--rouge-home` 会报错；按 §3.5 显式传参 |
| 将全 suite smoke 文件传给每个 partition | runner 要求全部选题属于本 partition；先按分区取交集，ALCE 还需按 task bundle 选择 |
| 只装 evaluator/DeepEval 就能跑 SN 和所有官方模型分 | SN backend、Perl、ALCE 评分环境及模型资源是独立前提；按 §2 准备 |
| 在 `.venv/bin/pytest` 和 `python -m pytest` 间任意切换 | 曾发生 `scripts` 导入失败；统一在仓库根使用指定解释器的 `-m pytest` |
| 对已存在的 Notebook run-dir 直接续跑 | 当前入口拒绝，使用新 attempt 并保留原产物 |
| 只运行 `--mode reasoning` 就得到 Agent 七项指标 | 只执行业务路径；专门评测要用 Agent 入口、judge 和适用的 `--trajectory` |
| 给 Agent CLI 传普通 runner 的 `--case-id-file` | 当前不支持；使用重复 `--case-id` |
| 用 `evaluate_agent_traces.py` 把旧 JSON 变成正式原生 Agent／DAG 成绩 | 该入口只保留历史确定性诊断；不能替代原生观测与 judge 评测 |
| 复制 Dashboard 单个 HTML，或等待旧报告自动刷新 | 同时复制 `details/` 等全目录；变更代码／工件后在新目录重新生成 |
| 照完整 external campaign 自动跑所有候选 | 与当前 SN-only 范围不符；先过滤计划。外部入口保留，并未因暂停而退役 |
| 将历史文档中的 passed 数字当成本次测试结果 | 都是各自日期和环境的证据；本次实际检查见下方，不据旧数字宣称当前服务器通过 |

以上对上传 prompt 的修正应在**下次复用前**应用；本文不声称已修改服务器正在执行的命令或重新打包上传文件。旧 Agent 示例及阶段记录保留供追溯，新操作以本页和 CLI 当前参数为准。

## 8. 本次核验与维护

本页最初依据源码、CLI、runtime、上传 prompt 和文档静态检查编写；本轮已同步退役清理及模块／配置迁移。删除后的离线回归为 **Python 538 passed、1 skipped（显式本地 Perl ROUGE 未配置），Node 34 passed**；更多检查与完整删除清单见 [DELETION_LOG](DELETION_LOG.md)。

初次编写时已实际执行 SN-only 过滤代码，得到 6 个 method／12 条模板行，结构预检 valid；该检查未提供 bundle，不代表真实数据或服务器环境已验收。未调用真实 SN、模型、judge 或官方模型评分，未安装依赖、构建 wheel、下载数据或操作服务器。本轮未提交或推送；服务器必须使用届时已交付并核验的版本。

维护本页时，从入口代码和最新实际工件核实变更；安装配置、CLI 或部署方式改变时同步本页，历史执行记录继续保存在[文档归档](docs/archive/README.md)。当前状态见[评测状态](docs/evaluation-status.md)，协议细节见[文档导航](docs/README.md)。
