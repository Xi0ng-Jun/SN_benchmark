# Public System Experiment

Experimental implementation of the user-approved end-to-end plan.

- [x] Prepare first 50 questions per dataset and union of their labeled documents.
- [x] Import Markdown through project upload_sources into an isolated SQLite runtime.
- [x] Build chunk embeddings and use project Ask with exact synthesis-context capture.
- [x] Persist public/source/chunk mappings, per-question results and deterministic metrics.
- [x] Run native DeepEval on actual answers; omit reference-dependent metrics where references are absent.
- [x] Audit artifacts and publish report with sources, revisions, configuration and limitations.

Use a single resumable script under scripts/ and existing rag_eval helpers. Never use
production storage or synthesize missing gold answers. The evidence-union corpus is
a restricted-corpus experiment, not a full-corpus benchmark. No external wall-clock
timeout. Existing tests plus direct artifact verification are the primary checks.

## Completion Record

Completed on 2026-09-09 with status `completed_with_metric_errors`.

- 100 final answers, 101 persisted Ask attempts including one failed first attempt.
- 400 DeepEval metric attempts: 394 valid scores and 6 errors; another 100 reference-dependent metrics skipped for SciFact.
- 129 imported documents, 1177 embedded chunks; production tracked code unchanged.
- 19 tests passed, with one new metadata regression test. Artifact and database checks passed.
- [Report](../var/public-system-50/report.md), [artifact index](../results/README.md), and [provenance deviations](../var/public-system-50/provenance-notes.md).

This completes the bounded public-system experiment, not the full continuous-evaluation program.
Remaining exploratory work is tracked in [evaluation-status.md](evaluation-status.md).
