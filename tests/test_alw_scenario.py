"""The ALW scenario's commands, with stand-ins for harbor, alw and docker.

Nothing here starts a container or calls a model. What it checks: the scenario directory
that prepare.py writes loads; the run command gives Harbor the right job and takes the
submission out of what the container entry left; the entry runs a harness's start, stops
it on time and copies the workspace; the launcher applies fixed settings; score and the
invalid-run condition read the verifier's output as documented.
"""
import json
import os
import shutil
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from harness_builder import scenario as sc

ALW = Path(__file__).resolve().parent.parent / "scenarios" / "alw"
sys.path.insert(0, str(ALW / "container"))
import claude_guard  # noqa: E402

FAKE_HARBOR = r'''#!/usr/bin/env python3
import json, os, sys
cfg = json.load(open(sys.argv[sys.argv.index("--config") + 1]))
trial = os.path.join(cfg["jobs_dir"], cfg["job_name"], "trial-1")
hb = os.path.join(trial, "agent", "hb")
os.makedirs(os.path.join(hb, "workspace", "submission"))
json.dump({"env": dict(os.environ), "config": cfg}, open(os.path.join(trial, "seen.json"), "w"))
if cfg["agents"][0]["model_name"] == "m-none":  # stands for a trial that broke early
    sys.exit(1)
open(os.path.join(hb, "workspace", "submission", "layout.gds"), "w").write("gds")
json.dump({"returncode": 0, "timed_out": False, "seconds": 100.0}, open(os.path.join(hb, "run.json"), "w"))
'''
FAKE_ALW = r'''#!/usr/bin/env python3
import json, os, sys
a = sys.argv[1:]
out = a[a.index("--out") + 1]; sub = a[a.index("--submission") + 1]
d = os.path.join(out, "verifier", "evaluation"); os.makedirs(d)
have = os.path.isfile(os.path.join(sub, "layout.gds"))
json.dump({"score": 0.1 if have else 0.0, "score_valid": True,
           "status": "evaluated" if have else "missing_submission", "argv": a}, open(os.path.join(d, "metrics.json"), "w"))
sys.exit(1)
'''


def _exe(path: Path, text: str) -> Path:
    path.write_text(text)
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


@pytest.fixture
def alw(tmp_path, monkeypatch):
    """A copy of the scenario directory, prepared against a made-up benchmark checkout."""
    root = tmp_path / "alw"
    shutil.copytree(ALW, root, ignore=shutil.ignore_patterns("__pycache__", "tasks",
                                                             "scenario.json", "site.json"))
    facts = json.loads((root / "facts.json").read_text())
    repo = tmp_path / "repo"
    for t in facts["practice"] + facts["exam"]:
        d = repo / "tasks" / t
        (d / "environment" / "task").mkdir(parents=True)
        (d / "tests").mkdir()
        (d / "instruction.md").write_text("do it")
        (d / "tests" / "hidden.txt").write_text("x")
        (d / "environment" / "GUIDE.md").write_text("guide")
        (d / "task.toml").write_text('[agent]\ntimeout_sec = 10800.0\n[environment]\ncpus = 6\n')
    fake = tmp_path / "fakebin"
    fake.mkdir()
    _exe(fake / "harbor", FAKE_HARBOR)
    _exe(fake / "alw", FAKE_ALW)
    _exe(fake / "docker", "#!/bin/sh\nexit 0\n")
    monkeypatch.setenv("PATH", f"{fake}{os.pathsep}{os.environ['PATH']}")
    p = subprocess.run([sys.executable, "-B", str(root / "prepare.py"), "--alw-repo", str(repo),
                        "--solver-model", "m-solver", "--developer-python", "python3"],
                       capture_output=True, text=True)
    assert p.returncode == 0, p.stderr
    return root


def command(root, name, env, *argv):
    cfg = json.loads((root / "scenario.json").read_text())
    full = {k: v for k, v in os.environ.items() if not k.startswith("HB_")} | env
    return subprocess.run([*cfg["commands"][name], *argv], cwd=root, env=full,
                          capture_output=True, text=True)


def test_prepared_directory_loads(alw):
    s = sc.load(alw)
    facts = json.loads((alw / "facts.json").read_text())
    assert list(s.practice) == facts["practice"] and list(s.exam) == facts["exam"]
    assert s.solver_settings == facts["solver_settings"]
    t = facts["practice"][0]
    assert (alw / "tasks" / t / "environment" / "GUIDE.md").is_file()
    assert not (alw / "tasks" / t / "tests").exists()          # only with --with-tests
    assert not (alw / "tasks" / facts["exam"][0]).exists()     # exam tasks get no folder
    assert s.models["solver"] == s.models["developer"] == "m-solver"


