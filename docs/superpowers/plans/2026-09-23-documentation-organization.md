# Evaluation Documentation Organization Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Reorganize the evaluation documentation so current capabilities and constraints are the default path while historical records remain traceable.

**Architecture:** Keep a small current documentation layer at the repository and `docs/` roots. Keep active protocol and operational documents in `docs/`; move only retired handoff and operation snapshots to `docs/archive/2026-09/`. Preserve process plans and specs in their existing directories, adding status labels instead of rewriting their history.

**Tech Stack:** Markdown, Git, Python 3 link scanner, pytest, Node test runner.

**Spec:** `docs/superpowers/specs/2026-09-23-documentation-organization-design.md`

## Global Constraints

- Do not delete historical documents or experiment artifacts.
- Do not turn server reports into local verification claims.
- Do not change benchmark scoring, runtime code, SN code, data bundles, or result files.
- Retired commands remain searchable in archived records but must disappear from current entry instructions.
- Preserve links from current documents to historical evidence where those links explain a decision.

---

### Task 1: Establish the current documentation navigation

**Files:**
- Create: `docs/README.md`
- Modify: `README.md`
- Modify: `docs/evaluation-status.md`
- Modify: `docs/evaluation-context.md`

**Interfaces:**
- `README.md` links to `docs/README.md` and the current status/context documents.
- `docs/README.md` links to each current protocol and the archive index.
- `evaluation-status.md` is the current snapshot consumed by README and handoff readers.

- [x] **Step 1: Write the current navigation content**

  Add a short `docs/README.md` with sections for current status, active protocols, operational guides, design/reference material, and archive. Link only to files that exist on the merged `main` tree.

- [x] **Step 2: Rewrite the README current section**

  Replace the date-stacked current narrative with the merged-main facts from the spec, current Dashboard/Notebook/Agent entrypoints, the no-online-experiment boundary, and links to `docs/README.md`, `docs/evaluation-status.md`, and `docs/evaluation-context.md`. Keep installation, testing, and historical result references that remain valid.

- [x] **Step 3: Rewrite the status snapshot**

  Replace the stale “branches not merged” opening with the verified `main`/remote relationship, current implementation list, fresh local test evidence, server-report boundary, untracked presentation-artifact note, and the next evidence-driven actions. Link older detailed reports instead of copying their chronology.

- [x] **Step 4: Rewrite the stable context**

  Retain the project goal and evaluation principles, then consolidate stable scope, protocol IDs, isolation rules, evidence categories, and explicit non-goals. Remove date-by-date instructions that are now historical; link the relevant current protocol or archive entry.

- [x] **Step 5: Inspect the rendered Markdown paths**

  Run `git diff --check` and a relative-link scan over `README.md` and `docs/**/*.md`. Fix any link introduced by this task before moving on.

---

### Task 2: Label current and historical topic documents

**Files:**
- Create: `docs/archive/README.md`
- Modify: `docs/experiment-dashboard.md`
- Modify: `docs/notebook-benchmarks.md`
- Modify: `docs/notebook-benchmark-experiment-plan.md`
- Modify: `docs/native-agent-evaluation.md`
- Modify: `docs/native-scoring-recovery.md`
- Modify: `docs/server-agent-tracing-prompt.md`
- Modify: `docs/benchmark-metrics-reference.md`
- Modify: `docs/notebook-data-corrections.md`
- Modify: `docs/notebook-rescoring.md`
- Modify: `docs/qmsum-bm25-baseline.md`
- Modify: `docs/ifeval-direct-scoring.md`
- Modify: `docs/agent-judge-input-inspection.md`
- Modify: `docs/sn-execution-tracing.md`
- Modify: `docs/qmsum-next-iteration.md`

**Interfaces:**
- Current topic headers link back to `docs/evaluation-status.md` and `docs/README.md`.
- Historical topic headers link to their current replacement and `docs/archive/README.md`.

- [x] **Step 1: Add the archive policy**

  Create `docs/archive/README.md` describing `current`, `historical snapshot`, `historical design`, `retired protocol`, and `server report` labels, and list the four files that will be moved in Task 3.

- [x] **Step 2: Add status labels to current topics**

  Add one short status paragraph to each current topic file. State whether the document is a current protocol, implementation guide, or server operation guide, and link to the current status snapshot.

- [x] **Step 3: Mark retained historical topics**

  Add a consistent historical banner to `agent-judge-input-inspection.md`, `sn-execution-tracing.md`, and `qmsum-next-iteration.md`. Point to `native-agent-evaluation.md` or `native-scoring-recovery.md` as applicable and keep their original evidence.

- [x] **Step 4: Remove retired commands from current instructions**

  In current files, replace operational references to `inspect_agent_inputs.py`, `--capture-agent-trace`, and `--judge/--dag` with links to the native protocol or recovery guide. Leave those strings inside historical files when they describe what actually happened.

---

### Task 3: Move retired snapshots without losing traceability

**Files:**
- Create: `docs/archive/2026-09/`
- Move: `docs/session-handoff-2026-09-23.md` to `docs/archive/2026-09/session-handoff-2026-09-23.md`
- Move: `docs/agent-judge-input-inspection.md` to `docs/archive/2026-09/agent-judge-input-inspection.md`
- Move: `docs/sn-execution-tracing.md` to `docs/archive/2026-09/sn-execution-tracing.md`
- Move: `docs/qmsum-next-iteration.md` to `docs/archive/2026-09/qmsum-next-iteration.md`
- Modify: all current Markdown files that link to moved files

**Interfaces:**
- Archive paths are stable evidence links from current status/protocol documents.
- No script or runtime code reads these Markdown paths.

- [x] **Step 1: Move only the four retired snapshots**

  Use `git mv` so history remains visible. Do not move active protocol files or `docs/superpowers/` plans/specs.

- [x] **Step 2: Update relative links**

  Update current docs and archive documents to use `archive/2026-09/...` paths from `docs/`, and `../../...` paths from the archive directory where required.

- [x] **Step 3: Verify archive discoverability**

  Confirm every moved file appears in `docs/archive/README.md`, and no current entry links to a nonexistent old path.

---

### Task 4: Validate the documentation boundary and repository behavior

**Files:**
- Modify: any Markdown file identified by the validation scans
- Test: none added; use existing repository tests and scanners

**Interfaces:**
- Documentation-only changes must leave Python and JavaScript behavior unchanged.

- [x] **Step 1: Run the link and retired-command scans**

  Run the repository-relative Markdown link scanner and `rg` checks for retired commands in `README.md`, `docs/README.md`, `docs/evaluation-status.md`, `docs/evaluation-context.md`, and current topic documents. Expected result: no missing links and no retired command in current instructions.

- [x] **Step 2: Run formatting checks**

  Run `git diff --check`. Expected result: no new whitespace errors in changed files.

- [x] **Step 3: Run the existing regression suites**

  Run `PYTHONPATH=. .venv/bin/pytest -q` and `node --test tests/dashboard_core.test.cjs tests/explorer_core.test.cjs tests/map_view.test.cjs`. Expected result: all existing tests pass with no source behavior changes.

- [x] **Step 4: Review the final status map**

  Run `git status --short`, `git diff --stat`, and `rg -n '当前|历史|退役|服务器转述|未核实'` over the current entry docs. Confirm each important claim has a status category and no current document repeats the old branch-not-merged claim.

- [x] **Step 5: Commit the documentation organization**

  Commit the reviewed changes with `git add README.md docs && git commit -m "docs: align evaluation guides with current implementation"`.
