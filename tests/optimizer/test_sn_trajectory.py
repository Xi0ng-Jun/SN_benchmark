"""SN trajectory views, predicate features, and SNAdapter wiring (method-v3 §2.3)."""
from __future__ import annotations

import json

import pytest

from rag_eval.optimizer.adapter import EvalResult, Predicate, Version
from rag_eval.optimizer.loop import ProtocolConfig, select_analysis_cases
from rag_eval.optimizer.sn_adapter import SNAdapter
from rag_eval.optimizer.sn_trajectory import (
    diagnostic_view, observation, read_rows, render_views, trace_features,
)


def _row(case_id, *, spans=None, clarify=False, answer="A", gold="GOLD-SECRET"):
    return {
        "case_id": case_id, "status": "clarification" if clarify else "success",
        "prediction": answer, "behavior": {"kind": "answer_returned"},
        "trace": {"trace_id": "t", "completeness": "partial" if spans else "none", "spans": spans or []},
        "product_record": {
            "question": f"question {case_id}",
            "intent_preview": {"needs_clarification": clarify, "confidence": 0.5,
                               "ambiguities": [{"question": "which?"}] if clarify else []},
            "response": {"grounded": True, "evidence_level": "grounded"},
            "deterministic": {"evidence_hit": 1, "evidence_coverage": 0.9,
                              "citation_count": 2, "citation_valid_count": 2, "citation_invalid_count": 0},
            "qasper_evidence": {"gold": gold}, "predicted_evidence": [gold],
            "anchor_documents": {"d": gold}, "retrieval_context": ["ctx text"],
            "usage": {"total_tokens": 100},
        },
    }


SPANS = [
    {"step_type": "plan", "summary": "p", "detail": {"sub_queries": ["q1", "q2"], "source": "intent"}},
    {"step_type": "retrieve", "summary": "r", "detail": {"count": 0, "result_ids": []}},
    {"step_type": "search_chunks", "summary": "s", "detail": {"found": 3, "result_ids": ["a", "b", "c"]}},
    {"step_type": "reflect", "summary": "x", "detail": {"next_action": "answer", "sufficient": False, "no_progress": True}},
    {"step_type": "synthesis", "summary": "y", "detail": {"citations": 2, "anchors": 1}},
]


def _write_run(tmp_path, rows, phase="finished"):
    run = tmp_path / "run"
    run.mkdir()
    (run / "outputs.jsonl").write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    (run / "state.json").write_text(json.dumps({"phase": phase}), encoding="utf-8")
    return run


# -- projection ------------------------------------------------------------

def test_view_excludes_gold_and_scorer_fields():
    text = json.dumps(diagnostic_view(_row("c", spans=SPANS)), ensure_ascii=False)
    assert "GOLD-SECRET" not in text
    assert "evidence_hit" not in text and "evidence_coverage" not in text


def test_observation_excludes_gold_and_scorer_fields():
    text = json.dumps(observation(_row("c", spans=SPANS)))
    assert "GOLD-SECRET" not in text and "evidence_hit" not in text


def test_trace_features_counts_steps():
    f = trace_features(_row("c", spans=SPANS))
    assert f["n_retrieval_calls"] == 2 and f["n_empty_retrievals"] == 1
    assert f["n_reflect_no_progress"] == 1 and f["final_sufficient"] is False
    assert f["n_sub_queries"] == 2 and f["synthesis_anchors"] == 1


def test_trace_features_missing_trace_is_none_not_zero():
    f = trace_features(_row("c", clarify=True))
    assert f["n_retrieval_calls"] is None and f["n_empty_retrievals"] is None
    assert f["clarification_requested"] is True  # intent stage is still observed


def test_id_lists_become_counts():
    steps = diagnostic_view(_row("c", spans=SPANS))["steps"]
    assert steps[2]["n_result_ids"] == 3 and "result_ids" not in steps[2]


