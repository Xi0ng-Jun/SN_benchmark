# Notebook 场景公开评测：实现与服务器使用

文档状态：当前协议与服务器操作说明。真实数据和服务器状态必须按运行产物核实；当前总览见 [评测状态](evaluation-status.md)。


本轮 SN 主实验及后续对照的执行口径见 [Notebook 实验计划](notebook-benchmark-experiment-plan.md)。

服务器真实文件预检后的适配修正与迁移说明见 [数据修正记录](notebook-data-corrections.md)。新 prepare 使用 `adaptation_revision=notebook-data-v2`；旧包缺字段仍按原规则加载，不能手改旧 manifest。

本阶段新增 **QASPER、MultiHop-RAG、ALCE、QMSum** 四套资料型评测，协议为 `sn-notebook-benchmarks-v1`。这些是独立公开数据集，不是四个新增 DeepEval 内置 Benchmark 类。它们复用本项目 SN 隔离执行、观测、结果账本与 Dashboard；确定性指标按各数据集方法实现，ALCE 的模型指标显式调用固定版本官方评分代码。没有默认新增 GEval 或 DeepEval LLM judge，也不把 Agent trace 完整度当作 Agent 得分。

旧十套的数据协议、资料上限和历史成绩不自动迁移。本轮只开发代码，用构造样本验证，没有下载真实数据、运行 SN、调用模型或恢复 timer。公开来源与许可见[可用性核验](notebook-benchmark-data-availability.md)，开发拆分见[实施计划](superpowers/plans/2026-09-17-notebook-benchmarks.md)。

## 能力、选择、资料范围

| Benchmark | SN 能力 | 选择方式 | 导入 notebook 的资料 | 保留在评测侧的标注 |
| --- | --- | --- | --- | --- |
| QASPER | 论文问答、证据定位、不可回答识别 | 指定官方 v0.3 JSON 文件全部题；有 `FLOAT SELECTED` 或合并空白后仍无法映射到已导入正文段落的证据时逐题排除并说明 | 一篇论文的 title、abstract、全部 full_text；一篇论文一个分区 | 所有备选答案、answer type、证据段落；不把 qas 导入 |
| MultiHop-RAG | 多文档比较、时间/推理题、无答案题 | 指定官方 queries 全部有效题，四类 question_type 单独展示；发布 split=train 不冒称独立 test | **整个 corpus**，包含 gold 之外文章；一个完整 corpus 分区 | answer、evidence_list.fact、证据文章映射 |
| ALCE | 长答案/列表回答、回答覆盖、引用支持 | 明确 asqa/qampari/eli5、retriever、普通/oracle variant；文件内全部有效题 | 每题原文件中的**全部候选片段**；保留顺序，同候选集合可共用分区 | qa_pairs、答案别名、claims、长答案等 |
| QMSum | 面向问题的会议摘要 | 官方 JSONL 中全部 general/specific 查询 | 一场会议的全部发言（含原样空发言），保留 speaker 与 `[turn N]`；一场会议一个分区 | 人工摘要、specific 的全部 relevant_text_span |

QASPER 排除规则依据已发布 evidence 字段；不能据此保证所有剩余题都语义上不依赖图表。QMSum general 没有局部证据金标准，不计算 specific 的 turn 诊断。MultiHop 证据 URL 优先匹配；无 URL 时要求唯一标题；冲突、丢失或 fact 无法在文章中定位时直接报错，不静默删题。

**一道题的资料不会分散到多个分区。** 如果资料总数超过准备时声明的容量，准备失败，要求显式调整容量；不会删掉候选或 gold 文章。默认 40 只是新命令的容量默认值，不是题量配额。MultiHop 当前发布 corpus 为 609 篇，应按实际文件记录数设足容量；ALCE top-100 候选一般需至少 100。新路径把容量写入隔离 SN 进程的 settings 与实验身份，不修改生产配置；旧十套仍按旧规则执行。

## 数据包和流转

本地准备命令只使用已存在的文件：

```text
原始 JSON / JSONL + source.json（来源说明）
  → 字段校验、排除记录、case IDs、资料及 gold 分离
  → 完整资料分区
  → 冻结 bundle（文件哈希 + 可重建协议）
  → 指定一个 partition 和 mode
  → 隔离 SN 导入全文、分块、向量化、scale index（KG 禁用）
  → 原生 repo.ask；每题 conversation_id=None
  → 保存正文、实际最终上下文、引用对象、anchors、行为与 trace
  → 确定性评分；ALCE 模型项先 unscored
  → 离线报告 / Dashboard；ALCE 可单独补算后生成派生 run
```

