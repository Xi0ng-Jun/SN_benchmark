"""Step 2: runs and scorings on the stub scenario are fully recorded; the split is locked."""
import json
import os

import pytest

from harness_builder import scenario as sc
from harness_builder.ledger import LedgerError
from harness_builder.runs import Runner, RunRefused
from harness_builder.split_lock import SplitChanged
from harness_builder.state import open_state

RECORD_KEYS = {"area", "run", "task", "version", "harness_hash", "run_seconds",
               "solver_settings", "run_result", "score_result", "invalid", "usage"}
SETTINGS = {"thinking": "high", "max_output_tokens": "4096"}  # as in the stub's scenario.json


@pytest.fixture
def state(open_stub_state):
    return open_stub_state()


def dev_runner(state):
    return Runner(state, "dev", state.scenario.practice)


def raw_score(runner, record):
    return json.loads((runner.run_dir(record["run"]) / "score" / "score.json").read_text())


# --- opening the state directory -----------------------------------------------------------

def test_open_records_split_scenario_and_initial_harness(state):
    kinds = [e["kind"] for e in state.ledger.entries()]
    assert kinds == ["split_locked", "scenario", "harness_saved"]
    lock = state.ledger.last("split_locked")["data"]["split"]
    assert lock["practice"] == ["p1", "p2", "p3"] and lock["exam"] == ["e1", "e2", "e3"]
    assert state.repo.versions()[0]["note"] == "initial harness"
    assert state.ledger.last("scenario")["data"]["config"] == state.scenario.config


def test_reopening_adds_nothing(open_stub_state):
    open_stub_state()
    st = open_stub_state()
    assert [e["kind"] for e in st.ledger.entries()] == ["split_locked", "scenario",
                                                        "harness_saved"]


@pytest.mark.parametrize("change", [
    lambda c: c["split"].update(practice=["p1", "p2", "p3", "e1"], exam=["e2", "e3"]),
    lambda c: c["tasks"][0].update(group="g9"),
], ids=["task moved", "group changed"])
def test_split_cannot_change_after_locking(open_stub_state, edit_config, change):
    open_stub_state()
    edit_config(change)
    with pytest.raises(SplitChanged):
        open_stub_state()


def test_changed_limits_and_conditions_are_recorded(open_stub_state, edit_config, stub_dir):
    open_stub_state()
    edit_config(lambda c: c["limits"].update(run_seconds=30))
    st = open_stub_state()
    assert st.ledger.last("scenario")["data"]["config"]["limits"]["run_seconds"] == 30
    with open(stub_dir / "invalid.py", "a") as f:
        f.write("# changed condition\n")
    st = open_stub_state()
    assert len(st.ledger.entries("scenario")) == 3


def test_tampered_ledger_stops_opening(open_stub_state):
    st = open_stub_state()
    lines = st.ledger.path.read_text().splitlines()
    st.ledger.path.write_text("\n".join(lines[1:]) + "\n")
    with pytest.raises(LedgerError):
        open_stub_state()


def test_state_inside_scenario_is_refused(stub_dir):
    with pytest.raises(ValueError):
        open_state(stub_dir / "state", sc.load(stub_dir))


# --- runs ----------------------------------------------------------------------------------

def test_all_practice_tasks_run_and_are_recorded(state, make_harness):
    v = state.repo.save(make_harness("correct"), note="solver told to answer correctly")
    runner = dev_runner(state)
    records = [runner.run(t, v["version"]) for t in state.scenario.practice]

    for task, r in zip(state.scenario.practice, records):
        d = runner.run_dir(r["run"])
        assert set(r) == RECORD_KEYS  # no score value: the program does not read scores
        assert r["task"] == task and r["version"] == v["version"]
        assert r["harness_hash"] == v["hash"]
        assert r["run_seconds"] == 60 and r["solver_settings"] == SETTINGS
        assert r["run_result"]["exit_code"] == 0 and not r["run_result"]["timed_out"]
        assert r["score_result"]["exit_code"] == 0
        assert r["invalid"]["verdict"] == "valid"
        assert raw_score(runner, r)["score"] == 1.0
        assert json.loads((d / "record.json").read_text()) == r
        for f in ("out/answer.json", "out/agent.log", "run.stdout", "run.stderr",
                  "score.stdout", "score.stderr", "invalid.stdout", "invalid.stderr"):
            assert (d / f).is_file(), f
        assert not os.access(d / "score" / "score.json", os.W_OK)
        assert not os.access(d / "score", os.W_OK)

    assert [r["run"] for r in records] == ["r0001", "r0002", "r0003"]
    kinds = [e["kind"] for e in state.ledger.entries()]
    assert kinds == (["split_locked", "scenario", "harness_saved", "harness_saved"]
                     + ["run_started", "run_finished"] * 3)
    assert [e["data"] for e in state.ledger.entries("run_finished")] == records
    assert state.ledger.verify() == []


