"""SN trajectory views, predicate features, and SNAdapter wiring (method-v3 §2.3)."""
from __future__ import annotations

import json
from types import SimpleNamespace

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

ANALYST = "agent:analyst"


class _FakeHypotheses:
    def __init__(self):
        self.records = [{"id": "old", "status": "draft", "parent_candidate_id": "P", "author": ANALYST,
                         "created_at": "2026-11-01T00:00:00+00:00",
                         "card": {"title": "old card", "implementer_brief": "x"}}]

    def list(self, task_id):
        return list(self.records)


class _FakeCreations:
    """v2 AgentCreationStore.list: one intent per creation key, prepared before the Agent drafts."""

    def __init__(self):
        self.intents = []

    def list(self, task_id, *, actor):
        return [i for i in self.intents if i["actor"] == actor]


class _FakeRoundOps:
    def __init__(self, hypotheses):
        self.hypotheses, self.texts = hypotheses, []
        self.creations = SimpleNamespace(store=_FakeCreations())

    @staticmethod
    def _key(kind, key):
        return kind + ":" + key

    def _agent_actor(self, key):
        return "alice"

    def agent(self, role, candidate_id, **kw):
        self.calls = getattr(self, "calls", []) + [(role, candidate_id, dict(kw))]
        self.texts.append(kw["text"])
        self.keys = getattr(self, "keys", []) + [kw["key"]]
        intent_key = self._key("agent-create", kw["key"])
        if any(i["request_key"] == intent_key for i in self.creations.store.intents):
            return {"status": "idle"}       # v2 replays the receipt: the same Agent, no new message
        self.creations.store.intents.append(dict(request_key=intent_key, actor="alice",
                                                 prepared_at="2026-11-02T10:00:00+00:00"))
        self.hypotheses.records += [
            {"id": "new1", "status": "draft", "parent_candidate_id": "P", "author": ANALYST,
             "created_at": "2026-11-02T10:01:00+00:00",
             "card": {"title": "empty retrieval", "implementer_brief": "fix retrieval",
                      "contract": {"slice_predicate": {"kind": "range", "field": "trace.n_empty_retrievals", "min": 1},
                                   "manipulation": {"direction": "decrease", "threshold": 0.5}}}},
            {"id": "other", "status": "draft", "parent_candidate_id": "OTHER", "author": ANALYST,
             "created_at": "2026-11-02T10:01:00+00:00",
             "card": {"title": "other parent", "implementer_brief": "y"}},
            {"id": "refiled", "status": "approved", "parent_candidate_id": "P", "author": "round:rebase-card:x",
             "created_at": "2026-11-02T10:01:00+00:00",
             "card": {"title": "a rebased copy", "implementer_brief": "z"}},
        ]
        return {"status": "idle"}