QASPER 的匹配只合并连续空白并去首尾空白，原文与原始证据保持原样，另存 evidence_mapping。QMSum 空/空白 turn 不填占位符、不删除、不重排；QAMPARI 空字符串 alias 原样留在 gold，评分公式和答案组分母不变。

数据包包括 `raw-data`、MultiHop 的 `raw-corpus`、`source.json`、`cases.jsonl`、`documents.jsonl`、`decisions.jsonl`、`partitions.jsonl`、`manifest.json`。文件加载时先验哈希，再从原文件确定性重建 cases 与分区。原始数据和标准答案可以保存在评测目录中；**只有 documents 的 title/text 被导入 SN，Ask 只收到原问题与任务要求**。

统一 case 包含 suite/task/sample_id/case_id/question/references/gold/material_document_ids/gold_document_ids/group_id；ALCE 另保留有序 candidate_documents。问题 ID 沿用 QASPER question_id；其他数据使用原文件顺序派生，跨文件是否可比还由整个 source identity 判断。不得重排文件后声称还是同一批 case。

## 本地文件准备

所有命令以仓库根目录为工作目录。`python` 指服务器已具备 SN 依赖的解释器，本文命令没有在本机执行实验。

来源文件最小示例（值必须由服务器实际核对后填写）：

```json
{
  "dataset": "allenai/qasper",
  "split": "test",
  "revision": "v0.3",
  "source_url": "https://qasper-dataset.s3.us-west-2.amazonaws.com/qasper-test-and-evaluator-v0.3.tgz",
  "license": "CC-BY-4.0"
}
```

ALCE 还必须记录 `"task": "asqa"`、`"retriever": "gtr"`、`"variant": "ordinary"`（以实际文件为准）。MultiHop 的 source.split 必须为 `train`。QMSum 使用 `val`/`test` 等原始 split。可额外记录原发布包 URL、SHA256、license_url 和转换历史；准备器会保留元数据并自行计算输入文件 SHA256，但**不会联网认证你填写的来源信息**。

```bash
python scripts/prepare_notebook_benchmarks.py \
  --suite qasper --raw /data/qasper-test-v0.3.json \
  --source /data/qasper-source.json --output /eval/bundles/qasper

python scripts/prepare_notebook_benchmarks.py \
  --suite multihop_rag --raw /data/MultiHopRAG.json --corpus /data/corpus.json \
  --source /data/multihop-source.json --max-documents 609 --output /eval/bundles/multihop

python scripts/prepare_notebook_benchmarks.py \
  --suite alce --raw /data/asqa-gtr.json --source /data/alce-source.json \
  --max-documents 100 --output /eval/bundles/alce-asqa

python scripts/prepare_notebook_benchmarks.py \
  --suite qmsum --raw /data/qmsum-test.jsonl \
  --source /data/qmsum-source.json --output /eval/bundles/qmsum
```

`--raw` 支持原始 QASPER paper-ID JSON、MultiHop JSON list、ALCE JSON list 或含 data 数组的对象、QMSum JSONL。不自动执行 HF loader、下载脚本、解压或格式猜测。上面的 ALCE 文件名只是路径示意，按官方包实际文件替换。

## SN 执行与隔离

从 `partitions.jsonl` 读取某个 `partition_id`，每个分区 × mode 单独启动命令：

```bash
python scripts/run_notebook_benchmarks.py \
  --bundle /eval/bundles/qasper --partition-id '<partitions.jsonl 中的 ID>' \
  --mode chunk --project-root /path/to/silicon-notebook/project \
  --run-dir /eval/runs/qasper-partition1-chunk
```

reasoning 用相同 bundle/partition、另一个新 run-dir 和新进程。默认不会遍历其他分区；服务器 Agent 应枚举完整 partitions，显式记录计划和实际执行范围。每次独立导入与建索引；代码没有实现跨 mode 共享已处理 notebook。数据库、上传存储、缓存、日志、模型服务配置快照均位于 run/runtime；禁用 KG、历史记忆、用户 profile 注入和检索经验。模型服务读取服务器 SN 已部署的配置，不要求额外 tested/judge 配置。

