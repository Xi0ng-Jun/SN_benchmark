"""Step 1: the scenario interface, exercised on the stub scenario."""
import json
import os
import time
from pathlib import Path

import pytest

from harness_builder import scenario as sc

MODEL = {"model_url": "http://127.0.0.1:9", "model_key": "key-test", "model": "stub-model"}

# Field counts of the stub's tasks, written down independently of the stub files.
PRACTICE_FIELDS = {"p1": 2, "p2": 3, "p3": 1}


@pytest.fixture
def stub(stub_dir):
    return sc.load(stub_dir)


def run_and_score(stub, task, harness, base: Path):
    run = stub.run(task, harness, base / "out", base, **MODEL)
    score = stub.score(task, base / "out", base / "score", base)
    return run, score


def raw_score(base: Path) -> dict:
    return json.loads((base / "score" / "score.json").read_text())


def gone(pid: int, wait: float = 5.0) -> bool:
    """True once ``pid`` no longer exists or is a zombie."""
    deadline = time.monotonic() + wait
    while time.monotonic() < deadline:
        try:
            stat = Path(f"/proc/{pid}/stat").read_text()
        except (FileNotFoundError, ProcessLookupError):  # ESRCH if it exits mid-read
            return True
        if stat.rsplit(")", 1)[1].split()[0] == "Z":
            return True
        time.sleep(0.05)
    return False


# --- loading -------------------------------------------------------------------------------

def test_load_stub(stub, stub_dir):
    assert stub.root == stub_dir.resolve()
    assert stub.practice == ("p1", "p2", "p3")
    assert stub.exam == ("e1", "e2", "e3")
    assert stub.tasks["p1"].group == "p1"
    assert stub.tasks["p2"].group == stub.tasks["p3"].group == "g1"
    assert stub.tasks["p1"].files == stub.root / "tasks" / "p1"
    assert stub.initial_harness == stub.root / "harness"
    assert set(stub.commands) == {"run", "score", "enter", "invalid"}
    assert stub.limits == {"run_seconds": 60, "score_seconds": 60, "enter_seconds": 60,
                           "run_budget": {"requests": 5}, "dev_budget": {},
                           "dev_seconds": 600, "parallel_runs": 2}
    assert stub.models["solver"] == "stub-solver"


BAD_CONFIGS = [
    ("unknown key", lambda c: c.update(extra=1), "unknown keys"),
    ("missing key", lambda c: c.pop("score"), "missing keys"),
    ("task on neither side", lambda c: c["split"]["exam"].remove("e3"), "neither side"),
    ("task on both sides", lambda c: c["split"]["exam"].append("p1"), "listed twice"),
    ("unknown task in split", lambda c: c["split"]["exam"].append("zz"), "unknown task"),
    ("related tasks split up", lambda c: c["tasks"][3].update(group="g1"), "both sides"),
    ("empty side", lambda c: c["split"].update(exam=[]), "non-empty"),
    ("bad id", lambda c: c["tasks"].append({"id": "../x"}), "bad id"),
    ("duplicate id", lambda c: c["tasks"].append("p1"), "duplicate"),
    ("files dir missing", lambda c: c["tasks"][0].update(files="tasks/nope"), "not found"),
    ("practice files hold exam files", lambda c: c["tasks"][0].update(files="tasks"),
     "overlaps exam"),
    ("practice files hold the scenario", lambda c: c["tasks"][0].update(files="."),
     "scenario directory"),
    ("missing command", lambda c: c["commands"].pop("enter"), "commands"),
    ("empty command", lambda c: c["commands"].update(run=[]), "commands"),
    ("unknown command", lambda c: c["commands"].update(foo=["x"]), "commands"),
    ("limit missing", lambda c: c["limits"].pop("enter_seconds"), "limits"),
    ("limit not positive", lambda c: c["limits"].update(run_seconds=0), "limits"),
    ("limit not a number", lambda c: c["limits"].update(run_seconds="1h"), "limits"),
    ("unknown budget counter", lambda c: c["limits"].update(dev_budget={"dollars": 5}),
     "limits"),
    ("budget not an integer", lambda c: c["limits"].update(run_budget={"requests": 1.5}),
     "limits"),
    ("models missing solver", lambda c: c["models"].pop("solver"), "models"),
    ("models unknown key", lambda c: c["models"].update(temperature=0), "models"),
    ("models bad port", lambda c: c["models"].update(listen_port="80"), "models"),
    ("parallel runs zero", lambda c: c["limits"].update(parallel_runs=0), "parallel_runs"),
    ("developer without command", lambda c: c["developer"].pop("command"), "developer"),
    ("developer bad cpus", lambda c: c["developer"].update(cpus="2"), "developer"),
    ("score file outside output", lambda c: c.update(score={"file": "../x"}), "score"),
    ("score spec extra key", lambda c: c.update(score={"file": "s", "k": 1}), "score"),
    ("harness without start", lambda c: c.update(initial_harness="tasks"), "initial_harness"),
]


