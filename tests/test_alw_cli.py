"""Milestone 3b: the six ``ssap alw-*`` subcommands (``--help`` and offline / dry-run execution).
No containers, no model calls, no network."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ssap import cli
from ssap.checkers.alw import gds_geom

ROOT = Path(__file__).resolve().parents[1]
CFG = str(ROOT / "configs" / "alw" / "config.server.json")
SPLIT = json.loads((ROOT / "configs" / "alw" / "split.json").read_text())
DEV = SPLIT["task_ids"]["dev"]
VAL = SPLIT["task_ids"]["val"]
TEST = SPLIT["task_ids"]["test"]
COMMANDS = ("alw-check-split", "alw-concordance", "alw-diagnose", "alw-eval", "alw-harvest", "alw-loop",
            "alw-report")
OK = {"proxy_requests": 10, "gateway_wait_seconds": 1.0, "wall_sec": 100.0}


def run(capsys, *argv) -> tuple[int, dict | str]:
    rc = cli.main(list(argv))
    out = capsys.readouterr().out
    try:
        return rc, json.loads(out)
    except ValueError:
        return rc, out


def base(tmp_path, cmd, *extra):
    return [cmd, "--config", CFG, "--state", str(tmp_path / "state"), *extra]


def jsonl(path: Path, rows) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return str(path)


@pytest.mark.parametrize("cmd", COMMANDS)
def test_help(cmd, capsys):
    with pytest.raises(SystemExit) as e:
        cli.main([cmd, "--help"])
    assert e.value.code == 0
    out = capsys.readouterr().out
    for flag in ("--model", "--parallel", "--timeout-s", "--t-warn", "--t-soft", "--delta-t-rep",
                 "--delta-s-rep", "--theta-gw-max", "--agent-image", "--dry-run"):
        assert flag in out, (cmd, flag)


def test_top_level_help_lists_six(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--help"])
    out = capsys.readouterr().out
    assert all(c in out for c in COMMANDS)


def test_check_split(capsys, monkeypatch, tmp_path):
    monkeypatch.delenv("ALW_REPO", raising=False)
    rc, out = run(capsys, *base(tmp_path, "alw-check-split"))
    assert rc == 0 and "ALL INVARIANTS HOLD" in out


def test_overrides_validated(capsys, tmp_path):
    with pytest.raises(ValueError):
        cli.main(base(tmp_path, "alw-loop", "--parallel", "99", "--dry-run"))
    with pytest.raises(ValueError):
        cli.main(base(tmp_path, "alw-loop", "--theta-gw-max", "1.5", "--dry-run"))


# ------------------------------------------------------------------------------------------
# alw-concordance
# ------------------------------------------------------------------------------------------

def test_concordance_actions(capsys, tmp_path):
    assert cli.main(base(tmp_path, "alw-concordance")) == 2
    rc, out = run(capsys, *base(tmp_path, "alw-concordance", "--freeze-blind"))
    assert rc == 0 and out["freeze_blind"]["blind_clusters"] == SPLIT["blind_clusters"]
    assert out["freeze_blind"]["G_dev"] == SPLIT["G_dev"]

    cl = {DEV[i]: SPLIT["cluster_of"][str(n)] for i, n in enumerate(SPLIT["splits"]["dev"])}
    d = {"gds": 1, "lvs": 1, "shape": 1, "fill": 0, "morph": 1, "drc": 1}
    rows = [{"sample_id": f"s{i}", "task_id": t, "cluster_id": cl[t], "source_kind": "trajectory",
             "Y_star": 0.5, "C_star": 0, "d_star": d,
             "cert_dict": {"raw_u": [0.0, 0.0, 1.0, 0.01 * (i + 1), 0.0, 0.0]}}
            for i, t in enumerate(DEV[:3])]
    samples = jsonl(tmp_path / "samples.jsonl", rows)
    rc, out = run(capsys, *base(tmp_path, "alw-concordance", "--build-bank", "--samples", samples,
                                "--dry-run"))
    assert rc == 0 and out["build_bank"]["written"] == 0
    assert not (tmp_path / "state" / "bank" / "b_dev.jsonl").exists()
    rc, out = run(capsys, *base(tmp_path, "alw-concordance", "--build-bank", "--samples", samples,
                                "--calibrate-eps", "--checker", "m_fill"))
    assert rc == 0 and out["build_bank"]["written"] == 3
    assert out["calibrate_eps"]["eps"] == pytest.approx(0.03 + 1e-6)

    bad = jsonl(tmp_path / "bad.jsonl", [{**rows[0], "task_id": VAL[0]}])
    assert cli.main(base(tmp_path, "alw-concordance", "--build-bank", "--samples", bad)) == 2


def test_concordance_mutate_and_smoke_dry_run(capsys, tmp_path):
    gds = gds_geom.write_gds(tmp_path / "base.gds", {
        "toy_top": {"boxes": [(65, 20, 0, 0, 20, 10), (68, 20, 1, 1, 3, 3)],
                    "texts": [(68, 5, 1, 1, "vdd")]}})
    rc, out = run(capsys, *base(tmp_path, "alw-concordance", "--mutate", "--gds", str(gds),
                                "--top", "toy_top", "--ports", "vdd", "--mutate-out",
                                str(tmp_path / "syn")))
    assert rc == 0 and out["mutate"]["n_variants"] >= 7
    assert all(Path(v["path"]).is_file() for v in out["mutate"]["variants"])
    rc, out = run(capsys, *base(tmp_path, "alw-concordance", "--offline-smoke-container", "--dry-run"))
    assert rc == 0 and out["offline_smoke_container"]["planned"]


# ------------------------------------------------------------------------------------------
# alw-diagnose
# ------------------------------------------------------------------------------------------

def test_diagnose(capsys, tmp_path):
    d = {"gds": 1, "drc": 1, "lvs": 1, "shape": 1, "fill": 1, "morph": 1, "pex": 1, "postsim": 0}
    rows = [{**OK, "arm": "H_base", "task_id": t, "seed": s, "Y_star": 0.5 + 0.1 * s, "C_star": 0,
             "d_star": d, "m": [1] * 6, "draft_scores": [0.5 + 0.1 * s]}
            for t in DEV for s in range(2)]
    runs = jsonl(tmp_path / "runs.jsonl", rows)
    out_dir = tmp_path / "diag"
    rc, out = run(capsys, *base(tmp_path, "alw-diagnose", "--split", "dev", "--runs", runs,
                                "--out-dir", str(out_dir)))
    assert rc == 0 and out["taxonomy"]["electrical_or_hidden_info_gap"] == 2 * len(DEV)
    assert out["module_switches"]["enable_checkpoint_ranker"] is False
    assert (out_dir / "dev_diagnostics.json").is_file() and (out_dir / "dev_diagnostics.md").is_file()
    assert cli.main(base(tmp_path, "alw-diagnose", "--split", "val", "--runs", runs)) == 2
    leak = jsonl(tmp_path / "leak.jsonl", rows + [{**rows[0], "task_id": TEST[0]}])
    assert cli.main(base(tmp_path, "alw-diagnose", "--runs", leak)) == 2
    rc, out = run(capsys, *base(tmp_path, "alw-diagnose", "--runs", runs, "--dry-run"))
    assert rc == 0 and out["dry_run"] and out["exists"]


# ------------------------------------------------------------------------------------------
# alw-eval
# ------------------------------------------------------------------------------------------

def test_eval_harbor_modes_stage_jobs(capsys, tmp_path):
    rc, out = run(capsys, *base(tmp_path, "alw-eval", "--mode", "smoke-10m", "--model", "test-model",
                                "--agent-image", "alw-eda-claude:2.1.302", "--dry-run"))
    assert rc == 0 and out["n_jobs"] == 1 and out["split"] == "dev"
    plan = json.loads(Path(out["launch_plan"]).read_text())
    job = json.loads(Path(plan[0]["job_config"]).read_text())
    assert job["agents"][0]["override_timeout_sec"] == 600.0 and plan[0]["task_id"] == DEV[0]
    assert job["agents"][0]["env"]["ANTHROPIC_API_KEY"] == "${SSAP_TRIAL_TOKEN}"
    assert job["agents"][0]["env"]["MCR_VERSION_ID"] == "H_base"
    assert job["agents"][0]["env"]["MCR_AGENT_IMAGE"] == "alw-eda-claude:2.1.302"
    assert job["n_concurrent_trials"] == 1 and "orchestrator" not in job
    assert isinstance(job["extra_instructions"], list) and "mcr_promote" in job["extra_instructions"][0]
    assert "extra_instructions" not in job["agents"][0]["kwargs"]
    overlays = job["environment"]["extra_docker_compose"]
    mcr_overlay = Path(overlays[-1]).read_text()
    assert ":/opt/harness:ro" in mcr_overlay
    assert 'ALW_BASE_IMAGE: "alw-eda-claude:2.1.302"' in mcr_overlay

    rc, out = run(capsys, *base(tmp_path, "alw-eval", "--mode", "baseline", "--model", "test-model",
                                "--seeds", "2", "--parallel", "2", "--t-soft", "9000",
                                "--t-warn", "8000", "--dry-run"))
    assert rc == 0 and out["n_jobs"] == 2 * len(DEV)
    rc, out = run(capsys, *base(tmp_path, "alw-eval", "--mode", "subset", "--arm", "H_raw",
                                "--tasks", DEV[1], DEV[2], "--model", "test-model", "--dry-run"))
    assert rc == 0 and out["n_jobs"] == 2
    raw_plan = json.loads(Path(out["launch_plan"]).read_text())
    raw_job = json.loads(Path(raw_plan[0]["job_config"]).read_text())
    raw_overlay = Path(raw_job["environment"]["extra_docker_compose"][-1]).read_text()
    assert "/opt/harness" not in raw_overlay and "extra_instructions" not in raw_job
    assert cli.main(base(tmp_path, "alw-eval", "--mode", "subset", "--tasks", VAL[0],
                         "--model", "m", "--dry-run")) == 2
    assert cli.main(base(tmp_path, "alw-eval", "--mode", "baseline", "--split", "test",
                         "--model", "m", "--dry-run")) == 2
    capsys.readouterr()
    assert cli.main(base(tmp_path, "alw-eval", "--mode", "smoke-10m", "--model", "m")) == 2  # no --harbor-cmd


FORK = {"granted_repair_budget_sec": 900.0, "repair_eligible": True}


def _archive():
    rows = []
    for i, t in enumerate(DEV[:3]):
        rows.append({"task_id": t, "seed": 0, "fork_meta": FORK, "m_best": [1] * 6,
                     "unevaluated_draft": False, "advisory_flags": {},
                     "terminal_m": {"m_pex": 0 if i < 2 else 1},
                     "Y_inc_final": 0.5, "Y_pre": 0.5})
    return rows


CAND = {"candidate_id": "pex_terminal_gate", "gate": "m_pex", "change": {"enabled_in_hbase": True}}


def _repairs(y):
    return [{"task_id": t, "seed": 0, "rep_seed": r, "Y_post": y, "ratchet_promoted": True,
             "promote_reason": "tier3"} for t in DEV[:2] for r in range(3)]


def test_eval_fork_path1_dry_run_and_offline(capsys, tmp_path):
    arc = tmp_path / "archive.json"
    arc.write_text(json.dumps(_archive()))
    cand = tmp_path / "cand.json"
    cand.write_text(json.dumps(CAND))
    rc, out = run(capsys, *base(tmp_path, "alw-eval", "--mode", "fork-path1", "--archive", str(arc),
                                "--candidate", str(cand), "--dry-run"))
    assert rc == 0 and out["n_divergent"] == 2 and len(out["planned_repairs"]) == 6
    assert all(p["granted_repair_budget_sec"] == 900.0 for p in out["planned_repairs"])
    assert cli.main(base(tmp_path, "alw-eval", "--mode", "fork-path1", "--archive", str(arc),
                         "--candidate", str(cand))) == 2                 # no repair results
    capsys.readouterr()
    rr = jsonl(tmp_path / "rr.jsonl", _repairs(0.8))
    rc, out = run(capsys, *base(tmp_path, "alw-eval", "--mode", "fork-path1", "--archive", str(arc),
                                "--candidate", str(cand), "--repair-results", rr))
    assert rc == 0 and out["promoted"] and out["sum_delta_Y"] == pytest.approx(0.6)
    assert out["counts"]["Rescued"] == 2
    led = (tmp_path / "state" / "ledgers" / "event_ledger.jsonl").read_text().splitlines()
    assert len(led) == 2
    traj = tmp_path / "traj.json"
    traj.write_text(json.dumps({"candidate_id": "fill_eps", "gate": "m_fill", "change": {"eps": 0.01}}))
    assert cli.main(base(tmp_path, "alw-eval", "--mode", "fork-path1", "--archive", str(arc),
                         "--candidate", str(traj), "--dry-run")) == 2   # trajectory -> Path 2


def test_eval_c1_val(capsys, tmp_path):
    inc = tmp_path / "inc.json"
    bas = tmp_path / "base.json"
    inc.write_text(json.dumps({t: 0.6 for t in VAL}))
    bas.write_text(json.dumps({t: 0.5 for t in VAL}))
    args = ["--mode", "c1-val", "--inc-scores", str(inc), "--base-scores", str(bas),
            "--inc-version", "inc7"]
    rc, out = run(capsys, *base(tmp_path, "alw-eval", *args, "--dry-run"))
    assert rc == 0 and out["promoted"] and not (tmp_path / "state" / "ledgers" / "holdout_audit.jsonl").exists()
    rc, out = run(capsys, *base(tmp_path, "alw-eval", *args))
    assert rc == 0 and set(out) == {"selected_version_id", "promoted", "delta_val",
                                    "val_access_count", "dry_run"}
    assert out["selected_version_id"] == "inc7" and out["val_access_count"] == 1
    assert cli.main(base(tmp_path, "alw-eval", *args)) == 2               # val is single-shot
    assert cli.main(base(tmp_path, "alw-eval", *args, "--split", "dev")) == 2


# ------------------------------------------------------------------------------------------
# alw-loop
# ------------------------------------------------------------------------------------------

def test_loop_adopts_then_prunes_after_k_idle(capsys, tmp_path):
    loop = tmp_path / "state" / "loop"
    r1 = loop / "round_1"
    r1.mkdir(parents=True)
    (r1 / "archive.json").write_text(json.dumps(_archive()))
    (r1 / "candidates.json").write_text(json.dumps([CAND]))
    jsonl(r1 / "repair_results.jsonl", _repairs(0.8))
    rc, out = run(capsys, *base(tmp_path, "alw-loop", "--rounds", "1", "--dry-run"))
    assert rc == 0 and out["rounds"][0]["adopted"] is None and not (loop / "h_inc.json").exists()

    # H_inc (now with the pex gate) archives with no own rescues in rounds 2 and 3
    quiet = [{**a, "inc_triggered": False, "inc_gate_signature": [], "terminal_m": {"m_pex": 1}}
             for a in _archive()]
    for g in (2, 3):
        (loop / f"round_{g}").mkdir()
        (loop / f"round_{g}" / "archive.json").write_text(json.dumps(quiet))
    rc, out = run(capsys, *base(tmp_path, "alw-loop", "--rounds", "5", "--parallel", "2"))
    assert rc == 0
    rounds = out["rounds"]
    assert rounds[0]["adopted"] == "pex_terminal_gate"
    assert rounds[1]["pruned"] == [] and rounds[2]["pruned"] == ["m_pex"]
    assert rounds[3]["status"] == "waiting_for_dev_archive"
    h = json.loads((loop / "h_inc.json").read_text())
    assert h["gates"] == [] and h["pruned"] == ["m_pex"] and "m_pex" not in h["changes"]
    assert h["round"] == 3


# ------------------------------------------------------------------------------------------
# alw-report
# ------------------------------------------------------------------------------------------

def _test_rows(with_manual=False):
    rows = []
    for i, t in enumerate(TEST):
        for s in range(3):
            y = 0.4 + 0.02 * s
            rows.append({**OK, "arm": "H_raw", "task_id": t, "seed": s, "Y_star": y - 0.1, "C_star": 0})
            rows.append({**OK, "arm": "H_base", "task_id": t, "seed": s, "Y_star": y, "C_star": 0,
                         "m": [1] * 6, "cost": {"input_tokens": 10}})
            rows.append({**OK, "arm": "H_star", "task_id": t, "seed": s, "Y_star": y + (0.2 if i < 3 else 0),
                         "C_star": 1 if i < 3 else 0, "cost": {"input_tokens": 12}})
            if with_manual:
                rows.append({**OK, "arm": "manual_instructions", "task_id": t, "seed": s,
                             "Y_star": y + 0.01, "C_star": 0})
    return rows


def test_report(capsys, tmp_path):
    runs = jsonl(tmp_path / "test_runs.jsonl", _test_rows())
    out_dir = tmp_path / "report"
    common = ["--split", "test", "--seeds", "3", "--runs", runs, "--out-dir", str(out_dir),
              "--selected-version", "inc7"]
    rc, out = run(capsys, *base(tmp_path, "alw-report", *common, "--dry-run"))
    assert rc == 0 and out["seeds_per_arm"] == {"H_raw": 3, "H_base": 3, "H_star": 3}
    assert not out["already_unblinded"]
    assert cli.main(base(tmp_path, "alw-report", *common, "--with-baselines")) == 2   # no manual arm
    assert cli.main(base(tmp_path, "alw-report", *common[:2], "--seeds", "2", *common[4:])) == 2
    assert cli.main(base(tmp_path, "alw-report", "--split", "val", *common[2:])) == 2
    capsys.readouterr()
    rc, out = run(capsys, *base(tmp_path, "alw-report", *common))
    assert rc == 0 and out["c_star_decomposition_exact"] is True
    assert out["three_stage_attribution"]["delta_base_to_star"] == pytest.approx(0.2 * 3 / 14)
    rep = json.loads((out_dir / "test_report.json").read_text())
    assert rep["cluster_bootstrap"]["conservative"]["G"] == 10
    assert rep["cluster_bootstrap"]["hard"]["G"] == 11
    assert (out_dir / "test_report.md").is_file() and (out_dir / "test_unblinded.lock").is_file()
    assert cli.main(base(tmp_path, "alw-report", *common)) == 2          # single unblind

    runs2 = jsonl(tmp_path / "with_manual.jsonl", _test_rows(with_manual=True))
    rc, out = run(capsys, *base(tmp_path, "alw-report", "--runs", runs2, "--out-dir",
                                str(tmp_path / "r2"), "--selected-version", "inc7", "--with-baselines"))
    rep = json.loads((tmp_path / "r2" / "test_report.json").read_text())
    assert rc == 0 and rep["honest_baselines"]["manual_instructions"]["delta_vs_H_base"] == pytest.approx(0.01)


# ------------------------------------------------------------------------------------------
# alw-harvest
# ------------------------------------------------------------------------------------------

SECRET = "sk-literal-upstream-secret-9876"
MET_EVAL = {"status": "evaluated", "score": 0.1, "score_valid": True, "design_passed": False,
            "evaluation_error": False, "score_is_lower_bound": False,
            "scoring_inputs": {"submission_present": True, "geometry_valid": True, "shape_ok": True,
                               "lvs_pass": False, "drc_pass": True},
            "stages": {"drc": {"status": "passed"}, "lvs": {"status": "failed"},
                       "area": {"status": "measured"}}}
MET_PASS = {**MET_EVAL, "score": 0.9, "design_passed": True}
MET_MISSING = {"status": "missing_submission", "score": 0.0, "score_valid": True,
               "design_passed": False, "evaluation_error": False,
               "scoring_inputs": {"submission_present": False}}


def _proxy(**kw):
    return {"proxy_requests": 200, "input_tokens": 1000, "output_tokens": 100, "cached_tokens": 10,
            "unrecovered_gateway_error": False, "gateway_wait_seconds": 50.0,
            "client_disconnects": 1, "model_swap": False} | kw


def _w(path: Path, obj) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def _trial(job: Path, name: str, task: str, seed: int, metrics: dict, *, timed_out: bool,
           promoted: bool, m_pre: list, m_post: list | None = None, summary: bool = True) -> None:
    trial = job / "harbor" / name / f"{task[:20]}__abc"
    snap = trial / "agent" / "mcr_snapshot"
    mcr = snap / "pre_repair" / "workspace" / ".mcr"
    fork = {"schema_version": 1, "phase_a_elapsed_sec": 9900.0 if timed_out else 9500.0,
            "granted_repair_budget_sec": 780.0, "granted_repair_turns": 80, "repair_eligible": True,
            "phase_a": {"returncode": 143 if timed_out else 0, "timed_out": timed_out},
            "phase_a_argv": ["--settings", "/tmp/claude-code-settings/settings.json", "--print"]}
    _w(mcr / "fork_meta.json", fork)
    _w(mcr / "gate_report.json", {"gate_triggered": True, "trigger_reasons": ["m_best_not_all_certified"],
                                  "granted_repair_budget_sec": 780.0, "granted_repair_turns": 80})
    _w(mcr / "best" / "cert.json", {"file_present": True, "m": m_pre, "raw_u": [0.0] * 6,
                                    "advisory_flags": {}, "artifact_sha256": "a" * 64})
    (snap / "pre_repair" / "workspace" / "submission").mkdir(parents=True)
    (snap / "pre_repair" / "workspace" / "submission" / "layout.gds").write_bytes(b"gds")
    if m_post is not None:
        _w(snap / "post_repair" / "best" / "cert.json", {"file_present": True, "m": m_post,
                                                         "raw_u": [0.0] * 6, "advisory_flags": {}})
    if summary:
        _w(snap / "artifact_summary.json", {
            "schema_version": 1, "task_id": task, "seed": str(seed), "version_id": "H_base",
            "agent_image": f"img via https://u:{SECRET}" + "@gw.invalid", "m_pre": m_pre,
            "m_post": m_post or m_pre, "gate_triggered": True, "ratchet_promoted": promoted,
            "infra_error": False, "outer_metrics": {"Y_star": None},
            "cost_ledger": {"input_tokens": None, "tool_cpu_sec": 100.0, "peak_ram_mb": 500.0,
                            "wall_sec": 9900.0}})
    _w(trial / "verifier" / "evaluation" / "metrics.json", metrics)


def _job(tmp_path, tag="h1", tasks=None):
    job = tmp_path / "state" / "jobs" / tag
    t0, t1 = tasks or DEV[:2]
    specs = [(t0, 0, MET_EVAL, True, False, [1, 0, 1, 0, 0, 1], None, _proxy(), True),
             (t0, 1, MET_MISSING, False, False, [0] * 6, None, _proxy(), True),
             (t1, 0, MET_PASS, False, True, [1, 0, 1, 0, 0, 1], [1] * 6, _proxy(), True),
             (t1, 1, MET_EVAL, False, False, [1, 0, 1, 0, 0, 1], None,
              _proxy(unrecovered_gateway_error=True), False)]
    recs = []
    for task, seed, met, to, prom, m_pre, m_post, px, summ in specs:
        name = f"{tag}-H_base-{task}-s{seed}"
        _trial(job, name, task, seed, met, timed_out=to, promoted=prom, m_pre=m_pre, m_post=m_post,
               summary=summ)
        recs.append({"job_name": name, "task_id": task, "seed": seed, "arm": "H_base",
                     "exit_code": 0, "proxy": px})
    _w(job / "launch_results.json", recs)
    return job, recs


def test_harvest_feeds_diagnose_and_bank(capsys, tmp_path, monkeypatch):
    monkeypatch.setenv("SSAP_UPSTREAM_KEY", SECRET)
    job, recs = _job(tmp_path)
    runs, arc, smp = tmp_path / "o" / "runs.jsonl", tmp_path / "o" / "arc.json", tmp_path / "o" / "s.jsonl"
    outs = ["--out-runs", str(runs), "--out-archive", str(arc), "--out-samples", str(smp)]
    rc, out = run(capsys, *base(tmp_path, "alw-harvest", "--tag", "h1", *outs, "--dry-run"))
    assert rc == 0 and out["written_files"] == [] and not runs.exists() and not arc.exists()
    assert out["n_trials"] == 4 and out["n_scored"] == 3 and out["n_infra_error"] == 1
    rc, out = run(capsys, *base(tmp_path, "alw-harvest", "--tag", "h1", *outs, "--backfill-summaries"))
    assert rc == 0 and out["source"] == "launch_results.json"
    assert out["mean_Y_star"] == pytest.approx((0.1 + 0.0 + 0.9) / 3)
    text = json.dumps(out)
    assert SECRET not in text
    rows = {(r["task_id"], r["seed"]): r for r in map(json.loads, runs.read_text().splitlines())}
    a, b, c, d = (rows[(DEV[0], 0)], rows[(DEV[0], 1)], rows[(DEV[1], 0)], rows[(DEV[1], 1)])
    assert a["t_soft_stop"] is True and a["infra_error"] is False and a["Y_star"] == 0.1
    assert a["d_star"]["lvs"] == 0 and a["d_star"]["pex"] == "bottom" and a["m"] == [1, 0, 1, 0, 0, 1]
    assert a["cost"] == {"input_tokens": 1000, "output_tokens": 100, "cached_tokens": 10, "usd": None,
                         "tool_cpu_sec": 100.0, "peak_ram_mb": 500.0, "wall_sec": 9900.0}
    assert a["wall_sec"] == 9900.0 and a["proxy_requests"] == 200 and a["client_disconnects"] == 1
    assert b["status"] == "missing_submission" and b["Y_star"] == 0.0 and not b["infra_error"]
    assert c["ratchet_promoted"] and c["m"] == [1] * 6 and c["C_star"] == 1
    assert d["infra_error"] and d["infra_category"] == "gateway_error" and d["Y_star"] == 0.1
    assert d["wall_sec"] is None and d["cost"]["wall_sec"] is None
    for k in ("run_id", "arm", "status", "exit_code", "gateway_wait_seconds", "evaluation_error",
              "score_is_lower_bound", "unrecovered_gateway_error"):
        assert k in a, k
    archive = json.loads(arc.read_text())
    assert sorted((t["task_id"], t["seed"]) for t in archive) == sorted([(DEV[0], 0), (DEV[0], 1), (DEV[1], 0)])
    by = {(t["task_id"], t["seed"]): t for t in archive}
    assert by[(DEV[0], 0)]["Y_pre"] == 0.1 and by[(DEV[1], 0)]["Y_pre"] is None
    assert by[(DEV[1], 0)]["Y_inc_final"] == 0.9 and by[(DEV[0], 0)]["inc_triggered"] is True
    assert by[(DEV[0], 0)]["inc_gate_signature"] == ["m_best_not_all_certified"]
    assert by[(DEV[0], 0)]["fork_meta"]["granted_repair_budget_sec"] == 780.0
    assert Path(by[(DEV[0], 0)]["snapshot_dir"]).name == "mcr_snapshot"
    assert out["skipped"] == [{"run_id": recs[3]["job_name"], "archive": "not_scored"}]
    samples = [json.loads(l) for l in smp.read_text().splitlines()]
    assert len(samples) == 4 and {s["source_kind"] for s in samples} == {"terminal"}
    s_c = next(s for s in samples if s["task_id"] == DEV[1] and s["meta"]["seed"] == 0)
    assert s_c["cert_dict"]["m"] == [1] * 6 and s_c["C_star"] == 1
    assert s_c["artifact_path"].endswith("pre_repair/workspace/submission/layout.gds")
    # backfilled summaries: outer metrics, token cost and the host-side infra verdict, scrubbed
    merged = sorted(job.rglob("artifact_summary_merged.json"))
    assert len(merged) == 4 and len(sorted(job.rglob("artifact_summary_merged.md"))) == 4
    ms = {json.loads(p.read_text())["seed"] + json.loads(p.read_text())["task_id"]: json.loads(p.read_text())
          for p in merged}
    ma, md = ms["0" + DEV[0]], ms["1" + DEV[1]]
    assert ma["outer_metrics"]["Y_star"] == 0.1 and ma["cost_ledger"]["input_tokens"] == 1000
    assert ma["cost_ledger"]["tool_cpu_sec"] == 100.0 and ma["infra_error"] is False
    assert md["infra_error"] is True and md["infra_reason"] == "unrecovered_gateway_error"
    assert all(SECRET not in p.read_text() for p in job.rglob("artifact_summary_merged.*"))

    # harvested runs drive the dev diagnostics and B_dev admission unchanged
    rc, diag = run(capsys, *base(tmp_path, "alw-diagnose", "--runs", str(runs),
                                 "--out-dir", str(tmp_path / "diag")))
    assert rc == 0 and diag["taxonomy"]["public_mirror_execution_gap"] == 1
    assert diag["taxonomy"]["missing_or_invalid_gds"] == 1
    rc, bank = run(capsys, *base(tmp_path, "alw-concordance", "--build-bank", "--samples", str(smp)))
    assert rc == 0 and bank["build_bank"]["written"] == 4 and bank["build_bank"]["n_samples"] == 4


def test_harvest_plan_fallback_and_holdout_guard(capsys, tmp_path):
    job, recs = _job(tmp_path, tag="h2")
    (job / "launch_results.json").unlink()
    _w(job / "launch_plan.json", [{k: v for k, v in r.items() if k not in ("proxy", "exit_code")}
                                  for r in recs])
    rc, out = run(capsys, *base(tmp_path, "alw-harvest", "--job-dir", str(job),
                                "--out-runs", str(tmp_path / "r.jsonl")))
    assert rc == 0 and out["source"] == "launch_plan.json" and out["n_infra_error"] == 0
    row = json.loads((tmp_path / "r.jsonl").read_text().splitlines()[0])
    assert "proxy_requests" not in row and row["exit_code"] is None
    assert cli.main(base(tmp_path, "alw-harvest", "--tag", "h2", "--job-dir", str(job))) == 2
    assert cli.main(base(tmp_path, "alw-harvest")) == 2
    assert cli.main(base(tmp_path, "alw-harvest", "--tag", "nope")) == 2
    capsys.readouterr()
    vjob, _ = _job(tmp_path, tag="v1", tasks=(DEV[0], VAL[0]))
    rc, out = run(capsys, *base(tmp_path, "alw-harvest", "--tag", "v1"))          # runs only: ok
    assert rc == 0 and out["n_trials"] == 4
    for flag in ("--out-archive", "--out-samples"):
        assert cli.main(base(tmp_path, "alw-harvest", "--tag", "v1", flag, str(tmp_path / "x"))) == 2
    assert not (tmp_path / "x").exists()
