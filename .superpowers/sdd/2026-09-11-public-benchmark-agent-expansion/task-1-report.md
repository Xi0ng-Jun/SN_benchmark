# Task 1 Report

Status: complete

Implemented the offline protocol and trace contracts for the public benchmark expansion.

- Added explicit metadata for MMLU, GSM8K, TruthfulQA, HellaSwag, and BBH, including Native/Product applicability and reasons for the two unsupported Product suites.
- Added task-aware answer normalization: A-D choice extraction, GSM8K numeric extraction with `####` preference, and TruthfulQA whitespace canonicalization. Every result retains `raw_output`.
- Added `TraceEnvelope` with `none`, `partial`, and `complete` completeness values, validation, serialization, and `missing()`.
- Re-exported expansion metadata and `TraceEnvelope` from `starter_protocol.py` without changing the frozen starter `SUITES` or protocol version.
- Added focused tests covering metadata, normalization, missing traces, and invalid completeness.

Verification:

- `python3 -m py_compile src/rag_eval/public_expansion_protocol.py src/rag_eval/starter_protocol.py` passed.
- Direct `python3` protocol smoke checks passed.
- `git diff --check` passed.
- `pytest -q tests/test_public_expansion_protocol.py` could not run because pytest is not installed in the environment. `uv run` began creating a new environment but stalled during dependency setup and was stopped.

Constraints respected: no data download, model call, SN Ask call, production project change, or online execution.
