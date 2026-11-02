"""SN evaluate / evaluate_pair dispatch v2 measure / smoke / segment and poll (binding plan §2.2, §2.3, §2.5)."""
from __future__ import annotations

from contextlib import contextmanager
import json
import sqlite3
from types import SimpleNamespace

import pytest

from rag_eval.optimizer.adapter import BudgetExhausted, EvaluationUnavailable, Version
from rag_eval.optimizer.sn_adapter import SNAdapter
from rag_eval.workbench.execution_scopes import smoke_scope_id
from rag_eval.workbench.splits import initialize as initialize_batches

CONFIG = {"iteration_id": "I", "task_id": "T", "run_id": "R1", "actor": "v3-test"}

PROTOCOL = dict(
    k_base=2,
    splits=dict(search=["s1", "s2", "s3"], validation=["v1", "v2"]),
    fresh_batches=[dict(batch_id="F1", case_ids=["f1a", "f1b"]),
                   dict(batch_id="F2", case_ids=["f2a", "f2b"])],
)


class _Repo:
    """WorkbenchRepository subset: sqlite connections, enough for splits.batches / segment_start."""

    def __init__(self, path):
        self.path = str(path)
        initialize_batches(self)

    @contextmanager
    def connect(self, write=False):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def claim(self, batch_id, candidate_id, request_key, *, completed=False):
        with self.connect(write=True) as conn:
            conn.execute("INSERT INTO iteration_fresh_batches VALUES(?,?,?,?,?,?,?)",
                         ("I", batch_id, candidate_id, request_key, "t0", "t1" if completed else None, None))

    def segment_footprint(self, action_id, start_id):
        with self.connect(write=True) as conn:
            conn.execute("CREATE TABLE IF NOT EXISTS round_measurements (key TEXT PRIMARY KEY, payload_json TEXT)")
            conn.execute("INSERT INTO round_measurements VALUES(?,?)",
                         ("k-" + action_id, json.dumps(dict(action_id=action_id, kind="segment", start_id=start_id))))


def _attempt(n, execution_id, batch_id=None, admitted=True):
    return dict(attempt=n, execution_id=execution_id, batch_id=batch_id, execution_state="finished",
                job_id="j" + execution_id,
                admission=dict(status="admitted" if admitted else "not_admitted", reason=None))


class _Service:
    """IterationService subset: candidate views with attempts, iteration_record."""

    def __init__(self):
        self.candidates = {
            "root": dict(id="root", iteration_id="I", status="frozen", frozen_commit="c0",
                         parent_candidate_id=None, proposal_id=None, parent_commit=None,
                         attempts=[], links=[], worktree=dict(status="ready", path="/wt/root")),
            "cand1": dict(id="cand1", iteration_id="I", status="frozen", frozen_commit="c1",
                          parent_candidate_id="root", proposal_id="H1", parent_commit="c0",
                          attempts=[], links=[], worktree=dict(status="ready", path="/wt/cand1")),
        }
        self.freezes = []

    def iteration_record(self, iteration_id):
        assert iteration_id == "I"
        return dict(id="I", task_id="T", protocol=PROTOCOL)

    def candidate(self, cid):
        return json.loads(json.dumps(self.candidates[cid]))

    def provision(self, cid):
        c = self.candidates[cid]
        c["worktree"] = dict(status="ready", path="/wt/" + cid)
        return self.candidate(cid)

    def freeze(self, cid, *, actor):
        self.freezes.append((cid, actor))
        self.candidates[cid].update(status="frozen", frozen_commit="c-" + cid)
        return self.candidate(cid)


class _Views:
    """RunViews subset: partition runs per execution and self-paired flips (per-case values)."""

    def __init__(self):
        self.runs = {}      # execution_id -> [run namespace]
        self.values = {}    # run_id -> {case: value}
        self.flip_calls = 0

    def add(self, execution_id, values_by_partition):
        runs = []
        for i, values in enumerate(values_by_partition):
            run_id = f"{execution_id}-{i}"
            runs.append(SimpleNamespace(id=run_id, run_dir=f"/runs/{run_id}", case_ids=tuple(values)))
            self.values[run_id] = values
        self.runs[execution_id] = runs

    def execution_runs(self, execution_id):
        return list(self.runs.get(execution_id, []))

    def shared_bundle(self, run_id):
        return "bundle"

    def flips(self, left, right, *, bundle=None):
        assert left == right
        self.flip_calls += 1
        return dict(admission="admitted", rows=[dict(case_id=c, left=v, right=v, delta=0.0)
                                                for c, v in self.values[left].items()])


