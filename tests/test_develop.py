"""Step 4: the stand-in developer goes through change, run, score, enter, ask, answer,
deliver — once as a local process and once in a network-less container.

The container test needs Docker and an image with python3; it uses $HB_TEST_IMAGE
(default ssap-alw-agent:2.1.302, present on the evaluation server) and is skipped,
with the reason shown by ``pytest -rs``, when either is missing.
"""
import json
import os
import shutil
import subprocess
import threading
from pathlib import Path

import pytest

from harness_builder.develop import DevSession, SessionRefused
from harness_builder.sandbox import ContainerSandbox, LocalSandbox

HERE = Path(__file__).parent
IMAGE = os.environ.get("HB_TEST_IMAGE", "ssap-alw-agent:2.1.302")
PYTHON_CANDIDATES = ["python3", "/opt/analog-layout/python/bin/python3.12",
                     "/usr/local/bin/python3", "/usr/bin/python3"]


def container_python():
    """Path of a python3 inside IMAGE, or a reason why the container test cannot run."""
    if shutil.which("docker") is None:
        return None, "docker not found"
    if subprocess.run(["docker", "image", "inspect", IMAGE], stdout=subprocess.DEVNULL,
                      stderr=subprocess.DEVNULL).returncode != 0:
        return None, f"image {IMAGE} not present"
    probe = " ".join(f'command -v {c} >/dev/null 2>&1 && {{ command -v {c}; exit 0; }};'
                     for c in PYTHON_CANDIDATES) + " exit 1"
    p = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh",
                        IMAGE, "-c", probe], stdout=subprocess.PIPE, text=True)
    if p.returncode != 0 or not p.stdout.strip():
        return None, f"no python3 in {IMAGE}"
    return p.stdout.strip().splitlines()[-1], None


def answer_questions(session, stop):
    """The researcher's side: answer every question as soon as it appears."""
    while not stop.is_set():
        for q in session.questions.list():
            if not q["answers"]:
                session.questions.answer(q["id"], "No preference: keep JSON as in the task.")
        stop.wait(0.2)


def run_stub_developer(state, gateway, sandbox, tmp_path):
    ws = state.root / "workspace"
    ws.mkdir(parents=True, exist_ok=True)
    shutil.copy(HERE / "stub_developer.py", ws / "stub_developer.py")
    session = DevSession(state, gateway, sandbox)
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "README.md").write_text("Answers are JSON objects.\n")
    session.materials.add(notes, note="initial materials")
    stop = threading.Event()
    t = threading.Thread(target=answer_questions, args=(session, stop), daemon=True)
    t.start()
    try:
        record = session.run()
    finally:
        stop.set()
        t.join()
    log_path = ws / "dev-log.json"
    log = json.loads(log_path.read_text()) if log_path.exists() else []
    agent_err = (state.root / "sessions" / record["session"] / "agent.stderr").read_text()
    return session, record, log, agent_err


def check_session(state, session, record, log, agent_err):
    failed = [s for s in log if not s["ok"]]
    assert log and not failed, json.dumps(failed, indent=1)[:4000] + agent_err[-2000:]
    assert record["agent_result"]["exit_code"] == 0, agent_err[-3000:]

    kinds = [e["kind"] for e in state.ledger.entries()]
    for k in ("material_added", "dev_session_started", "op_request", "run_finished",
              "score_finished", "enter_finished", "question", "answer",
              "delivery_designated", "dev_session_finished"):
        assert k in kinds, k
    assert kinds.count("run_finished") == 2 and kinds.count("score_finished") == 2

    delivered = state.ledger.last("delivery_designated")["data"]
    v = state.repo.path(delivered["version"])
    assert json.loads((v / "settings.json").read_text())["answer"] == "correct"
    assert state.repo.verify(delivered["version"])

    usage = record["usage"]  # the developer agent's own model use
    assert usage["model"] == "stub-developer"
    assert usage["requests"] == 1 and usage["refused"] == {"model": 1}
    runs = [e["data"] for e in state.ledger.entries("run_finished")]
    assert all(r["usage"]["requests"] == 1 for r in runs)  # the solver's call in each run
    assert session.gateway.pool_usage("dev")["requests"] == 3
    assert state.ledger.verify() == [] and session.materials.verify() == []


def test_session_in_local_sandbox(open_stub_state, make_gateway, tmp_path):
    state = open_stub_state()
    session, record, log, err = run_stub_developer(state, make_gateway(), LocalSandbox(),
                                                   tmp_path)
    check_session(state, session, record, log, err)
    assert record["kind"] == "local"


def test_session_in_container(open_stub_state, make_gateway, tmp_path):
    python, reason = container_python()
    if python is None:
        pytest.skip(reason)
    state = open_stub_state()
    sandbox = ContainerSandbox(IMAGE, python=python, memory="2g", cpus=2)
    session, record, log, err = run_stub_developer(state, make_gateway(), sandbox, tmp_path)
    check_session(state, session, record, log, err)
    assert record["kind"] == "container" and record["network"] == "none"
    assert any(s["step"] == "no network" and s["ok"] for s in log)


def test_session_refused_when_time_is_used_up(open_stub_state, edit_config, make_gateway):
    edit_config(lambda c: c["limits"].update(dev_seconds=1))
    state = open_stub_state()
    state.ledger.append("dev_session_finished", session="s0000", seconds=5)
    with pytest.raises(SessionRefused, match="time"):
        DevSession(state, make_gateway(), LocalSandbox()).run()


def test_session_refused_when_budget_is_used_up(open_stub_state, edit_config, make_gateway):
    edit_config(lambda c: c["limits"].update(dev_budget={"requests": 1}))
    state = open_stub_state()
    gw = make_gateway()
    key = gw.issue("someone", "dev", "stub-developer")
    import urllib.request
    req = urllib.request.Request(gw.url + "/v1/models", headers={"x-api-key": key})
    urllib.request.urlopen(req).read()
    with pytest.raises(SessionRefused, match="budget"):
        DevSession(state, gw, LocalSandbox()).run()


def test_brief_has_no_unfilled_fields(open_stub_state, make_gateway):
    state = open_stub_state()
    session = DevSession(state, make_gateway(), LocalSandbox())
    lay = session.prepare()
    text = lay.brief.read_text()
    assert "{" not in text and str(lay.workspace) in text and "p1, p2, p3" in text
    assert sorted(p.name for p in lay.tasks.iterdir()) == ["p1", "p2", "p3"]
    assert (lay.workspace / "harness" / "start").is_file()
    assert os.access(lay.workspace / "harness" / "settings.json", os.W_OK)
