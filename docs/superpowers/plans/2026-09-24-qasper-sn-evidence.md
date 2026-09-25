# QASPER SN Evidence F1 Implementation Plan

**Goal:** Convert final SN citations into frozen, public-source QASPER evidence predictions and score them using the pinned official evaluator.

**Architecture:** A pure projector consumes a canonical public paragraph catalogue, saved synthesis/answer observations, and a bounded snapshot of cited source objects. A read-only capture adapter freezes that snapshot at generation time; an explicit export option can recover older saved runs without rewriting them. Official preparation replays the projector and rejects inconsistent evidence. Mapping failure is separate from model invalid citations and never removes a question.

**Spec:** User-approved mapping policy in the conversation; current source investigation is in `docs/notebook-benchmark-standards-and-conformance.md` §3.4. Repeated prose citations and overlapping chunks form a unique paragraph-ID prediction set; identical text at different original IDs is not deduplicated. Invalid references retain nonmatching slots. Partial visibility maps only observed content; unsupported or ambiguous provenance fails closed. No gold or generated evidence-selection step.

**Workspace:** Existing linked worktree `feat/benchmark-protocol-correctness`; preserve all prior uncommitted work. No product behavior changes, model calls, commit, merge, or push in this task.

## Work and acceptance

- [x] Add failing tests for public-source catalogue, multi-paragraph/heading/duplicate/invalid/empty/truncated citations, capture failures, and snapshot tampering.
- [x] Implement `qasper_evidence.py` for public catalogue, read-only source snapshot, deterministic projection and replay verification. Derive catalogue without changing frozen v3 document bytes or generation requests.
- [x] Wire runtime capture, method identity, explicit old-run recovery (`export-sn --qasper-evidence`), and official input verification. Preserve answer on mapping error; no partial Evidence F1 denominator.
- [x] Add integration tests for capture/export/official preparation and error states. Ensure incompatible policies cannot silently compare.
- [x] Run real saved QASPER sample through recovery and original official CLI, preserve original run hashes, and independently check complete-public-data catalogue/real-parser mapping coverage without generation.
- [x] Run full Python regression with actual Perl dependencies; review final diff and update current docs with exact evidence and remaining limits.

Baseline on 2026-09-24: 657 Python tests passed, 0 skipped, 10.10 seconds. Existing source audit proves one final chunk citation reaches original paragraph `4:0`; it does not constitute a complete implementation.

## Verification commands

Focused: `.venv/bin/pytest -q tests/test_qasper_evidence.py tests/test_benchmark_official.py tests/test_benchmark_submission.py tests/test_notebook_v3_runtime.py`.