class _Ops:
    """RoundOperations subset: measure / smoke / segment with scripted v2 statuses."""

    def __init__(self, service, repository, script=None, on_call=None):
        self.service, self.repository = service, repository
        self.script = dict(script or {})   # kind -> list of statuses, last one repeats
        self.calls = []
        self.on_call = on_call

    def _status(self, kind):
        seq = self.script.get(kind, ["complete"])
        return seq.pop(0) if len(seq) > 1 else seq[0]

    def measure(self, candidate_id, **kw):
        self.calls.append(("measure", candidate_id, kw))
        if self.on_call:
            self.on_call(self, "measure", candidate_id, kw)
        return dict(status=self._status("measure"), reason="scripted")

    def smoke(self, candidate_id, case_ids, **kw):
        self.calls.append(("smoke", candidate_id, dict(kw, case_ids=list(case_ids))))
        return dict(status=self._status("smoke"))

    def segment(self, start_id, end_id, batch_id, **kw):
        self.calls.append(("segment", end_id, dict(kw, start_id=start_id, batch_id=batch_id)))
        return dict(status=self._status("segment"))


@pytest.fixture
def env(tmp_path):
    service, views, repo = _Service(), _Views(), _Repo(tmp_path / "wb.sqlite")

    def build(script=None, on_call=None, **kw):
        ops = _Ops(service, repo, script, on_call)
        adapter = SNAdapter(dict(CONFIG), round_operations=ops, run_views=views,
                            eval_poll_interval=0, agent_poll_interval=0, **kw)
        return adapter, ops

    return SimpleNamespace(service=service, views=views, repo=repo, build=build)


def _version(cid, commit="c"):
    return Version(content_hash=commit + cid, metadata=dict(candidate_id=cid))


V_ROOT, V_CAND = _version("root"), _version("cand1")
KW = dict(actor="v3-test", infra_retry=1, timeout_seconds=7200)


# -- validation ---------------------------------------------------------------

def test_k_mismatch_raises_before_dispatch(env):
    adapter, ops = env.build()
    with pytest.raises(ValueError, match="k=2"):
        adapter.evaluate(V_CAND, ["v1"], 3, purpose="screen")
    with pytest.raises(ValueError, match="k=1"):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["s1"], 2, purpose="smoke")
    assert ops.calls == []


@pytest.mark.parametrize("purpose,ids", [
    ("diagnose", ["s1", "v1"]),          # validation case in a search read
    ("screen", ["v1", "s1"]),            # search case in the validation pool
    ("baseline", ["v1", "f1a"]),         # fresh batch not yet completed
    ("screen", ["v1", "nope"]),
    ("screen", []),
])
def test_task_ids_outside_partition_raise(env, purpose, ids):
    adapter, ops = env.build()
    with pytest.raises(ValueError):
        adapter.evaluate(V_CAND, ids, 2, purpose=purpose)
    assert ops.calls == []


def test_pair_only_purposes_refused_by_evaluate(env):
    adapter, _ = env.build()
    for purpose in ("smoke", "confirm", "sealed", "bogus"):
        with pytest.raises(ValueError):
            adapter.evaluate(V_CAND, ["v1"], 2, purpose=purpose)
    with pytest.raises(ValueError):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["v1"], 2, purpose="diagnose")


# -- measure dispatch and polling ---------------------------------------------

def test_measure_polled_until_complete_with_deterministic_key(env):
    adapter, ops = env.build(script={"measure": ["running", "running", "complete"]})
    adapter.evaluate(V_CAND, ["v1", "v2"], 2, purpose="screen")
    assert [c[0] for c in ops.calls] == ["measure"] * 3
    assert all(c[1] == "cand1" and c[2] == dict(KW, key="v3/R1/measure/cand1/0") for c in ops.calls)


