"""W6: sealed one-shot lineage evaluation through the v2 SealedService (fakes only)."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from rag_eval.optimizer import sn_sealed
from rag_eval.optimizer.adapter import EvaluationUnavailable, Version
from rag_eval.optimizer.ledger import Lineage
from rag_eval.optimizer.sn_adapter import SNAdapter
from rag_eval.optimizer.sn_sealed import evaluate_lineage_sealed, lineage_node_set
from rag_eval.workbench.sealed import lineage_curve, node_set_hash

BUDGET = dict(timeout_seconds=3600)
CONFIG = dict(run_id="r1", actor="operator", iteration_id="it1", task_id="t1")


def adapter(**kwargs):
    return SNAdapter(CONFIG, eval_poll_interval=0.0, **dict(dict(eval_timeout=5.0), **kwargs))


def build_lineage():
    """seed (certified) -> a (certified) -> b (rolled back); a -> c (provisional)."""
    lineage, versions = Lineage(), {}
    for h, parent in (("h-seed", None), ("h-a", "h-seed"), ("h-b", "h-a"), ("h-c", "h-a")):
        lineage.add_provisional(h, parent, None, None, None, 0.0)
        versions[h] = Version(h, metadata=dict(candidate_id="c-" + h[2:]))
    lineage.certify("h-seed", {})
    lineage.certify("h-a", {})
    lineage.rollback("h-b")
    return lineage, versions


class FakeSealed:
    """Mirrors v2 SealedService: one claim, progress while running, frozen curve after."""

    def __init__(self, polls_until_done=2, k=3):
        self.claim, self.result, self.calls, self.reads = None, None, [], 0
        self.polls_until_done, self.k = polls_until_done, k

    def run_lineage(self, iteration_id, *, budget, actor, confirmed=False, k=None, nodes=None):
        self.calls.append(dict(iteration_id=iteration_id, budget=budget, actor=actor, confirmed=confirmed,
                               k=k, nodes=list(nodes)))
        if confirmed is not True:
            raise ValueError("sealed-run requires explicit human confirmation")
        if self.claim is not None and self.claim["status"] != "dispatching":
            raise RuntimeError("sealed study was already claimed; no second call is allowed")
        self.claim = dict(nodes=list(nodes), hash=node_set_hash(nodes), actor=actor, status="dispatched",
                          k=k or self.k)
        return dict(iteration_id=iteration_id, nodes=len(nodes), k=self.claim["k"], status="dispatched")

    def lineage(self, iteration_id, *, alpha=.05):
        self.reads += 1
        if self.claim is None:
            return None
        if self.result is not None:
            return json.loads(json.dumps(self.result))
        base = dict(kind="lineage", status=self.claim["status"], confirmed_by=self.claim["actor"],
                    claimed_at="2026-11-02T00:00:00+00:00", k=self.claim["k"], nodes=len(self.claim["nodes"]),
                    node_set_hash=self.claim["hash"], metric=None, n=None, mean=None, curve=None,
                    progress=dict(slots=3, dispatched=3, terminal=0))
        if self.claim["status"] != "dispatched":
            return base  # v2: no curve before every slot is dispatched
        if self.polls_until_done > 0:
            self.polls_until_done -= 1
            return base
        cases = [f"c{i}" for i in range(6)]
        nodes = [dict(candidate_id=cid, frozen_commit="h-" + cid[2:],
                      scores={c: .1 * i + .01 * j for j, c in enumerate(cases)})
                 for i, cid in enumerate(self.claim["nodes"])]
        base.update(lineage_curve(nodes, alpha=alpha), status="finished", metric="str_em", n=len(cases))
        self.result = base
        return json.loads(json.dumps(base))


def test_node_set_is_seed_plus_every_node_in_append_order_with_state_marks():
    lineage, versions = build_lineage()
    nodes = lineage_node_set(lineage, versions)
    assert [n["candidate_id"] for n in nodes] == ["c-seed", "c-a", "c-b", "c-c"]
    assert [n["index"] for n in nodes] == [0, 1, 2, 3]
    assert [n["state"] for n in nodes] == ["certified", "certified", "rolled_back", "provisional"]
    assert [n["style"] for n in nodes] == ["solid", "solid", "dashed", "hollow"]


def test_v2_entry_called_once_with_the_candidate_ids_then_polled_to_the_curve(monkeypatch):
    lineage, versions = build_lineage()
    sealed, sleeps = FakeSealed(polls_until_done=3), []
    monkeypatch.setattr(sn_sealed.time, "sleep", sleeps.append)
    out = evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    assert sealed.calls == [dict(iteration_id="it1", budget=BUDGET, actor="operator", confirmed=True, k=None,
                                 nodes=["c-seed", "c-a", "c-b", "c-c"])]
    assert len(sleeps) == 3 and sealed.reads == 5  # pre-check + 3 progress reads + the settled one
    json.dumps(out)
    assert out["status"] == "finished" and out["k"] == 3 and out["m"] == 3
    assert out["correction"] == "holm-p+bonferroni-interval" == out["metadata"]["correction"]
    assert out["metadata"] == dict(iteration_id="it1", sealed_claim_id="it1",
                                   node_set_hash=node_set_hash(["c-seed", "c-a", "c-b", "c-c"]),
                                   created_at="2026-11-02T00:00:00+00:00", confirmed_by="operator",
                                   correction="holm-p+bonferroni-interval", n_families=None)
    points = out["points"]
    assert [(p["index"], p["version_hash"], p["state"], p["style"]) for p in points] == [
        (0, "h-seed", "certified", "solid"), (1, "h-a", "certified", "solid"),
        (2, "h-b", "rolled_back", "dashed"), (3, "h-c", "provisional", "hollow")]
    assert [round(p["score"], 6) for p in points] == [.025, .125, .225, .325]
    assert all(p["n"] == 6 and p["k"] == 3 for p in points)
    assert points[0]["p_holm"] is None and all(p["p_holm"] is not None for p in points[1:])
    assert all(p["lower_adj"] <= p["lower"] for p in points[1:])


def test_second_call_replays_and_never_reruns():
    lineage, versions = build_lineage()
    sealed = FakeSealed(polls_until_done=0)
    first = evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, k=3, sealed=sealed)
    second = evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    assert first == second and len(sealed.calls) == 1


def test_rerun_after_poll_timeout_only_polls_the_dispatched_claim(monkeypatch):
    lineage, versions = build_lineage()
    sealed = FakeSealed(polls_until_done=10**6)
    monkeypatch.setattr(sn_sealed.time, "sleep", lambda s: None)
    with pytest.raises(EvaluationUnavailable, match="claim is kept"):
        evaluate_lineage_sealed(adapter(eval_timeout=0.0), lineage, versions, confirmed=True, budget=BUDGET,
                                sealed=sealed)
    sealed.polls_until_done = 0
    out = evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    assert out["status"] == "finished" and len(sealed.calls) == 1


def test_interrupted_dispatch_is_resumed_through_v2():
    lineage, versions = build_lineage()
    sealed = FakeSealed(polls_until_done=0)
    ids = ["c-seed", "c-a", "c-b", "c-c"]
    sealed.claim = dict(nodes=ids, hash=node_set_hash(ids), actor="operator", status="dispatching", k=3)
    out = evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    assert out["status"] == "finished" and [c["nodes"] for c in sealed.calls] == [ids]


def test_unconfirmed_call_raises_before_any_v2_contact():
    lineage, versions = build_lineage()
    sealed = FakeSealed()
    for confirmed in (False, None, 1, "yes"):
        with pytest.raises(ValueError, match="human confirmation"):
            evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=confirmed, budget=BUDGET, sealed=sealed)
    assert sealed.calls == [] and sealed.reads == 0


@pytest.mark.parametrize("metadata", [{}, dict(candidate_id=""), dict(candidate_id=None)])
def test_missing_candidate_id_is_a_clear_error_and_claims_nothing(metadata):
    lineage, versions = build_lineage()
    versions["h-b"] = Version("h-b", metadata=metadata)
    sealed = FakeSealed()
    with pytest.raises(ValueError, match=r"lineage node 2 \('h-b'\) carries no v2 candidate_id"):
        evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    del versions["h-b"]
    with pytest.raises(ValueError, match="has no Version"):
        evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    assert sealed.calls == [] and sealed.reads == 0


def test_claim_for_another_node_set_or_k_is_refused_not_rerun():
    lineage, versions = build_lineage()
    sealed = FakeSealed(polls_until_done=0)
    evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    lineage.add_provisional("h-d", "h-c", None, None, None, 0.0)
    versions["h-d"] = Version("h-d", metadata=dict(candidate_id="c-d"))
    with pytest.raises(ValueError, match="another node set"):
        evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    lineage2, versions2 = build_lineage()
    with pytest.raises(ValueError, match="k=2 differs"):
        evaluate_lineage_sealed(adapter(), lineage2, versions2, confirmed=True, budget=BUDGET, k=2, sealed=sealed)
    assert len(sealed.calls) == 1


def test_curve_whose_commits_differ_from_version_hashes_is_rejected():
    lineage, versions = build_lineage()
    sealed = FakeSealed(polls_until_done=0)
    sealed.lineage("it1")  # no claim yet: None
    evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    sealed.result["curve"][2]["frozen_commit"] = "other"
    with pytest.raises(ValueError, match=r"nodes \[2\]"):
        evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)


def test_unmeasurable_result_keeps_every_point_with_null_scores():
    lineage, versions = build_lineage()
    sealed = FakeSealed(polls_until_done=0)
    ids = ["c-seed", "c-a", "c-b", "c-c"]
    sealed.claim = dict(nodes=ids, hash=node_set_hash(ids), actor="operator", status="dispatched", k=3)
    sealed.result = dict(kind="lineage", status="unmeasurable", confirmed_by="operator", claimed_at="t", k=3,
                         nodes=4, node_set_hash=node_set_hash(ids), metric=None, n=None, mean=None, curve=None)
    out = evaluate_lineage_sealed(adapter(), lineage, versions, confirmed=True, budget=BUDGET, sealed=sealed)
    assert out["status"] == "unmeasurable" and len(out["points"]) == 4
    assert all(p["score"] is None and p["status"] == "unmeasurable" for p in out["points"])
    assert sealed.calls == []


def test_default_service_is_built_from_the_adapter_round_operations(monkeypatch):
    built = []

    class Recorder(FakeSealed):
        def __init__(self, repository, iterations, views):
            super().__init__(polls_until_done=0)
            built.append((repository, iterations, views))
    import rag_eval.workbench.sealed as v2_sealed
    monkeypatch.setattr(v2_sealed, "SealedService", Recorder)
    service = SimpleNamespace(repository="repo")
    ops = SimpleNamespace(service=service, api=SimpleNamespace(views="views"))
    lineage, versions = build_lineage()
    out = evaluate_lineage_sealed(adapter(round_operations=ops), lineage, versions, confirmed=True, budget=BUDGET,
                                  actor="human-x")
    assert built == [("repo", service, "views")] and out["metadata"]["confirmed_by"] == "human-x"


def test_real_v2_sealed_service_measures_the_v3_node_set_once(tmp_path, monkeypatch):
    """Against the real workbench SealedService: claim, dispatch, poll, frozen curve, no rerun."""
    from pathlib import Path
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[1]))
    from test_workbench_iterations import env as env_fixture, BUDGET as V2_BUDGET
    from test_workbench_sealed_lineage import registered_tree, v3_lineage, sealed_attempts
    from rag_eval.workbench.sealed import SealedService
    env = env_fixture.__wrapped__(tmp_path, monkeypatch)
    tree = registered_tree(env)
    ids = v3_lineage(env, tree)  # seed, a, b (rolled back), c
    service = env["service"]
    lineage, versions = Lineage(), {}
    for cid, parent in zip(ids, (None, ids[0], ids[1], ids[1])):
        commit = service.candidate(cid)["frozen_commit"]
        parent_commit = parent and service.candidate(parent)["frozen_commit"]
        lineage.add_provisional(commit, parent_commit, None, None, None, 0.0)
        versions[commit] = Version(commit, metadata=dict(candidate_id=cid))
    lineage.certify(service.candidate(ids[0])["frozen_commit"], {})
    lineage.certify(service.candidate(ids[1])["frozen_commit"], {})
    lineage.rollback(service.candidate(ids[2])["frozen_commit"])
    sealed = SealedService(env["repo"], service, None)
    score = {}

    def finish_all(_seconds):
        for index, cid in enumerate(ids):
            for rep, attempt in enumerate(sealed_attempts(env, cid)):
                score[attempt["execution_id"]] = .2 * index + .1 * rep
                env["executions"]._finish(attempt["execution_id"], state="finished", exit_code=0,
                                          error_type=None, run_state=dict(phase="finished"))
    monkeypatch.setattr(sn_sealed.time, "sleep", finish_all)
    monkeypatch.setattr(sealed, "_case_scores", lambda case_ids, execution: dict(
        metric="str_em", scores={c: score[execution["execution_id"]] for c in case_ids},
        families={c: "paper" for c in case_ids}))
    config = dict(CONFIG, iteration_id=tree["id"], task_id=tree["task_id"])
    sn = SNAdapter(config, eval_poll_interval=0.0, eval_timeout=60.0)
    out = evaluate_lineage_sealed(sn, lineage, versions, confirmed=True, budget=V2_BUDGET, actor="human",
                                  sealed=sealed)
    k = tree["protocol"]["k_base"]
    assert out["status"] == "finished" and out["k"] == k and out["m"] == 3
    assert [p["candidate_id"] for p in out["points"]] == ids
    assert [p["style"] for p in out["points"]] == ["solid", "solid", "dashed", "hollow"]
    assert [round(p["delta_vs_seed"], 6) for p in out["points"]] == [0, .2, .4, .6]
    assert out["metadata"]["node_set_hash"] == node_set_hash(ids) and out["metadata"]["confirmed_by"] == "human"
    assert "qasper:" not in json.dumps(out)
    again = evaluate_lineage_sealed(sn, lineage, versions, confirmed=True, budget=V2_BUDGET, sealed=sealed)
    assert again == out and [len(sealed_attempts(env, cid)) for cid in ids] == [k] * 4