def test_render_budget_drops_whole_cases_and_reports():
    items = [(f"c{i}", "failure", diagnostic_view(_row(f"k{i}", spans=SPANS))) for i in range(10)]
    one = len(render_views(items[:1], 10**6)[0])
    text, shown, omitted = render_views(items, one * 3 + 10)
    assert (shown, omitted) == (3, 7) and "7 selected case(s) omitted" in text


# -- reading ---------------------------------------------------------------

def test_read_rows_parses_only_wanted(tmp_path):
    run = _write_run(tmp_path, [_row("a"), _row("b"), _row("c")])
    assert set(read_rows(run, ["a", "c"])) == {"a", "c"}


def test_read_rows_missing_file_is_empty(tmp_path):
    assert read_rows(tmp_path / "nope", ["a"]) == {}


# -- SNAdapter.features ----------------------------------------------------

def _stubs(run, ids):
    return {t: [{"run_dir": str(run), "case_ids": [t]}] for t in ids}


def test_features_matches_v2_signature_on_trace_field(tmp_path):
    run = _write_run(tmp_path, [_row("a", spans=SPANS), _row("b", clarify=True)])
    adapter = SNAdapter({})
    sig = {"kind": "range", "field": "trace.n_empty_retrievals", "min": 1}
    out = adapter.features(_stubs(run, ["a", "b", "missing"]), Predicate(sig, "decrease"))
    assert out == {"a": True, "b": None, "missing": None}


def test_features_behaviour_field_signature(tmp_path):
    run = _write_run(tmp_path, [_row("a", clarify=True), _row("b")])
    sig = {"kind": "regex", "field": "status", "pattern": "clarification"}
    out = SNAdapter({}).features(_stubs(run, ["a", "b"]), Predicate(sig, "decrease"))
    assert out == {"a": True, "b": False}


def test_features_does_not_eval_strings(tmp_path):
    run = _write_run(tmp_path, [_row("a")])
    out = SNAdapter({}).features(_stubs(run, ["a"]), Predicate("__import__('os')", "decrease"))
    assert out == {"a": None}


def test_cache_only_for_finished_runs(tmp_path):
    run = _write_run(tmp_path, [_row("a")], phase="predicting")
    adapter = SNAdapter({})
    adapter.diagnostic_views(_stubs(run, ["a"]))
    assert adapter._traj_cache == {}


# -- SNAdapter.analyse -----------------------------------------------------

class _FakeHypotheses:
    def __init__(self):
        self.records = [{"id": "old", "status": "draft", "parent_candidate_id": "P",
                         "card": {"title": "old card", "implementer_brief": "x"}}]

    def list(self, task_id):
        return list(self.records)


class _FakeRoundOps:
    def __init__(self, hypotheses):
        self.hypotheses, self.texts = hypotheses, []

    def agent(self, role, candidate_id, **kw):
        self.calls = getattr(self, "calls", []) + [(role, candidate_id, dict(kw))]
        self.texts.append(kw["text"])
        self.keys = getattr(self, "keys", []) + [kw["key"]]
        self.hypotheses.records += [
            {"id": "new1", "status": "draft", "parent_candidate_id": "P",
             "card": {"title": "empty retrieval", "implementer_brief": "fix retrieval",
                      "contract": {"slice_predicate": {"kind": "range", "field": "trace.n_empty_retrievals", "min": 1},
                                   "manipulation": {"direction": "decrease", "threshold": 0.5}}}},
            {"id": "other", "status": "draft", "parent_candidate_id": "OTHER",
             "card": {"title": "other parent", "implementer_brief": "y"}},
        ]
        return {"status": "idle"}