def test_measure_key_counts_completed_fresh_batches(env):
    env.repo.claim("F1", "cand0", "action:x", completed=True)
    env.repo.claim("F2", "cand9", "action:y", completed=False)   # used, not yet reconciled
    adapter, ops = env.build()
    adapter.evaluate(V_CAND, ["v1", "f1a"], 2, purpose="screen")  # completed batch joins the pool
    assert ops.calls[0][2]["key"] == "v3/R1/measure/cand1/1"
    with pytest.raises(ValueError):
        adapter.evaluate(V_CAND, ["f2a"], 2, purpose="screen")


def test_batch_completing_during_measure_triggers_new_key(env):
    def complete_batch_once(ops, kind, cid, kw):
        if kw["key"].endswith("/0"):
            env.repo.claim("F1", "other", "action:z", completed=True)
    adapter, ops = env.build(on_call=complete_batch_once)
    adapter.evaluate(V_CAND, ["v1"], 2, purpose="screen")
    assert [c[2]["key"] for c in ops.calls] == ["v3/R1/measure/cand1/0", "v3/R1/measure/cand1/1"]


def test_budget_exhausted_raises_budget_exhausted(env):
    adapter, _ = env.build(script={"measure": ["running", "budget_exhausted"]})
    with pytest.raises(BudgetExhausted):
        adapter.evaluate(V_CAND, ["v1"], 2, purpose="screen")


@pytest.mark.parametrize("status", ["failed", "exhausted", "stopped"])
def test_failed_raises_evaluation_unavailable(env, status):
    adapter, _ = env.build(script={"measure": [status]})
    with pytest.raises(EvaluationUnavailable) as info:
        adapter.evaluate(V_CAND, ["v1"], 2, purpose="screen")
    assert not isinstance(info.value, BudgetExhausted)
    assert "scripted" in str(info.value)


def test_poll_timeout_raises_evaluation_unavailable(env):
    adapter, _ = env.build(script={"measure": ["running"]}, eval_timeout=0)
    with pytest.raises(EvaluationUnavailable, match="not complete"):
        adapter.evaluate(V_CAND, ["v1"], 2, purpose="screen")


# -- reading scores -------------------------------------------------------------

def test_per_case_means_over_admitted_attempts_only(env):
    env.service.candidates["cand1"]["attempts"] = [
        _attempt(1, "e1"), _attempt(2, "e2"),
        _attempt(3, "e3", admitted=False),          # infrastructure failure: excluded
        _attempt(4, "e4", batch_id="smoke:x"),      # another scope: excluded
    ]
    env.views.add("e1", [{"v1": 1.0, "s1": 0.2}, {"v2": 0.0}])
    env.views.add("e2", [{"v1": 0.0, "s1": 0.4}, {"v2": 1.0}])
    env.views.add("e3", [{"v1": 1.0, "s1": 1.0}, {"v2": 1.0}])
    env.views.add("e4", [{"v1": 1.0}])
    adapter, _ = env.build()
    res = adapter.evaluate(V_CAND, ["v1", "v2"], 2, purpose="screen")
    assert res.per_task_scores == {"v1": 0.5, "v2": 0.5}
    assert set(res.trajectories) == {"v1", "v2"}            # restricted to task_ids
    assert [s["execution_id"] for s in res.trajectories["v1"]] == ["e1", "e2"]
    stub = res.trajectories["v2"][0]
    assert stub["run_dir"] == "/runs/e1-1" and stub["run_id"] == "e1-1" and stub["batch_id"] is None
    assert res.costs == {"v1": 0.0, "v2": 0.0} and res.error_classes == {"v1": None, "v2": None}
    diag = adapter.evaluate(V_CAND, ["s1"], 2, purpose="diagnose")
    assert diag.per_task_scores == pytest.approx({"s1": 0.3})


