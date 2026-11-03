"""The automatic-run page's read model, projected from state files a real loop wrote."""
from __future__ import annotations

import json
import threading

import pytest

from rag_eval.optimizer import ProtocolConfig, run_optimization, run_view
from rag_eval.optimizer.adapter import BudgetExhausted
from tests.optimizer.mock_adapter import MockAdapter

TASKS = [f"t{i}" for i in range(6)]
UP = {t: 0.12 for t in TASKS}


def _config(**over):
    return ProtocolConfig(**dict(dict(mde=0.05, sigma_d=0.1, ladder_c=1.0, K_max=20, T=0.10, n_fresh=6, b_cards=1,
                                      k_screen=1, n_smoke_sample=2, predicate_coverage_floor=0.0), **over))


def _run(tmp_path, script, *, config=None, adapter=None, n_batches=3, max_rounds=10):
    adapter = adapter or MockAdapter(script=script, n_tasks=6, base_score=0.3)
    result = run_optimization(adapter, adapter.seed, config or _config(), search_set_ids=TASKS, val_pool_ids=TASKS,
                              fresh_batch_ids=[TASKS] * n_batches, smoke_sample_ids=TASKS[:2], max_rounds=max_rounds,
                              state_dir=tmp_path)
    return result, run_view.read_run(tmp_path)


def test_certified_segment_carries_its_nodes_and_interval(tmp_path):
    _, view = _run(tmp_path, [dict(cards=["card A"], deltas=UP, smoke_pass=True)])
    assert view["run"]["status"] == "halted" and view["run"]["alive"] is False
    assert view["run"]["halt_reason"] == "max_rounds"
    assert view["seed"]["candidate_id"] == "seed_v0"
    certified = [s for s in view["segments"] if s["status"] == "certified"]
    assert len(certified) == 1
    seg = certified[0]
    conf = seg["confirmation"]
    assert conf["verdict"] == "confirmed" and conf["lower"] <= conf["delta_fresh"] <= conf["upper"]
    assert [n["title"] for n in seg["nodes"]] == ["card A"]
    assert seg["nodes"][0]["state"] == "certified" and seg["nodes"][0]["delta_val"] == pytest.approx(0.12)
    assert view["progress"]["milestones_certified"] == 1 and view["progress"]["fresh_used"] == 1
    assert view["next_action"] == "sealed"


def test_rolled_back_segment_stays_visible(tmp_path):
    # Adopted on the validation pool, gone on the fresh batch: t6+ exist only on fresh.
    fresh = [f"f{i}" for i in range(6)]
    deltas = {**UP, **{t: -0.12 for t in fresh}}
    adapter = MockAdapter(script=[dict(cards=["card B"], deltas=deltas, smoke_pass=True)], n_tasks=6, base_score=0.3)
    adapter._scores[adapter._seed_hash].update({t: 0.3 for t in fresh})
    result = run_optimization(adapter, adapter.seed, _config(), search_set_ids=TASKS, val_pool_ids=TASKS,
                              fresh_batch_ids=[fresh], smoke_sample_ids=TASKS[:2], max_rounds=3, state_dir=tmp_path)
    assert result.rounds[0].rolled_back
    view = run_view.read_run(tmp_path)
    seg = view["segments"][0]
    assert seg["status"] == "rolled_back" and seg["confirmation"]["verdict"] in ("rejected", "insufficient")
    assert seg["nodes"][0]["state"] == "rolled_back" and seg["nodes"][0]["title"] == "card B"
    assert view["progress"]["milestones_certified"] == 0
    assert not any(t in json.dumps(view) for t in ('"f0"', '"t0"')), "the projection never names a task"


def test_open_segment_and_failed_attempts_are_reported(tmp_path):
    down = {t: -0.2 for t in TASKS}
    # b=1: each failed card refills from the queue, so all three get an attempt.
    script = [dict(cards=[dict(name="worse", deltas=down),
                          dict(name="no behaviour", deltas=UP, smoke_pass=False),
                          dict(name="kept", deltas={t: 0.08 for t in TASKS})])]
    _, view = _run(tmp_path, script, config=_config(T=5.0, sigma_d=0.01, b_cards=1, analyst_cards=3), max_rounds=1)
    verdicts = {a["title"]: a["verdict"] for a in view["rounds"][0]["attempts"]}
    assert verdicts == {"kept": "screen_pass", "worse": "screen_fail", "no behaviour": "smoke_fail"}
    assert view["segments"][-1]["status"] == "open"
    assert [n["title"] for n in view["segments"][-1]["nodes"]] == ["kept"]
    assert view["segments"][-1]["confirmation"] is None
    assert view["progress"]["cumulative_delta_val"] == pytest.approx(0.08)
    kept = next(a for a in view["rounds"][0]["attempts"] if a["title"] == "kept")
    assert kept["lower_val"] == pytest.approx(kept["delta_val"] - 0.84 * kept["se_val"])
    assert kept["threshold"] == pytest.approx(kept["best_before"] + kept["ladder_m"])