@pytest.mark.parametrize("change,message", [b[1:] for b in BAD_CONFIGS],
                         ids=[b[0] for b in BAD_CONFIGS])
def test_load_rejects(stub_dir, edit_config, change, message):
    edit_config(change)
    with pytest.raises(sc.ScenarioError, match=message):
        sc.load(stub_dir)


def test_load_rejects_start_that_is_not_executable(stub_dir):
    (stub_dir / "harness" / "start").chmod(0o644)
    with pytest.raises(sc.ScenarioError, match="not executable"):
        sc.load(stub_dir)


def test_load_rejects_missing_or_broken_config(tmp_path):
    with pytest.raises(sc.ScenarioError, match="scenario.json"):
        sc.load(tmp_path)
    (tmp_path / "scenario.json").write_text("{not json")
    with pytest.raises(sc.ScenarioError, match="not valid JSON"):
        sc.load(tmp_path)


# --- run and score -------------------------------------------------------------------------

@pytest.mark.parametrize("mode,expected", [("correct", 1.0), ("wrong", 0.0), ("none", 0.0)])
def test_scores_match_what_the_solver_was_told(stub, make_harness, tmp_path, mode, expected):
    harness = make_harness(mode)
    for task, n_fields in PRACTICE_FIELDS.items():
        base = tmp_path / task
        run, score = run_and_score(stub, task, harness, base)
        assert run.exit_code == 0 and not run.timed_out, run.stderr.read_text()
        assert score.exit_code == 0, score.stderr.read_text()
        raw = raw_score(base)
        assert raw["score"] == expected
        assert len(raw["fields"]) == n_fields
        assert all(v is (mode == "correct") for v in raw["fields"].values())
        assert stub.score_value(base / "score") == expected
        assert (base / "out" / "answer.json").exists() == (mode != "none")


def test_initial_harness_scores_zero(stub, tmp_path):
    run, score = run_and_score(stub, "p1", stub.initial_harness, tmp_path)
    assert run.exit_code == 0 and score.exit_code == 0
    assert stub.score_value(tmp_path / "score") == 0.0


def test_run_passes_inputs_and_keeps_logs(stub, make_harness, tmp_path):
    run, _ = run_and_score(stub, "p2", make_harness("correct"), tmp_path)
    assert "mode=correct model=stub-model" in (tmp_path / "out" / "agent.log").read_text()
    assert run.stdout == tmp_path / "run.stdout"
    assert "agent exited with 0" in run.stdout.read_text()
    d = run.to_dict()
    assert d["argv"] == ["python3", "run.py"] and d["exit_code"] == 0
    assert d["started"].endswith("+00:00")


def test_failed_run_is_reported_and_still_scorable(stub, make_harness, tmp_path):
    run, score = run_and_score(stub, "p1", make_harness("crash"), tmp_path)
    assert run.exit_code == 3 and not run.timed_out
    assert "crashing on purpose" in (tmp_path / "out" / "agent.log").read_text()
    assert score.exit_code == 0
    assert stub.score_value(tmp_path / "score") == 0.0


def test_scorer_error_is_reported_and_gives_no_score(stub, make_harness, tmp_path):
    run, score = run_and_score(stub, "p1", make_harness("break_scorer"), tmp_path)
    assert run.exit_code == 0
    assert score.exit_code == 2
    assert "failing on purpose" in score.stderr.read_text()
    assert stub.score_value(tmp_path / "score") is None


