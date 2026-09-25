# Benchmark protocol correctness implementation plan

> For agentic workers: execute the tasks below with tests before implementation. Independent data and reference-method work may be delegated with explicit file ownership; the main agent owns integration and official scoring verification.

**Goal:** Evaluate SN and comparison methods on explicitly frozen QASPER, MultiHop-RAG, ALCE and QMSum protocols, with reproducible input, official scoring evidence and honest completion/coverage reporting.

**Architecture:** Reuse frozen notebook bundles and isolated SN execution. Add an explicit data/request revision, a gold-free prediction submission boundary, fixed official scoring adapters and a comparison report that validates dataset/scorer identity before computing differences. Keep method implementations independent of gold and share the same prepared inputs and output requirements.

**Tech stack:** Python, existing notebook runners, official QASPER/MultiHop/ALCE code, author-confirmed Perl ROUGE for QMSum, pytest.

**Spec:** This plan records the concrete interpretation of the user's 2026-09-23 instruction below; operational defaults and evidence will be consolidated in `docs/notebook-benchmark-experiment-plan.md` after validation.

**Status — 2026-09-24, after proxy-enabled acceptance:** Implementation, independent review and regression are complete for the new protocol pipeline. All four suites now have complete official-file validation; MultiHop and ALCE text scoring were checked against fixed original CLIs. QASPER/QMSum also have one real SN/BM25 question per suite. Actual ALCE model scoring, formal experiment freeze and published-method comparisons remain open and belong on the server. The branch is uncommitted; this is not a declaration that the four-suite evaluation is complete. Current commands are in [the experiment guide](../../notebook-benchmark-experiment-plan.md); the latest scope and evidence are in [real-data acceptance](../../notebook-benchmark-real-data-validation.md).

## User direction and acceptance

- Correct new experiments and comparisons take priority over preserving the value of old results. Do not request old server run directories as a prerequisite.
- Necessary implementation changes and reruns are authorized. Do not silently start production services, alter production databases or publish results.
- Distinguish benchmark task rules from an original baseline's architecture. Different models can be compared as complete systems; causal attribution requires controlled variables.
- Public code is evidence, not automatically a mathematically standard implementation. MultiHop's upstream weak matching and retrieval metric quirks must remain explicitly identified.
- QASPER paper wording and released reader differ. New default is an explicitly documented full-question text-input track, not a claim of replicating the original paper table.
- QMSum author links `MatchSum/metrics.py` at `c7754245a454d0ba3535db0e4cc1a13b3d35680d`: Perl ROUGE-1.5.5 with `-c 95 -r 1000 -n 2 -m -a`, Average_F. Verify with public HMNet gold-input predictions; they are scorer calibration only.
- ALCE official track uses the fixed CLI preprocessing and default three-reference limit, including task selection and batch-level metrics. Raw answers and citation conversion remain inspectable.
- An incomplete run cannot silently become a complete ranked result. Preserve errors/clarifications and state the official submission/denominator rule separately.
- No aggregate score across benchmark suites. Any comparison states dataset, task, input access, model, generation budget, scorer and completion.

## Interfaces

- `notebook_data.OFFICIAL_ADAPTATION_REVISION = 'notebook-data-v3'`; existing v1/v2 remain readable.
- `prepare(..., adaptation_revision=...)` freezes the chosen revision. Preparation CLI defaults to v3; new SN runs use `notebook-request-v3`.
- Submission prediction rows contain `case_id`, `status`, `prediction` and optional observed citation/evidence/context metadata. Gold is read only by the scorer from the frozen bundle.
- A submission identifies the full bundle fingerprint, selected case IDs, method/configuration and input access policy. Duplicates, unknown IDs, incompatible scopes and nonfinite scores are errors.
- Generation and scoring have separate artifacts. Scoring records exact source/model/dependency identities and preprocessing; comparison consumes only validated scored artifacts.
- All new artifacts use a fresh output directory. Generated artifacts stay under `var/`; shared documentation records conclusions and reproduction commands.

## Tasks

### 1. Complete material and request contract

Files: `notebook_data.py`, `notebook_bundle.py`, `prepare_notebook_benchmarks.py`, `tests/test_notebook_data_v3.py`.

