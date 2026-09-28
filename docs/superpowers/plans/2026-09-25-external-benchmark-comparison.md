# External Benchmark Comparison Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to execute this plan task-by-task. The plan is intentionally staged at campaign level; each stage has a concrete review gate before server execution expands.

**Goal:** 在固定官方协议、数据版本和输入条件下，为 QASPER、MultiHop-RAG、ALCE、QMSum、HotpotQA 建立可追溯的 SN 外部方法比较，分别产出逐题重评分、受控重跑和论文参考值，避免把不同条件混成一个排名。

**Architecture:** 先建立候选方法与证据矩阵，再采集原始逐题预测或官方 checkpoint；只有能映射冻结 case ID、核验输入条件并复用固定 scorer 的结果才进入 paired comparison。无法恢复逐题输出的论文表格保留为 `published-reference`，可运行但条件不同的方法作为 `controlled-rerun`，三者在 manifest、submission 和报告中分开。

**Tech Stack:** Python 3.13；本项目 `notebook-data-v3`、`notebook-request-v3`、`benchmark-submission-v1`、`benchmark-comparison-v1`；QASPER evaluator、MultiHop-RAG 固定 `qa_evaluate.py/retrieval_evaluate.py`、ALCE 固定 `eval.py`、QMSum Perl ROUGE 入口、HotpotQA 固定 `hotpot_evaluate_v1.py` 公式镜像；服务器上的 SN 与公开模型运行时。

**Spec:** [五套 Benchmark 的标准、实现对应与验收边界](../../notebook-benchmark-standards-and-conformance.md) 与 [Notebook 公开 Benchmark 实验计划](../../notebook-benchmark-experiment-plan.md)。

## 2026-09-28 执行进度与解释层级

- Task 7 的确定性复核包入口已实现，先重建 comparison 再按任务/指标/分差方向抽样；另从完整 paired rows 自动输出题型均值、实际分母及四种方向计数，与抽样分开。初始 4 项加汇总 2 项测试，当前全量 766 passed、2 skipped。真实 MultiHop 45 题与 ALCE 72 个配对项/23 题已输出至 `case-review-task-summary/`，原比较、选择和案例内容未改。完整题型差异及字符串 scorer 反例已核对，助手辅助观察与空白人工模板分开；正式 SN 结果和独立人工复核仍待完成，不能将此项标为完整 Task 7 验收。

- Hotpot SN 最终引用投影已接入新 v3 runtime、导出与官方评分。以实际引用可见源区间映射句位，复用 QASPER 绑定/快照，跨文档保留重复文本的独立句子编号；映射错误保留答案且整批 supporting/joint pending。全部 73,700 个真实段落经实际 SN parser/chunker，92,312 个 chunk 的映射与独立区间计算一致；不可观测句位如实保留，未调用模型，服务器回放待验收。

- ALCE 的原公开样本无法恢复完整 shown-doc，故新增可重新生成的 VANILLA HF 入口。使用作者 Llama2 两示例/前五候选配置，保存实际输入、引用映射、原文和失败；按有效 token 数修正 BOS 预算，披露加载/RNG 差异。三任务完整 2,948 题与作者 main 的 prompt/所见文档逐题一致，8 个源文件对固定 Git tree 校验；实际 HF 分支只用替身模型验收。4 项新增测试及独立审查已完成，真实模型/完整引用评分仍待服务器，原 human 样本保持 answer-only。

- HotpotQA 完整 HF distractor validation 7,405 题/73,700 段落已冻结；原始空白句和越界 supporting 标注按原样保留。原生 answer/sp 导入已实现，12 项官方指标全量合成扰动校准与原版 CLI 一致；独立复核无重要发现，最新回归见上方 Task 7 与评测状态。尚无真实外部 Hotpot 答卷。
- KG2RAG 的 QA prompt 与验证集 4 题重叠，KG 提取示例也涉及验证资料；完整数据有 54 个标题对应不同句子列表，原 title-only cache 不能直接复用。已实现受控入口处理 prompt、context-keyed KG、错误状态及 devices 参数，实际全量 prepare-only 成功；真实依赖与模型执行尚待服务器验收，不能称为原版论文复现。