def test_initial_harness_runs_as_v0(state):
    runner = dev_runner(state)
    r = runner.run("p1", "v0")
    assert raw_score(runner, r)["score"] == 0.0


@pytest.mark.parametrize("mode,run_exit,score_exit,verdict", [
    ("crash", 3, 0, "valid"),           # run failure: still scored, counted, not invalid
    ("unreachable", 5, 0, "invalid"),   # matches the researcher's invalid-run condition
    ("break_scorer", 0, 2, "valid"),    # the scorer itself fails
])
def test_failures_are_recorded_as_what_they_are(state, make_harness, mode, run_exit,
                                                score_exit, verdict):
    v = state.repo.save(make_harness(mode))["version"]
    runner = dev_runner(state)
    r = runner.run("p2", v)
    assert r["run_result"]["exit_code"] == run_exit
    assert r["score_result"]["exit_code"] == score_exit
    assert r["invalid"]["verdict"] == verdict
    if verdict == "invalid":
        assert r["invalid"]["reason"] == "the model service was unreachable during the run"
    assert state.ledger.last("run_finished")["data"] == r
    assert state.ledger.verify() == []


def test_run_over_time_limit_is_killed_and_scored(open_stub_state, edit_config, make_harness):
    edit_config(lambda c: c["limits"].update(run_seconds=1))
    st = open_stub_state()
    v = st.repo.save(make_harness("hang"))["version"]
    runner = dev_runner(st)
    r = runner.run("p1", v)
    assert r["run_result"]["timed_out"] is True
    assert r["score_result"]["exit_code"] == 0
    assert raw_score(runner, r)["score"] == 0.0
    assert r["invalid"]["verdict"] == "valid"


def test_broken_invalid_check_is_recorded_as_such(open_stub_state, edit_config):
    edit_config(lambda c: c["commands"].update(invalid=["python3", "-c", "raise SystemExit(1)"]))
    st = open_stub_state()
    assert dev_runner(st).run("p1", "v0")["invalid"]["verdict"] == "check_error"


def test_no_invalid_condition(open_stub_state, edit_config):
    edit_config(lambda c: c["commands"].pop("invalid"))
    st = open_stub_state()
    assert dev_runner(st).run("p1", "v0")["invalid"] == {"verdict": "no_condition"}


def test_tasks_outside_the_area_are_refused(state):
    runner = dev_runner(state)
    for task in ("e1", "nope"):
        with pytest.raises(RunRefused):
            runner.run(task, "v0")
    with pytest.raises(RunRefused):
        runner.score("e1", state.root)
    assert not state.ledger.entries("run_started")


