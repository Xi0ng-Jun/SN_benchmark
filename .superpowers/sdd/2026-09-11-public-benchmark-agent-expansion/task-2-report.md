# Task 2 Report: Local Source Adapters

Implemented local-only JSONL adapters for MMLU, GSM8K, TruthfulQA, HellaSwag, and BBH in `src/rag_eval/public_expansion_sources.py`. Records preserve the raw source object, zero-based source line, task/domain, expected answer mapping, fixed source revision, per-record hash, and source-file SHA256. Moving revisions (`main`, `master`, `latest`, `unknown`, `pending`) are rejected.

Input validation rejects empty fields, boolean MMLU labels, non-integer HellaSwag labels, and GSM8K answers without a non-empty numeric `####` final value. Expansion manifests now have an explicit verified loader through `rag_eval.starter_protocol.load_bundle`.

HellaSwag integer labels may be encoded as numeric strings (for example, `"2"`); booleans, non-integers, and out-of-range values remain rejected. Malformed manifest artifacts and case rows are normalized to `ValueError`.

Extended `scripts/prepare_public_starter.py` to accept expansion suites and build offline bundles without importing DeepEval or downloading data. Existing starter-suite preparation remains on its original path.

Validation:

- `python3 -m py_compile src/rag_eval/public_expansion_sources.py scripts/prepare_public_starter.py tests/test_public_expansion_sources.py` passed.
- `git diff --check` passed.
- Local MMLU preparation smoke test passed and emitted all five bundle artifacts.
- Expansion bundle loader smoke test passed (`mmlu`, one case).
- Focused pytest command could not run because `pytest` is not installed (`No module named pytest`).

No network, model, SN Ask, or production code/configuration was used.
