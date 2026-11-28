"""Step 5: the stand-in developer's delivery goes through the trial and the exam, and every
number in the report is found in the records."""
import json
import statistics

import pytest
from test_develop import run_stub_developer

from harness_builder import delivery, exam, report
from harness_builder.runs import Runner
from harness_builder.sandbox import LocalSandbox

REFERENCE_KEYS = ("record", "run_record", "results_record", "split_record")


def referenced(obj):
    """Every ledger sequence number the report points to."""
    if isinstance(obj, dict):
        for k, v in obj.items():
            if k in REFERENCE_KEYS and v is not None:
                yield v
            elif k == "records":
                yield from v
            else:
                yield from referenced(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from referenced(v)


@pytest.fixture
def course(open_stub_state, make_gateway, tmp_path):
    """The whole course once: development by the stand-in developer, check, exam."""
    state, gw = open_stub_state(), make_gateway()
    session, record, log, err = run_stub_developer(state, gw, LocalSandbox(), tmp_path)
    assert record["agent_result"]["exit_code"] == 0, err[-3000:]
    assert delivery.check(state, gw)["outcome"] == "passed"
    exam.run(state, gw)
    return state, gw


def test_every_number_in_the_report_is_in_the_records(course):
    state, gw = course
    r = report.build(state)
    entries = state.ledger.entries()
    refs = list(referenced(r))
    assert refs and all(isinstance(n, int) and 0 <= n < len(entries) for n in refs)
    assert r["ledger"] == {"entries": len(entries), "last_hash": entries[-1]["hash"],
                           "problems": []}
    assert r["materials_problems"] == []

    # scores: per object, task and repeat, as the official scoring wrote them
    (x,) = r["exams"]
    assert x["exam"] == "x0001" and x["status"] == "finished"
    assert [o["name"] for o in x["objects"]] == ["delivered", "general"]
    counted = {"delivered": [], "general": []}
    for name, tasks in x["scores"].items():
        assert sorted(tasks) == ["e1", "e2", "e3"]
        for task, repeats in tasks.items():
            assert [rep["repeat"] for rep in repeats] == [1, 2]
            for rep in repeats:
                (a,) = rep["attempts"]
                on_ledger = entries[a["record"]]
                assert on_ledger["kind"] == "exam_run"
                assert on_ledger["data"]["score"] == a["score"] == rep["counts"]
                assert (on_ledger["data"]["object"], on_ledger["data"]["task"],
                        on_ledger["data"]["repeat"]) == (name, task, rep["repeat"])
                raw = json.loads((state.root / a["run"] / "score" / "score.json").read_text())
                assert raw["score"] == a["score"]
                run = entries[a["run_record"]]
                assert run["kind"] == "run_finished"
                assert f"{run['data']['area']}/runs/{run['data']['run']}" == a["run"]
                assert a["run_seconds"] == run["data"]["run_result"]["seconds"]
                assert a["usage"]["requests"] == run["data"]["usage"]["requests"]
                counted[name].append(raw["score"])
    # the aggregate: as the exam recorded it, and as recomputed here from the raw scores
    finished = entries[x["results_record"]]
    assert finished["kind"] == "exam_finished" and finished["data"]["results"] == x["results"]
    for name in counted:
        assert len(counted[name]) == x["results"][name]["counted"] == 6
        assert x["results"][name]["aggregate"] == statistics.fmean(counted[name])
    assert x["results"]["delivered"]["aggregate"] == 1.0
    assert x["results"]["general"]["aggregate"] == 0.0

    # costs
    dev = r["costs"]["development"]
    assert dev["developer_agent"]["requests"] == 1 and dev["runs"]["requests"] == 3
    assert dev["total"]["requests"] == gw.pool_usage("dev")["requests"] == 4
    assert dev["total"]["total_tokens"] == gw.pool_usage("dev")["total_tokens"] > 0
    assert all(entries[n]["kind"] == "run_finished" for n in dev["runs"]["records"])
    assert r["costs"]["delivery_trials"]["requests"] == gw.pool_usage("delivery")["requests"] == 1
    assert x["costs"]["delivered"]["runs"] == 6 and x["costs"]["delivered"]["requests"] == 6
    assert x["costs"]["general"]["runs"] == 6 and x["costs"]["general"]["requests"] == 0
    assert gw.pool_usage("exam")["requests"] == 6

    # the development process
    d = r["development"]
    assert {k: d[k]["count"] for k in ("sessions", "runs", "runs_refused", "runs_aborted",
                                       "scorings", "environment_commands", "questions",
                                       "answers", "materials_added")} == {
        "sessions": 1, "runs": 3, "runs_refused": 0, "runs_aborted": 0, "scorings": 2,
        "environment_commands": 1, "questions": 1, "answers": 1, "materials_added": 2}
    assert d["sessions"]["seconds"] == entries[d["sessions"]["records"][0]]["data"]["seconds"]
    assert d["scenario_changes"] == [] and d["invalid_runs"] == []

    # the delivered harness
    got = r["delivery"]["delivered"]
    named = state.ledger.last("delivery_designated")["data"]
    assert (got["version"], got["hash"]) == (named["version"], named["hash"])
    assert got["manifest"] == (state.repo.path(named["version"]) / "MANIFEST.md").read_text()
    assert "settings.json now says `correct`" in got["manifest"]
    assert got["folder_matches_hash"] is True and got["manifest_matches_hash"] is True
    assert entries[got["record"]]["kind"] == "delivery_checked"
    assert r["scenario"]["solver_settings"] == {"thinking": "high", "max_output_tokens": "4096"}
    assert r["scenario"]["exam"] == state.scenario.config["exam"]


def test_report_is_written_as_text_and_json(course):
    state, _ = course
    json_path, text_path = report.write(state)
    assert json_path == state.root / "report" / "report.json"
    r = json.loads(json_path.read_text())
    assert r["exams"][0]["results"]["delivered"]["aggregate"] == 1.0
    text = text_path.read_text()
    for part in ("## Scores", "## Costs", "## Development process", "## Delivered harness",
                 "- delivered: 1.0 over 6 runs", "- general: 0.0 over 6 runs",
                 "delivered / e2 / repeat 2 / attempt 1: scored, score 1.0",
                 r["delivery"]["delivered"]["hash"], "Manifest as delivered:",
                 "settings.json now says `correct`", '"thinking": "high"'):
        assert part in text, part
    report.write(state)  # writing again only replaces the two files
    assert state.ledger.verify() == []


def test_report_before_anything_was_delivered(open_stub_state):
    state = open_stub_state()
    r = report.build(state)
    assert r["exams"] == [] and r["delivery"]["delivered"] is None
    assert r["development"]["runs"] == {"count": 0, "records": []}
    text = report.render(r)
    assert "No exam has been held." in text and "Nothing was delivered." in text


def test_report_lists_limit_changes_invalid_runs_and_refused_exams(
        open_stub_state, edit_config, make_harness):
    state = open_stub_state()
    v = state.repo.save(make_harness("unreachable"))["version"]
    Runner(state, "dev", state.scenario.practice).run("p2", v)
    edit_config(lambda c: c["limits"].update(run_seconds=30, dev_budget={"requests": 9}))
    state = open_stub_state()
    with pytest.raises(exam.ExamRefused):
        exam.run(state, None)
    r = report.build(state)
    (change,) = r["development"]["scenario_changes"]
    assert change["config_keys"] == ["limits"]
    assert change["limits"] == {"run_seconds": {"before": 60, "after": 30},
                                "dev_budget": {"before": None, "after": {"requests": 9}}}
    assert state.ledger.entries()[change["record"]]["kind"] == "scenario"
    (invalid,) = r["development"]["invalid_runs"]
    assert invalid["run"] == "dev/runs/r0001" and invalid["task"] == "p2"
    assert invalid["reason"] == "the model service was unreachable during the run"
    (refused,) = r["exams_refused"]
    assert "delivery check" in refused["reason"]
    text = report.render(r)
    assert "run_seconds: 60 -> 30" in text and "Exam refused" in text
    assert "dev/runs/r0001 (task p2): the model service was unreachable" in text


def test_unfinished_exam_is_reported_without_an_aggregate(open_stub_state, designate,
                                                          make_harness, monkeypatch):
    state = open_stub_state()
    designate(state, make_harness("correct"))
    delivery.check(state, None)
    real, calls = Runner.run, []

    def stops_at_the_second(self, task, version, **kw):
        calls.append(task)
        if len(calls) == 2:
            raise RuntimeError("program stopped")
        return real(self, task, version, **kw)
    monkeypatch.setattr(Runner, "run", stops_at_the_second)
    with pytest.raises(RuntimeError):
        exam.run(state, None)
    (x,) = report.build(state)["exams"]
    assert x["status"] == "unfinished" and x["results"] is None
    text = report.render(report.build(state))
    assert "Not finished; no aggregate." in text and "not run" in text
