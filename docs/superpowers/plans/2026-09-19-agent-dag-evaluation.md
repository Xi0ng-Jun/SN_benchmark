# Agent/DAG Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add an offline, completeness-aware Agent/DAG evaluation path for existing Silicon Notebook benchmark runs.

**Architecture:** Adapt saved SN reasoning steps into a versioned `AgentTraceEnvelope`, calculate deterministic diagnostics for every record, and only pass complete envelopes to DeepEval trajectory metrics. Provide an opt-in DAGMetric and a CLI that writes separate agent artifacts without changing existing benchmark scores or runs.

**Tech Stack:** Python 3.11+, dataclasses, JSONL artifacts, pytest, optional DeepEval 4.2.x.

**Spec:** `docs/superpowers/specs/2026-09-19-agent-dag-evaluation-design.md`

## Global Constraints

- Do not download benchmark data, start Silicon Notebook, or modify Silicon Notebook production code.
- Deterministic diagnostics must run without importing DeepEval or making network requests.
- `none`, `partial`, and `complete` completeness values are distinct; incomplete traces never receive trajectory scores.
- Agent/DAG scores remain advisory and are not combined with benchmark primary metrics.
- Existing `outputs.jsonl` and `scores.jsonl` are read-only; write agent artifacts to a separate output directory.

---

### Task 1: Add trace adapter and completeness audit

**Files:**
- Create: `src/rag_eval/agent_trace.py`
- Create: `tests/test_agent_trace.py`

**Interfaces:**
- `AgentTraceEnvelope.from_record(record, *, case_id, mode) -> AgentTraceEnvelope`
- `AgentTraceEnvelope.to_dict() -> dict`
- `AgentTraceEnvelope.to_deepeval_dict() -> dict`
- `audit_trace(raw, *, final_output_available, context_available, citations_available) -> tuple[str, str]`

- [x] **Step 1: Write failing tests** for missing traces, SN partial traces, explicit complete traces, malformed spans, and JSON serialization.
- [x] **Step 2: Run `pytest -q tests/test_agent_trace.py` and verify the new imports/API fail.**
- [x] **Step 3: Implement the dataclass, raw SN step normalization, conservative completeness audit, and DeepEval-safe projection.
- [x] **Step 4: Run the focused tests and verify they pass.**

### Task 2: Add deterministic Agent diagnostics and mode comparison

**Files:**
- Create: `src/rag_eval/agent_diagnostics.py`
- Create: `tests/test_agent_diagnostics.py`

**Interfaces:**
- `diagnose_trace(envelope: AgentTraceEnvelope) -> dict`
- `compare_modes(rows: Iterable[dict]) -> list[dict]`

- [x] **Step 1: Write failing tests** for action counts, repeated retrieval, termination extraction, duration totals, and chunk/reasoning pairing without zero-filling.
- [x] **Step 2: Run the focused tests and verify failure.**
- [x] **Step 3: Implement deterministic projection and paired comparison.**
- [x] **Step 4: Run focused tests and verify pass.**

### Task 3: Add optional DeepEval trajectory adapter

**Files:**
- Create: `src/rag_eval/agent_deepeval.py`
- Create: `tests/test_agent_deepeval.py`

**Interfaces:**
- `build_test_case(case: dict, output: dict, envelope: AgentTraceEnvelope, diagnostics: dict) -> LLMTestCase`
- `evaluate_trajectory(test_case, envelope, *, metrics, model=None) -> list[dict]`
- `available_agent_metrics() -> tuple[str, ...]`

- [x] **Step 1: Write failing tests** that build an `LLMTestCase`, reject partial traces as `not_applicable`, and expose the four supported metric names without invoking a model.
- [x] **Step 2: Run focused tests and verify failure.**
- [x] **Step 3: Implement lazy DeepEval import, trace projection, metric factory, completeness gate, score/reason/error serialization.**
- [x] **Step 4: Run focused tests and verify pass without network.**

### Task 4: Add first DAG metric definition

**Files:**
- Create: `src/rag_eval/agent_dag.py`
- Create: `tests/test_agent_dag.py`

**Interfaces:**
- `build_evidence_path_metric(*, model=None, threshold=None) -> DAGMetric`
- `dag_metadata(diagnostics: dict) -> dict`

- [x] **Step 1: Write failing tests** for optional DeepEval import, DAG construction, metadata normalization, and fixed metric name.
- [x] **Step 2: Run focused tests and verify failure.**
- [x] **Step 3: Implement the retrieval/context/citation/support graph using DeepEval nodes and fixed terminal scores.**
- [x] **Step 4: Run focused tests and verify pass without measuring a model.**

### Task 5: Add offline evaluator CLI and artifacts

**Files:**
- Create: `src/rag_eval/agent_evaluator.py`
- Create: `scripts/evaluate_agent_traces.py`
- Create: `tests/test_evaluate_agent_traces.py`
- Modify: `README.md`
- Modify: `docs/evaluation-status.md`

**Interfaces:**
- CLI: `python scripts/evaluate_agent_traces.py --run-dir RUN --output-dir OUT [--judge --metrics ...]`
- Writes `agent-traces.jsonl`, `agent-diagnostics.jsonl`, `agent-scores.jsonl`, `agent-summary.json`.

- [x] **Step 1: Write failing tests** using a temporary synthetic run with one partial and one complete record; assert separate output files, counts, no run mutation, and default no-judge behavior.
- [x] **Step 2: Run focused tests and verify failure.**
- [x] **Step 3: Implement read-only JSONL loading, case/output extraction, diagnostics, optional judge invocation, mode comparison, and summary writing.**
- [x] **Step 4: Run focused tests and verify pass.**
- [x] **Step 5: Update README/status with command and boundaries.**

### Task 6: Full offline verification

**Files:**
- No new production files.

- [x] **Step 1: Run the focused Agent/DAG tests.**
- [x] **Step 2: Run the repository Python test suite with network attempts blocked.**
- [x] **Step 3: Inspect the diff for accidental SN changes, data downloads, timer changes, or result overwrites.**
- [x] **Step 4: Record validation and remaining limitations in the final response.**