def run_env(alw, tmp_path, name="out", **extra):
    facts = json.loads((alw / "facts.json").read_text())
    out = tmp_path / name
    out.mkdir()
    return out, {"HB_TASK": facts["practice"][0], "HB_HARNESS": str(alw / "harness"),
                 "HB_OUT": str(out), "HB_MODEL": "m-solver", "HB_MODEL_KEY": "k-123",
                 "HB_MODEL_URL": "http://host.docker.internal:1234",
                 "ANTHROPIC_AUTH_TOKEN": "host-secret", **extra}


def test_run_gives_harbor_the_job_and_takes_the_submission(alw, tmp_path):
    out, env = run_env(alw, tmp_path, HB_SETTING_reasoning_effort="max",
                       HB_SETTING_max_output_tokens="64000")
    p = command(alw, "run", env)
    assert p.returncode == 0, p.stderr
    assert (out / "submission" / "layout.gds").read_text() == "gds"
    meta = json.loads((out / "meta.json").read_text())
    assert meta["agent_seconds"] == 10800 and meta["agent_started"] and meta["submission_present"]
    seen = json.loads(next((out / "harbor").glob("*/*/seen.json")).read_text())
    agent = seen["config"]["agents"][0]
    assert agent["override_timeout_sec"] == 10800 + 300
    assert agent["kwargs"] == {"version": "2.1.302", "reasoning_effort": "max"}
    assert agent["env"]["ANTHROPIC_API_KEY"] == "${ALW_RUN_KEY}"   # never the key itself
    assert agent["env"]["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:1234"
    assert agent["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"
    assert agent["env"]["ANTHROPIC_MODEL"] == agent["env"]["CLAUDE_CODE_SUBAGENT_MODEL"] == "m-solver"
    assert "k-123" not in json.dumps(seen["config"])
    assert seen["env"]["ALW_RUN_KEY"] == "k-123"
    assert seen["env"]["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"
    assert "ANTHROPIC_AUTH_TOKEN" not in seen["env"]               # host credentials stay out
    overlay = (out / "hb" / "compose.yaml").read_text()
    assert f"{alw / 'harness'}:/opt/hb-harness:ro" in overlay
    assert f"{alw / 'container'}:/opt/harness/bin:ro" in overlay
    fixed = json.loads((out / "hb" / "fixed.json").read_text())
    assert fixed["settings"] == {"reasoning_effort": "max", "max_output_tokens": "64000"}
    assert not list(alw.rglob("__pycache__"))                      # the directory's hash stays


def test_run_time_limit(alw, tmp_path):
    out, env = run_env(alw, tmp_path, "a", HB_SECONDS="2000.0")
    assert command(alw, "run", env).returncode == 0
    assert json.loads((out / "meta.json").read_text())["agent_seconds"] == 1100
    out, env = run_env(alw, tmp_path, "b", HB_SECONDS="20000.0")    # never above the task's own
    assert command(alw, "run", env).returncode == 0
    assert json.loads((out / "meta.json").read_text())["agent_seconds"] == 10800
    out, env = run_env(alw, tmp_path, "c", HB_SECONDS="900.0")
    p = command(alw, "run", env)
    assert p.returncode == 2 and "900" in p.stderr and not (out / "harbor").exists()


def test_run_refuses_unknown_setting_and_reports_no_agent(alw, tmp_path):
    out, env = run_env(alw, tmp_path, "a", HB_SETTING_thinking="high")
    p = command(alw, "run", env)
    assert p.returncode == 2 and "thinking" in p.stderr
    out, env = run_env(alw, tmp_path, "b", HB_MODEL="m-none")
    assert command(alw, "run", env).returncode == 1
    meta = json.loads((out / "meta.json").read_text())
    assert meta["agent_started"] is False and meta["submission_present"] is False


def test_score_and_invalid(alw, tmp_path):
    out, env = run_env(alw, tmp_path)
    assert command(alw, "run", env).returncode == 0
    run = tmp_path / "run"
    (run / "score").mkdir(parents=True)
    shutil.copytree(out, run / "out")
    senv = {"HB_TASK": env["HB_TASK"], "HB_PRODUCT": str(run / "out"), "HB_OUT": str(run / "score")}
    p = command(alw, "score", senv)
    assert p.returncode == 0, p.stderr                             # alw verify's 1 is a verdict
    s = sc.load(alw)
    assert s.score_value(run / "score") == 0.1
    ienv = {"HB_TASK": env["HB_TASK"], "HB_RUN": str(run)}
    (run / "usage.json").write_text(json.dumps({"requests": 5, "wait_seconds": 1.0}))
    assert command(alw, "invalid", ienv).returncode == 0
    (run / "usage.json").write_text(json.dumps({"requests": 5, "wait_seconds": 60.0}))
    p = command(alw, "invalid", ienv)
    assert p.returncode == 10 and "model service" in p.stdout
    (run / "usage.json").write_text(json.dumps({"requests": 0}))
    assert command(alw, "invalid", ienv).returncode == 10
    # no submission: the verifier is still asked, and scores it as it does
    empty = tmp_path / "empty"
    empty.mkdir()
    (tmp_path / "s2").mkdir()
    p = command(alw, "score", {"HB_TASK": env["HB_TASK"], "HB_PRODUCT": str(empty),
                               "HB_OUT": str(tmp_path / "s2")})
    assert p.returncode == 0 and s.score_value(tmp_path / "s2") == 0.0


def test_launcher_applies_fixed_settings():
    argv, env = claude_guard.apply(["--effort", "low", "-p", "--effort=medium", "--verbose"],
                                   {"CLAUDE_CODE_MAX_OUTPUT_TOKENS": "1"},
                                   {"reasoning_effort": "max", "max_output_tokens": "64000"})
    assert argv == ["-p", "--verbose", "--effort", "max"]
    assert env["CLAUDE_CODE_EFFORT_LEVEL"] == "max" and env["CLAUDE_CODE_MAX_OUTPUT_TOKENS"] == "64000"
    argv, env = claude_guard.apply(["--effort", "low", "-p"], {"X": "1"}, {})   # nothing fixed
    assert argv == ["--effort", "low", "-p"] and env == {"X": "1"}


def entry(tmp_path, start_text, seconds, argv=("--verbose", "--print")):
    harness, ws, logs = tmp_path / "h", tmp_path / "ws", tmp_path / "logs"
    harness.mkdir()
    (ws / "submission").mkdir(parents=True)
    _exe(harness / "start", start_text)
    fixed = tmp_path / "fixed.json"
    fixed.write_text(json.dumps({"agent_seconds": seconds, "settings": {"reasoning_effort": "max"},
                                 "real_claude": "/bin/echo"}))
    env = dict(os.environ, HB_FIXED_FILE=str(fixed), HB_HARNESS_DIR=str(harness),
               HB_LOG_DIR=str(logs), HB_WORKSPACE_DIR=str(ws))
    p = subprocess.run([sys.executable, "-B", str(ALW / "container" / "mcr_entrypoint.py"), *argv],
                       input=b"the instruction", cwd=ws, env=env, capture_output=True, timeout=60)
    return p, logs, ws


def test_entry_runs_start_and_copies_the_workspace(tmp_path):
    p, logs, ws = entry(tmp_path, '#!/bin/sh\ncat > submission/prompt.txt\n'
                                  'echo "args:$* claude:$HB_CLAUDE secs:$HB_AGENT_SECONDS"\n', 30)
    assert p.returncode == 0, p.stderr
    out = p.stdout.decode()
    assert "args:--verbose --print" in out and "container/claude" in out and "secs:30.0" in out
    assert (logs / "workspace" / "submission" / "prompt.txt").read_text() == "the instruction"
    rec = json.loads((logs / "run.json").read_text())
    assert rec["returncode"] == 0 and rec["timed_out"] is False


def test_entry_stops_start_when_time_is_up(tmp_path):
    p, logs, ws = entry(tmp_path, '#!/bin/sh\necho early > submission/layout.gds\n'
                                  'sleep 300 &\nsleep 300\n', 2)
    assert p.returncode == 0
    rec = json.loads((logs / "run.json").read_text())
    assert rec["timed_out"] is True and rec["seconds"] < 30
    assert (logs / "workspace" / "submission" / "layout.gds").read_text() == "early\n"


def test_entry_passes_other_calls_to_the_real_program(tmp_path):
    p, logs, ws = entry(tmp_path, "#!/bin/sh\nexit 9\n", 5, argv=("doctor", "now"))
    assert p.returncode == 0 and p.stdout.decode().strip() == "doctor now"   # /bin/echo
    assert not logs.exists()