def test_completed_fresh_batch_cases_read_from_their_batch_scope(env):
    env.repo.claim("F1", "cand0", "action:x", completed=True)
    env.service.candidates["cand1"]["attempts"] = [
        _attempt(1, "e1"), _attempt(2, "e2"), _attempt(3, "b1", "F1"), _attempt(4, "b2", "F1")]
    env.views.add("e1", [{"v1": 1.0}])
    env.views.add("e2", [{"v1": 1.0}])
    env.views.add("b1", [{"f1a": 1.0, "f1b": 0.0}])
    env.views.add("b2", [{"f1a": 0.0, "f1b": 0.0}])
    adapter, _ = env.build()
    res = adapter.evaluate(V_CAND, ["v1", "f1a", "f1b"], 2, purpose="baseline")
    assert res.per_task_scores == {"v1": 1.0, "f1a": 0.5, "f1b": 0.0}
    assert {s["batch_id"] for s in res.trajectories["f1a"]} == {"F1"}


def test_case_without_admitted_attempt_has_error_class(env):
    adapter, _ = env.build()
    res = adapter.evaluate(V_CAND, ["v1"], 2, purpose="screen")
    assert res.per_task_scores == {} and res.trajectories == {"v1": []}
    assert res.error_classes == {"v1": "no_admitted_attempt"}


def test_stubs_feed_features_and_views(env, monkeypatch):
    env.service.candidates["cand1"]["attempts"] = [_attempt(1, "e1"), _attempt(2, "e2")]
    env.views.add("e1", [{"v1": 1.0}])
    env.views.add("e2", [{"v1": 1.0}])
    adapter, _ = env.build()
    res = adapter.evaluate(V_CAND, ["v1"], 2, purpose="screen")
    seen = {}
    monkeypatch.setattr(adapter, "_rows_by_task", lambda trajs: seen.update(trajs) or {})
    adapter.diagnostic_views(res.trajectories)
    assert seen["v1"][-1]["run_dir"] == "/runs/e2-0"


# -- pairs ----------------------------------------------------------------------

def test_screen_pair_measures_candidate_and_reference(env):
    env.service.candidates["cand1"]["attempts"] = [_attempt(1, "c1"), _attempt(2, "c2")]
    env.service.candidates["root"]["attempts"] = [_attempt(1, "r1"), _attempt(2, "r2")]
    for eid, value in (("c1", 1.0), ("c2", 1.0), ("r1", 0.0), ("r2", 1.0)):
        env.views.add(eid, [{"v1": value}])
    adapter, ops = env.build()
    cand, ref = adapter.evaluate_pair(V_CAND, V_ROOT, ["v1"], 2, purpose="screen")
    assert sorted(c[2]["key"] for c in ops.calls) == ["v3/R1/measure/cand1/0", "v3/R1/measure/root/0"]
    assert cand.per_task_scores == {"v1": 1.0} and ref.per_task_scores == {"v1": 0.5}


def test_smoke_pair_uses_smoke_kind_k1_and_smoke_scope(env):
    scope = smoke_scope_id(PROTOCOL, ["s2", "s1"])
    env.service.candidates["cand1"]["attempts"] = [_attempt(1, "e1"), _attempt(2, "sc", scope)]
    env.service.candidates["root"]["attempts"] = [_attempt(1, "sr", scope)]
    env.views.add("e1", [{"s1": 0.0, "s2": 0.0}])
    env.views.add("sc", [{"s1": 1.0, "s2": 1.0}])
    env.views.add("sr", [{"s1": 0.0, "s2": 1.0}])
    adapter, ops = env.build(script={"smoke": ["running", "complete"]})
    cand, ref = adapter.evaluate_pair(V_CAND, V_ROOT, ["s2", "s1"], 1, purpose="smoke")
    assert [c[0] for c in ops.calls] == ["smoke", "smoke"]
    assert ops.calls[0][1] == "cand1"
    assert ops.calls[0][2] == dict(KW, key="v3/R1/smoke/cand1/" + scope, case_ids=["s2", "s1"])
    assert cand.per_task_scores == {"s1": 1.0, "s2": 1.0}
    assert ref.per_task_scores == {"s1": 0.0, "s2": 1.0}
    assert cand.trajectories["s1"][0]["batch_id"] == scope


