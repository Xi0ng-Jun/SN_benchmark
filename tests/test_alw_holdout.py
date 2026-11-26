"""Sealed test runs (``alw-test``): live H_raw / H_base, H_star by Phase B replay, 1-seed report.
No containers, no model calls: launch, harvest, gate, repair and verifier are fakes."""
from __future__ import annotations

import json
import shutil
from pathlib import Path
from types import SimpleNamespace

import pytest

from ssap import cli
from ssap.config import load_config
from ssap.eval import holdout as ho
from ssap.eval import proposer_sandbox as ps
from ssap.targets.alw.harvest import Harvested

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "alw" / "config.server.json"
SPLIT = json.loads((ROOT / "configs" / "alw" / "split.json").read_text())
TEST = SPLIT["task_ids"]["test"]
REPAIR = "src/ssap/checkers/alw/repair.py"
FORK = {"granted_repair_budget_sec": 600.0, "granted_repair_turns": 80, "repair_eligible": True}


def config() -> dict:
    return load_config(CFG, env={"SSAP_MODEL": "test-model", "SSAP_GATEWAY": "http://127.0.0.1:9",
                                 "SSAP_UPSTREAM_KEY": "upstream-literal-key"})


@pytest.fixture(scope="module")
def hbase(tmp_path_factory):
    d = tmp_path_factory.mktemp("hbase") / "h"
    ps.stage_clean_hbase_source(ROOT, d)
    return d


def make_state(tmp_path: Path, hbase: Path) -> Path:
    state = tmp_path / "state"
    shutil.copytree(hbase, state / "loop" / "h_base_src")
    star = shutil.copytree(hbase, state / "loop" / "h_inc_harness")
    p = star / REPAIR
    p.write_text(p.read_text().replace("then stop.", "then stop and report.", 1))
    (state / "loop" / "h_inc.json").write_text(json.dumps({"version_id": "H_base+c1", "round": 2}))
    return state


class Fakes:
    """Stage / launch / harvest of the live arms. The first H_raw slot is an infra fault in the
    main job and recovers in its retry."""

    def __init__(self, tmp_path: Path):
        self.tmp, self.staged, self.launched = tmp_path, [], []

    def stage(self, cfg, state, *, arm, tasks, seeds, tag, timeout_s, harness_root=None):
        self.staged.append((tag, arm, harness_root))
        return [{"job_name": f"{tag}-{arm}-{t}-s{s}", "arm": arm, "task_id": t, "seed": s}
                for t in tasks for s in seeds]

    def launch(self, cfg, jobs, template, state, tag):
        self.launched.append((tag, len(jobs)))
        return [{**j, "exit_code": 0, "proxy": {"input_tokens": 100, "output_tokens": 10}} for j in jobs]

    def harvest(self, d: Path):
        out = []
        for r in json.loads((d / "launch_results.json").read_text()):
            t, arm = r["task_id"], r["arm"]
            infra = arm == "H_raw" and t == TEST[0] and "-retry-" not in d.name
            snap = self.tmp / "snaps" / f"{arm}-{t}"
            run = {"run_id": r["job_name"], "arm": arm, "task_id": t, "seed": r["seed"],
                   "Y_star": None if infra else 0.4, "C_star": 0, "d_star": {"gds": 1},
                   "status": "failed", "infra_error": infra, "ratchet_promoted": False,
                   "cost": {"input_tokens": 100, "output_tokens": 10}}
            out.append(Harvested(run=run, job_root=d, snapshot_dir=snap, fork_meta=dict(FORK),
                                 gate={"gate_triggered": True, "trigger_reasons": []},
                                 pre_cert={"m": [1, 0, 0, 0, 0, 0]}))
        return out


def gate(which, t):
    """TEST[0], TEST[1] trigger the repair (prompt differs); TEST[2] triggers in neither arm."""
    if t["task_id"] == TEST[2]:
        return {"gate": {"gate_triggered": False}, "triggered": False, "reasons": [], "prompt_sha256": None}
    return {"gate": {"gate_triggered": True}, "triggered": True, "reasons": ["m_best_not_all_certified"],
            "prompt_sha256": "B" if which == "cand" else "A"}


