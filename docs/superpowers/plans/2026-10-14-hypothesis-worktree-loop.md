# Hypothesis worktree and first verified loop

Goal: complete the user's approved steps 1–3, then stop. No stage replay or full-suite expansion.

## Design and acceptance

Each approved hypothesis owns a fresh Paseo worktree, pinned to the trusted SN project's current committed HEAD. Existing dirty experimental trees are never implicitly copied. Save its workspace identity and immutable start snapshot. The Task binding is the current Changes-page selection; changing it must preserve prior bindings, session cwd, captured run provenance, and the card-specific diff used for leak scanning. Browser requests choose only a card, never an arbitrary path, command or base ref.

Paseo creation is an external operation: persist intent before dispatch, use a deterministic branch per card, and reconcile uncertain results by read-only workspace/Git checks. A timeout must not create a second tree. Old manually registered worktrees remain usable for old cards. New UI implementation flow prepares/selects the card's tree before creating its Agent.

The real loop uses a new task and one small fixed QASPER scope with target and guard predictions registered before implementation. Analysis and implementation are distinct pi Agents. Only the approved implementation fields go to the implementation Agent; no benchmark questions/gold/case IDs. Actual SN generation remains gold-free, uses captured source, independent runtime and official scoring. Preserve original artifacts and every outcome, including regression or no improvement. Human approval of a concrete card and budget is required unless already covered by explicit session authorization; do all independent preparation first.

## Work items

- [x] 1. Backend worktree service and regression tests: card scope, approval, immutable pin, repeated/concurrent requests, uncertain creation recovery, wrong-repo/workspace rejection, old binding/session preservation. Own new `hypothesis_worktrees.py`, tests, and the small snapshot extraction in `task_worktree.py`.
- [x] 2. Integrate API, card UI, Agent creation/brief and leak-check scope; configure opt-in local creator. Test that two cards never cross directories and that baseline/candidate runs remain available after selection. Browser-test actual creation using an isolated synthetic bridge/repo.
- [x] 3. Real analysis: read existing runs and old H1 stopped evidence, ask pi analyst for one minimal falsifiable card; prepare fixed scope and budget without generation. Independently check the proposal and obtain any missing concrete authorization.
- [x] 4. Real implementation: create the approved card's workspace through the new path; send only the implementation brief to pi; review diff, synthetic tests, source identity and leak scan.
- [x] 5. Run the approved fixed baseline/candidate scope and official scoring, paired flips, target/guard checks and cost. Write effect and cause conclusions, deployment and limits; stop here.

## Execution record

- Existing linked evaluation worktree retained; its large prior uncommitted work is outside this delta. No broad staging, resets, merges or pushes.
- Prior H1 experiment has a stopped receipt; do not treat old 'implementing' prose as permission to resume it or consume its budget.
- Root coordinates API/UI and experiment; a scoped implementation agent owns worktree persistence, then an independent reviewer checks the integrated change. Reviews inspect the session delta, not all inherited untracked work.

### 2026-10-14 implementation and experiment preparation