2026-09-20 新增 `--request-revision notebook-request-v2`：QMSum 的追加指令改为 `Provide a query-focused summary using only the meeting transcript.`，QASPER 将 `if it is not answerable` 改为 `if the question is not answerable`。避免评测包装本身引入 SN 的指代澄清检查；原题中的指代照常保留，不绕过意图预览。默认 `notebook-request-v1` 完整复现旧模板。无需重新 prepare 数据；v2 仅用于新 run，同一模式对比的两侧必须选择同一请求版本。版本进入 `identity.notebook_context.request_revision` 与 product bundle，Dashboard 和 baseline cohort 不会将 v1/v2 静默混为同配置。动机、已知结果及下一步见 [QMSum 后续工作](archive/2026-09/qmsum-next-iteration.md)。

chunk 直接原生 Ask；reasoning 先走原生 intent preview，只在无需澄清时确认。不会用 gold 替 SN 填澄清答案。正常、clarification、no_answer、error 单独保存，每题没有历史对话。实际模型服务仍共享算力和服务资源，运行时隔离不等于资源隔离。

每次保存 frozen input、product-bundle、planned、outputs、scores、manifest、state，以及源码/配置身份和导入产物。导入/评分中断可读已有产物，不会用 0 填缺失项。`finished` 表示本次编排走完，不代表 ALCE 额外模型分已经算完；以 scores 的 unscored/error 和覆盖率为准。

## QMSum BM25 对照

QMSum 的第一条常规 RAG 对照是 `mode=bm25`：每个会议分区独立运行，按会议 turn 建立 BM25，按 query 选择 turn，在固定字符预算内恢复会议顺序，再用显式 `tested` 生成模型回答。它复用 QMSum 的 ROUGE 和上下文诊断 scorer，保存检索 turn、BM25 分数、完整 prompt、回答和模型事件；gold answer 与 relevant span 不进入 prompt。

```bash
python scripts/run_notebook_baseline.py \
  --bundle /eval/bundles/qmsum \
  --partition-id '<partitions.jsonl 中的 ID>' \
  --project-root /path/to/silicon-notebook/project \
  --model-config /eval/configs/qmsum-baseline-models.json \
  --run-dir /eval/runs/qmsum-bm25-<partition> \
  --top-k 8 --max-context-chars 12000
```

模型配置文件需先从 `configs/public-starter-models.example.json` 复制并填写，只使用 `tested` 角色，不需要 judge。服务器应对齐 SN 最终回答角色的实际模型与采样设置；代码不会仅凭模型名称认定一致。这个 baseline 自行检索和生成，不创建 notebook、不调用 SN Ask，仅复用显式模型客户端。

结果可进入现有 Dashboard 查看和筛选。BM25 与 SN 使用不同配置族，不进入原来的严格 chunk/reasoning 配对；请用独立 `scripts/compare_notebook_baseline.py` 生成同题报告。完整命令、保存字段、预算和比较条件见 [QMSum BM25 对照](qmsum-bm25-baseline.md)。先完成单分区模型验收，再安排全量；本地实现未执行真实模型实验。


## 指标与适用条件

以下为新适配版本的指标；旧包仍使用 v1 的 QASPER 精确段落与 QMSum 原分母诊断，不回写历史分。所有新 scorer 以 `product.notebook.` 开头；源码及公式也出现在 Dashboard 指标详情。