- 后续已取得 `Salesforce/socratic-pretraining-qmsum@d127cbc5…` 的 281 条测试预测，新增 `import_qmsum_socratic_predictions.py`，按作者代码的会议/general/specific 顺序显式映射。真实 Perl ROUGE 已完成（0.38955/0.13960/0.33942），原运行清单和覆盖参数未知，不标论文复现。
- ALCE 已实现 `--alce-answer-only`，保留原 scorer 的答案模型指标，引用指标继续 pending；与 `--alce-full` 互斥并进入 scorer 身份。实际模型执行仍后置。两项新增功能 31 项定向测试通过，独立审查无重要发现。
- 已核实原 SegEnc run-1 链接 403、HGN 包 409、SAE 链接 404；HGN 原入口会在被评 dev 上选择 support threshold。SummN full 公开文件只有 279 行，尚不能映射到当前 281 题。上述事实改变候选优先级，详见外部结果文档 §8。下一阶段继续 QASPER 真实输出和 HotpotQA distractor 可运行方法，不能把下载链接存在当作完成。

- Multi-Meta-RAG GPT-4/PaLM 的完整 2,556 题及实际 ordered rankings 已取得并核对固定 commit/blob/hash；两份原生导入、QA/检索官方重评分、原 CLI 对照和真实配对报告已完成。旧 146 题来源保留为历史验收，主范围升级为全量。
- SN MultiHop chunk 模式已新增 native selection snapshot：捕获 AskService 在原生选择完成、回答合成前返回的有序 chunks，保存冻结 source/SQLite 文本校验；可在官方 retrieval scorer 中重放。reasoning 模式仍无单一 passage ranking，不能从最终 context 反推 Hits/MAP/MRR。定向接入与既有 runtime 测试通过；尚无真实 SN 模型结果。
- ALCE 官方 `human_eval` 的 ASQA/ELI5 各四配置、各100题已导入；ASQA 四配置文本指标已配对。ELI5 语义指标与完整引用映射仍 pending，不把人工标签导入为自动评分。
- 增加原生文件入口 `scripts/import_multimeta_predictions.py` 与 `scripts/import_alce_human_predictions.py`，均复用统一 submission/score/compare；没有启动新 SN/模型调用或服务器任务。全量回归 734 passed、2 skipped。
- 明确区分**同题描述性配对**与**同条件方法比较**：前者要求唯一问题/标签/评分身份对齐，后者还要求完整生成条件兼容。ALCE 未恢复的 shown-doc、checkpoint、prompt 等不阻止文本分数的描述性比较，但禁止同条件排名和因果结论。gold-input/oracle 仍排除普通对照，缺失评分观测不补零。
- `recomputed-subset` 保留为公开答卷重评的既有来源标签，实际范围由 `scope`/case IDs 决定；不把完整 MultiHop 文件强行截成旧子集。
- 来源、精确结果、条件缺项和可重放命令见[2026-09-28 外部结果](../../notebook-external-results-2026-09-28.md)。ALCE 仅答案模型评分入口已完成；继续补齐 QASPER/HotpotQA 外部方法与 ALCE 引用映射，服务器实际生成继续后置。

- LAB 公共 1.41GB ZIP 已通过字节范围核对全部目录，results 为空、CSV 为模板；未取得 QASPER 答卷。已完成 LAB LongChat citation 受控入口；完整 1,451 题 ITG 对齐、去 gold、训练示例及原 parser/图渲染已验证，权重/tokenizer/模型尚未运行。后续按服务器授权执行两个新增候选，不把本地 prepare 当模型验收。

## Global Constraints

- 当前实现基线为 `feat/benchmark-protocol-correctness` 提交 `6423475`；服务器必须记录实际部署的评测提交和 SN 提交，不得用本地 SHA 代替服务器事实。
- 论文、数据、代码和 scorer 必须保存固定 URL/revision/SHA256；论文数字只有在条件完全对齐时才可进入同条件表。
- 每个 benchmark 单独报告；不制作五套 benchmark 的综合分或总排名。
- 生成端不得收到 answer、evidence、claims、gold span、null 标签或答案类型；gold 只在评分和独立诊断侧使用。
- `success`、`no_answer`、`clarification`、`error`、`missing` 保留原状态；不把失败补零，也不从分母静默删除。
- 正式运行前冻结 case ID、输入资料、提示/请求版本、模型、检索器、预算、采样和 scorer；低分不得重跑，技术失败只能以带父运行和原因的新 attempt 重试。
- 本计划不启动本地 SN/LLM，不修改生产服务；服务器实验使用独立 runtime 和 campaign 目录。