- Backend/API/UI integration complete and deployed to the managed workbench. Service was restarted only after all four saved executions were terminal. Live HTTP 200, new capability enabled, served index/script bytes equal the verified build. No real hypothesis worktree or SN generation has been dispatched yet.
- Scoped backend/integration/roles/creation tests: 66 passed. Job provenance/adjacent tests: 60 passed (overlapping scopes; not 126 distinct tests). One existing Starlette warning. TypeScript and Expo export passed; isolated Chromium fixture exercised delayed attachment, approved-card worktree creation, selection, implementer brief-only messages and old analyst switching.
- Independent review found and verified corrections for saved-card dispatch, legacy brief/leak scope, NULL-cwd sessions, and prepared-source provenance after selection. No open Important/Blocking findings in reviewed scope. Same captured source bytes associated with multiple worktrees remain explicitly ambiguous at dispatch and are rejected; a per-preparation worktree association is deferred.
- Real pi analyst `9bc78b81-be5e-4ade-97d6-34348379b147` completed on existing observations. New task `da5e6fdf04504a1590ce3ed56282789e`, fixed plan `b0e4aca3abd94f93946c6afb24171ed1`, all three cases of the first complete non-holdout qualifying partition retained.
- Analyst draft `2980f20f773d44f9b11a34021247bd72` remains as original evidence. Root review found pre-render sorting changes budget survivors; reviewed draft `d616c0858d8a4035bab5cf729d0cca6a` instead sorts only final emitted records, preserving text, truncation, original keys and mapping. The observed failure is not proof of a causal ordering defect.
- Artifacts: `var/sn-eval/r1-loop-20261014/{analysis.md,reviewed-card.json,reviewed-card-receipt.json,fixed-plan.json,budget-proposal.json,deployment-receipt.json}`. Budget proposal: same three cases twice, ¥0.50 and 1200s each, total ¥1, no run retries or paid judge. Observed same-partition request estimate: 9 chat + 21 embedding each; actual counts may differ.
- Specific card and budget authorization requested asynchronously; do not approve on the user's behalf while pending. Steps 2–3 of the user-facing workflow remain incomplete. After approval: create fresh card tree through API, capture baseline before edits, separate pi implementer receives approved fields only, review synthetic checks/leak scan, run both captures and official scoring, inspect flips/guards, record effect separately from cause, then stop. Do not resume stopped H1 or add stage replay.

- User subsequently explicitly approved revised hypothesis and ¥1 total. Decision, authorization, real workspace creation, immutable baseline capture, baseline execution, implementation Agent creation/brief/message receipts are now saved alongside the proposal. Real worktree: `sn-hyp-d616c0858d8a4035bab5cf729d0cca6a`, workspace `wks_1c3fdb3b67b6a358`, clean start at `0680aa12313a5d3e67f4e0a4be94144a5b6af28c`. Baseline source `81a682e4e65f66b5f1a69c3b18bce6d637a6dbb6c04605676fe357b182599a5a`, execution `b3edf004621d40529d3980854e605c3b` launched via API. Separate pi implementer `7360ab31-e9ab-4052-8bb9-82af80f60692` received only the server-resolved approved brief and generic execution instructions through workbench message API. Baseline and implementation currently in progress; no final quality claim.


### Final result — user steps 1–3 complete; stopped

- New live worktree and separate pi implementation completed. Final four-file hash verification and independent review clear; 271 relevant SN tests passed, 9,331 pure ordering-property cases passed, leak-check clean. Full backend lane interrupted/no result; no claim of full G1.
- First baseline failed importing after three ~60-second embedding failures/circuit break, with three missing cases and no budget stop. Original artifacts retained. User then explicitly raised budget to less than ¥8. Adopted ¥7.50 total, original attempt cap ¥0.50 plus recovery baseline/candidate caps ¥3 each (sum maximum ¥6.50). Recovery uses original captured clean source and fixed plan, never the now-edited tree as baseline.
- Recovery baseline execution `271ecd31c52a480fbda5d0345d0be820` and candidate `50f00516bf234e179e531bc1357c32c5` each finished, 3/3 success, official scoring and paired admission complete. Observed two improvements/one decline. Target prediction fails (0.256410→0.250000), guard passes, final card `concluded`, effect `regression_or_tradeoff`, cause `undecided`; candidate not adopted.
- A/B query rewrites and selected evidence differ despite identical frozen configuration; C holds rewrite semantics and evidence set constant while changing order. Single paired observations cannot establish causal ordering benefit. Captured source comparison finds exactly the two approved production files changed out of 663; official identities and invocation config hashes match.
- Total SN request-ledger accounting ¥0.0437579, of which ¥0.0110385 remains conservative reservation for old timeout calls; not an invoice and excludes pi Agents. Full report: `docs/experiments/2026-10-14-r1-ordering-loop.md`. Workbench conclusion, user-facing history and evidence receipts saved. No further models, suites, stage replay, commit, merge or push after step 3.