def test_analyse_sends_views_and_binds_cards_to_request_and_parent(tmp_path):
    run = _write_run(tmp_path, [_row("a", spans=SPANS), _row("b", clarify=True), _row("ok")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "parent_candidate_id": "P", "run_id": "R"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        evidence_dir=tmp_path / "evidence")
    cards = adapter.analyse(_stubs(run, ["a", "b", "ok"]), 4,
                            case_roles={"a": "failure", "b": "failure", "ok": "control"}, round_id=3)
    assert ops.keys == ["v3/R/analyse/3/P"]  # deterministic: run, round, parent
    assert cards[0].ref == "new1"
    assert [c.failure_mode_desc for c in cards] == ["empty retrieval"]
    assert cards[0].predicate.boolean_expression["field"] == "trace.n_empty_retrievals"
    text, evidence = ops.texts[0], _evidence(adapter)
    assert "[failure]" in evidence and "[control]" in evidence and "GOLD-SECRET" not in evidence
    assert "\"a\"" not in evidence  # opaque refs, not task ids
    assert adapter.last_analysis["evidence_path"] in text and adapter.last_analysis["evidence_sha256"] in text
    assert "### c1" in evidence and "### c1" not in text                                 # views are in the file
    assert adapter.last_analysis["shown"] == 3 and ops.calls[0][2]["brief"] == "method-v3"


def test_analyse_renders_history_labels_only_within_budget(tmp_path):
    run = _write_run(tmp_path, [_row("a", spans=SPANS), _row("ok")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "parent_candidate_id": "P", "run_id": "R"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        analyst_view_budget=4000, evidence_dir=tmp_path / "evidence")
    history = [dict(title=f"old idea {i}", label="screen_fail") for i in range(60)]
    history += [dict(title="leaky", label="score=0.71", delta=0.3),          # not a §2.3 label
                dict(title="adopted idea", label="rejected", attribution="supported",
                     attribution_possibly_biased=True, delta_val=0.12)]
    adapter.analyse(_stubs(run, ["a", "ok"]), 2, case_roles={"a": "failure", "ok": "control"},
                    round_id=1, history=history)
    text = _evidence(adapter)
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
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        evidence_dir=tmp_path / "evidence")
    adapter.analyse(_stubs(run, ["a"]), 1, round_id=1, history=[])
    assert "already tried" not in _evidence(adapter) and adapter.last_analysis["history_chars"] == 0


def test_analyst_is_a_node_analyst_of_the_rounds_parent(tmp_path):
    """Regression: scope="iteration" needs two evaluated v2 candidates; round 1 has only C0."""
    run = _write_run(tmp_path, [_row("a")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "run_id": "R"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        evidence_dir=tmp_path / "evidence")
    adapter.analyse(_stubs(run, ["a"]), 1, round_id=1, history=[],
                    parent=Version("c0hash", metadata={"candidate_id": "P"}))
    role, candidate_id, kw = ops.calls[0]
    assert (role, candidate_id) == ("analyst", "P") and "scope" not in kw
    assert "proposal draft --parent P " in kw["text"]
    with pytest.raises(ValueError, match="parent candidate"):
        adapter.analyse(_stubs(run, ["a"]), 1, round_id=2, history=[])
    assert len(ops.calls) == 1


def test_analyse_rerun_finds_cards_drafted_before_the_restart(tmp_path):
    """Regression (rehearsal): a kill while the analyst drafted lost its cards on rerun.

    Cards are bound to the durable v2 creation intent of the round's analyst (same key on
    rerun), not to "created after this call began".
    """
    run = _write_run(tmp_path, [_row("a")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    config = {"iteration_id": "I", "task_id": "T", "run_id": "R", "parent_candidate_id": "P"}
    first = SNAdapter(config, round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        evidence_dir=tmp_path / "evidence")
    first.analyse(_stubs(run, ["a"]), 3, round_id=1, history=[])     # the process dies after this
    replay = SNAdapter(config, round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        evidence_dir=tmp_path / "evidence")
    cards = replay.analyse(_stubs(run, ["a"]), 3, round_id=1, history=[])
    assert [c.ref for c in cards] == ["new1"]           # not the older card, nor the re-filed copy
    assert len(ops.creations.store.intents) == 1
    assert first.last_analysis["evidence_sha256"] == replay.last_analysis["evidence_sha256"]
    assert replay.last_analysis["evidence_reused"] and not first.last_analysis["evidence_reused"]


def test_view_budget_is_bounded():
    assert SNAdapter({}, analyst_view_budget=60000)._analyst_view_budget == 60000
    with pytest.raises(ValueError):
        SNAdapter({}, analyst_view_budget=60001)


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



def _evidence(adapter):
    from pathlib import Path
    return Path(adapter.last_analysis["evidence_path"]).read_text(encoding="utf-8")


def test_evidence_file_carries_large_evidence_and_the_message_stays_small(tmp_path):
    """User decision: views go in a file in the run directory; v2 message caps no longer bound them."""
    import hashlib
    from pathlib import Path
    from rag_eval.workbench.agent_messages import MAX_MESSAGE_BYTES
    rows = [_row(f"k{i}", spans=SPANS, answer="论文报告的结果如下。" * 40) for i in range(12)]
    run = _write_run(tmp_path, rows)
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "run_id": "R", "parent_candidate_id": "P"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        analyst_view_budget=60000, evidence_dir=tmp_path / "evidence")
    adapter.analyse(_stubs(run, [r["case_id"] for r in rows]), 2, round_id=4, history=[])
    info = adapter.last_analysis
    path = Path(info["evidence_path"])
    assert path == (tmp_path / "evidence" / "round-4-P.md").resolve() and path.is_absolute()
    data = path.read_bytes()
    assert hashlib.sha256(data).hexdigest() == info["evidence_sha256"]
    assert len(data) > 2 * MAX_MESSAGE_BYTES and info["shown"] == 12 and info["omitted"] == 0
    assert data.decode().startswith("# method-v3 analyst evidence\nformat: sn-v3-analyst-evidence-v1\nround: 4\n")
    assert info["message_bytes"] < 2000 and ops.texts[0] == adapter.last_analyst_text
    assert not list(path.parent.glob(".*.tmp"))          # atomic write left no temporary file
    # A replay renders the same bytes; an existing file is never rewritten.
    path.write_bytes(data + b"edited")
    again = SNAdapter({"iteration_id": "I", "task_id": "T", "run_id": "R", "parent_candidate_id": "P"},
                      round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                      analyst_view_budget=60000, evidence_dir=tmp_path / "evidence")
    again.analyse(_stubs(run, [r["case_id"] for r in rows]), 2, round_id=4, history=[])
    assert path.read_bytes() == data + b"edited" and again.last_analysis["evidence_reused"]
    assert again.last_analysis["evidence_sha256"] == hashlib.sha256(data + b"edited").hexdigest()


def test_analyse_needs_an_evidence_directory(tmp_path):
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "run_id": "R", "parent_candidate_id": "P"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0)
    with pytest.raises(RuntimeError, match="evidence directory"):
        adapter.analyse({}, 1, round_id=1, history=[])
    assert ops.texts == []


# -- §2.1 失败后的处理: history details, escalation, repair feedback -----------

def test_history_lines_carry_only_whitelisted_discrete_details():
    from rag_eval.optimizer.sn_adapter import _render_history
    history = [
        dict(title="worked but no gain", label="screen_fail", smoke="behaviour_changed"),
        dict(title="unchecked", label="screen_fail", smoke="unchecked"),
        dict(title="leaky smoke", label="screen_fail", smoke="r_after=0.31"),               # not a token
        dict(title="costly", label="unmeasurable", safeguards_breached=["cost_per_task", "0.71", "a b=2"]),
        dict(title="smoke only", label="smoke_fail", safeguards_breached=["cost_per_task"]),  # names only on unmeasurable
        dict(title="did not take effect", label="smoke_fail", repaired=True),
        dict(title="second step", label="rejected", rolled_back_segment=True, attribution="refuted",
             attribution_possibly_biased=True),
        dict(title="truthy", label="screen_fail", repaired="yes", rolled_back_segment=1),       # only True counts
    ]
    text, shown, omitted = _render_history(history, 10_000)
    assert (shown, omitted) == (8, 0)
    lines = [line for line in text.splitlines() if line.startswith("- ")]
    assert lines == [
        "- worked but no gain -> screen_fail; smoke: behaviour changed as predicted",
        "- unchecked -> screen_fail; smoke: behaviour unchecked",
        "- leaky smoke -> screen_fail",
        "- costly -> unmeasurable; breached safeguards: cost_per_task",
        "- smoke only -> smoke_fail",
        "- did not take effect -> smoke_fail; after repair",
        "- second step -> rejected; tried in a rolled-back segment; attribution: refuted (possibly biased)",
        "- truthy -> screen_fail",
    ]


def _escalation_adapter(tmp_path, **kw):
    tmp_path.mkdir(parents=True, exist_ok=True)
    run = _write_run(tmp_path, [_row("a", spans=SPANS), _row("ok")])
    hyps = _FakeHypotheses()
    ops = _FakeRoundOps(hyps)
    adapter = SNAdapter({"iteration_id": "I", "task_id": "T", "parent_candidate_id": "P", "run_id": "R"},
                        round_operations=ops, hypotheses=hyps, agent_poll_interval=0,
                        evidence_dir=tmp_path / "evidence", **kw)
    return adapter, ops, _stubs(run, ["a", "ok"])


def test_analyse_steps_up_the_budget_and_asks_for_another_direction(tmp_path):
    adapter, ops, stubs = _escalation_adapter(tmp_path, analyst_view_budget=4000, analyst_view_budget_step=1000)
    adapter.analyse(stubs, 2, round_id=1, history=[])
    assert adapter.last_analysis["budget"] == 4000 and adapter.last_analysis["no_adoption_rounds"] == 0
    assert "consecutive" not in ops.texts[0] and "consecutive" not in _evidence(adapter)
    adapter.analyse(stubs, 2, round_id=2, history=[dict(title="old", label="screen_fail")], no_adoption_rounds=2)
    assert adapter.last_analysis["budget"] == 6000
    assert "for 2 consecutive round(s): change direction" in ops.texts[1]
    assert "different from every hypothesis listed as already tried" in ops.texts[1]
    assert "no adoption under this parent for 2 consecutive round(s)\n" in _evidence(adapter)
    assert "(budget 6000 chars)" in _evidence(adapter)
    capped, _, stubs = _escalation_adapter(tmp_path / "x", analyst_view_budget=59000, analyst_view_budget_step=1000)
    capped.analyse(stubs, 1, round_id=1, history=[], no_adoption_rounds=5)
    assert capped.last_analysis["budget"] == 60000                       # MAX_ANALYST_VIEW_BUDGET
    with pytest.raises(ValueError):
        SNAdapter({}, analyst_view_budget_step=-1)


def test_repair_feedback_holds_predicate_values_and_views_without_scores_or_ids(tmp_path):
    (tmp_path / "p").mkdir()
    (tmp_path / "c").mkdir()
    parent_run = _write_run(tmp_path / "p", [_row("case-a", spans=SPANS), _row("case-b")])
    cand_run = _write_run(tmp_path / "c", [_row("case-a", spans=SPANS, answer="CANDIDATE ANSWER"),
                                           _row("case-b", answer="OTHER")])
    adapter = SNAdapter({}, evidence_dir=tmp_path / "evidence")
    pred = Predicate({"kind": "range", "field": "trace.n_empty_retrievals", "min": 1}, "decrease")
    from rag_eval.optimizer.adapter import Card

    def result(run):
        return EvalResult(per_task_scores={"case-a": 0.777, "case-b": 0.123},
                          trajectories=_stubs(run, ["case-a", "case-b"]), costs={}, error_classes={})
    content, stats = adapter._repair_feedback(Card("empty retrieval", "retry once", pred, ref="h1"), "CANDX", 2,
                                              result(cand_run), result(parent_run))
    assert content.startswith("# method-v3 smoke repair feedback\nformat: sn-v3-repair-feedback-v1\nattempt: 2\n")
    assert "- s1: predicate parent=true, previous attempt=true" in content
    assert "- s2: predicate parent=not computable, previous attempt=not computable" in content
    assert "should decrease from parent to candidate" in content
    assert "### s1 [parent]" in content and "### s1 [previous attempt]" in content and "CANDIDATE ANSWER" in content
    # Opaque refs (the fixture's question text itself names the case, as in the analyst's views).
    assert "### case-a" not in content and "- case-a" not in content
    assert "0.777" not in content and "0.123" not in content
    assert "GOLD-SECRET" not in content
    assert stats == dict(cases=2, views=4, views_shown=4, views_omitted=0, budget=12000)
