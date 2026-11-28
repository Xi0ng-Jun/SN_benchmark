"""Step 5: the final exam is held as the scenario's exam settings say, and only then."""
import json
import shutil

import pytest

from harness_builder import delivery, exam
from harness_builder.develop import DevSession, SessionRefused
from harness_builder.runs import Runner
from harness_builder.sandbox import LocalSandbox

EXAM_TASKS = ["e1", "e2", "e3"]


@pytest.fixture
def ready(open_stub_state, designate, make_harness, make_gateway):
    """ready(**solver) -> (state, gateway) with a delivered version that passed its check."""
    def ready(mode="correct", **settings):
        state, gw = open_stub_state(), make_gateway()
        designate(state, make_harness(mode, **settings))
        while delivery.check(state, gw)["outcome"] == "invalid":
            pass
        assert delivery.accepted(state) is not None
        return state, gw
    return ready


@pytest.fixture
def compare_with(stub_dir, edit_config):
    """compare_with(name, **solver settings) adds a compared harness folder to the scenario."""
    def add(name, **settings):
        shutil.copytree(stub_dir / "harness", stub_dir / f"harness_{name}")
        (stub_dir / f"harness_{name}" / "settings.json").write_text(json.dumps(settings))
        edit_config(lambda c: c["exam"]["compare"].update({name: f"harness_{name}"}))
    return add


def exam_runs(state, xid="x0001"):
    return [e["data"] for e in state.ledger.entries("exam_run") if e["data"]["exam"] == xid]


def run_records(state, xid="x0001"):
    return {e["data"]["run"]: e["data"] for e in state.ledger.entries("run_finished")
            if e["data"]["area"] == f"exam/{xid}"}


def test_every_object_sits_every_exam_task_under_the_same_conditions(ready):
    state, gw = ready(model_calls=1)
    delivered = delivery.accepted(state)
    done = exam.run(state, gw)

    start = state.ledger.last("exam_started")["data"]
    assert start["exam"] == "x0001" and start["tasks"] == EXAM_TASKS
    assert start["objects"] == [
        {"name": "delivered", "version": delivered["version"], "hash": delivered["hash"]},
        {"name": "general", "version": "v0", "hash": state.repo.get("v0")["hash"]}]
    assert start["settings"] == state.scenario.config["exam"]
    assert start["solver_settings"] == {"thinking": "high", "max_output_tokens": "4096"}

    runs, records = exam_runs(state), run_records(state)
    assert len(runs) == len(records) == 2 * 3 * 2  # objects x exam tasks x repeats
    assert sorted((r["object"], r["task"], r["repeat"]) for r in runs) == sorted(
        (o, t, n) for o in ("delivered", "general") for t in EXAM_TASKS for n in (1, 2))
    assert all(r["attempt"] == 1 and r["outcome"] == "scored" for r in runs)
    for r in runs:  # the recorded score is what the official scoring wrote, nothing else
        raw = json.loads((state.root / "exam" / "x0001" / "runs" / r["run"] / "score"
                          / "score.json").read_text())
        assert r["score"] == raw["score"] == (1.0 if r["object"] == "delivered" else 0.0)
        assert records[r["run"]]["task"] == r["task"]
        assert records[r["run"]]["version"] == r["version"]
    # same limits, solver settings, model and budget cap for every compared object
    conditions = {json.dumps([rec["run_seconds"], rec["solver_settings"], rec["usage"]["model"],
                              rec["usage"]["caps"], rec["usage"]["pool"]], sort_keys=True)
                  for rec in records.values()}
    assert conditions == {json.dumps([60, {"max_output_tokens": "4096", "thinking": "high"},
                                      "stub-solver", {"requests": 5}, "exam"], sort_keys=True)}
    assert all(rec["invalid"]["verdict"] == "valid" for rec in records.values())

    assert done == state.ledger.last("exam_finished")["data"]
    assert done["results"] == {
        "delivered": {"aggregate": 1.0, "counted": 6, "no_score": 0, "without_valid_run": []},
        "general": {"aggregate": 0.0, "counted": 6, "no_score": 0, "without_valid_run": []}}
    assert done["changed_since_start"] == []
    assert not (state.root / "dev" / "runs").exists()  # nothing of the exam under dev records
    assert state.ledger.verify() == []