- [x] Write failing cases for QASPER FLOAT/unmapped questions, caption material, MultiHop source/date metadata, and gold mutation invariance.
- [x] Implement v3 without selecting examples based on gold evidence availability.
- [x] Add a v3 request format with benchmark-appropriate concise/list/single-paragraph output requirements and no annotation fields in generation requests.
- [x] Rebuild saved bundles and test identity/hash rejection plus v2 readability.

Representative acceptance: after replacing every annotation answer/evidence with another valid value, `documents` and generated requests remain identical. QASPER keeps both ordinary and FLOAT questions; MultiHop temporal input contains the original `published_at`.

### 2. Prediction submission boundary

Files: new `src/rag_eval/benchmark_submission.py`, CLI export entry, tests; integration in `notebook_runner.py`/`notebook_scoring.py`.

- [x] Reject duplicate/unknown case IDs and cross-bundle run imports in failing tests.
- [x] Export SN raw answers and observed anchors using selected v3 requests; move gold-dependent diagnostics entirely after generation.
- [x] Freeze the method identity and record complete, failed, missing and non-answer counts without silently removing rows.
- [x] Test complete and interrupted submissions and verify that reading does not call models.

### 3. Official scoring adapters

Files: new `src/rag_eval/benchmark_official.py`, scorer CLI, targeted tests; existing ALCE exporter reused.

- [x] Freeze/check official source versions before invocation.
- [x] QASPER: implement canonical predicted-answer export and official evaluator for an explicitly frozen question scope; evidence score only when actual predicted evidence exists. Full 1451-question generation is still pending.
- [x] MultiHop: upstream answer scorer identity preserved; retrieval metrics evaluated only from real ranked retrieval output, with upstream quirks disclosed.
- [x] ALCE: real citation conversion, fixed CLI preprocessing/parameters and batch scoring adapter; model metrics stay explicitly pending if unavailable. Actual model scoring is still pending.
- [x] QMSum: reproduce author-confirmed ROUGE invocation and calibrate against released HMNet outputs; retain unresolved historical segmentation/file-order differences.
- [x] Test failure/incomplete inputs, preprocessing counterexamples, malformed score output and score scale using independent reference behavior.

### 4. Runnable reference methods

Files: new generic baseline module/CLI and tests, reusing existing explicit model adapter where appropriate.

- [x] Implement BM25 over public text for all four suites, preserving input scope and ranked evidence.
- [x] Implement full-context only when the complete input fits an explicit budget; reject silent truncation.
- [x] Use the same benchmark output requirements and explicit model identity as SN comparisons; do not claim identical internal prompts or retrieval-only causality.
- [x] Test gold invariance, corpus boundaries, context budgets, empty/failed generations and durable saved predictions.

### 5. Comparable results and external reference evidence

Files: new comparison module/CLI and tests; official resources and experiment guide.

- [x] Reject mismatched data, preprocessing, scorer identity, duplicate attempts and missing expected cases in primary comparisons.
- [x] Produce per-case paired scores, full completion/coverage counts and separate quality/cost reporting; absent observations remain unavailable.
- [x] Preserve paper/meeting grouping for later uncertainty estimates; small smoke results cannot establish ranking. Statistical inference is not implemented or claimed.
- [x] Record a bounded candidate matrix with published-reference versus recomputed versus controlled-rerun labels. Prioritize public predictions or runnable code and explicitly disclose original model/input differences.
- [ ] Obtain and align complete public predictions or rerun representative published methods; produce an actual external-method comparison. The candidate matrix and local BM25 control do not satisfy this item.

### 6. Integration and real-input verification

- [x] Run focused tests during changes and the full Python/JS regression after integration.
- [x] Verify complete QASPER/QMSum public data preparation and QMSum scorer calibration without depending on old SN runs.
- [x] Verify complete MultiHop/ALCE public files after acquisition. The user enabled a proxy and separately authorized local download/acceptance; full checks completed on 2026-09-24, including five ALCE ordinary retrieval variants.
- [x] Run fresh one-question SN and BM25 experiments on QASPER and QMSum; save raw answers, official scores and comparison reports. These are real model calls and pipeline acceptance only.
- [ ] Run ALCE's actual full model evaluator with verified offline model/tokenizer resources.
- [ ] Freeze the full experiment configuration only after smoke validation; report any resource requirement that prevents real model/NLI execution separately from completed implementation.
- [x] Update the current experiment guide to remove the superseded old-results requirement and provide concrete new commands and result limitations.