def test_smoke_refuses_non_search_cases_and_non_parent_reference(env):
    env.service.candidates["other"] = dict(env.service.candidates["root"], id="other")
    adapter, ops = env.build()
    with pytest.raises(ValueError):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["s1", "v1"], 1, purpose="smoke")
    with pytest.raises(ValueError, match="parent"):
        adapter.evaluate_pair(V_CAND, _version("other"), ["s1"], 1, purpose="smoke")
    assert ops.calls == []


def test_confirm_picks_batch_by_case_set_and_segments_reference_to_version(env):
    env.service.candidates["cand1"]["attempts"] = [_attempt(1, "b1", "F2"), _attempt(2, "b2", "F2")]
    env.service.candidates["root"]["attempts"] = [_attempt(1, "b3", "F2"), _attempt(2, "b4", "F2")]
    for eid, value in (("b1", 1.0), ("b2", 1.0), ("b3", 0.0), ("b4", 0.0)):
        env.views.add(eid, [{"f2a": value, "f2b": value}])
    adapter, ops = env.build(script={"segment": ["running", "complete"]})
    end, start = adapter.evaluate_pair(V_CAND, V_ROOT, ["f2b", "f2a"], 2, purpose="confirm")
    assert [c[0] for c in ops.calls] == ["segment", "segment"]
    assert ops.calls[0][1] == "cand1"
    assert ops.calls[0][2] == dict(KW, key="v3/R1/segment/root/cand1/F2", start_id="root", batch_id="F2")
    assert end.per_task_scores == {"f2a": 1.0, "f2b": 1.0}
    assert start.per_task_scores == {"f2a": 0.0, "f2b": 0.0}


def test_confirm_rejects_unknown_and_used_batches(env):
    adapter, ops = env.build()
    with pytest.raises(ValueError, match="fresh batch"):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["f1a"], 2, purpose="confirm")       # partial batch
    with pytest.raises(ValueError, match="fresh batch"):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["v1", "v2"], 2, purpose="confirm")
    env.repo.claim("F1", "cand7", "action:other")
    with pytest.raises(ValueError, match="already used"):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["f1a", "f1b"], 2, purpose="confirm")
    with pytest.raises(ValueError, match="k=2"):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["f2a", "f2b"], 1, purpose="confirm")
    assert ops.calls == []


def test_confirm_resumes_its_own_segment_claim(env):
    env.repo.claim("F1", "cand1", "action:abc")
    env.repo.segment_footprint("abc", "root")
    adapter, ops = env.build()
    adapter.evaluate_pair(V_CAND, V_ROOT, ["f1a", "f1b"], 2, purpose="confirm")
    assert ops.calls[0][0] == "segment"
    # The same batch claimed by this end against a different start is someone else's.
    env.repo.claim("F2", "cand1", "action:def")
    env.repo.segment_footprint("def", "cand0")
    with pytest.raises(ValueError, match="already used"):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["f2a", "f2b"], 2, purpose="confirm")


def test_segment_failure_maps_to_evaluation_unavailable(env):
    adapter, _ = env.build(script={"segment": ["failed"]})
    with pytest.raises(EvaluationUnavailable):
        adapter.evaluate_pair(V_CAND, V_ROOT, ["f1a", "f1b"], 2, purpose="confirm")


# -- partitions -----------------------------------------------------------------

def test_partitions_reads_iteration_protocol(env):
    env.repo.claim("F1", "cand1", "action:x", completed=True)
    env.repo.claim("F2", "cand1", "action:y")
    adapter, _ = env.build()
    assert adapter.partitions() == dict(
        search=["s1", "s2", "s3"], validation=["v1", "v2"],
        fresh_batches=[dict(batch_id="F1", case_ids=["f1a", "f1b"]),
                       dict(batch_id="F2", case_ids=["f2a", "f2b"])],
        completed_fresh_batch_ids=["F1"], k_base=2)


# -- rebase ---------------------------------------------------------------------

