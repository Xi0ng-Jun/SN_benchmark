"""VeRO adapter: access rules, evaluation through the runner, and a full method-v4.1 run (offline fake VeRO)."""
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from rag_eval.vero import adapter as vero_adapter
from rag_eval.vero import cli
from rag_eval.vero.access import WEB_SEARCH_AGENT_RULES, AccessRules
from rag_eval.vero.agent_dispatch import last_json

from . import fake_vero

TAU_RULES = """
[exclude]
tau_bench/envs/retail/tasks_test.py
tau_bench/envs/retail/data
tau_bench/envs/retail/data/**
**/__pycache__
.git
[read]
tau_bench/vero_tasks/**
.veroaccess
[write]
tau_bench/agents/**
"""


def test_access_rules_last_match_wins():
    tau = AccessRules(TAU_RULES)
    assert tau.excluded("tau_bench/envs/retail/tasks_test.py")
    assert tau.excluded("tau_bench/envs/retail/data/orders.json")
    assert tau.excluded("tau_bench/envs/__pycache__/x.pyc")                  # no-slash rule: any depth, and below
    assert tau.level("tau_bench/agents/__pycache__/x.pyc") == "write"        # a later [write] rule wins
    assert tau.level("tau_bench/agents/tool_calling_agent.py") == "write"
    assert tau.level("tau_bench/vero_tasks/retail.py") == "read"
    assert tau.level("setup.py") == "read"
    web = AccessRules(WEB_SEARCH_AGENT_RULES)
    assert web.writable("src/web_search_agent/agent.py")
    assert not web.writable("src/web_search_agent/vero_tasks/utils.py")
    assert not web.writable("pyproject.toml")


def test_last_json_prefers_last_fenced_block():
    text = 'x ```json\n[1]\n``` then\n```json\n[{"title": "t"}]\n```\nbye'
    assert last_json(text) == [{"title": "t"}]
    assert last_json('answer: [{"a": 1}] done') == [{"a": 1}]


@pytest.fixture
def world(tmp_path, monkeypatch):
    env = fake_vero.build(tmp_path)
    monkeypatch.setitem(vero_adapter.PRESETS, "fake", vero_adapter.Preset(
        name="fake", agent_subdir="agent", module="fake_agent.vero_tasks", task="fake_qa", import_root=".",
        dataset="fake", description="a toy QA agent", gold_keys=("answer",)))
    monkeypatch.setenv("FAKE_MARKER_DIR", str(tmp_path))
    root = tmp_path / "state"
    rc = cli.main(["init", "--state-root", str(root), "--preset", "fake", "--vero-root", str(tmp_path / "vero-src"),
                   "--agent-python", sys.executable, "--dataset-path", str(env["dataset"]),
                   "--extra-sys-path", str(env["libs"]), "--analyst-cmd", env["analyst_cmd"],
                   "--implementer-cmd", env["implementer_cmd"], "--sample-concurrency", "4"])
    assert rc == 0
    return dict(env, root=root, tmp=tmp_path)


def test_seed_evaluation_and_cache(world):
    setup = json.loads((world["root"] / "setup.json").read_text())
    assert setup["splits"] == {"train": 12, "validation": 20, "test": 10}
    ad = vero_adapter.VeROAdapter(setup, world["root"], world["root"] / "adhoc")
    ids = vero_adapter.split_ids(setup["splits"])["validation"]
    res = ad.evaluate(ad.seed(), ids, 2)
    assert set(res.per_task_scores) == set(ids)
    assert 0 < sum(res.per_task_scores.values()) < len(ids)
    assert len(res.attempt_scores) == 2 and res.attempt_scores[0] == res.attempt_scores[1]
    calls = ad.evaluator.calls
    ad.evaluate(ad.seed(), ids, 2)
    assert ad.evaluator.calls == calls          # cached per (commit, task, attempt)
    obs = ad.observe_safeguards(res, res)
    assert obs["answer_produced_rate"]["subject"] == obs["answer_produced_rate"]["reference"]
    assert obs["tokens_per_task_ratio"]["subject"] == 1.0


