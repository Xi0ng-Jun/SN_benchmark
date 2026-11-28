"""Step 6: one test per fixed rule (method-design section 6) that tries to break it.

What isolation itself must stop (reading exam tasks, the network, writing into the
records) is tried from inside a container by ``rule_probe.py``; that test needs
Docker and $HB_TEST_IMAGE like the one in test_develop.py and is skipped otherwise.
The other tests check, without Docker, what the outside program hands to the
sandbox and what it refuses.
"""
import json
import os
import shutil
from pathlib import Path

import pytest
from test_develop import IMAGE, container_python

from harness_builder import delivery, exam
from harness_builder.develop import DevSession
from harness_builder.harness_repo import tree_hash
from harness_builder.ledger import LedgerError
from harness_builder.materials import Materials
from harness_builder.runs import Runner, RunRefused
from harness_builder.sandbox import ContainerSandbox, LocalSandbox

HERE = Path(__file__).parent


@pytest.fixture
def state(open_stub_state):
    return open_stub_state()


def exam_markers(scenario) -> list[str]:
    """A piece of text from each exam task that no practice task contains."""
    out = []
    for tid in scenario.exam:
        fields = json.loads((scenario.root / "tasks" / tid / "task.json").read_text())["fields"]
        k, v = next(iter(fields.items()))
        out.append(json.dumps({k: v})[1:-1])
    return out


def mounts(sandbox, lay) -> dict[str, tuple[Path, str]]:
    """What a container sandbox mounts: inside path -> (host path, mode)."""
    argv = sandbox.docker_argv(lay, ["true"], [], "name")
    out = {}
    for i, a in enumerate(argv):
        if a == "-v":
            host, inside, mode = argv[i + 1].rsplit(":", 2)
            out[inside] = (Path(host), mode)
    return out


def make_writable(path: Path) -> None:
    os.chmod(path.parent, 0o755)
    os.chmod(path, 0o644)


# --- rule 1: the exam tasks are kept apart -------------------------------------------------

def test_rule_1_sandbox_gets_no_exam_task_and_no_network(state, designate, make_harness,
                                                         make_gateway):
    gw = make_gateway()
    runner = Runner(state, "dev", state.scenario.practice, gateway=gw)
    runner.run("p1", designate(state, make_harness("correct"))["version"])
    delivery.check(state, gw)
    session = DevSession(state, gw, LocalSandbox())
    session.materials.add_text("note", "note.md", "Answers are JSON objects.")
    lay = session.prepare()
    sandbox = ContainerSandbox("some-image")
    argv = sandbox.docker_argv(lay, ["true"], [], "name")
    assert argv[argv.index("--network") + 1] == "none"

    shown = mounts(sandbox, lay)
    assert sorted(shown) == ["/hb-ops/requests", "/hb-ops/responses", "/hb/BRIEF.md", "/hb/bin",
                             "/materials", "/records", "/tasks", "/versions", "/workspace"]
    assert sorted(p.name for p in lay.tasks.iterdir()) == sorted(state.scenario.practice)
    kept_out = [state.scenario.root, state.root, state.root / "ledger", state.root / "exam",
                *(state.scenario.root / "tasks" / t for t in state.scenario.exam)]
    markers = exam_markers(state.scenario)
    tasks_dir = state.scenario.root / "tasks"
    for tid, m in zip(state.scenario.exam, markers):  # the search below can find them
        assert m in (tasks_dir / tid / "task.json").read_text()
        assert not any(m in (tasks_dir / p / "task.json").read_text()
                       for p in state.scenario.practice)
    for host, _ in shown.values():
        for secret in kept_out:  # not mounted, and not inside anything mounted
            secret = secret.resolve()
            assert host != secret and host not in secret.parents, (host, secret)
        files = [host] if host.is_file() else [p for p in host.rglob("*") if p.is_file()]
        for f in files:
            data = f.read_bytes()
            assert not any(m.encode() in data for m in markers), f
    # and the request channel does not hand out exam tasks either
    for task in state.scenario.exam:
        with pytest.raises(RunRefused):
            runner.run(task, "v0")
        with pytest.raises(RunRefused):
            runner.enter(task, ["cat", "task.json"], lay.workspace / "enter")


