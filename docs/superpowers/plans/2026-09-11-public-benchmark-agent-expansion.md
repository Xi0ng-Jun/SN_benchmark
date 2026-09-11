# Public Benchmark and SN Agent Evaluation Expansion Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extend the offline protocol with MMLU, GSM8K, TruthfulQA, HellaSwag, and selected BIG-Bench Hard tasks, while reserving truthful trace fields for future SN Agent evaluation.

**Architecture:** Reuse starter protocol, runner, ledger, and report layers. Add local-only source adapters, task normalizers, applicability metadata, and an optional trace envelope. Product execution remains opt-in and is not run in this phase.

**Tech Stack:** Python 3.11+, JSONL, existing `rag_eval` modules, DeepEval 4.2.2 compatibility layer, pytest.

**Spec:** `docs/public-benchmark-agent-expansion-design.md`

## Global Constraints

- Do not modify Silicon Notebook production code/configuration.
- Do not download data, call models or SN Ask, resume baseline scoring, or enable the timer.
- Keep Native and Product tracks separate; preserve provenance and non-applicable states.
- Never fabricate traces or set uncalibrated quality thresholds.

### Task 1: Protocol and trace contracts

**Files:** `src/rag_eval/public_expansion_protocol.py`, `src/rag_eval/starter_protocol.py`, `tests/test_public_expansion_protocol.py`

- [ ] Add failing tests for suite metadata, answer normalization, and `TraceEnvelope.missing()`.
- [ ] Implement explicit metadata for all five suites, with Product false for HellaSwag/BBH.
- [ ] Implement task-aware MCQ, numeric, and TruthfulQA normalization while retaining raw output.
- [ ] Add trace completeness `none|partial|complete`; reject unknown values.
- [ ] Run focused tests and commit.

### Task 2: Local source adapters

**Files:** `src/rag_eval/public_expansion_sources.py`, `scripts/prepare_public_starter.py`, `tests/test_public_expansion_sources.py`

- [ ] Add failing tests for MMLU choice mapping and missing revision rejection.
- [ ] Implement local JSONL readers for MMLU, GSM8K, TruthfulQA, HellaSwag, and BBH.
- [ ] Preserve raw record, line number, task/domain, expected answer, source revision, and hash.
- [ ] Extend preparation entry point without network access or data download.
- [ ] Run focused tests and commit.

### Task 3: Scoring and applicability

**Files:** `src/rag_eval/public_expansion_scoring.py`, `src/rag_eval/starter_native.py`, `src/rag_eval/starter_product.py`, `tests/test_public_expansion_scoring.py`

- [ ] Add failing tests for MMLU label matching, GSM8K numeric extraction, and unsupported Product suites.
- [ ] Reuse official DeepEval Native templates/scorers where available.
- [ ] Implement deterministic normalization around structured-output failures.
- [ ] Return `not_applicable` rather than zero for unsupported Product suites.
- [ ] Keep TruthfulQA behavior labels and evidence fields separate for later human review.
- [ ] Run focused tests and commit.

### Task 4: Runner, ledger, and report integration

**Files:** `src/rag_eval/starter_runner.py`, `src/rag_eval/starter_results.py`, `src/rag_eval/starter_report.py`, `scripts/run_public_starter.py`, `scripts/report_public_starter.py`, `tests/test_public_expansion_reporting.py`

- [ ] Add failing tests proving Native/Product scores remain separate and missing traces suppress Agent metrics.
- [ ] Add suite/task identity, applicability, normalized answer, and trace envelope to append-only records.
- [ ] Extend CLI suite choices while preserving existing commands.
- [ ] Render per-suite/task summaries, non-applicable counts, and trace completeness.
- [ ] Run focused tests and commit.

### Task 5: Offline audit and documentation

**Files:** `scripts/check_public_expansion_offline.py`, `README.md`, `docs/evaluation-status.md`, `docs/development-roadmap.md`, `tests/test_public_expansion_offline.py`

- [ ] Add failing test for missing source hash.
- [ ] Implement manifest, suite, applicability, and trace-value checks with no online calls.
- [ ] Update docs to distinguish implemented offline logic from unrun/unverified experiments.
- [ ] Run focused tests and commit.

### Task 6: Verification and handoff

- [ ] Run only focused offline tests for the new modules.
- [ ] Check benchmark-repository status and recent commits.
- [ ] Confirm production repository is unchanged.
- [ ] Record that data is not frozen and online Native/Product/Agent runs remain pending.
