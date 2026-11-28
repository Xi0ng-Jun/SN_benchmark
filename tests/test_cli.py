"""Step 5: the host-side command line takes a scenario and a state directory through
development, delivery check, exam and report."""
import json
import os
import shutil
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

import harness_builder
from harness_builder import cli
from harness_builder.ledger import Ledger
from harness_builder.state import holder

HERE = Path(__file__).parent
SRC = str(Path(harness_builder.__file__).parent.parent)


@pytest.fixture
def host(stub_dir, tmp_path, stub_model, monkeypatch, capsys):
    """host(*args) -> (exit status, stdout, stderr) of one `hb-host` command on the stub."""
    monkeypatch.setenv("STUB_UPSTREAM_KEY", stub_model.key)
    monkeypatch.setenv("STUB_UPSTREAM_URL", stub_model.url)

    def host(*args):
        capsys.readouterr()
        code = cli.main(["--scenario", str(stub_dir), "--state", str(tmp_path / "state"), *args])
        out = capsys.readouterr()
        return code, out.out, out.err
    host.state = tmp_path / "state"
    host.scenario = stub_dir
    return host


def answer_when_asked(host, stop):
    """The researcher at another terminal: answers each question once it is on record."""
    answered = set()
    while not stop.is_set():
        for e in Ledger(host.state / "ledger").entries("question"):
            qid = e["data"]["id"]
            if qid not in answered:
                answered.add(qid)
                cli.main(["--scenario", str(host.scenario), "--state", str(host.state),
                          "answer", qid, "No preference: keep JSON as in the task."])
        stop.wait(0.2)


def test_whole_course_from_the_command_line(host, tmp_path):
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "README.md").write_text("Answers are JSON objects.\n")
    (host.state / "workspace").mkdir(parents=True)
    shutil.copy(HERE / "stub_developer.py", host.state / "workspace" / "stub_developer.py")

    assert host("exam")[0] == 1 and host("deliver")[0] == 1  # nothing delivered yet
    stop = threading.Event()
    t = threading.Thread(target=answer_when_asked, args=(host, stop), daemon=True)
    t.start()
    try:
        code, _, err = host("develop", "--sandbox", "local", "--materials", str(notes))
    finally:
        stop.set()
        t.join()
    log = json.loads((host.state / "workspace" / "dev-log.json").read_text())
    assert code == 0 and all(s["ok"] for s in log), err + json.dumps(
        [s for s in log if not s["ok"]], indent=1)[:3000]

    code, out, _ = host("questions")
    (question,) = json.loads(out)
    assert code == 0 and len(question["answers"]) == 1
    code, out, _ = host("status")
    status = json.loads(out)
    assert code == 0 and status["in_use"] is None
    assert status["development"]["runs"]["count"] == 3
    assert status["development"]["materials_added"]["count"] == 2  # the notes and the answer
    assert status["delivery"]["delivered"] is None and status["exams"] == []

    code, out, _ = host("deliver")
    assert code == 0 and json.loads(out)["outcome"] == "passed"
    code, out, _ = host("exam")
    results = json.loads(out)["results"]
    assert code == 0 and results["delivered"]["aggregate"] == 1.0
    assert results["general"]["aggregate"] == 0.0
    code, _, err = host("exam")
    assert code == 1 and "already on record" in err

    code, out, err = host("report")
    assert code == 0 and "- delivered: 1.0 over 6 runs" in out and "report.md" in err
    assert (host.state / "report" / "report.json").is_file()
    code, out, _ = host("verify")
    assert code == 0 and json.loads(out) == {"ledger": [], "materials": [],
                                             "harness_versions": []}

    # after the exam nothing goes into this development any more
    for args in (("develop", "--sandbox", "local"), ("add-material", str(notes)),
                 ("answer", question["id"], "one more thing"), ("deliver",)):
        code, _, err = host(*args)
        assert code == 1 and "exam has been started" in err, args
    ledger = Ledger(host.state / "ledger")
    assert len(ledger.entries("dev_session_started")) == 1
    assert len(ledger.entries("material_added")) == 2


def test_materials_given_again_are_not_added_twice(host, tmp_path, edit_config):
    edit_config(lambda c: c["developer"].update(command=["true"]))
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("first")
    for _ in range(2):
        assert host("develop", "--sandbox", "local", "--materials", str(notes))[0] == 0
    ledger = Ledger(host.state / "ledger")
    assert len(ledger.entries("material_added")) == 1
    assert len(ledger.entries("dev_session_finished")) == 2
    (notes / "a.md").write_text("changed")
    code, out, _ = host("add-material", str(notes), "--note", "second thoughts")
    assert code == 0 and json.loads(out)["material"] == "0002-notes"
    assert host("develop", "--sandbox", "local", "--materials", str(tmp_path / "nope"))[0] == 1