# --- rule 2: the scoring cannot be changed -------------------------------------------------

def test_rule_2_raw_score_output_is_read_only_for_the_sandbox(state, make_gateway):
    gw = make_gateway()
    runner = Runner(state, "dev", state.scenario.practice, gateway=gw)
    record = runner.run("p1", "v0")
    score_dir = runner.run_dir(record["run"]) / "score"
    raw = (score_dir / "score.json").read_text()
    with pytest.raises(OSError):
        (score_dir / "score.json").write_text("{}")
    with pytest.raises(OSError):
        (score_dir / "extra.json").write_text("{}")
    with pytest.raises(OSError):
        (score_dir / "score.json").unlink()
    assert (score_dir / "score.json").read_text() == raw

    lay = DevSession(state, gw, LocalSandbox()).prepare()
    shown = mounts(ContainerSandbox("some-image"), lay)
    assert shown["/records"] == (lay.records.resolve(), "ro")  # a chmod inside cannot undo this
    assert score_dir.resolve().is_relative_to(lay.records.resolve())
    writable = sorted(inside for inside, (_, mode) in shown.items() if mode != "ro")
    assert writable == ["/hb-ops/requests", "/workspace"]


def test_rules_1_and_2_hold_in_a_container(state, make_gateway):
    python, reason = container_python()
    if python is None:
        pytest.skip(reason)
    ws = state.root / "workspace"
    ws.mkdir(parents=True)
    shutil.copy(HERE / "rule_probe.py", ws / "rule_probe.py")
    (ws / "probe-input.json").write_text(json.dumps({
        "host_paths": [str(state.scenario.root), str(state.root), str(state.root / "ledger")],
        "exam_markers": exam_markers(state.scenario)}))
    sandbox = ContainerSandbox(IMAGE, python=python, memory="2g", cpus=2)
    session = DevSession(state, make_gateway(), sandbox,
                         command=["sh", "-c", 'exec "$HB_PYTHON" "$HB_WORKSPACE/rule_probe.py"'])
    record = session.run()
    err = (state.root / "sessions" / record["session"] / "agent.stderr").read_text()
    assert record["agent_result"]["exit_code"] == 0, err[-3000:]
    found = json.loads((ws / "probe.json").read_text())

    assert found["tasks"] == sorted(state.scenario.practice)
    assert found["host_paths_present"] == [] and found["exam_content_found"] == []
    assert found["connect"].startswith("failed") and found["resolve"].startswith("failed")
    assert all(v.startswith("refused") for v in found["score_changes"].values()), found
    assert found["score_unchanged"] is True and found["score_dir"] == ["score.json"]
    run = state.ledger.last("run_finished")["data"]
    raw = state.root / "dev" / "runs" / run["run"] / "score" / "score.json"
    assert json.loads(raw.read_text())["score"] == 0.0


# --- rule 3: everything stays on record ----------------------------------------------------

@pytest.mark.parametrize("change", ["edit", "delete", "delete last", "append"])
def test_rule_3_changed_or_deleted_record_is_found(state, open_stub_state, change):
    Runner(state, "dev", state.scenario.practice).run("p1", "v0")
    assert state.ledger.verify() == []
    lines = state.ledger.path.read_text().splitlines()
    run_line = next(i for i, x in enumerate(lines) if json.loads(x)["kind"] == "run_finished")
    if change == "edit":  # turn a failed run into an invalid one
        e = json.loads(lines[run_line])
        e["data"]["invalid"] = {"verdict": "invalid", "reason": "made up"}
        lines[run_line] = json.dumps(e, sort_keys=True, separators=(",", ":"))
    elif change == "delete":
        del lines[run_line - 1]
    elif change == "delete last":
        del lines[-1]
    else:  # an entry written without going through the ledger
        e = json.loads(lines[-1])
        e.update(seq=e["seq"] + 1, prev=e["hash"])
        lines.append(json.dumps(e, sort_keys=True, separators=(",", ":")))
    state.ledger.path.write_text("".join(x + "\n" for x in lines))
    assert state.ledger.verify() != []
    with pytest.raises(LedgerError):
        open_stub_state()