## 比较类别与准入条件

| 类别 | 进入条件 | 可以支持的结论 |
| --- | --- | --- |
| `recomputed-subset` | 有逐题预测；case ID/标签与冻结 bundle 一一映射；同一官方 scorer 重评；已核实及缺失的输入/模型/检索条件分别记录 | 共同题目上的描述性 paired 差异；只有生成条件完整且兼容才支持同条件方法比较 |
| `controlled-rerun` | 公开代码和 checkpoint/模型可运行；在服务器固定 bundle 上重新生成；保存原论文设置与本项目设置差异 | 当前固定协议下的可复现实验比较；若模型/输入不同，只描述完整系统差异 |
| `published-reference` | 只有论文/README 总分，或逐题文件不可恢复、条件无法证明兼容 | 背景范围和历史结果；不能进入 SN 排名、paired delta 或“超过某方法”结论 |

## Candidate matrix（调研结论）

| Benchmark | 优先候选 | 主要官方依据 | 预期类别 | 关键控制变量/阻塞 |
| --- | --- | --- | --- | --- |
| QASPER | LED-base full text；LED-base + evidence scaffold；TF-IDF/随机段落；gold-evidence T5/UnifiedQA 仅 oracle | [论文](https://aclanthology.org/2021.naacl-main.365/)、[作者仓库](https://github.com/allenai/qasper-led-baseline)、固定 evaluator | 先查逐题输出；能恢复则 `recomputed-subset`，否则 LED 论文表为 `published-reference`，服务器重跑为 `controlled-rerun` | full_text reader 是否包含摘要/caption；16K 截断；是否使用 evidence supervision；base/large；全量 test split；Evidence F1 必须有显式段落预测 |
| MultiHop-RAG | 作者 BM25/simple；论文 embedding：bge-large-en-v1.5、text-embedding-ada-002、voyage-02；bge-reranker-large；论文 LLM：GPT-4/ChatGPT/Mixtral/Llama2/PaLM | [论文](https://arxiv.org/html/2401.15391v1)、[作者仓库](https://github.com/yixuantt/MultiHop-RAG)、固定 retrieval/QA scripts | 官方脚本可直接受控重跑；论文表为 `published-reference`；若取得逐题 ranked/QA 输出再 `recomputed-subset` | 609 篇完整 corpus；256-token chunk、top-K、top-6/2048-token 生成预算；null 不进 retrieval 分母；retrieved chunk 与 gold evidence 分轨；官方 QA 是任意词交集成功比例 |
| ALCE | ChatGPT VANILLA 5-psg；GTR/DPR retrieval；RERANK；SUMM/SNIPPET；CLOSEDBOOK/POSTCITE；QAMPARI 与 ELI5 按各自配置 | [论文](https://aclanthology.org/2023.emnlp-main.398/)、[作者仓库](https://github.com/princeton-nlp/ALCE)、固定 `eval.py` 与 `configs/` | `result/*.json` 可导入则 `recomputed-subset`；官方 `run.py` 为 `controlled-rerun`；论文表为 `published-reference` | ASQA/QAMPARI/ELI5 分开；top-100 候选与送入模型的 top-5/3 不同；首行截断与最多 3 引用；AutoAIS、QA/NLI、MAUVE 模型和版本；普通候选与 oracle 文件不能混轨 |
| QMSum | Random；TextRank；PGNet/BART/HMNet + Locator；HMNet/BART + gold span 只作 oracle；作者 `model_output` | [论文](https://aclanthology.org/2021.naacl-main.472/)、[作者仓库](https://github.com/Yale-LILY/QMSum) | 作者逐题输出优先 `recomputed-subset`；QMSum BM25 是本项目 `controlled-rerun`；论文表/README HMNet golden-input 为 `published-reference` | 35 场/281 题服务器文件与论文 279 题差异；general/specific；定位 span 与 gold span；Perl ROUGE 版本/参数；按 meeting 聚类统计 |
| HotpotQA | 官方 distractor reader；BM25/full-context 控制组；fullwiki 检索单列 | [论文](https://aclanthology.org/D18-1259/)、[官方仓库](https://github.com/hotpotqa/hotpot)、固定 evaluator commit `fa3a36370899e1d85822de61e58c85ea19993154` | `controlled-rerun` 使用 validation 的完整 distractor context；论文/Codalab test 结果为 `published-reference` | distractor/fullwiki；validation/test；句子级 `[title, sent_id]`；SN projection implemented; real server replay pending |

## Task 1: Freeze the comparison protocol and candidate registry

**Files:**

- Create on the server: `campaigns/2026-09-25-external-comparison-v1/candidate-manifest.json`
- Create on the server: `campaigns/2026-09-25-external-comparison-v1/comparison-matrix.jsonl`
- Reference: `docs/notebook-benchmark-standards-and-conformance.md`, `docs/notebook-benchmark-official-resources.md`

**Work:**

1. Record one row per candidate with `suite`, `method_id`, category, paper/repository URL, fixed commit, dataset revision, prediction availability, model identity, retriever, chunking, context budget, prompt/config, scorer, license, and expected output format.
2. Mark every condition as `verified`, `inferred`, `missing`, or `not_applicable`. Inferred/missing generation conditions prohibit same-condition and causal claims; descriptive rescoring requires verified question, gold, prediction-source and scoring identity and must disclose those gaps.
3. Define the primary common scope for each suite before opening any result file: QASPER full v0.3 test; MultiHop all 2556 questions with retrieval denominator 2255 non-null; ALCE one ordinary variant per task first; QMSum the frozen server test file without deleting the 281st question; HotpotQA distractor validation with one complete context partition per question.
4. Define the only accepted primary metrics and diagnostic metrics. For example, MultiHop retrieval uses official Hits/MAP/MRR profile, while SN context fact coverage remains diagnostic until an ordered ranking contract is captured.

**Gate:** The registry contains no unnamed “SOTA” row, no paper total without a condition label, and no candidate whose case scope or scorer is unknown. Review the matrix before downloading checkpoints or launching generation.

## Task 2: Acquire and audit external predictions/checkpoints

**Files:**

- Create on the server: a `source.json` below `campaigns/2026-09-25-external-comparison-v1/sources/`; the method directory name must equal the exact `method_id` recorded in `candidate-manifest.json`.
- Preserve immutable originals below the same method directory: `raw/`
- Create in the same method directory: `mapping.jsonl`, `audit.json`

**Work:**

1. QMSum: inspect the author `model_output/` and `extracted_span/`; determine whether each file is query-addressable, whether it uses Locator or gold spans, and whether its order maps to current query IDs. Import only files with deterministic mapping.
2. ALCE: inspect official `result/` files/releases before generating. If absent, pin the official repository commit, config, prompt, retriever and model; retain the generated JSON exactly as produced by `run.py`.
3. QASPER: inspect official repository fixtures, releases and checkpoint paths. Verify LED reader fields, context mode, evidence scaffold, checkpoint identity and whether predictions contain answer plus evidence indices. If no usable predictions/checkpoint can be restored, stop this candidate at `published-reference` and record the reason.
4. MultiHop: inspect repository output conventions and paper supplementary material. If ranked retrieval outputs are available, preserve rank/order/text; if only totals exist, keep them as `published-reference`. Do not infer ranking from SN final context.
5. HotpotQA: pin the distractor validation file and source shape (raw list or HF dict), verify ten-paragraph context ownership, title uniqueness and `[title, sent_id]` annotations. Keep fullwiki/test files out of this track; if a source lacks explicit supporting-fact predictions, import Answer only and mark Supporting/Joint pending.
5. For every source, compute SHA256, save license/provenance, and build an explicit mapping from external ID/order to frozen `case_id`. Duplicate, missing, ambiguous, or out-of-scope rows fail the audit instead of being dropped.

**Gate:** Every candidate is either importable with a complete mapping or explicitly downgraded to `published-reference`. No model run starts while a source is silently relying on row order.

## Task 3: Implement only the required native import adapters

**Likely files:**

- Modify: `src/rag_eval/benchmark_submission.py` or add `src/rag_eval/external_submission_import.py`
- Modify: `src/rag_eval/benchmark_comparison.py` only when the adapter exposes an existing comparison field
- Add tests: `tests/test_external_submission_import.py`
- Add fixture directories under `tests/fixtures/external/`, named by the suite IDs from the candidate manifest.

**Interfaces:**

- `import_external_predictions(bundle_dir: Path, source_dir: Path, method: dict, *, case_map: Path, output_dir: Path) -> Path` writes a validated `submission.json` outside the source directory.
- The adapter must preserve `raw_prediction`, normalized `prediction`, external ID, source row, status, evidence/citation/ranking fields, and mapping decision.
- `validate_submission()` remains the final boundary; adapters must not bypass bundle identity, method identity, duplicate detection, status semantics, or incomplete coverage checks.

**Work:**

1. Implement one adapter at a time, starting with QMSum because it is most likely to provide complete query-level output.
2. Add fixtures for one normal row, one missing row, duplicate ID, ambiguous mapping, multi-line output, and a gold-input/oracle row that must be rejected from the ordinary track.
3. For QASPER, convert only explicit predicted evidence units to frozen original paragraph IDs; never treat all retrieved/final-context paragraphs as predicted evidence.
4. For MultiHop, accept ordered ranked items separately from final answer text; do not synthesize a ranking from unordered context.
5. For ALCE, preserve citation preprocessing and candidate numbering; reject citations to candidates not actually shown to the method.

**Gate:** Offline tests prove that importing the same source twice is deterministic, all unknown/duplicate/missing mappings fail loudly, and the produced submission passes `validate_submission()` only when its coverage and identity are valid. Do not add adapters for candidates that remain paper-only.

## Task 4: Server smoke matrix and protocol acceptance

**Files:**

- Create: `campaigns/2026-09-25-external-comparison-v1/experiment-manifest.json`
- Create: `campaigns/2026-09-25-external-comparison-v1/execution-plan.jsonl`
- Create/update during execution: `execution-status.jsonl`
- Create one smoke directory per method ID under `campaigns/2026-09-25-external-comparison-v1/smoke/`

**Work:**

1. Select a small but representative smoke set per suite: a normal answer, a no-answer/null case, a multi-evidence case, a long-context case, QMSum general/specific queries, and HotpotQA bridge/comparison questions with multiple supporting sentences and distractors. The smoke set validates plumbing; it is not a performance subset.
2. Run SN chunk and reasoning on the same smoke cases and same frozen bundle. Run the selected reference/import adapter or official baseline with the declared condition.
3. Check actual request payloads for gold leakage, documents/candidates shown, chunk/turn ordering, citations, answer status, timeout/error retention, and scorer input preprocessing.
4. Run fixed official scorers on all available rows. For ALCE, run text metrics first; mark AutoAIS/QA/claims/MAUVE `unscored` until the server has the pinned models and dependencies.
5. Produce a smoke report with planned/completed/success/clarification/no-answer/error/missing counts and metric-specific denominators. Include at least one raw answer and one converted official submission row per method.

**Gate:** The smoke report proves that each method's public input, output conversion and scoring path are correct. Any leakage, ID mismatch, scorer mismatch or unexplained denominator blocks expansion and returns to Task 3 or the protocol registry.

## Task 5: Freeze the formal comparison campaign

**Files:**

- Freeze: `campaigns/2026-09-25-external-comparison-v1/experiment-manifest.json`
- Freeze: `campaigns/2026-09-25-external-comparison-v1/execution-plan.jsonl`
- Record every attempt in: `execution-status.jsonl`

**Work:**

1. Pin the deployed evaluator commit, SN commit, benchmark source revisions, scorer source hashes, Python/package lock, model and endpoint identities, retrieval index, prompt/config, temperature/seed, top-k, context/token budget and retry policy.
2. Enumerate every `suite × candidate × partition × mode` once. Each row includes bundle hash, partition ID, expected case count, run directory and declared category.
3. Separate primary runs from derived scoring runs. ALCE model scoring attaches to the original answer run; it does not create a second generation attempt.
4. Reserve train/dev only for any parameter selection. Never tune on the frozen test scope. MultiHop's published collection is not to be called an independent test split unless the source proves that split.
5. Define the report tables before execution: per-suite official metrics; coverage/status; common-case paired deltas; subgroup metrics; cost/latency with unavailable fields preserved; failure examples.

**Gate:** The manifest is immutable and reviewable. A reviewer can reconstruct what will run, what will be scored, and which claims each row can support without consulting shell history.

## Task 6: Execute, score and compare on the server

**Work:**

1. Run the campaign serially first; increase concurrency only after checking shared model service capacity and preserving deterministic per-case status.
2. Keep raw outputs, official submissions, scores, logs and configuration snapshots in separate directories. Never overwrite a failed attempt with a retry.
3. For each suite, create a full-scope report and a common-case report. The common-case report must show the fixed planned scope and missing/error coverage beside paired metrics.
4. QASPER: report Answer F1 first; report Evidence F1 only when explicit paragraph predictions are complete for the declared scope. Context coverage is a diagnostic.
5. MultiHop: report answer weak-match accuracy over all questions, retrieval metrics over non-null questions, and question-type strata. Keep retrieved-chunk and gold-evidence generation as separate rows.
6. ALCE: report ASQA, QAMPARI and ELI5 separately. Keep text metrics, AutoAIS, QA/NLI and MAUVE in separate metric groups with their actual case IDs and denominators.
7. QMSum: report ROUGE-1/2/L F1 using the pinned Perl profile; split general/specific and preserve the 281-question source scope. Treat author HMNet golden-input output as a reference/oracle row.
8. HotpotQA: report Answer EM/F1/Precision/Recall on distractor validation. Report Supporting Fact and Joint only when explicit `[title, sent_id]` predictions cover the declared scope; otherwise preserve each metric as pending and keep context coverage diagnostic-only.

**Gate:** Every reported number links to a submission, score artifact, scorer identity, case list and source manifest. A result with incomplete or unresolved status is reported as partial, never promoted by selecting the best attempt.

## Task 7: Statistical analysis, failure review and conclusion wording

**Files:**

- Create one `comparison.json` and one `comparison.md` under each suite directory in `campaigns/2026-09-25-external-comparison-v1/comparison/`.
- Create: `campaigns/2026-09-25-external-comparison-v1/summary.md`
- Optional dashboard input: a read-only export consumed by `scripts/build_experiment_dashboard.py`

**Work:**

1. Use paired differences only on the frozen intersection of valid case IDs; keep full-scope coverage next to it.
2. For uncertainty, resample at the natural grouping level where possible: QASPER by paper, QMSum by meeting, HotpotQA by question (or declare a different grouping), and otherwise declare the unit used. Do not claim significance from a raw mean difference.
3. Inspect a fixed sample of wins, losses, retrieval misses, null/hallucination cases, citation failures and scorer errors. Record human review separately from automatic metrics.
4. Classify each conclusion as `same-condition`, `controlled-system`, `published-reference`, or `diagnostic-only`.
5. Use wording such as “SN scored X on the frozen QASPER v0.3 input profile” or “under the declared model/input difference, SN was higher/lower on the common cases.” Do not write “SN beats method Y” when Y is only a paper total or when input/evidence conditions differ.

**Gate:** The final summary states what is supported, what is not comparable, and what experiment would resolve the largest remaining uncertainty. It does not hide missing model scores, denominator differences, or failed attempts.

## Completion checklist

- [ ] Candidate registry and source hashes are complete.
- [ ] Every imported prediction has deterministic case mapping and provenance.
- [ ] Native adapters are covered by offline negative tests.
- [ ] Server smoke matrix passed for SN and every selected candidate.
- [ ] Campaign manifest and execution plan were frozen before full generation.
- [ ] Raw outputs, statuses, submissions, scores and logs are immutable and reconciled.
- [ ] Official metrics and diagnostic metrics are separated per suite.
- [ ] `recomputed-subset`, `controlled-rerun`, and `published-reference` are separated in reports.
- [ ] Paired uncertainty and human failure review are included.
- [ ] Final conclusions match the evidence category and list unresolved blockers.