Full: existing explicit `SILICON_NOTEBOOK_PROJECT_ROOT`, `QMSUM_ROUGE_HOME`, `PERL5LIB` command in the parent [protocol plan](2026-09-23-benchmark-protocol-correctness.md#evidence-log). Offline acceptance commands/results will be recorded below; no model service invocation is required.

## Evidence log — 2026-09-24

Implementation was completed in the linked worktree and committed as `128246c`, then pushed to `origin/feat/benchmark-protocol-correctness`. No product edits, generated model calls, dependency installation or deployment occurred. Product tracked files remain unchanged; original `.deepeval/` and `results/` remain present. The branch is not merged into local `main`.

Policy implementation SHA256: `057d74e54306f60f2ee49d10399619fb658c66c9dfe462c6691cfbb661160df6`. The source hash is part of method configuration; all QASPER scores also record the mapper dependency equally, so SN/reference share scorer identity.

TDD evidence: initial 18 pure mapper cases failed on the absent module, then passed. Four integration cases initially failed for missing derived catalogue/replay/mapping-error fields/policy enforcement; three runtime/export cases initially failed for missing integration. Independent review and real parser probes produced additional reproducible failures before fixes: trailing blank source offsets, cleanup exception, stripped code block losing a final original paragraph, bare SN evidence bypass, clipped context partition separators, folded image description provenance, and unknown keys with missing observations. New test file now has 40 cases; execute integration tests exercise success and mapping error without live models.

Independent reviewer final focused result: **83 passed, 0 failed, 0.79 seconds**. Reported important findings are closed. Ordinary sectioned/fallback and prediction-ID semantics were checked; no new reproducible high-impact issue reported. Review is supplementary evidence, not a substitute for the independent CLI and parser checks.

### Full regression

```bash
PYTHONPATH=. \
SILICON_NOTEBOOK_PROJECT_ROOT=/home/wabiwabi/silicon-notebook/project \
QMSUM_ROUGE_HOME="$PWD/var/qmsum-official-calibration/hmnet-source/ThirdParty/ROUGE/ROUGE-1.5.5" \
PERL5LIB="$PWD/var/qmsum-official-calibration/perl-local/extracted/usr/lib/x86_64-linux-gnu/perl5/5.38:$PWD/var/qmsum-official-calibration/perl-local/extracted/usr/share/perl5" \
.venv/bin/pytest -q
```

Final result: **697 passed, 0 skipped, 10.74 seconds** (baseline 657 plus 40 evidence tests). No frontend changes; the previous 34-test Dashboard result is historical, not rerun this turn. `git diff --check` passed.

### Real public parser and truncation probe

```bash
.venv/bin/python var/benchmark-protocol-validation/qasper-evidence-20260924/corpus_probe.py
```

Report: `var/benchmark-protocol-validation/qasper-evidence-20260924/corpus-report.json`.

- 416 papers, 23,111 public units, including 19,817 body paragraphs.
- Real SN parser produces 29,627 elements; 11,065 whole chunks and 81,316 chosen partial prefixes all project successfully, with no uncovered public units.
- Replacing every paper's `qas` with unrelated malformed annotations leaves the public catalogue identical.
- First pass exposed 12 conservative partial errors. Actual block offsets included more than one trailing newline; accepting **only whitespace** outside the single overlapping unit fixed these without allowing unseen tail paragraphs. Final report has zero errors.
- These are synthetic citation selections over actual public documents and real parser/chunk output. They do not establish generated SN quality, all character cuts, or all future Markdown/parser variants. Unsupported cross-unit partial transformations still fail closed.

### Real saved answer and original official CLI

```bash
.venv/bin/python var/benchmark-protocol-validation/qasper-evidence-20260924/acceptance.py \
  --output var/benchmark-protocol-validation/qasper-evidence-20260924/replay-v1
```

Use a **fresh** output directory for a repeat. Report: `replay-v1/report.json` under that directory.

- Original saved case `qasper:397a1e851aab41c455c2b284f5e4947500d797f0` exports through `--qasper-evidence` semantics; one final `[k1]` maps to original `4:0`, not the 16 fallback citation candidates.
- Fixed unmodified evaluator SHA256 `781aba7cd8e524bef4f0a1b4bf3504e5b02cb1d8d5bf32a8f0a89dfa83e86bfe` executed as its actual CLI with raw-format gold. Both CLI and bridge: Answer F1 `0.6666666666666666`, Evidence F1 `1.0`, denominators 1.
- Eight handwritten calibration cases cover extra evidence, duplicate text slots, invalid sentinel, both empty, missed evidence, FLOAT, and missing prediction. Original CLI batch Evidence F1 `0.625`; handwritten arithmetic and bridge per-case/complete-batch results agree.
- Existing BM25 submission rescored under the same current scorer; strict comparison succeeds with equal scorer ID. The single real case is only a chain acceptance, not a method ranking.
- 151 old run/reference files retain identical SHA256 before/after. The report records their hashes. Snapshot export does not mutate the live DB or original answer.

CLI entrypoint separately exercised:

```bash
.venv/bin/python scripts/benchmark_protocol.py export-sn \
  --bundle var/benchmark-protocol-validation/bundles/qasper-v3 \
  --runs var/benchmark-protocol-validation/smoke-qasper-sn \
  --case-ids qasper:397a1e851aab41c455c2b284f5e4947500d797f0 \
  --qasper-evidence \
  --output var/benchmark-protocol-validation/qasper-evidence-20260924/cli-submission
```

`var/` artifacts and acceptance scripts are locally ignored, not automatically distributed by Git. Current semantics and summarized evidence are in the tracked docs and regression tests. Transfer the full evidence directory separately if needed.

### Remaining limits and future triggers

The mapping policy is our disclosed method adaptation, while the evaluator is original. Current public input still includes abstract/captions and differs from the LED full_text reader. No new selector model was added. Final citation group/Chinese-marker parsing is supported for evidence; the pre-existing answer-body scoring profile still removes only its existing `[kN]` syntax and was not silently changed.

No full 1451-question SN generation or new real reasoning/sectioned model run occurred. Server acceptance must inspect mapping-error counts, especially generated context refinement, KG/external objects and unsupported Markdown source transformations. Failures preserve answer and whole-scope denominator; fix producer provenance or disclose unavailable evidence, never pick a gold-matching paragraph. Content hashes prove saved-data consistency, not authenticity against someone rewriting all artifacts and hashes.

Concrete regression triggers: parser stripping or text folding changes → verify rendered/source offset alignment; adding context partitions → check last clipped handle boundaries; new source object types → require precise public provenance before support; missing observation → classify instrumentation error before invalid model reference; failure cleanup → never erase an already generated answer. These fixes belong to the projector/capture boundary, not every consumer or the official evaluator.