def test_full_run_adopts_and_tests(world, tmp_path):
    cfg = tmp_path / "v4.json"
    cfg.write_text(json.dumps(dict(time_budget_hours=1, calibration_k=2, b=1, cards_per_analysis=1, diagnose_size=0,
                                   agent_estimate_minutes=1, max_consecutive_run_failures=2, review=False)))
    run_dir = world["root"] / "runs" / "run1"
    rc = cli.main(["run", "--state-root", str(world["root"]), "--run-dir", str(run_dir), "--v4-config", str(cfg)])
    st = json.loads((run_dir / "state.json").read_text())
    # After the adoption, failed implementations alternate with empty (deduplicated) analyses: the run
    # halts after two run failures instead of spinning until the time budget (v4.1 loop regression).
    assert st["halt_reason"] == "process_failure", st["halt_reason"]
    assert len(st["rounds"]) <= 6, len(st["rounds"])
    assert len(st["lineage"]) >= 2, json.dumps(st["rounds"], indent=1)[:4000]
    final = st["lineage"][-1]["version"]
    repo = world["root"] / "target" / "repo"
    show = lambda path: subprocess.run(["git", "show", f"{final}:{path}"], cwd=repo, capture_output=True,  # noqa: E731
                                       text=True).stdout
    assert "SKILL = 0.8" in show("fake_agent/agent.py")
    assert "grader tampering" not in show("fake_agent/vero_tasks/qa.py")      # read-only edit reverted
    assert show("notes.txt") == ""                                           # untracked non-writable file dropped
    assert not (world["tmp"] / "secret_seen").exists()                       # excluded file hidden
    assert rc in (cli.EXIT_OK, cli.EXIT_HALTED)

    assert cli.main(["test", "--state-root", str(world["root"]), "--run-dir", str(run_dir)]) == 0
    summary = json.loads((run_dir / "endpoint.json").read_text())["summary"]
    assert summary["final"] == final and summary["delta"] > 0.3
    assert summary["optimisation_executions"]["validation"] >= 20 * 2
    assert "test" not in summary["optimisation_executions"]


def test_candidate_crash_scores_zero(world):
    setup = json.loads((world["root"] / "setup.json").read_text())
    ad = vero_adapter.VeROAdapter(setup, world["root"], world["root"] / "adhoc")
    seed = ad.seed()
    ws = ad.workspace(seed, None)
    Path(ws["path"], "fake_agent/agent.py").write_text("raise RuntimeError('boom')\n")
    diff, _ = ad.repo.stage_changes(Path(ws["path"]), seed.content_hash, ad.rules)
    assert "boom" in diff
    from rag_eval.optimizer.adapter import Diff
    broken = ad.materialize(Diff(diff, metadata=dict(workspace=ws["path"], title="broken")), seed)
    ids = vero_adapter.split_ids(setup["splits"])["validation"][:5]
    res = ad.evaluate(broken, ids, 1)
    assert res.per_task_scores == {t: 0.0 for t in ids}
    assert all("candidate_crash" in (e or "") for e in res.error_classes.values())


def test_execution_budget_halts_the_run_and_survives_resume(world, tmp_path):
    # Calibration costs 2 x 20 validation + 12 train (diagnosis) = 52 executions; one candidate's validation
    # measurement needs 20 more, so a budget of 70 halts inside the first round.
    cfg = tmp_path / "v4.json"
    cfg.write_text(json.dumps(dict(time_budget_hours=1, calibration_k=2, b=1, cards_per_analysis=1, diagnose_size=0,
                                   agent_estimate_minutes=1, review=False, speculative_prefetches=0,
                                   speculative_analysis=False)))
    run_dir = world["root"] / "runs" / "budget"
    args = ["run", "--state-root", str(world["root"]), "--run-dir", str(run_dir), "--v4-config", str(cfg),
            "--max-executions", "70"]
    assert cli.main(args) == cli.EXIT_HALTED
    st = json.loads((run_dir / "state.json").read_text())
    assert st["halt_reason"] == "budget_exhausted", st["halt_reason"]
    used = cli.executions(run_dir / "evals")
    assert used.get("validation", 0) + used.get("train", 0) <= 70
    assert json.loads((run_dir / "identity.json").read_text())["max_executions"] == 70
    # A resume must keep the pre-registered budget.
    assert cli.main(args[:-1] + ["71"]) == cli.EXIT_USAGE
    setup = json.loads((world["root"] / "setup.json").read_text())
    ad = vero_adapter.VeROAdapter(setup, world["root"], run_dir)
    ad.evaluator.set_budget(70)
    assert 52 <= ad.evaluator.spent() <= 70                       # counted from the cache after a restart
