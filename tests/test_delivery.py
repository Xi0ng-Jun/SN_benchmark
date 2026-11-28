"""Step 5: the delivered version is frozen and tried once on a practice task in a new place."""
import hashlib
import json
import os

import pytest

from harness_builder import delivery
from harness_builder.runs import Runner, RunRefused


@pytest.fixture
def state(open_stub_state):
    return open_stub_state()


def kinds(state):
    return [e["kind"] for e in state.ledger.entries()]


def test_check_freezes_the_version_and_tries_it_once(state, designate, make_harness,
                                                     make_gateway):
    folder = make_harness("correct", model_calls=1)
    meta = designate(state, folder)
    check = delivery.check(state, make_gateway())

    assert check["outcome"] == "passed" and check["problems"] == []
    assert check["version"] == meta["version"] and check["hash"] == meta["hash"]
    assert check["task"] == "p1" and check["area"] == "dev/trial" and check["run"] == "r0001"
    assert kinds(state)[-5:] == ["delivery_designated", "delivery_frozen", "run_started",
                                 "run_finished", "delivery_checked"]
    frozen = state.ledger.last("delivery_frozen")["data"]
    manifest = hashlib.sha256((folder / "MANIFEST.md").read_bytes()).hexdigest()
    assert frozen["hash"] == meta["hash"] and frozen["manifest_sha256"] == manifest
    assert check["manifest_sha256"] == manifest

    run = state.ledger.last("run_finished")["data"]
    assert run["area"] == "dev/trial" and run["harness_hash"] == meta["hash"]
    assert run["run_seconds"] == 60  # the scenario's own limit
    assert run["usage"]["pool"] == "delivery" and run["usage"]["requests"] == 1
    d = state.root / "dev" / "trial" / "runs" / "r0001"
    assert json.loads((d / "record.json").read_text()) == run

    # the trial ran a new copy outside the state directory, which is gone afterwards
    line = next(x for x in (d / "out" / "agent.log").read_text().splitlines()
                if x.startswith("solver: started from "))
    place = line.removeprefix("solver: started from ")
    assert not place.startswith(str(state.root)) and not os.path.exists(place)

    assert delivery.accepted(state)["version"] == meta["version"]
    assert state.ledger.verify() == []


def test_score_is_not_looked_at(state, designate, make_harness):
    designate(state, make_harness("wrong"))
    assert delivery.check(state, None)["outcome"] == "passed"


def test_another_practice_task_may_be_chosen(state, designate, make_harness):
    designate(state, make_harness("correct"))
    assert delivery.check(state, None, task="p3")["task"] == "p3"
    with pytest.raises(delivery.DeliveryError, match="not a practice task"):
        delivery.check(state, None, task="e1")


@pytest.mark.parametrize("mode,limit,outcome,problem", [
    ("crash", 60, "failed", "run command ended with exit code 3"),
    ("break_scorer", 60, "failed", "score command ended with exit code 2"),
    ("hang", 1, "failed", "run command ran into its time limit"),
    ("unreachable", 60, "invalid", "model service was unreachable"),
])
def test_trial_that_does_not_go_through_sends_the_delivery_back(
        open_stub_state, edit_config, designate, make_harness, mode, limit, outcome, problem):
    edit_config(lambda c: c["limits"].update(run_seconds=limit))
    state = open_stub_state()
    designate(state, make_harness(mode))
    check = delivery.check(state, None)
    assert check["outcome"] == outcome and problem in check["problems"][0]
    assert state.ledger.last("delivery_checked")["data"] == check
    assert delivery.accepted(state) is None
    # the developer agent may repair and deliver again
    designate(state, make_harness("correct"))
    assert delivery.check(state, None)["outcome"] == "passed"
    assert delivery.accepted(state)["run"] == "r0002"


def test_folder_without_manifest_or_start_fails_before_any_run(state, designate, tmp_path):
    bare = tmp_path / "bare"
    bare.mkdir()
    (bare / "start").write_text("#!/bin/sh\n")  # not executable
    (bare / "MANIFEST.md").write_text("  \n")   # empty
    designate(state, bare)
    check = delivery.check(state, None)
    assert check["outcome"] == "failed" and check["run"] is None
    assert any("start" in p for p in check["problems"])
    assert any("MANIFEST.md" in p for p in check["problems"])
    assert "run_started" not in kinds(state) and "delivery_frozen" not in kinds(state)


def test_stored_version_changed_after_delivery_fails_the_check(state, designate, make_harness):
    meta = designate(state, make_harness("correct"))
    f = state.repo.path(meta["version"]) / "settings.json"
    os.chmod(f.parent, 0o755)
    os.chmod(f, 0o644)
    f.write_text('{"answer": "wrong"}')
    check = delivery.check(state, None)
    assert check["outcome"] == "failed" and "no longer matches" in check["problems"][0]
    assert "run_started" not in kinds(state)


def test_nothing_delivered(state):
    with pytest.raises(delivery.DeliveryError, match="no version has been delivered"):
        delivery.check(state, None)
    assert delivery.accepted(state) is None


def test_a_later_delivery_needs_its_own_check(state, designate, make_harness):
    designate(state, make_harness("correct"))
    assert delivery.check(state, None)["outcome"] == "passed"
    designate(state, make_harness("none"))
    assert delivery.accepted(state) is None
    assert delivery.check(state, None)["outcome"] == "passed"
    assert delivery.accepted(state)["version"] == state.ledger.last(
        "delivery_designated")["data"]["version"]


def test_trial_works_when_the_development_budget_is_used_up(state, designate, make_harness,
                                                           make_gateway):
    gw = make_gateway()
    gw.set_pool_caps("dev", {"requests": 1})
    v = designate(state, make_harness("correct", model_calls=1))["version"]
    dev = Runner(state, "dev", state.scenario.practice, gateway=gw, pool="dev")
    dev.run("p1", v)
    with pytest.raises(RunRefused, match="budget"):
        dev.run("p1", v)
    check = delivery.check(state, gw)
    assert check["outcome"] == "passed"
    assert state.ledger.last("run_finished")["data"]["usage"]["requests"] == 1
    assert gw.pool_usage("dev")["requests"] == 1 and gw.pool_usage("delivery")["requests"] == 1