# --- rule 4: budget and model are enforced outside -----------------------------------------

def test_rule_4_other_model_and_calls_over_budget_are_refused(state, make_gateway, make_harness,
                                                              stub_model):
    gw = make_gateway()
    gw.set_pool_caps("dev", {"requests": 6})
    runner = Runner(state, "dev", state.scenario.practice, gateway=gw)

    other = state.repo.save(make_harness("correct", model_calls=2, model="bigger-model"))
    r = runner.run("p1", other["version"])
    assert r["usage"]["requests"] == 0 and r["usage"]["refused"] == {"model": 2}
    assert stub_model.received == []  # nothing reached the model service

    greedy = state.repo.save(make_harness("correct", model_calls=7))
    r = runner.run("p1", greedy["version"])  # limits.run_budget allows 5 requests per run
    assert r["usage"]["requests"] == 5 and r["usage"]["refused"] == {"budget": 2}
    assert len(stub_model.received) == 5
    assert all(m["body"]["model"] == "stub-solver" for m in stub_model.received)

    r = runner.run("p2", greedy["version"])  # the development budget has 1 request left
    assert r["usage"]["requests"] == 1 and r["usage"]["refused"] == {"budget": 6}
    with pytest.raises(RunRefused, match="budget"):
        runner.run("p3", greedy["version"])
    assert state.ledger.last("run_refused")["data"]["task"] == "p3"
    assert len(stub_model.received) == 6


# --- rule 5: materials only grow -----------------------------------------------------------

def test_rule_5_changed_material_is_found(state, tmp_path):
    materials = Materials(state.root / "materials", state.ledger)
    notes = tmp_path / "notes"
    notes.mkdir()
    (notes / "a.md").write_text("the units are millimetres")
    materials.add(notes)
    materials.add_text("tip", "tip.md", "check the units")
    assert materials.verify() == []
    stored = materials.root / "0001-notes" / "a.md"
    assert not os.access(stored, os.W_OK)
    make_writable(stored)
    stored.write_text("the units are metres")
    tip = materials.root / "0002-tip" / "tip.md"
    make_writable(tip)
    tip.unlink()
    problems = materials.verify()
    assert any("0001-notes" in p and "changed" in p for p in problems)
    assert any("0002-tip" in p and "changed" in p for p in problems)
    lay = DevSession(state, None, LocalSandbox()).layout()
    assert mounts(ContainerSandbox("some-image"), lay)["/materials"][1] == "ro"


# --- rule 6: the delivery is frozen --------------------------------------------------------

@pytest.mark.parametrize("also_forge_the_version_record", [False, True])
def test_rule_6_exam_refuses_a_delivery_changed_after_freezing(
        state, designate, make_harness, make_gateway, also_forge_the_version_record):
    gw = make_gateway()
    meta = designate(state, make_harness("wrong"))
    assert delivery.check(state, gw)["outcome"] == "passed"

    stored = state.repo.path(meta["version"]) / "settings.json"
    make_writable(stored)
    stored.write_text('{"answer": "correct"}')
    if also_forge_the_version_record:
        record = state.repo.versions_dir / f"{meta['version']}.json"
        record.write_text(json.dumps({**meta, "hash": tree_hash(stored.parent)}))
        assert state.repo.verify(meta["version"])  # the version's own record is fooled

    with pytest.raises(exam.ExamRefused, match="changed after it was frozen"):
        exam.run(state, gw)
    assert "hash differs" in state.ledger.last("exam_refused")["data"]["reason"]
    assert not state.ledger.entries("exam_started")
    assert not state.ledger.entries("exam_run") and not (state.root / "exam").exists()
    assert gw.pool_usage("exam")["requests"] == 0


def test_rule_6_exam_stops_when_a_compared_folder_changes_midway(
        state, designate, make_harness, edit_config, open_stub_state, monkeypatch):
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
    monkeypatch.setattr(Runner, "run", real)
    stored = state.repo.path("v0") / "settings.json"  # the compared general agent
    make_writable(stored)
    stored.write_text('{"answer": "none"}')
    with pytest.raises(exam.ExamRefused, match="changed since exam x0001 began"):
        exam.run(state, None)
    assert not state.ledger.entries("exam_finished")
