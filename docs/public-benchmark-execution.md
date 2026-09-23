# Public Benchmark Execution Ledger

> 历史实验台账：本页只描述早期公开 benchmark 运行，不作为当前服务器命令或质量结论。


Plan: `docs/superpowers/plans/2026-09-09-public-benchmark-evaluation.md`

Started: 2026-09-09. User authorized execution after reviewing the plan.

## Decisions

- Existing benchmark directory is a standalone workspace outside the production Git repository. Changes stay here; source snapshots and hashes provide execution provenance without adding benchmark files to production.
- Human labels cannot be produced by coding agents. Prepare a blind review packet and mark calibration pending; run the baseline with explicitly provisional semantic scoring and no quality release gate.
- Model service currently has one chat model (`qwen-turbo`); generation and judge share it. Record shared-model bias, and keep human calibration pending.
- Main product calls use separate dataset/mode runtime directories; disable response caching and profile/experience injection to prevent cross-question contamination. Embedding/index caches stay isolated.

## Tasks

| Task | Owner | Status | Contract |
| --- | --- | --- | --- |
| Data acquisition and freezing | public_data | completed | Each dataset: 100 questions, 200 documents; frozen source hashes |
| Product runtime and capture | root | completed, 400 primary attempts | 383 answers persisted, 17 native clarification guard failures |
| Quality scoring and reporting | quality_reports | paused by user | Smoke report complete; baseline scoring remains partial; blind review pending |
| Baseline and comparison | root + quality_reports | paused by user | Do not treat the partial baseline as a complete quality baseline |
| Automation and handoff | root | foundation installed, product work pending | Offline checks and weekly timer exist; semantic gate remains advisory |

## Interface Review

| Shared boundary | Decision |
| --- | --- |
| Data -> runtime | id/dataset/question/references/gold_document_ids/split/group/answer_type plus diagnostic/calibration/smoke selectors |
| Runtime -> metrics | final answer, actual retrieval_context, context_supported, independent capture records; no reference evidence in generation |
| Metrics -> report | score records keyed by dataset/id/mode/metric/repeat, explicit valid/error/skipped |
| Run -> comparison | comparison_identity records data and scoring protocol; product configuration is the compared treatment |
| Human review -> gate | No labels synthesized by agents; quality results remain advisory until reviewed |

Baseline verification before edits: existing 19 tests passed.

2026-09-09 execution notes (historical status at each observation; superseded by the 2026-09-10 entries below):
- Started isolated preparation in `var/public-benchmark/20260909-baseline-v1`.
- Before any Ask or judge calls, finalized scorer hash to `77ae5ebc1b069e78a4926a0ba89868fd921956d9804c101a370c338aded38a19`; Contextual Precision is explicitly skipped without confirmed comparable ranking. The prepare-only root manifest was updated from its preliminary scorer hash. Original invocation source snapshots remain preserved.
- Full offline suite currently passes 34 tests, including the judge schema contract and usage observation.
- Preparation initially overlapped final input adapter changes. Identity guard rejected the first attempted Ask before any product call. The four completed native corpora were then checked source by source against final IDs, original text hashes and titles: all 800 match, and all four answer tables were empty. The prepare-only manifests/identities were archived in `preask-provenance/`; `finalize_preask.py` records the one-time reconciliation. Final input copies are retained in the run's `inputs/` directory and used by all subsequent commands.
- Initial pilot completed 20/20 native outputs with persisted answers verified; chunk mean 2.285 seconds / 6355.5 observed tokens, reasoning mean 6.033 seconds / 16106.7 observed tokens. This is a small cost sample, not a quality conclusion.
- Judge contract pilot: 24 metric records, 20 valid and 4 Contextual Precision explicitly skipped. No judge errors in this pilot. Native audit found no remaining integrity error after recognizing the product's exact, intent-conditional completeness warning prefix.
- Current offline suite: 42 passing tests. Scoped regression/smoke plans now use immutable planned keys; missing outputs cannot be hidden by baseline projection.
- Independent regeneration in `var/public-benchmark/reproduction-check` reproduced all four question/document files and the manifest byte-for-byte.
- Full Ask: 400 attempts; SQuAD chunk 100 answers, reasoning 90; DROP chunk 100, reasoning 93. All 17 no-answer outcomes reproduce the native deterministic clarification guard without a model call. Details are in `failure-diagnostics.json`; original questions were not rewritten to bypass this product behavior.
- Six evaluator-only whitespace persistence checks were corrected after exact native payload validation, without replaying Ask. Original output files and attempt statuses are retained in `persistence-correction/` and `attempts.jsonl`.
- Judge identity now also enforces the custom adapter, case conversion, native judge transport sources and exact DeepEval/Pydantic/OpenAI/HTTPX/json-repair versions. Archived pilot-era sources and configuration matched in all four cells; `judge-identity-finalization/` records why existing pilot scores remain valid.
- Full native DB/capture audit passed for 383 stored answers. Full scoring is in progress.
- Weekly user timer installed and enabled; next trigger observed as 2026-09-14 00:22 CST. A separate one-shot timer started smoke run `20260909T122132Z-smoke-7f0fd2da`; its completion remains pending.
- Latest offline entrypoint verification: 44 tests passed; CLI checks passed. Human calibration packet contains 40 frozen selections; labels remain pending.
- Deliberate duplicate automation trigger `20260909T123048Z-smoke-606884a3` was rejected by the global lock before starting any stage. Its terminal failed status is retained as the failure-path verification, not counted as a product evaluation run.
- 2026-09-10: User asked to pause concrete benchmark execution and return focus to Silicon Notebook itself. No further judge or product calls should be started until explicitly requested. The baseline directory contains a partial judge run; the completed smoke run remains auditable. Future work should first define product-specific scenarios and human labels, then resume or create a new benchmark run under an explicitly chosen protocol.
- At 10:23 CST the four cells contained 360 / 169 / 123 / 6 primary metric records (SQuAD chunk/reasoning, DROP chunk/reasoning): 658 of 1440 planned, 581 valid, 77 skipped, no recorded judge errors. Last write was at 10:01:07; processes were absent and no terminal success record existed. Exit cause is unknown. No judge/product repeats have run.
- Smoke finished all five stages: 40 outputs, 38 successful answers, 2 product errors; 114 valid metrics and 6 skipped; integrity audit passed. It started without a complete baseline, so no completed-baseline comparison was validated.
- The weekly timer was disabled and stopped on 2026-09-10 to honor the execution pause. Unit templates and local artifacts remain. Offline checks freshly passed 47 tests and CLI help checks.
- This directory is now initialized as its own Git repository for `git@github.com:Xi0ng-Jun/SN_benchmark.git`. Historical runs retain their original snapshots and hashes. Source, frozen public samples, documentation and curated status are versioned; raw runs, product snapshots, credentials, private candidates and local dependencies are excluded. See `results/public-benchmark-status.md` and `docs/development-roadmap.md`.