def test_analyse_sends_views_and_binds_cards_to_request_and_parent(tmp_path):
    run = _write_run(tmp_path, [_row("a", spans=SPANS), _row("b", clarify=True), _row("ok")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "parent_candidate_id": "P", "run_id": "R"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0)
    cards = adapter.analyse(_stubs(run, ["a", "b", "ok"]), 4,
                            case_roles={"a": "failure", "b": "failure", "ok": "control"}, round_id=3)
    assert ops.keys == ["v3/R/analyse/3/P"]  # deterministic: run, round, parent
    assert cards[0].ref == "new1"
    assert [c.failure_mode_desc for c in cards] == ["empty retrieval"]
    assert cards[0].predicate.boolean_expression["field"] == "trace.n_empty_retrievals"
    text = ops.texts[0]
    assert "[failure]" in text and "[control]" in text and "GOLD-SECRET" not in text
    assert "\"a\"" not in text  # opaque refs, not task ids
    assert len(text) <= 16384
    assert adapter.last_analysis["shown"] == 3


def test_analyse_renders_history_labels_only_within_budget(tmp_path):
    run = _write_run(tmp_path, [_row("a", spans=SPANS), _row("ok")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "parent_candidate_id": "P", "run_id": "R"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        analyst_view_budget=4000)
    history = [dict(title=f"old idea {i}", label="screen_fail") for i in range(60)]
    history += [dict(title="leaky", label="score=0.71", delta=0.3),          # not a §2.3 label
                dict(title="adopted idea", label="rejected", attribution="supported",
                     attribution_possibly_biased=True, delta_val=0.12)]
    adapter.analyse(_stubs(run, ["a", "ok"]), 2, case_roles={"a": "failure", "ok": "control"},
                    round_id=1, history=history)
    text = ops.texts[0]
    assert "- adopted idea -> rejected; attribution: supported (possibly biased)" in text
    assert "leaky" not in text and "0.71" not in text and "0.12" not in text and "0.3" not in text
    assert "old idea 59 -> screen_fail" in text and "- old idea 0 ->" not in text   # oldest trimmed
    assert "older entr(ies) omitted" in text
    info = adapter.last_analysis
    assert info["history_chars"] <= 1000 and info["history_omitted"] > 0
    assert info["history_chars"] + info["evidence_chars"] <= 4000
    assert info["shown"] == 2


def test_analyse_without_history_has_no_history_section(tmp_path):
    run = _write_run(tmp_path, [_row("a")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "run_id": "R", "parent_candidate_id": "P"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0)
    adapter.analyse(_stubs(run, ["a"]), 1, round_id=1, history=[])
    assert "already tried" not in ops.texts[0] and adapter.last_analysis["history_chars"] == 0


def test_analyst_is_a_node_analyst_of_the_rounds_parent(tmp_path):
    """Regression: scope="iteration" needs two evaluated v2 candidates; round 1 has only C0."""
    run = _write_run(tmp_path, [_row("a")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "run_id": "R"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0)
    adapter.analyse(_stubs(run, ["a"]), 1, round_id=1, history=[],
                    parent=Version("c0hash", metadata={"candidate_id": "P"}))
    role, candidate_id, kw = ops.calls[0]
    assert (role, candidate_id) == ("analyst", "P") and "scope" not in kw
    assert "proposal draft --parent P " in kw["text"]
    with pytest.raises(ValueError, match="parent candidate"):
        adapter.analyse(_stubs(run, ["a"]), 1, round_id=2, history=[])
    assert len(ops.calls) == 1


def test_view_budget_must_fit_message_cap():
    with pytest.raises(ValueError):
        SNAdapter({}, analyst_view_budget=20000)


# -- loop case selection ---------------------------------------------------

def test_select_analysis_cases_is_fixed_rule():
    ev = EvalResult(per_task_scores={"a": 0.0, "b": 0.3, "c": 0.9, "d": 1.0, "e": 0.2},
                    trajectories={t: [{"run_dir": "r"}] for t in "abcde"}, costs={}, error_classes={})
    cfg = ProtocolConfig(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=10, T=0.1, n_fresh=10,
                         analyst_max_failures=2, analyst_n_controls=1)
    traj, roles = select_analysis_cases(ev, cfg)
    assert [t for t, r in roles.items() if r == "failure"] == ["a", "e"]  # worst first, capped
    assert sum(r == "control" for r in roles.values()) == 1
    assert set(traj) == set(roles)