## Evidence log

- Worktree: `.worktrees/benchmark-protocol-correctness`, branch `feat/benchmark-protocol-correctness`, starting commit `e022c60`.
- Initial regression: 446 passed, 1 skipped with `PYTHONPATH=. .venv/bin/pytest -q`; skipped test needs explicit sibling product path in this worktree. The final regression will provide `SILICON_NOTEBOOK_PROJECT_ROOT`.
- Existing synthetic formula probe found no differences on 348 metric comparisons, but did reproduce ALCE newline and QASPER missing-prediction differences. It is not a full official protocol validation.

### Final regression and review

Run from this implementation worktree on 2026-09-24 after the last code change:

```bash
PYTHONPATH=. \
SILICON_NOTEBOOK_PROJECT_ROOT=/home/wabiwabi/silicon-notebook/project \
QMSUM_ROUGE_HOME="$PWD/var/qmsum-official-calibration/hmnet-source/ThirdParty/ROUGE/ROUGE-1.5.5" \
PERL5LIB="$PWD/var/qmsum-official-calibration/perl-local/extracted/usr/lib/x86_64-linux-gnu/perl5/5.38:$PWD/var/qmsum-official-calibration/perl-local/extracted/usr/share/perl5" \
.venv/bin/pytest -q

node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs
```

Results: **657 Python tests passed, zero skipped, 10.51 seconds; 34 JavaScript tests passed, zero skipped.** The Python run includes the real Perl scorer regression. This worktree's `.venv` points to the main evaluation checkout; no dependency was installed through that shared symlink. Perl dependencies were unpacked into ignored `var/` directories.

Confirmed review findings and fixes:

| Failure mechanism | Correction and checked boundary |
| --- | --- |
| Malformed ALCE numeric prefixes could bypass citation validation | Match upstream prefix parsing; invalid or unshown candidates map outside the valid range; keep the raw answer |
| ALCE empty ASQA/ELI5 outputs change AutoAIS's eligible questions | Extract fixed upstream preprocessing/segmentation, record actual per-metric case IDs, reject comparisons with different eligible IDs even when counts match |
| Unknown or duplicate QASPER predicted evidence could be dropped before scoring | Keep false-positive/duplicate slots and raw responses; unknown IDs map to a deterministic reserved nonparagraph sentinel |
| A legacy gold-bearing request on v3 data could be exported | Require request-v3 for SN official export; reject gold-bearing generation records at scoring preparation |
| Reference method identity omitted implementation/runtime | Freeze evaluator sources, product snapshot, packages and normalized runtime before the first generation |
| Cost reports omitted measured information or could imply zeros for missing data | Preserve observed latency boundaries and usage coverage; aggregate compatible provider records only; unavailable currency/token values stay unavailable |
| NLTK could load unrecorded global tokenizers despite NLTK_DATA | Restrict `nltk.data.path` before first tokenization and perform a real resource preflight; include launcher hash in scorer identity |

The final independent review reproduced the NLTK fallback with actual NLTK source, then verified the fix. It also independently checked all QASPER full-context evidence offsets and full-context planning for 1451 QASPER / 281 QMSum questions; fixed ALCE AutoAIS control flow agreed with recorded eligibility on preprocessing counterexamples. No concrete important unresolved scoring/comparison defect was found. This review did not run real ALCE weights or GPU inference.

### Real data and scorer evidence

All paths below are relative to the implementation worktree and are ignored local artifacts, not committed datasets.

