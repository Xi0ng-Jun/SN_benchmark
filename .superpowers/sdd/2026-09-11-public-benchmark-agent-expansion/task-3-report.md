# Task 3 Report: Scoring and applicability

Implemented in commit `97cc7f5`.

## Changes

- Added `rag_eval.public_expansion_scoring` with offline deterministic MMLU label matching and GSM8K numeric extraction/scoring.
- Added TruthfulQA answer scoring that preserves `behavior_label` and `evidence` as independent fields for later human review.
- Added Product applicability metadata and made unsupported HellaSwag/BBH Product bundles return `status: not_applicable` with `score` absent rather than treating them as zero.

## Verification

- `git diff --check` passed.
- Direct offline smoke checks passed for MMLU (`Final answer: B`), GSM8K (`#### 1,200`), TruthfulQA fields, and HellaSwag applicability.

## Concerns

- The parent Task 1 work currently adds `starter_protocol.py` imports for `public_expansion_protocol.py`; that module is not present in this worktree yet, so the full package test suite cannot run until Task 1 lands.
- DeepEval templates/scorers remain runtime-bound in `starter_native.py`; these new helpers intentionally make no SDK/model calls and can be wired by the expansion runner.