def test_budget_halt_asks_for_budget_and_keeps_the_open_round(tmp_path):
    adapter = MockAdapter(script=[dict(cards=["c"], deltas=UP)], n_tasks=6, base_score=0.3,
                          fail_on={"screen": BudgetExhausted("out")})
    _, view = _run(tmp_path, [], adapter=adapter)
    assert view["run"]["halt_reason"] == "budget_exhausted" and view["next_action"] == "budget"
    assert view["open_round"]["round_id"] == 1
    assert view["open_round"]["cards"][0]["title"] == "c"


def test_interrupted_run_asks_to_resume(tmp_path):
    _, view = _run(tmp_path, [dict(cards=["c"], deltas=UP)])
    state = json.loads((tmp_path / "state.json").read_text())
    state["halt_reason"] = None
    view = run_view.project(state)
    assert view["run"]["status"] == "interrupted" and view["next_action"] == "resume"
    assert run_view.project(state, alive=True)["run"]["status"] == "running"
    assert run_view.project(state, alive=True)["next_action"] is None


def test_sealed_curve_moves_the_next_action_to_review(tmp_path):
    _run(tmp_path, [dict(cards=["c"], deltas=UP)])
    (tmp_path / "sealed-curve.json").write_text(json.dumps(dict(status="finished", points=[])))
    view = run_view.read_run(tmp_path)
    assert view["next_action"] == "review" and view["sealed"]["status"] == "finished"


@pytest.mark.parametrize("current,expected", [
    (dict(cards=None, inflight={}, submitted=[]), "analysing"),
    (dict(cards=[{}, {}], inflight={"0": "h"}, submitted=[0]), "building"),
    (dict(cards=[{}, {}], inflight={}, submitted=[0]), "building"),
    (dict(cards=[{}], inflight={}, submitted=[0]), "between_rounds"),
])
def test_phase_of_an_open_round(current, expected):
    state = dict(baseline_done=True, current_round=dict(current, round=dict(round_id=1)), milestone={}, halt_tracker={})
    assert run_view._phase(state, dict(T=0.1)) == expected


def test_phase_confirming_only_with_a_batch_left():
    state = dict(baseline_done=True, current_round=dict(cards=[{}], inflight={"0": "h"}, submitted=[0], round=dict(round_id=1)),
                 milestone=dict(current_provisional_id="b", last_certified_id="a", cumulative_delta_val=0.2),
                 halt_tracker=dict(fresh_batches_remaining=1, confirmations_used=0))
    assert run_view._phase(state, dict(T=0.1, max_confirmations=3)) == "confirming"
    state["halt_tracker"]["fresh_batches_remaining"] = 0
    assert run_view._phase(state, dict(T=0.1, max_confirmations=3)) == "building"


def test_lock_marks_the_run_alive_and_excludes_a_second_process(tmp_path):
    assert run_view.run_alive(tmp_path) is False
    with run_view.hold_run_lock(tmp_path):
        assert run_view.run_alive(tmp_path) is True
        with pytest.raises(run_view.RunLocked):
            with run_view.hold_run_lock(tmp_path, attempts=2, wait=0.0):
                pass
    assert run_view.run_alive(tmp_path) is False


def test_a_probe_does_not_make_the_runner_fail(tmp_path):
    stop = threading.Event()
    (tmp_path / run_view.LOCK_FILE).touch()

    def probe():
        while not stop.is_set():
            run_view.run_alive(tmp_path)
    worker = threading.Thread(target=probe)
    worker.start()
    try:
        for _ in range(50):
            with run_view.hold_run_lock(tmp_path):
                pass
    finally:
        stop.set()
        worker.join()


def test_registered_run_without_state(tmp_path):
    view = run_view.read_run(tmp_path)
    assert view["run"]["status"] == "not_started" and view["segments"] == []