class _RebaseOps(_Ops):
    def __init__(self, service, repository, outcome):
        super().__init__(service, repository)
        self.outcome = outcome

    def derive(self, baseline_id, *, hypothesis_id, key, actor, rebase_from=None):
        self.calls.append(("derive", baseline_id, dict(hypothesis_id=hypothesis_id, key=key, actor=actor,
                                                       rebase_from=rebase_from)))
        self.service.candidates.setdefault("cand2", dict(
            id="cand2", iteration_id="I", status="ready", frozen_commit=None, parent_candidate_id=baseline_id,
            proposal_id="H1-rebased", parent_commit="c-new", attempts=[], links=[],
            worktree=dict(status="unknown", path=None)))
        return "cand2"

    def rebase(self, candidate_id, *, key, actor=None):
        self.calls.append(("rebase", candidate_id, dict(key=key)))
        return dict(status=self.outcome)


@pytest.mark.parametrize("outcome", ["applied", "conflict"])
def test_rebase_derives_applies_and_freezes(env, outcome):
    env.service.candidates["newp"] = dict(env.service.candidates["root"], id="newp", frozen_commit="c-new")
    ops = _RebaseOps(env.service, env.repo, outcome)
    adapter = SNAdapter(dict(CONFIG), round_operations=ops, run_views=env.views, agent_poll_interval=0)
    result = adapter.rebase(V_CAND, _version("newp"))
    assert ops.calls[0] == ("derive", "newp", dict(hypothesis_id="H1", key="v3/R1/rebase/cand1/newp",
                                                   actor="v3-test", rebase_from="cand1"))
    assert ops.calls[1] == ("rebase", "cand2", dict(key="v3/R1/rebase-apply/cand2"))
    if outcome == "applied":
        assert env.service.freezes == [("cand2", "v3-test")]
        assert result.content_hash == "c-cand2"
        assert result.metadata["candidate_id"] == "cand2" and result.metadata["parent_candidate_id"] == "newp"
    else:
        assert result is None and env.service.freezes == []


# -- key generation (operator retry after a failed v2 result) -------------------

def test_key_generation_suffixes_measure_and_smoke_but_not_segment(env):
    adapter, ops = env.build()
    adapter.set_key_generation(2)
    adapter.evaluate(V_CAND, ["v1"], 2, purpose="screen")
    adapter.evaluate_pair(V_CAND, V_ROOT, ["s1"], 1, purpose="smoke")
    adapter.evaluate_pair(V_CAND, V_ROOT, ["f1a", "f1b"], 2, purpose="confirm")
    keys = {c[0]: c[2]["key"] for c in ops.calls}
    assert keys["measure"] == "v3/R1/measure/cand1/0/g2"
    assert keys["smoke"] == "v3/R1/smoke/cand1/" + smoke_scope_id(PROTOCOL, ["s1"]) + "/g2"
    assert keys["segment"] == "v3/R1/segment/root/cand1/F1"      # a claimed batch is never re-keyed


def test_key_generation_from_config_and_validation(env):
    assert SNAdapter(dict(CONFIG, key_generation=3)).key_generation == 3
    assert SNAdapter(dict(CONFIG)).key_generation == 0
    with pytest.raises(ValueError):
        SNAdapter(dict(CONFIG, key_generation=-1))
    with pytest.raises(ValueError):
        SNAdapter(dict(CONFIG)).set_key_generation("1")


def test_first_calls_touching_one_candidate_do_not_plan_concurrently():
    """Regression (rehearsal): siblings' smoke keys both topped up the shared parent's smoke scope.

    v2 plans a key's footprint from the attempts existing at its first call and launches in that
    same call, so first calls that touch one candidate are serialized; polls are not.
    """
    import threading
    import time
    adapter = SNAdapter({}, eval_poll_interval=0)
    active, overlaps, lock = set(), [], threading.Lock()

    def slow(name, touched):
        def call():
            with lock:
                overlaps.extend((name, other) for other, t in active if set(t) & set(touched))
                active.add((name, touched))
            time.sleep(0.05)
            with lock:
                active.discard((name, touched))
            return {"status": "complete"}
        return call

    jobs = [("a", ("cand1", "root")), ("b", ("cand2", "root")), ("c", ("cand3", "other"))]
    threads = [threading.Thread(target=adapter._await, args=({n: ("k-" + n, slow(n, t))}, {n: t}))
               for n, t in jobs]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert overlaps == []