def test_score_a_product_built_by_hand(stub, tmp_path):
    product = tmp_path / "product"
    product.mkdir()
    (product / "answer.json").write_text(json.dumps({"shape": "square", "sides": 5}))
    score = stub.score("p2", product, tmp_path / "score", tmp_path)
    assert score.exit_code == 0
    assert raw_score(tmp_path)["fields"] == {"shape": True, "sides": False, "closed": False}
    assert stub.score_value(tmp_path / "score") == pytest.approx(1 / 3)


def test_unknown_task_is_refused(stub, make_harness, tmp_path):
    with pytest.raises(sc.ScenarioError, match="unknown task"):
        stub.run("zz", make_harness("correct"), tmp_path / "out", tmp_path, **MODEL)


# --- enter ---------------------------------------------------------------------------------

def test_enter_runs_in_the_task_environment(stub, tmp_path):
    work = tmp_path / "work"
    r = stub.enter("p1", ["sh", "-c", "cat task.json; echo hi > made.txt; exit 7"], work,
                   tmp_path / "logs")
    assert r.exit_code == 7
    assert '"color": "red"' in r.stdout.read_text()
    assert (work / "made.txt").read_text() == "hi\n"


def test_enter_rejects_bad_command(stub, tmp_path):
    with pytest.raises(sc.ScenarioError, match="non-empty list"):
        stub.enter("p1", "ls", tmp_path / "work", tmp_path / "logs")


def test_commands_see_only_their_own_hb_variables(stub, tmp_path, monkeypatch):
    monkeypatch.setenv("HB_LEFTOVER", "x")
    r = stub.enter("p1", ["sh", "-c", 'echo "${HB_LEFTOVER:-unset} $HB_TASK $HB_SCENARIO"'],
                   tmp_path / "work", tmp_path / "logs")
    assert r.stdout.read_text().split() == ["unset", "p1", str(stub.root)]


# --- process handling ----------------------------------------------------------------------

def test_timeout_kills_the_whole_process_group(stub, tmp_path):
    work = tmp_path / "work"
    t0 = time.monotonic()
    r = stub.enter("p1", ["sh", "-c", "echo $$ > pid; sleep 60"], work, tmp_path / "logs",
                   timeout=2)
    assert r.timed_out
    assert time.monotonic() - t0 < 30
    assert gone(int((work / "pid").read_text()))


def test_leftover_processes_are_killed(stub, tmp_path):
    work = tmp_path / "work"
    r = stub.enter("p1", ["sh", "-c", "sleep 60 & echo $! > pid"], work, tmp_path / "logs")
    assert r.exit_code == 0 and not r.timed_out
    assert gone(int((work / "pid").read_text()))


def test_command_that_cannot_start_is_reported(stub_dir, edit_config, tmp_path):
    edit_config(lambda c: c["commands"].update(score=["./no-such-scorer"]))
    stub = sc.load(stub_dir)
    product = tmp_path / "product"
    product.mkdir()
    r = stub.score("p1", product, tmp_path / "score", tmp_path)
    assert r.exit_code is None and r.error
    assert "could not start" in r.stderr.read_text()


# --- reading the score field ---------------------------------------------------------------

def test_score_value_reads_only_the_configured_place(stub, tmp_path):
    d = tmp_path / "raw"
    d.mkdir()
    assert stub.score_value(d) is None
    for text, expected in [('{"score": 0.5}', 0.5), ('{"score": true}', 1.0),
                           ('{"score": "high"}', None), ('{"other": 1}', None),
                           ("not json", None), ('{"score": NaN}', None)]:
        (d / "score.json").write_text(text)
        assert stub.score_value(d) == expected, text


def test_score_value_plain_number_and_nested_key(stub_dir, edit_config, tmp_path):
    d = tmp_path / "raw"
    (d / "verifier").mkdir(parents=True)
    (d / "verifier" / "reward.txt").write_text("1\n")
    (d / "out.json").write_text('{"a": {"b": 0.25}}')
    edit_config(lambda c: c.update(score={"file": "verifier/reward.txt"}))
    assert sc.load(stub_dir).score_value(d) == 1.0
    edit_config(lambda c: c.update(score={"file": "out.json", "key": "a.b"}))
    assert sc.load(stub_dir).score_value(d) == 0.25