- `var/benchmark-protocol-validation/qasper-validation.json`: 416 papers / 1451 questions, zero excluded, every question's gold mutation leaves public inputs unchanged. All raw answer/type/evidence annotations were independently compared with the official parser. 331 questions with unmapped evidence are preserved. Raw JSON SHA256: `6e29ad410e6e39aa1936017fb965b30a20eb2e7751997f55b97c9d281aa884e5`.
- `var/benchmark-protocol-validation/qmsum-validation.json`: 35 meetings / 281 queries, 20718 turns including 17 empty turns, zero excluded; full reconstruction and gold mutation checks passed. Raw SHA256: `6bcd428211260ad2efae3af76cbaf6a7f5ae4bb5e1e59c45a4b8e89539cb9208`.
- `var/qmsum-official-calibration/verified-calibration/report.json`: actual Perl with author-confirmed flags; same ordering/segmentation yields 36.464 / 11.374 / 31.558 for both the current SPL wrapper and an independent pyrouge SEE wrapper. It does **not** exactly reproduce the historical 36.51 / 11.41 / 31.60.
- The calibration's `alignment-audit.json` finds 273 unique reference matches out of 279 public HMNet predictions versus 281 current questions. Six published references and eight current questions are unmatched. HMNet uses gold input and serves as scorer calibration only.
- MultiHop preserves the fixed upstream QA weak word-intersection rule. Retrieval checks all supplied actual ranks but only ranks at most 10 contribute to the upstream reported metrics. Its nonstandard MAP can exceed 1 and is explicitly named `upstream_map_at_10`; null questions are excluded from retrieval denominators. SN has no verified comparable ranking output, so those metrics remain pending for SN.

### Real model smoke artifacts

Model generation used the configured `qwen-turbo` service through the product client, with credentials only in the child environment. Each suite's canonical first question was selected before inference. The product snapshot was `74e9c61e4600102e5324553b55a85be15339660f`; model/configuration identity, public inputs and raw outputs are saved. Different prompts, runtime boundaries and models are not asserted equivalent merely from their names.

Under `var/benchmark-protocol-validation/`:

| Suite | SN run / submission | Reference submission | Validated score and comparison directories |
| --- | --- | --- | --- |
| QASPER | `smoke-qasper-sn/`, `submission-qasper-sn/` | `smoke-qasper-reference-validated-20260924/` | `score-qasper-{sn,reference}-validated-20260924/`, `comparison-qasper-validated-20260924/` |
| QMSum | `smoke-qmsum-sn-validated-20260924/`, `submission-qmsum-sn-validated-20260924/` | `smoke-qmsum-reference-validated-20260924/` | `score-qmsum-{sn,reference}-validated-20260924/`, `comparison-qmsum-validated-20260924/` |

Both comparison CLIs completed on one shared question per suite. No method ranking or general quality conclusion follows from these scores. QASPER SN lacks explicit predicted evidence, so Evidence F1 remains pending. QMSum's original SN run is `finished_with_errors` because the optional legacy Python ROUGE package was absent; its real answer was preserved and subsequently scored successfully by the separate Perl path. The diagnostic error was neither concealed nor used to trigger answer regeneration.

### Resource limits before the proxy retry — historical observation

The following download failures describe the earlier attempt. The user subsequently enabled a proxy and authorized retry; the complete dataset acquisition/validation supersedes those blockers as recorded below. ALCE model inference and external-method results remain pending.

- MultiHop's fixed HF revision is `71ac0d0bd1f951d2d6b70311f7d2ae404e1ffa82`; ALCE's is `334fa2e7dd32040c3fef931a123c4be1a81e91a0`. HF metadata, fixed resolve endpoints and official dataset-server routes repeatedly timed out or reset. The fixed MultiHop GitHub tree has no data fallback. Attempt details are in `var/benchmark-protocol-validation/validation-summary.json`; no complete bundle was made for either suite.
- ALCE's expected archive is 451297280 bytes with published SHA256 `eda837bf659a91b3648dc6e7ab6b17197664d93593857e8fdf3800b6aa6a98f0`; neither is a local completed-download verification. Full scoring still requires the explicit AutoAIS/QA/MAUVE offline model environment in the experiment guide.
- A public Multi-Meta-RAG prediction download was incomplete, so it was not scored as a complete external method. QASPER LED, MultiHop original/Multi-Meta-RAG, ALCE VANILLA/RERANK/Self-RAG and QMSum SegEnc/SummN remain bounded candidates requiring actual aligned outputs or controlled reruns.
- At that point the next independent work was to freeze and enlarge QASPER/QMSum experiments while finding a working download route. The route is now available. Full-context budgets must cover complete source inputs; budget errors must not select the successful subset for reporting. Freeze the declared question scope and generation settings before scoring, and preserve paper/meeting grouping for subsequent uncertainty analysis.
- Full four-suite results, statistical ranking and published-method comparison are **not complete**. New submission reports are JSON/Markdown and are not yet displayed in Dashboard. No historical server directory is needed to continue.