@pytest.mark.parametrize("prepare,reason", [
    (lambda state, designate, harness: None, "has not passed a delivery check"),
    (lambda state, designate, harness: designate(state, harness("correct")),
     "has not passed a delivery check"),
    (lambda state, designate, harness: (designate(state, harness("crash")),
                                        delivery.check(state, None)),
     "has not passed a delivery check"),
], ids=["nothing delivered", "not checked", "check failed"])
def test_no_exam_without_a_passed_delivery_check(open_stub_state, designate, make_harness,
                                                 prepare, reason):
    state = open_stub_state()
    prepare(state, designate, make_harness)
    with pytest.raises(exam.ExamRefused, match=reason):
        exam.run(state, None)
    assert reason in state.ledger.last("exam_refused")["data"]["reason"]
    assert not state.ledger.entries("exam_started") and not (state.root / "exam").exists()


def test_exam_tasks_are_used_only_as_often_as_allowed(ready, open_stub_state, edit_config):
    state, gw = ready()
    exam.run(state, gw)
    with pytest.raises(exam.ExamRefused, match=r"1 exam\(s\); 1 already on record"):
        exam.run(state, gw)
    assert len(state.ledger.entries("exam_started")) == 1 and len(run_records(state)) == 12
    assert not (state.root / "exam" / "x0002").exists()

    edit_config(lambda c: c["exam"].update(uses=2))  # the researcher's decision, on record
    state = open_stub_state()
    assert state.ledger.last("scenario")["data"]["config"]["exam"]["uses"] == 2
    assert exam.run(state, gw)["exam"] == "x0002"
    with pytest.raises(exam.ExamRefused):
        exam.run(state, gw)


def test_runs_without_an_official_score_count_as_failures(ready, compare_with, edit_config):
    compare_with("scorer_breaks", answer="break_scorer")
    compare_with("crashes", answer="crash")
    edit_config(lambda c: c["exam"].update(failure_score=-1, repeats=1))
    state, gw = ready()
    results = exam.run(state, gw)["results"]
    assert results["scorer_breaks"] == {"aggregate": -1.0, "counted": 3, "no_score": 3,
                                        "without_valid_run": []}
    # a run that failed but was scored keeps its official score
    assert results["crashes"] == {"aggregate": 0.0, "counted": 3, "no_score": 0,
                                  "without_valid_run": []}
    assert results["delivered"]["aggregate"] == 1.0
    by_object = {}
    for r in exam_runs(state):
        by_object.setdefault(r["object"], set()).add((r["outcome"], r["score"]))
    assert by_object["scorer_breaks"] == {("no_score", None)}
    assert by_object["crashes"] == {("scored", 0.0)}


def test_invalid_runs_are_run_again_and_do_not_count(ready, compare_with, edit_config,
                                                     tmp_path):
    markers = tmp_path / "markers"
    markers.mkdir()
    compare_with("always_invalid", answer="unreachable")
    edit_config(lambda c: c["limits"].update(parallel_runs=1))
    # the delivered solver is unreachable on its first run of each task, then answers
    state, gw = ready(unreachable_once=str(markers))
    assert [e["data"]["outcome"] for e in state.ledger.entries("delivery_checked")] == [
        "invalid", "passed"]
    results = exam.run(state, gw)["results"]

    attempts = {}
    for r in exam_runs(state):
        attempts.setdefault((r["object"], r["task"], r["repeat"]), []).append(r["outcome"])
    for task in EXAM_TASKS:
        assert attempts[("delivered", task, 1)] == ["invalid", "scored"]
        assert attempts[("delivered", task, 2)] == ["scored"]
        for repeat in (1, 2):  # invalid_reruns is 1: two attempts, then left without a run
            assert attempts[("always_invalid", task, repeat)] == ["invalid", "invalid"]
    assert results["delivered"] == {"aggregate": 1.0, "counted": 6, "no_score": 0,
                                    "without_valid_run": []}
    assert results["always_invalid"]["aggregate"] is None
    assert results["always_invalid"]["counted"] == 0
    assert results["always_invalid"]["without_valid_run"] == [
        {"task": t, "repeat": n} for n in (1, 2) for t in EXAM_TASKS]
    reasons = {rec["invalid"].get("reason") for rec in run_records(state).values()
               if rec["invalid"]["verdict"] == "invalid"}
    assert reasons == {"the model service was unreachable during the run"}