def test_holdout_pipeline(tmp_path, hbase):
    state, fk = make_state(tmp_path, hbase), Fakes(tmp_path)
    calls = []

    def repair(t, r, budget):
        calls.append((t["task_id"], budget))
        if t["task_id"] == TEST[1]:
            return {"infra_error": True, "status": "tool_error"}
        return {"Y_post": 0.7, "ratchet_promoted": True, "promote_reason": "promoted",
                "llm_calls": 4, "proxy": {"input_tokens": 50, "output_tokens": 5}}
    fns = dict(stage_fn=fk.stage, launch_fn=fk.launch, harvest_fn=fk.harvest, gate_fn=gate,
               repair_fn=repair, verifier_fn=lambda *a: SimpleNamespace(Y_star=None))
    tasks = TEST[:3]
    out = ho.run_holdout(config(), state, tasks=tasks, seeds=[0], harbor_cmd="h {job_config}",
                         verify_cmd="v", **fns)

    staged = {tag: (arm, root) for tag, arm, root in fk.staged}
    assert staged["test-H_base"] == ("H_base", state / "loop" / "h_base_src")
    assert staged["test-H_raw"] == ("H_raw", None)
    assert fk.launched == [("test-pool", 6), ("test-pool-retry", 1)]
    assert out["launch"]["retried"] == 1 and any("-retry-" in t for t in staged)

    assert sorted(calls) == sorted([(TEST[0], 600.0), (TEST[1], 600.0), (TEST[1], 600.0)])
    assert out["replay"]["by_verdict"] == {"replayed": 1, "replay_infra_error": 1, "identical": 1}
    assert out["replay"]["infra_slots"] == {"H_raw": 0, "H_base": 0, "H_star": 1}
    assert "0.7" not in json.dumps(out) and "0.4" not in json.dumps(out)   # counts only

    rows = [json.loads(l) for l in ho.runs_path(state).read_text().splitlines()]
    star = {r["task_id"]: r for r in rows if r["arm"] == "H_star"}
    assert star[TEST[0]]["Y_star"] == 0.7 and star[TEST[0]]["cost"]["input_tokens"] == 150
    assert star[TEST[1]]["Y_star"] is None and star[TEST[1]]["infra_error"]
    assert star[TEST[2]]["Y_star"] == 0.4 and star[TEST[2]]["replay_verdict"] == "identical"
    raw = [r for r in rows if r["arm"] == "H_raw" and r["task_id"] == TEST[0]]
    assert [r["infra_error"] for r in raw] == [True, False]                # retry recorded last

    man = json.loads((state / "test" / "manifest.json").read_text())
    assert man["h_star_version"] == "H_base+c1" and man["seeds"] == 1
    again = ho.run_holdout(config(), state, tasks=tasks, seeds=[0], step="launch",
                           harbor_cmd="h {job_config}", **fns)
    assert sorted(again["launch"]["already_launched"]) == ["test-H_base", "test-H_raw"]
    assert again["launch"]["launched"] == 0 and again["launch"]["retried"] == 0

    p = state / "loop" / "h_inc_harness" / REPAIR
    p.write_text(p.read_text() + "\n# changed after the holdout started\n")
    with pytest.raises(ValueError, match="h_star_tree_sha256"):
        ho.run_holdout(config(), state, tasks=tasks, seeds=[0], step="launch", **fns)


def test_holdout_refuses_without_incumbent(tmp_path, hbase):
    state = tmp_path / "state"
    shutil.copytree(hbase, state / "loop" / "h_base_src")
    with pytest.raises(FileNotFoundError):
        ho.write_manifest(config(), state, seeds=1, n_tasks=14)


def test_report_single_seed_needs_explicit_flag(tmp_path, capsys):
    runs = tmp_path / "runs.jsonl"
    rows = [{"arm": a, "task_id": t, "seed": 0, "Y_star": y, "C_star": 0, "status": "failed",
             "m": [1, 0, 0, 0, 0, 0], "cost": {"input_tokens": 10, "output_tokens": 1}}
            for a, y in (("H_raw", 0.1), ("H_base", 0.2), ("H_star", 0.25)) for t in TEST]
    runs.write_text("".join(json.dumps(r) + "\n" for r in rows))
    base = ["alw-report", "--config", str(CFG), "--state", str(tmp_path / "st"), "--runs", str(runs),
            "--selected-version", "H_base+c1", "--out-dir", str(tmp_path / "rep"), "--seeds", "1"]
    assert cli.main(base) == 2
    assert cli.main(base + ["--accept-fewer-seeds"]) == 0
    att = json.loads(capsys.readouterr().out)["three_stage_attribution"]
    assert abs(att["delta_raw_to_base"] - 0.1) < 1e-9 and abs(att["delta_base_to_star"] - 0.05) < 1e-9