| Benchmark | 主指标 | 诊断指标 | 算法与边界 |
| --- | --- | --- | --- |
| QASPER | `qasper_answer_token_f1_body_v1` | `qasper_context_paragraph_f1_whitespace_v2` | 答案规范化 token F1，对全部标注取 max；SN 完整正文只去 `[kN]`，不抽出最有利答案。上下文诊断从有可靠来源映射的最终上下文中仅合并空白后匹配**完整段落**，计算与 gold 的集合 F1；不是官方模型预测 evidence 字段的成绩。不可回答参考为 Unanswerable |
| MultiHop-RAG | `multihop_official_weak_match_body_v1` | `multihop_context_fact_recall_v1` | 按指定官方版本的弱词重合：小写、空白分词，交集非空即 1；不是严格正确性。fact 诊断要求全文片段和原文档一致，null_query 为 N/A；没有检索排序，不提供 Hits@k/MRR/MAP |
| ALCE ASQA | `alce_asqa_str_em_body_v1` | `alce_asqa_str_hit_body_v1` | 每组短答案任意别名是否出现在规范化正文中；覆盖组比例 / 全部覆盖二元值。沿用 substring，不能理解为语义判定 |
| ALCE QAMPARI | `alce_qampari_f1_top5_body_v1` | prec、rec、rec_top5、f1 | 逗号拆分预测，与答案别名集比较；重复预测按官方实现参与 precision；top5 recall 的分母为 min(5, gold 数) |
| ALCE ELI5 | `alce_eli5_claims_official_v1` | 引用分 | 显式官方 NLI 推断回答是否支持每个参考 claim，未执行时 unscored |
| ALCE 全部任务 | 各任务主指标如上 | `alce_citation_rec_official_v1` / `alce_citation_prec_official_v1` | 固定官方 compute_autoais，逐句检查引用联合支持和多引用必要性；显式本地模型推断，不是 GEval |
| QMSum | `qmsum_rouge1_f1_body_v1`、`qmsum_rouge2_f1_body_v1`、`qmsum_rougeL_f1_body_v1` | specific: `qmsum_context_nonempty_turn_recall_v2` | 固定 rouge-score==0.1.2、use_stemmer=True，全文回答与人工摘要比较，不冒充原论文完全复现。turn 诊断匹配原始非空发言全文，空发言不计分母并记录 ID；相关 span 全为空时 N/A，重复发言不能证明唯一位置 |
| 全部四套 | — | `product.citation_object.existence_ratio` | 复用 SN 隔离库对象存在性检查，不代表语义支持 |

QMSum 依赖通过项目可选 extra `notebook` 声明，服务器需自行准备。缺依赖是 **error**，不是数据集不适用；不会静默改用自制 ROUGE。已保存的完整上下文保持原样；仅在重新验证 context_block、handle 和来源分段完全一致后，证据评分忽略未绑定来源的前导说明，并记录 `unbound_context_indices`。缺可靠上下文映射时诊断 N/A；无法正常作答时评分 unscored。连续主分仅报告均值和评分覆盖率，不生成答对率、综合质量总分或发布门禁。

QASPER/ALCE 的字符串算法是本仓库根据固定官方公式实现的 SN 正文适配；marker 处理、输入场景和缺输出口径与原榜单不完全相同。所有结果都保留独立 scorer 名称，不宣称官方榜单复现。DeepEval 语义诊断和完整 Agent 轨迹评测不在这次默认流程中。

## ALCE：真实引用转换、官方评分、回填展示

SN 使用 `[k1]` 这样的 marker，ALCE 评分器使用 `[1]` 对应候选数组。转换只依据**实际回答中的 marker → 实际 anchor → 导入资料对象 → 原候选片段**。chunk anchor 缺 source_id 时，可根据它实际返回的 object_id/element_id 只读回查隔离数据库。不会用 gold 或文本相似度替 SN 找正确引用。

正文 marker 无法映射到原候选时转换为超范围编号，交由官方规则记不支持，并保存原因；无引用不会补引用。完全相同 title/text 的重复候选保留原顺序，映射到第一个等价位置并记录全部等价位置。SN 原文、转换文本、每次编号转换和错误分别保存。保留完整多行回答，**不使用官方 CLI 的首行截断**，这是显式的 SN 适配。官方引用函数使用 `at_most_citations=None`，不采用 CLI 默认最多 3 引用截断。

第一步只导出已有结果：

```bash
python scripts/score_notebook_alce.py export \
  --run /eval/runs/alce-asqa-chunk --output /eval/exports/alce-asqa-chunk.json
```

第二步由服务器另行明确执行模型推断。需要 ALCE 仓库固定 commit `246c476a4edfc564266b7346b6e29ef4861ae937` 的干净 checkout，提前具备其依赖（torch/transformers/nltk 等和句子分割资源），以及**本地** AutoAIS 模型目录；命令不下载这些资源。

```bash
PYTHONPATH=src python -m rag_eval.notebook_alce score \
  --input /eval/exports/alce-asqa-chunk.json --checkout /data/ALCE \
  --output-dir /eval/official-scores/alce-asqa-chunk \
  --metrics citations --autoais-model /models/t5_xxl_true_nli_mixture \
  --allow-model-inference
```