def test_interrupted_run_is_recorded(state, monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("program stopped")
    monkeypatch.setattr(sc.Scenario, "run", boom)
    with pytest.raises(RuntimeError):
        dev_runner(state).run("p1", "v0")
    kinds = [e["kind"] for e in state.ledger.entries()]
    assert kinds[-2:] == ["run_started", "run_aborted"]
    assert "program stopped" in state.ledger.last("run_aborted")["data"]["error"]


# --- a shorter time limit for one run ------------------------------------------------------

def test_run_may_ask_for_a_shorter_time_limit(state, make_harness):
    v = state.repo.save(make_harness("hang"))["version"]
    runner = dev_runner(state)
    r = runner.run("p1", v, seconds=1)
    assert r["run_seconds"] == 1 and r["run_result"]["timed_out"] is True
    assert r["run_result"]["seconds"] < 30  # the scenario's limit is 60
    assert r["score_result"]["exit_code"] == 0  # scored as it stands
    assert state.ledger.last("run_started")["data"]["run_seconds"] == 1
    assert state.ledger.last("run_finished")["data"]["run_seconds"] == 1
    assert runner.run("p1", "v0", seconds=60)["run_seconds"] == 60  # the limit itself is fine
    assert runner.run("p1", "v0")["run_seconds"] == 60


@pytest.mark.parametrize("seconds,message", [
    (61, "more than the limit"), (0, "positive"), (-5, "positive"), ("30", "positive"),
    (True, "positive"), (float("nan"), "positive"), (float("inf"), "positive"),
    (10 ** 400, "positive"),
])
def test_bad_time_limits_are_refused_before_anything_runs(state, seconds, message):
    with pytest.raises(RunRefused, match=message):
        dev_runner(state).run("p1", "v0", seconds=seconds)
    assert not state.ledger.entries("run_started")


def test_without_a_scenario_limit_any_positive_time_may_be_asked(open_stub_state, edit_config):
    edit_config(lambda c: c["limits"].update(run_seconds=None))
    runner = dev_runner(open_stub_state())
    assert runner.run("p1", "v0")["run_seconds"] is None
    assert runner.run("p1", "v0", seconds=100000)["run_seconds"] == 100000
    with pytest.raises(RunRefused):
        runner.run("p1", "v0", seconds=0)


# --- solver settings fixed by the researcher -----------------------------------------------

def test_solver_settings_reach_every_run_and_its_record(state, make_harness):
    runner = dev_runner(state)
    r = runner.run("p1", state.repo.save(make_harness("correct"))["version"])
    d = runner.run_dir(r["run"])
    assert r["solver_settings"] == SETTINGS
    assert json.loads((d / "out" / "solver_settings.json").read_text()) == SETTINGS
    assert "solver: settings " + json.dumps(SETTINGS, sort_keys=True) in (
        d / "out" / "agent.log").read_text()
    assert state.ledger.last("run_started")["data"]["solver_settings"] == SETTINGS


def test_harness_cannot_change_the_solver_settings(state, make_harness, tmp_path):
    forged = {"thinking": "low", "max_output_tokens": "999999"}
    folder = make_harness("correct", forge_settings=forged)
    (folder / "solver_settings.json").write_text(json.dumps(forged))  # nobody reads this
    runner = dev_runner(state)
    r = runner.run("p2", state.repo.save(folder)["version"])
    assert r["solver_settings"] == SETTINGS
    assert json.loads((runner.run_dir(r["run"]) / "out" / "solver_settings.json").read_text()) \
        == SETTINGS


def test_solver_settings_are_optional_and_changes_are_recorded(open_stub_state, edit_config):
    st = open_stub_state()
    edit_config(lambda c: c.pop("solver_settings"))
    st = open_stub_state()
    assert "solver_settings" not in st.ledger.last("scenario")["data"]["config"]
    runner = dev_runner(st)
    r = runner.run("p1", "v0")
    assert r["solver_settings"] == {}
    assert json.loads((runner.run_dir(r["run"]) / "out" / "solver_settings.json").read_text()) \
        == {}


def test_a_copy_of_a_version_may_be_run_in_its_place(state, make_harness, tmp_path):
    folder = make_harness("correct")
    v = state.repo.save(folder)["version"]
    runner = dev_runner(state)
    r = runner.run("p1", v, harness=folder)
    assert f"started from {folder}" in (runner.run_dir(r["run"]) / "out" / "agent.log").read_text()
    (folder / "settings.json").write_text('{"answer": "wrong"}')
    with pytest.raises(RunRefused, match="not a copy"):
        runner.run("p1", v, harness=folder)


# --- scoring a product ---------------------------------------------------------------------

def test_score_keeps_a_copy_of_the_product(state, tmp_path):
    product = tmp_path / "mine"
    product.mkdir()
    (product / "answer.json").write_text(json.dumps({"color": "red", "count": 4}))
    runner = dev_runner(state)
    r = runner.score("p1", product)
    d = runner.score_dir(r["score"])
    assert r["score"] == "s0001" and r["score_result"]["exit_code"] == 0
    assert json.loads((d / "score" / "score.json").read_text())["fields"] == {
        "color": True, "count": False}
    (product / "answer.json").write_text("{}")
    assert "red" in (d / "product" / "answer.json").read_text()
    assert [e["kind"] for e in state.ledger.entries()][-2:] == ["score_started",
                                                                "score_finished"]


def test_score_a_previous_run_output(state, make_harness):
    runner = dev_runner(state)
    v = state.repo.save(make_harness("correct"))["version"]
    run = runner.run("p3", v)
    r = runner.score("p3", runner.run_dir(run["run"]) / "out")
    assert json.loads((runner.score_dir(r["score"]) / "score" / "score.json").read_text())[
        "score"] == 1.0