def test_interrupted_exam_is_continued_not_held_again(ready, edit_config, monkeypatch):
    edit_config(lambda c: c["limits"].update(parallel_runs=1))
    state, gw = ready()
    real, calls = Runner.run, []

    def stops_at_the_fourth(self, task, version, **kw):
        calls.append(task)
        if len(calls) == 4:
            raise RuntimeError("program stopped")
        return real(self, task, version, **kw)
    monkeypatch.setattr(Runner, "run", stops_at_the_fourth)
    with pytest.raises(RuntimeError):
        exam.run(state, gw)
    assert len(exam_runs(state)) == 3 and not state.ledger.entries("exam_finished")

    monkeypatch.setattr(Runner, "run", real)
    done = exam.run(state, gw)
    assert done["exam"] == "x0001" and len(state.ledger.entries("exam_started")) == 1
    assert state.ledger.last("exam_continued")["data"] == {"exam": "x0001"}
    runs = exam_runs(state)
    assert len(runs) == 12 == len({(r["object"], r["task"], r["repeat"]) for r in runs})
    assert done["results"]["delivered"]["counted"] == 6
    with pytest.raises(exam.ExamRefused):  # finished: the one allowed use is spent
        exam.run(state, gw)


def test_exam_is_not_continued_under_changed_conditions(ready, edit_config, open_stub_state,
                                                        monkeypatch):
    edit_config(lambda c: c["limits"].update(parallel_runs=1))
    state, gw = ready()
    real, calls = Runner.run, []

    def stops_at_the_third(self, task, version, **kw):
        calls.append(task)
        if len(calls) == 3:
            raise RuntimeError("program stopped")
        return real(self, task, version, **kw)
    monkeypatch.setattr(Runner, "run", stops_at_the_third)
    with pytest.raises(RuntimeError):
        exam.run(state, gw)
    monkeypatch.setattr(Runner, "run", real)

    edit_config(lambda c: c["limits"].update(run_seconds=30))
    state = open_stub_state()
    with pytest.raises(exam.ExamRefused, match="scenario directory changed"):
        exam.run(state, gw)
    assert len(exam_runs(state)) == 2

    closed = exam.close(state)  # ended as it stands
    assert closed == state.ledger.last("exam_closed")["data"]
    assert closed["results"]["delivered"]["counted"] == 1
    assert closed["results"]["general"]["counted"] == 1
    assert len(closed["results"]["delivered"]["without_valid_run"]) == 5
    with pytest.raises(exam.ExamRefused, match="no unfinished exam"):
        exam.close(state)
    with pytest.raises(exam.ExamRefused, match="already on record"):  # it was a use
        exam.run(state, gw)


def test_nothing_of_the_exam_reaches_development(ready):
    state, gw = ready()
    session = DevSession(state, gw, LocalSandbox())
    exam.run(state, gw)
    exam_dir = (state.root / "exam").resolve()
    assert exam_dir.is_dir()
    lay = session.layout()
    for shown in (lay.bin, lay.brief, lay.requests, lay.responses, lay.materials, lay.tasks,
                  lay.records, lay.versions, lay.workspace):
        shown = shown.resolve()
        assert shown != exam_dir and shown not in exam_dir.parents, shown
    with pytest.raises(SessionRefused, match="exam has been started"):
        session.run()
    with pytest.raises(delivery.DeliveryError, match="exam has been started"):
        delivery.check(state, gw)
    assert not state.ledger.entries("dev_session_started")


@pytest.mark.parametrize("how,scores,expected", [
    ("mean", [1.0, 0.5, 0.0], 0.5), ("median", [1.0, 0.25, 0.0], 0.25),
    ("min", [1.0, 0.5, 0.25], 0.25), ("max", [1.0, 0.5, 0.25], 1.0),
    ("sum", [1.0, 0.5, 0.25], 1.75),
])
def test_aggregates(how, scores, expected):
    start = {"settings": {"repeats": 1, "aggregate": how, "failure_score": 0},
             "tasks": ["a", "b", "c"], "objects": [{"name": "delivered"}]}
    runs = [{"repeat": 1, "task": t, "object": "delivered", "outcome": "scored", "score": s}
            for t, s in zip("abc", scores)]
    assert exam.results(start, runs)["delivered"]["aggregate"] == expected