ELI5 用 `--metrics citations claims`；可选 `qa` 仅 ASQA 并需另传 `--qa-model`，其附加指标当前仅保存独立结果，**不在默认 Dashboard 回填计划中**。官方 scorer 可能需要独立虚拟环境，使用 `--python-executable /path/to/python` 指定。

结果目录保存 input、invocation、官方 Python 源码、模型文件哈希、stdout/stderr、execution 和逐题 scores。使用本地模型与 HF offline 环境；仍会真实消耗服务器算力。不要把 `--allow-model-inference` 用在本机当前开发验证中。

第三步离线挂接到**新** run 目录：

```bash
python scripts/score_notebook_alce.py attach \
  --run /eval/runs/alce-asqa-chunk \
  --official-dir /eval/official-scores/alce-asqa-chunk \
  --output /eval/scored-runs/alce-asqa-chunk
```

挂接核对原题、原答案、转换输入、官方代码哈希、计划指标与分数范围，保留原 run。新 run 加入官方 scorer/模型身份，因此不会把不同 judge 的成绩混入同一配置组。每份原 run 挂接一次；ELI5 要同时补引用与 claims 时一起评分。复制的原实验状态不会因补算自动变成成功。

## Dashboard 与验收顺序

```bash
python scripts/build_experiment_dashboard.py \
  /eval/runs/qasper-partition1-chunk /eval/runs/qasper-partition1-reasoning \
  --output /eval/reports/notebook-comparison
```

选择 suite/task/scorer，再比较 chunk/reasoning。同 source、资料范围、代码、模型配置及评分器身份才配对；不会拿原 run 和其补分派生 run 当成独立重复实验。生成 ALCE 总览时选择派生 run，避免同时纳入其原 run 造成两次尝试计数。

单个 run 的 Markdown 显示全题单与分区数量；当前 Dashboard 发现的是**已保存运行**，未启动分区尚无 planned ledger，不会凭空出现在覆盖率分母。服务器必须用 bundle/partitions.jsonl 对账全部预期分区 × mode，再解释全套完成度；现有十套 public-selection 报告器不接受新协议。

下一步由服务器：核验官方文件和来源 → 离线 prepare 与排除/容量审计 → 检查依赖 → 执行每个分区的两种 mode → 对账保存/澄清/缺评分 → 显式 ALCE 模型补分 → 生成 Dashboard → 核验少量案例和结论。人工核验用于解释评测有效性，不是运行官方规则前的审批门槛。

## 历史离线验证记录（2026-09-17）

以下数字是 Notebook 适配阶段的历史本地验证，不能替代合并后主线当前回归；当前回归以[评测状态](evaluation-status.md)中的 447 项 Python 和 34 项 Dashboard JavaScript 检查为准。

- 全量 Python 回归 **319 passed**，无跳过；测试进程 socket/getaddrinfo 阻断，网络尝试 **0**。使用已有 venv 和只读 SN 类型/schema；SN 导入/Ask 与官方模型推断用测试替身，未运行产品实验。
- Dashboard JavaScript 回归 **18 passed**；三个脚本及 ALCE module 的命令帮助检查通过。
- 两项独立审阅发现已修复并复核：真实 reasoning 上下文前导说明的来源分段，以及 Dashboard 分区标签传递。
- 本地没有 rouge-score，验证了缺依赖错误和调用协议，**没有声称运行了真实 ROUGE**；ALCE 真实模型分、公开完整文件适配与服务器 SN 端到端验收仍待执行。

复现测试（不会安装依赖）：

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 \
SILICON_NOTEBOOK_PROJECT_ROOT=/path/to/silicon-notebook/project \
DEEPEVAL_TELEMETRY_OPT_OUT=YES DEEPEVAL_DISABLE_DOTENV=1 python - <<'PYTEST'
import socket
import pytest
attempts = []
def blocked(*args, **kwargs):
    attempts.append('network')
    raise RuntimeError('Network forbidden during offline regression')
socket.create_connection = blocked
socket.socket.connect = blocked
socket.socket.connect_ex = blocked
socket.getaddrinfo = blocked
code = pytest.main(['-q', '-rs'])
print('Network attempts:', len(attempts))
raise SystemExit(code or bool(attempts))
PYTEST
node --test tests/dashboard_core.test.cjs
```