### Delivery state

Changes remain in `.worktrees/benchmark-protocol-correctness` on `feat/benchmark-protocol-correctness`, based on `e022c60`. No task commit, merge, push, production change or timer change was made. Main's existing uncommitted documentation was preserved. The product checkout has no tracked changes; existing `.deepeval/` and `results/` directories remain. Local `main` is 59 commits ahead of the saved `origin/main`; no fresh remote query was made during this protocol task.

### Proxy retry and complete-file acceptance — 2026-09-24

User steering: formal execution takes place on the server; this local step is specifically authorized to download MultiHop-RAG/ALCE and validate correctness. No new generation or large scoring models were run. No additional source-code defect was found, so this step adds evidence and corrects documentation rather than changing the implementation.

Acquisition now succeeded through the existing Windows proxy, without changing system proxy settings. Both MultiHop files match the pinned HF Git blob identities and byte counts. ALCE's 451297280-byte tar matches the published SHA256 `eda837bf659a91b3648dc6e7ab6b17197664d93593857e8fdf3800b6aa6a98f0`; all eight extracted JSON files have recorded hashes. Download receipts are in `var/benchmark-protocol-validation/vpn-acquisition-20260924/`.

- MultiHop: 2556 questions, 609 corpus documents, 6084 facts, zero exclusions. All metadata, original body text, evidence alignment and gold-mutation/public-request checks passed. An independent raw-corpus BM25 calculation matches all 20448 top-8 ranked entries from 4976 chunks. Original retrieval CLI and the bridge agree for all four metrics on the same 2255 non-null denominator.
- MultiHop QA: a fixed all-row mixed calibration covers extraction, non-extraction, whitespace/multiline and positive/negative cases. Original function and bridge agree for all 2556 rows (959 zeros, 1597 ones), and the full original CLI agrees overall and by question type. These constructed answers intentionally use scoring labels and are not method performance.
- ALCE: five ordinary files contain 4896 records across retrieval variants, representing 2948 questions across the three principal task files. All 479062 candidate slots and 1117 within-question duplicate slots survive. Each ordinary file passes full prepare/load and capacity refusal checks; no questions are removed. ASQA has 948 rows per retriever, QAMPARI 1000, ELI5 1000 with 31–100 candidates each.
- ALCE isolation: every private field is independently mutated on every row. Combined gold and candidate auxiliary changes leave all three reference strategies unchanged across 29376 prompt comparisons. All 4896 stored partitions match the actual complete scopes used for checks. All three real oracle files are audited and explicitly excluded from ordinary reference planning.
- ALCE scoring: full ASQA GTR and QAMPARI GTR calibration agrees with original CLI per-case/batch text metrics in 1898 and 5005 comparisons, respectively; normalized text matches on all 2948 principal rows. The original CLI also executes ASQA/ELI5 ROUGE with actual NumPy/NLTK/rouge-score. Only unused heavyweight imports are blocked by fail-on-use placeholders; no AutoAIS/QA/MAUVE scores are claimed. ELI5 semantic scoring remains pending.
- ALCE citation scope: actual fixed AutoAIS control flow and real NLTK yield exactly the same eligible question IDs as the adapter (711/1000/750 for the constructed ASQA/QAMPARI/ELI5 outputs). NLI is replaced only to trace execution, and its synthetic return values are discarded.
- Final Python regression with the existing explicit product/Perl environment: **657 passed, zero skipped, 10.76 seconds**. The previous 34 JavaScript checks remain relevant; no JavaScript or evaluation source was edited during this retry. Lightweight ALCE calibration dependencies were installed only into an ignored `--target` directory, never through the shared `.venv`.

Full evidence paths, hashes and remaining server work are recorded in [the current validation report](../../notebook-benchmark-real-data-validation.md). Earlier failed download logs and calibration outputs remain historical artifacts; they are not overwritten to imply earlier success.
