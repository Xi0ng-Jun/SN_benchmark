"""Step 4: the request channel refuses what it must and records everything."""
import json

import pytest

from harness_builder.materials import Materials, Questions
from harness_builder.ops import OpsServer
from harness_builder.runs import Runner


@pytest.fixture
def server(open_stub_state, tmp_path):
    state = open_stub_state()
    materials = Materials(state.root / "materials", state.ledger)
    ws = tmp_path / "ws"
    ws.mkdir()
    s = OpsServer(runner=Runner(state, "dev", state.scenario.practice),
                  questions=Questions(state.root / "questions", state.ledger, materials),
                  materials=materials, requests=tmp_path / "ops" / "requests",
                  responses=tmp_path / "ops" / "responses", workspace=ws,
                  view={"records": "/records", "materials": "/materials"}, parallel_runs=1)
    s.requests.mkdir(parents=True)
    s.responses.mkdir(parents=True)
    return s


def request(server, op, args=None, rid="r-1", raw=None):
    (server.requests / f"{rid}.json").write_text(
        raw if raw is not None else json.dumps({"op": op, "args": args or {}}))
    server.poll_once()
    for t in server._threads:
        t.join()
    return json.loads((server.responses / f"{rid}.json").read_text())


@pytest.mark.parametrize("op,args,message", [
    ("run", {"task": "e1"}, "not a practice task"),
    ("enter", {"task": "e2", "command": ["ls"]}, "not a practice task"),
    ("score", {"task": "p1", "product": "../outside"}, "leaves the workspace"),
    ("score", {"task": "p1", "product": "/etc"}, "relative"),
    ("run", {"task": "p1", "version": "v7"}, "no harness version"),
    ("run", {"task": "p1", "version": "v0", "harness": "harness"}, "not both"),
    ("run", {"task": "p1", "version": "v0", "seconds": 61}, "more than the limit"),
    ("run", {"task": "p1", "version": "v0", "seconds": 0}, "positive"),
    ("run", {"task": "p1", "version": "v0", "seconds": "10"}, "positive"),
    ("deliver", {"version": "v0"}, "MANIFEST"),
    ("ask", {"question": "?", "why": "", "assumption": "x"}, "non-empty"),
    ("format_disk", {}, "unknown operation"),
])
def test_refusals_are_replied_and_recorded(server, op, args, message):
    if op == "deliver":  # v0 of the stub has a manifest; take it away in a new version
        folder = server.workspace / "bare"
        folder.mkdir()
        (folder / "start").write_text("#!/bin/sh\n")
        args = {"harness": "bare"}
    reply = request(server, op, args)
    assert reply["ok"] is False and message in reply["error"]
    done = server.ledger.last("op_done")["data"]
    assert done["ok"] is False and done["op"] == op


def test_malformed_requests(server):
    reply = request(server, None, raw="not json")
    assert reply["ok"] is False and "not JSON" in reply["error"]
    (server.requests / "bad name!.json").write_text("{}")
    server.poll_once()
    for t in server._threads:
        t.join()
    assert server.ledger.last("op_refused")["data"]["error"] == "bad request id"


def test_run_from_workspace_folder_makes_a_version(server):
    harness = server.workspace / "harness"
    import shutil
    shutil.copytree(server.state.repo.path("v0"), harness)
    for p in [harness, *harness.iterdir()]:
        p.chmod(0o755)
    (harness / "settings.json").write_text('{"answer": "correct"}')
    reply = request(server, "run", {"task": "p3", "harness": "harness"})
    assert reply["ok"], reply
    assert reply["result"]["record"]["version"] == "v1"
    assert json.loads(reply["result"]["score_output"]["score.json"])["score"] == 1.0
    assert reply["result"]["run_dir"] == "/records/runs/r0001"
    reply = request(server, "deliver", {"version": "v1"}, rid="r-2")
    assert reply["ok"] and server.ledger.last("delivery_designated")["data"]["version"] == "v1"


def test_run_with_a_shorter_time_limit(server):
    reply = request(server, "run", {"task": "p1", "version": "v0", "seconds": 20})
    assert reply["ok"], reply
    assert reply["result"]["record"]["run_seconds"] == 20
    assert server.ledger.last("op_request")["data"]["args"]["seconds"] == 20
    reply = request(server, "run", {"task": "p1", "harness": "nowhere", "seconds": 600},
                    rid="r-2")
    assert reply["ok"] is False and "more than the limit" in reply["error"]
    assert len(server.state.repo.versions()) == 1  # refused before any version was stored


def test_status_shows_fixed_settings_and_delivery_checks(server, designate, make_harness):
    from harness_builder import delivery
    designate(server.state, make_harness("crash"))
    assert delivery.check(server.state, None)["outcome"] == "failed"
    status = request(server, "status")["result"]
    assert status["run_seconds"] == 60
    assert status["solver_settings"] == {"thinking": "high", "max_output_tokens": "4096"}
    (check,) = status["delivery_checks"]
    assert check["outcome"] == "failed" and "exit code 3" in check["problems"][0]
    assert check["run_dir"] == "/records/trial/runs/r0001"
    assert (server.state.root / "dev" / "trial" / "runs" / "r0001" / "record.json").is_file()