def test_limits_are_shown_changed_and_recorded(host):
    code, out, _ = host("limits")
    assert code == 0 and json.loads(out)["run_seconds"] == 60
    code, out, _ = host("limits", "run_seconds=30", 'dev_budget={"total_tokens": 1000}',
                        "dev_seconds=null")
    changed = json.loads(out)
    assert code == 0 and changed["changed"] == {
        "run_seconds": {"before": 60, "after": 30},
        "dev_budget": {"before": None, "after": {"total_tokens": 1000}},
        "dev_seconds": {"before": 600, "after": None}}
    config = json.loads((host.scenario / "scenario.json").read_text())
    assert config["limits"]["run_seconds"] == 30 and config["limits"]["dev_seconds"] is None
    last = Ledger(host.state / "ledger").last("scenario")
    assert last["seq"] == changed["record"] and last["data"]["config"] == config

    before = (host.scenario / "scenario.json").read_text()
    for bad in ("run_seconds=0", "speed=3", "run_seconds", "run_seconds=soon",
                'run_budget={"dollars": 5}'):
        code, _, err = host("limits", bad)
        assert code == 1 and err, bad
    assert (host.scenario / "scenario.json").read_text() == before
    assert host("limits", "run_seconds=30")[0] == 0  # no change: nothing new on record
    assert len(Ledger(host.state / "ledger").entries("scenario")) == 2


def test_verify_reports_what_was_changed(host, tmp_path):
    f = tmp_path / "paper.txt"
    f.write_text("a paper")
    assert host("add-material", str(f))[0] == 0
    stored = host.state / "materials" / "0001-paper.txt" / "paper.txt"
    os.chmod(stored.parent, 0o755)
    os.chmod(stored, 0o644)
    stored.write_text("another paper")
    code, out, _ = host("verify")
    assert code == 1 and "0001-paper.txt: changed" in json.loads(out)["materials"][0]
    lines = (host.state / "ledger" / "ledger.jsonl").read_text().splitlines()
    (host.state / "ledger" / "ledger.jsonl").write_text("\n".join(lines[:-1]) + "\n")
    code, _, err = host("verify")
    assert code == 1 and "ledger" in err
    assert host("status")[0] == 1  # nothing works on a ledger that does not verify


def test_stop_ends_a_running_session(host, edit_config):
    assert host("stop")[0] == 1  # nothing is running
    edit_config(lambda c: c["developer"].update(command=["sleep", "60"]))
    env = dict(os.environ, PYTHONPATH=SRC)
    argv = [sys.executable, "-m", "harness_builder", "--scenario", str(host.scenario),
            "--state", str(host.state), "develop", "--sandbox", "local"]
    p = subprocess.Popen(argv, env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                         text=True)
    try:
        deadline = time.monotonic() + 30
        while holder(host.state) is None and p.poll() is None and time.monotonic() < deadline:
            time.sleep(0.1)
        assert holder(host.state) == {"what": "development session", "pid": p.pid}, \
            p.stderr.read() if p.poll() is not None else "not started"
        code, _, err = host("develop", "--sandbox", "local")
        assert code == 1 and "in use" in err  # one session at a time
        assert host("deliver")[0] == 1 and host("exam")[0] == 1
        assert host("stop")[0] == 0
        assert p.wait(timeout=30) == 1
    finally:
        if p.poll() is None:
            p.kill()
        err = p.stderr.read()
    assert "stopped" in err and holder(host.state) is None
    ledger = Ledger(host.state / "ledger")
    assert ledger.last("stop_requested")["data"]["pid"] == p.pid
    assert "KeyboardInterrupt" in ledger.last("dev_session_finished")["data"]["error"]
    assert ledger.verify() == []


def test_module_entry_and_usage_errors(host):
    p = subprocess.run([sys.executable, "-m", "harness_builder", "--help"],
                       env=dict(os.environ, PYTHONPATH=SRC), capture_output=True, text=True)
    assert p.returncode == 0 and "hb-host" in p.stdout and "develop" in p.stdout
    with pytest.raises(SystemExit) as e:
        cli.main(["--scenario", "x", "--state", "y", "no-such-command"])
    assert e.value.code == 2
    assert cli.main(["--scenario", str(host.scenario / "nope"), "--state", str(host.state),
                     "status"]) == 1
    # the state directory may not be inside the scenario directory
    assert cli.main(["--scenario", str(host.scenario), "--state",
                     str(host.scenario / "state"), "status"]) == 1
