"""Phase 3 autonomous outer loop: proposer sandbox, Stage 0 screen, Stage 1 Path 1 / Path 2.
Docker, the metering proxy, the verifier and Harbor are mocked; no network, no model calls."""
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from ssap import cli
from ssap.bank.blind_clusters import BlindBoundary, PrivacyViolation, freeze_blind_clusters
from ssap.bank.referee_bank import BankSample
from ssap.config import load_config
from ssap.eval import auto_loop as al
from ssap.eval import proposer_sandbox as ps
from ssap.targets.alw.harvest import Harvested

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs" / "alw" / "config.server.json"
SPLIT = json.loads((ROOT / "configs" / "alw" / "split.json").read_text())
CATALOG = json.loads((ROOT / "configs" / "alw" / "catalog.json").read_text())
BLIND = freeze_blind_clusters(SPLIT)
VIS = sorted(BLIND.visible_task_ids)
BL = sorted(BLIND.blind_task_ids)
CLUSTER = ps.dev_cluster_of(SPLIT)
DEV = SPLIT["task_ids"]["dev"]
MIRRORS = list(ps.HBASE_MIRRORS)
REG = "src/ssap/checkers/alw/registry.py"
REPAIR = "src/ssap/checkers/alw/repair.py"


def config(**env) -> dict:
    base = {"SSAP_MODEL": "test-model", "SSAP_GATEWAY": "http://127.0.0.1:9",
            "SSAP_UPSTREAM_KEY": "upstream-literal-key"}
    return load_config(CFG, env=base | env)


def leaks(text: str) -> list[str]:
    return [n for n in [*BL, *BLIND.blind] if n in text]


@pytest.fixture(scope="module")
def hbase(tmp_path_factory):
    d = tmp_path_factory.mktemp("hbase") / "h"
    ps.stage_clean_hbase_source(ROOT, d)
    return d


def copy(src: Path, dst: Path) -> Path:
    shutil.copytree(src, dst)
    return dst


def edit(root: Path, rel: str, old: str, new: str) -> None:
    p = root / rel
    t = p.read_text()
    assert old in t, (rel, old)
    p.write_text(t.replace(old, new, 1))


def write_proposal(path: Path, **kw) -> Path:
    path.write_text(json.dumps({"candidate_id": "c1", "mutation_class": 4,
                                "evaluation_path": "path1"} | kw))
    return path


def write_bank(path: Path) -> Path:
    rows = []
    for t in VIS[:3] + BL[:2]:
        for lab in (1, 0):
            rows.append(BankSample(
                sample_id=f"{t}:lab{lab}", task_id=t, cluster_id=CLUSTER[t], source_kind="terminal",
                Y_star=0.9 if lab else 0.2, C_star=lab,
                d_star={k: lab for k in ("gds", "drc", "lvs", "shape", "fill", "morph")},
                cert_dict={"m": [1] * 6 if lab else [1, 1, 1, 1, 1, 0], "mirror_names": MIRRORS},
                artifact_path=f"/data/{t}/{lab}.gds").as_dict())
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))
    return path


def evaluator(cand_root: Path, cand_fn=None):
    calls = []

    def ev(root, s):
        calls.append((Path(root) == Path(cand_root), s.sample_id))
        cert = {"m": list(s.cert_dict["m"]), "u": [0.5] * 6, "mirror_names": MIRRORS,
                "advisory_flags": {}, "notes": {"lvs": "ok"}}
        if Path(root) == Path(cand_root) and cand_fn is not None:
            cand_fn(s, cert)
        return cert
    ev.calls = calls
    return ev


def smoke_ok(cfg, *, root, work_dir, use_docker):
    return {"ok": True, "root": str(root)}


# ------------------------------------------------------------------------------------------
# wrapper / CLI wiring / staging / prompt
# ------------------------------------------------------------------------------------------

def test_phase_b_invocation_runs_as_sandbox(tmp_path):
    from ssap.targets.alw.agent_wrapper import build_stateless_repair_invocation
    pre = tmp_path / "pre_repair"
    (pre / "workspace" / ".mcr").mkdir(parents=True)
    (pre / "workspace" / ".mcr" / "fork_meta.json").write_text(json.dumps(
        {"granted_repair_budget_sec": 600.0, "granted_repair_turns": 80}))
    g = tmp_path / "gate.json"
    g.write_text(json.dumps({"gate_triggered": True, "trigger_reasons": ["x"]}))
    inv = build_stateless_repair_invocation(pre, g, model="m", proxy_url="http://h:1", trial_token="tok")
    assert inv.env["IS_SANDBOX"] == "1" and inv.env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert "--no-session-persistence" in inv.argv and inv.timeout_sec == 600.0


def test_stage_jobs_h_star_defaults_to_h_inc_harness(tmp_path, hbase):
    cfg, state = config(), tmp_path / "state"
    jobs = cli._stage_jobs(cfg, state, arm="H_star", tasks=[DEV[0]], seeds=[0], tag="t0", timeout_s=None)
    staged = state / "jobs" / "t0" / "harness" / "python" / "ssap" / "checkers" / "alw" / "repair.py"
    assert jobs and "MARKER" not in staged.read_text()
    h = copy(hbase, state / "loop" / "h_inc_harness")
    edit(h, REPAIR, "then stop.", "then stop. MARKER")
    cli._stage_jobs(cfg, state, arm="H_star", tasks=[DEV[0]], seeds=[0], tag="t1", timeout_s=None)
    assert "MARKER" in (state / "jobs" / "t1" / "harness" / "python" / "ssap" / "checkers" / "alw" /
                        "repair.py").read_text()
    cli._stage_jobs(cfg, state, arm="H_base", tasks=[DEV[0]], seeds=[0], tag="t2", timeout_s=None)
    assert "MARKER" not in (state / "jobs" / "t2" / "harness" / "python" / "ssap" / "checkers" /
                            "alw" / "repair.py").read_text()
    other = copy(hbase, tmp_path / "explicit")
    edit(other, REPAIR, "then stop.", "then stop. EXPLICIT")
    cli._stage_jobs(cfg, state, arm="H_star", tasks=[DEV[0]], seeds=[0], tag="t3", timeout_s=None,
                    harness_root=other)
    assert "EXPLICIT" in (state / "jobs" / "t3" / "harness" / "python" / "ssap" / "checkers" /
                          "alw" / "repair.py").read_text()


@pytest.mark.parametrize("cmd,flags", [
    ("alw-eval", ("--harness-root",)),
    ("alw-loop", ("--auto", "--propose", "--screen-candidate", "--eval-candidate", "--round",
                  "--max-propose-attempts", "--proposer-max-turns", "--eval-seeds", "--harbor-cmd",
                  "--verify-cmd", "--inc-job-dir"))])
def test_cli_flags(cmd, flags, capsys):
    with pytest.raises(SystemExit):
        cli.main([cmd, "--help"])
    out = capsys.readouterr().out
    assert all(f in out for f in flags)


def test_stage_clean_hbase_source(tmp_path, hbase):
    assert not (hbase / "src" / "ssap" / "checkers" / "alw" / "pex.py").exists()
    assert "m_pex" not in (hbase / REG).read_text()
    assert (ROOT / "src" / "ssap" / "checkers" / "alw" / "pex.py").is_file()      # checkout untouched
    assert "m_pex" in (ROOT / REG).read_text()
    specs = ps.registry_specs(hbase)
    assert [s["name"] for s in specs if s["klass"] == 1] == MIRRORS
    assert [s["name"] for s in specs if s["klass"] == 2] == list(ps.HBASE_ADVISORIES)
    assert ps.kernel_hashes(hbase) == ps.kernel_hashes(ROOT) and ps.kernel_hashes(hbase)
    assert (hbase / "container" / "alw" / "settings.json").is_file()
    assert not (hbase / "src" / "ssap" / "targets").exists() and not (hbase / "tests").exists()


def test_proposer_prompt_is_hint_free():
    for p in (ps.build_proposer_prompt(1), ps.build_proposer_prompt(
            3, inc_version="H_base+c1", attempt=2, feedback={"stage": "screen", "reason": "x"})):
        low = p.lower()
        assert not [w for w in ps.FORBIDDEN_HINT_WORDS if w.lower() in low]
        for must in ("Class 1", "Class 2", "Class 3", "Class 4", "Path 1", "Path 2", "2048",
                     "src/ssap/kernel/", "--no-session-persistence", "tests/", "proposal.json"):
            assert must in p
        assert not leaks(p)


# ------------------------------------------------------------------------------------------
# objective evaluation path
# ------------------------------------------------------------------------------------------

def test_objective_path_classification(tmp_path, hbase):
    inc_specs = ps.registry_specs(hbase)

    def path_of(mutate) -> str:
        c = tmp_path / f"c{len(list(tmp_path.iterdir()))}"
        copy(hbase, c)
        mutate(c)
        _, changed = ps.diff_trees(hbase, c)
        return ps.classify_evaluation_path(hbase, c, changed, inc_specs, ps.registry_specs(c))[0]

    assert path_of(lambda c: edit(c, REPAIR, "then stop.", "then stop now.")) == "path1"
    assert path_of(lambda c: edit(c, "container/alw/bin/mcr_hook.py", "import json", "import json  # x")) == "path2"
    assert path_of(lambda c: edit(c, "container/alw/bin/mcr_check", "gate, _ = gate_report(args.elapsed)",
                                  "gate, _ = gate_report(args.elapsed)\n        gate['v'] = 2")) == "path1"
    assert path_of(lambda c: edit(c, "container/alw/bin/mcr_check", '"would_promote": dec.promote',
                                  '"would_promote": bool(dec.promote)')) == "path2"
    assert path_of(lambda c: edit(c, "container/alw/bin/mcr_entrypoint.py",
                                  "def repair_prompt(gate: dict, fork: dict) -> str:\n",
                                  "def repair_prompt(gate: dict, fork: dict) -> str:\n"
                                  "    gate = dict(gate)\n")) == "path1"
    assert path_of(lambda c: edit(c, "container/alw/bin/mcr_entrypoint.py", "grace=wm.kill_grace_sec)\n",
                                  "grace=wm.kill_grace_sec + 0)\n")) == "path2"
    assert path_of(lambda c: edit(c, REG, 'details: dict = {"backend": self.backend}',
                                  'details: dict = {"backend": self.backend, "v": 2}')) == "path2"
    assert path_of(lambda c: edit(c, REG, 'description="0 DRC markers"),',
                                  'description="0 DRC markers"),\n        CheckerSpec("m_extra", 1, '
                                  'scope="terminal_only", eps=0.0, description="x"),')) == "path1"
    assert path_of(lambda c: edit(c, REG, 'CheckerSpec("m_morph", 1, eps=0.0',
                                  'CheckerSpec("m_morph", 1, eps=0.01')) == "path2"
    assert path_of(lambda c: (c / "container/alw/bin/new_tool").write_text("#!/bin/sh\n")) == "path2"
    assert path_of(lambda c: edit(c, "src/ssap/checkers/alw/lvs.py", '"""', '"""x ', )) == "path2"


# ------------------------------------------------------------------------------------------
# Stage 0
# ------------------------------------------------------------------------------------------

def screen(tmp_path, hbase, cand, proposal, **kw):
    bank = write_bank(tmp_path / "bank" / "b_dev.jsonl")
    return ps.validate_and_screen_proposal(
        hbase, cand, proposal, bank_path=bank, blind=BLIND, cfg=config(), alw_repo=None,
        work_dir=tmp_path / "work", use_docker=False, catalog=CATALOG,
        smoke_fn=kw.pop("smoke_fn", smoke_ok), **kw)


def test_stage0_rejections(tmp_path, hbase):
    cand = copy(hbase, tmp_path / "cand")
    rep = screen(tmp_path, hbase, cand, tmp_path / "missing.json")
    assert rep["reason"] == "proposal_missing_or_invalid_json"
    prop = write_proposal(tmp_path / "p.json", mutation_class=5)
    assert screen(tmp_path, hbase, cand, prop)["reason"] == "proposal_mutation_class_invalid"
    prop = write_proposal(tmp_path / "p.json")
    assert screen(tmp_path, hbase, cand, prop)["reason"] == "empty_diff"
    (cand / "src/ssap/targets").mkdir(parents=True)
    (cand / "src/ssap/targets/x.py").write_text("X = 1\n")
    assert screen(tmp_path, hbase, cand, prop)["reason"] == "file_outside_harness_scope"
    shutil.rmtree(cand / "src/ssap/targets")
    edit(cand, "src/ssap/kernel/ratchet.py", '"""', '"""edited ')
    assert screen(tmp_path, hbase, cand, prop)["reason"] == "kernel_sha256_changed"

    def static(text):
        c = copy(hbase, tmp_path / f"s{len(list(tmp_path.iterdir()))}")
        edit(c, REPAIR, "then stop.\n", "then stop.\n")
        (c / "src/ssap/checkers/alw/extra.py").write_text(text)
        return screen(tmp_path, hbase, c, prop)
    rep = static(f'X = "{DEV[3]}"\n')
    assert rep["reason"] == "r2_r3_violation" and "task_id_literal" in rep["checks"]["static_violation_kinds"]
    assert static('P = "../tests/fixtures"\n')["checks"]["static_violation_kinds"] == ["tests_path"]
    assert static("import harbor\n")["reason"] == "r2_r3_violation"
    assert static("import numpy\n")["checks"]["static_violation_kinds"] == ["new_external_import"]
    top = next(t["top"] for t in CATALOG["tasks"] if len(t["top"]) >= 6
               and t["top"].lower() not in ps.GENERIC_CIRCUIT_WORDS)
    assert static(f"# tuned for {top}\n")["ok"]                         # comments are not literals
    assert static(f'"""Docstring about {top}."""\nX = 1\n')["ok"]
    assert static('STAGE = "comparator"\n')["ok"]                        # generic category word
    assert static(f'X = "{top} stage"\n')["checks"]["static_violation_kinds"] == ["circuit_name_literal"]
    c = copy(hbase, tmp_path / "settings_name")
    edit(c, "container/alw/settings.json", "{", "{" + f'"_note": "{top}",', )
    assert screen(tmp_path, hbase, c, prop)["checks"]["static_violation_kinds"] == ["circuit_name_literal"]

    c = copy(hbase, tmp_path / "noreg")
    edit(c, REG, 'CheckerSpec("m_drc", 1, eps=0.0, depends_on=("m_gds",), description="0 DRC markers"),', "")
    assert screen(tmp_path, hbase, c, write_proposal(tmp_path / "p1.json", mutation_class=1))[
        "reason"] == "hbase_mirror_checkers_not_intact"
    c = copy(hbase, tmp_path / "two")
    edit(c, REG, 'description="0 DRC markers"),', 'description="0 DRC markers"),\n'
         '        CheckerSpec("advisory_a", 2, eps=None),\n        CheckerSpec("advisory_b", 2, eps=None),')
    assert screen(tmp_path, hbase, c, write_proposal(tmp_path / "p2.json", mutation_class=2))[
        "reason"] == "single_mutation_discipline"
    c = copy(hbase, tmp_path / "smokefail")
    edit(c, REPAIR, "then stop.", "then stop now.")
    assert screen(tmp_path, hbase, c, prop, smoke_fn=lambda *a, **k: {"ok": False})[
        "reason"] == "offline_smoke_failed"


def test_stage0_class4(tmp_path, hbase):
    prop = write_proposal(tmp_path / "p.json", mutation_class=4)
    c = copy(hbase, tmp_path / "ok")
    edit(c, REPAIR, "then stop.", "then stop and report the remaining failures.")
    rep = screen(tmp_path, hbase, c, prop)
    assert rep["ok"] and rep["evaluation_path"] == "path1" and rep["screen"]["phase_b_stateless"]
    assert (tmp_path / "work" / "candidate.patch").read_text().count("then stop and report") == 1
    assert json.loads((tmp_path / "work" / "stage0_report.json").read_text())["ok"]
    c = copy(hbase, tmp_path / "stateful")
    edit(c, REPAIR, '["-p", "--no-session-persistence", "--max-turns"', '["-p", "--max-turns"')
    rep = screen(tmp_path, hbase, c, prop)
    assert not rep["ok"] and rep["stage"] == "screen" and rep["screen"]["phase_b_stateless"] is False
    c = copy(hbase, tmp_path / "phase_a")
    edit(c, "container/alw/bin/mcr_hook.py", "import json", "import json  # touched")
    rep = screen(tmp_path, hbase, c, write_proposal(tmp_path / "p.json", evaluation_path="path1"))
    assert not rep["ok"] and rep["evaluation_path"] == "path2" and rep["declared_path"] == "path1"


def _add_spec(root: Path, line: str) -> None:
    edit(root, REG, 'description="0 DRC markers"),', 'description="0 DRC markers"),\n        ' + line)


def test_stage0_class1_screen_and_blind_boundary(tmp_path, hbase):
    c = copy(hbase, tmp_path / "c1")
    _add_spec(c, 'CheckerSpec("m_extra", 1, scope="terminal_only", eps=0.0, depends_on=("m_lvs",), '
                 'description="terminal candidate"),')
    prop = write_proposal(tmp_path / "p.json", mutation_class=1, checker="m_extra", d_star_key="drc")

    def good(s, cert):
        cert["terminal_m"] = {"m_extra": 1 if s.d("drc") == 1 else 0}
    ev = evaluator(c, good)
    rep = screen(tmp_path, hbase, c, prop, evaluator=ev)
    assert rep["ok"] and rep["evaluation_path"] == "path1"
    sc = rep["screen"]["screen"]
    assert sc["checks"]["directional_violations"] == 0 and sc["blind_aggregate"]["n_samples"] == 2
    assert all(is_cand for is_cand, _ in ev.calls)                     # Class 1: candidate only
    n_calls = len(ev.calls)
    rep = screen(tmp_path, hbase, c, prop, evaluator=ev, cache_dir=tmp_path / "work" / "cert_cache")
    assert rep["ok"]

    def bad_on_blind(s, cert):
        cert["terminal_m"] = {"m_extra": 0 if s.task_id in BL else (1 if s.d("drc") == 1 else 0)}
    c2 = copy(c, tmp_path / "c1bad")
    edit(c2, REG, 'description="terminal candidate"', 'description="terminal candidate v2"')
    rep = screen(tmp_path, hbase, c2, prop, evaluator=evaluator(c2, bad_on_blind))
    sc = rep["screen"]["screen"]
    assert not rep["ok"] and sc["checks"]["directional_violations"] == 2
    assert sc["visible"]["violating_sample_ids"] == [] and sc["blind_aggregate"]["n_fp"] == 2
    text = json.dumps(rep) + (tmp_path / "work" / "stage0_report.json").read_text()
    assert not leaks(text)
    fb = ps.stage0_feedback(rep)
    assert fb["screen_checks"]["directional_violations"] == 2 and not leaks(json.dumps(fb))
    assert ps.build_proposer_prompt(1, attempt=2, feedback=fb).count('"directional_violations": 2') == 1
    assert n_calls > 0

    def boom(root, s):
        raise RuntimeError("docker down")
    c3 = copy(c, tmp_path / "c1infra")
    edit(c3, REG, 'description="terminal candidate"', 'description="terminal candidate v3"')
    rep = screen(tmp_path, hbase, c3, prop, evaluator=boom)
    assert rep["reason"] == "stage0_infra_error"                       # never a label
    rep = screen(tmp_path, hbase, c3, prop)                            # no docker evaluator
    assert rep["reason"] == "stage0_evaluator_unavailable"


def test_stage0_class2_screen(tmp_path, hbase):
    c = copy(hbase, tmp_path / "c2")
    _add_spec(c, 'CheckerSpec("advisory_probe_x", 2, eps=None, gate_trigger=True, description="p"),')
    prop = write_proposal(tmp_path / "p.json", mutation_class=2, checker="advisory_probe_x")

    def flags(s, cert):
        cert["advisory_flags"] = {"advisory_probe_x": s.C_star == 0}
    ev = evaluator(c, flags)
    rep = screen(tmp_path, hbase, c, prop, evaluator=ev)
    assert rep["ok"], rep
    chk = rep["screen"]["screen"]["checks"]
    assert chk["k_FP_S_plus"] == 0 and chk["k_TP_blind"] == 2 and chk["overwrite_authority"] is False
    assert {is_cand for is_cand, _ in ev.calls} == {True, False}

    def flags_and_m(s, cert):
        flags(s, cert)
        cert["m"] = [0] * 6
    c2 = copy(c, tmp_path / "c2m")
    edit(c2, REG, 'description="p"', 'description="p2"')
    rep = screen(tmp_path, hbase, c2, prop, evaluator=evaluator(c2, flags_and_m))
    assert not rep["ok"] and rep["screen"]["m_changed_samples"] == 10
    assert rep["screen"]["screen"]["checks"]["overwrite_authority"] is True

    def fp(s, cert):
        cert["advisory_flags"] = {"advisory_probe_x": True}
    c3 = copy(c, tmp_path / "c2fp")
    edit(c3, REG, 'description="p"', 'description="p3"')
    rep = screen(tmp_path, hbase, c3, prop, evaluator=evaluator(c3, fp))
    assert not rep["ok"] and rep["screen"]["screen"]["checks"]["k_FP_S_plus"] == 5
    assert not leaks(json.dumps(rep))


OBSERVE = '''"""Deterministic output mapper."""


def map_output(text):
    keep = [line for line in text.splitlines() if "ERROR" in line]
    return "\\n".join(keep)[:2048]
'''


def test_stage0_class3_screen(tmp_path, hbase):
    c = copy(hbase, tmp_path / "c3")
    (c / "src/ssap/checkers/alw/observe.py").write_text(OBSERVE)
    cases = [{"raw": "info\nERROR: net mismatch a\nok", "root_cause_lines": ["ERROR: net mismatch a"]}]
    prop = write_proposal(tmp_path / "p.json", mutation_class=3,
                          class3_entry="ssap.checkers.alw.observe:map_output", class3_self_test=cases)
    rep = screen(tmp_path, hbase, c, prop, evaluator=evaluator(c))
    assert rep["ok"], rep
    s = rep["screen"]
    assert s["deterministic"] and s["m_changed_samples"] == 0 and s["u_changed_samples"] == 0
    assert s["screen"]["checks"]["root_cause_lines_lost"] == 0 and rep["evaluation_path"] == "path1"

    def long_note(smp, cert):
        cert["notes"] = {"lvs_excerpt": "x" * 3000}
    c2 = copy(c, tmp_path / "c3long")
    (c2 / "src/ssap/checkers/alw/observe.py").write_text(OBSERVE + "\n# v2\n")
    rep = screen(tmp_path, hbase, c2, prop, evaluator=evaluator(c2, long_note))
    assert not rep["ok"] and rep["screen"]["notes_over_cap"] == 10
    c3 = copy(c, tmp_path / "c3lossy")
    (c3 / "src/ssap/checkers/alw/observe.py").write_text(OBSERVE.replace('"ERROR" in line', "False"))
    rep = screen(tmp_path, hbase, c3, prop, evaluator=evaluator(c3))
    assert not rep["ok"] and rep["screen"]["screen"]["checks"]["root_cause_lines_lost"] == 1
    c4 = copy(c, tmp_path / "c3nondet")
    (c4 / "src/ssap/checkers/alw/observe.py").write_text(
        OBSERVE.replace('return "\\n".join(keep)[:2048]',
                        'import time\n    return "\\n".join(keep) + str(time.perf_counter_ns())'))
    rep = screen(tmp_path, hbase, c4, prop, evaluator=evaluator(c4))
    assert not rep["ok"] and rep["screen"]["deterministic"] is False
    rep = screen(tmp_path, hbase, c, write_proposal(tmp_path / "p2.json", mutation_class=3,
                                                    class3_entry="os:system", class3_self_test=cases),
                 evaluator=evaluator(c))
    assert rep["reason"] == "class3_transform_error"


# ------------------------------------------------------------------------------------------
# visible-only evidence export
# ------------------------------------------------------------------------------------------

def _w(p: Path, data) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(data, bytes):
        p.write_bytes(data)
    else:
        p.write_text(data if isinstance(data, str) else json.dumps(data))


def make_snapshot(base: Path, task: str, seed: int, marker: str = "") -> Path:
    trial = base / "jobs" / "tagx" / "harbor" / f"job-{task}-s{seed}" / "trial1"
    snap = trial / "agent" / "mcr_snapshot"
    ws = snap / "pre_repair" / "workspace"
    mcr = ws / ".mcr"
    _w(mcr / "gate_report.json", {"gate_triggered": True, "trigger_reasons": ["m_best_not_all_certified"],
                                  "where": "/home/alice/run", "marker": marker})
    _w(mcr / "handoff.json", {"summary": "drc errors remain", "marker": marker})
    _w(mcr / "best" / "cert.json", {"m": [1, 1, 1, 1, 1, 0], "marker": marker})
    _w(mcr / "fork_meta.json", {"granted_repair_budget_sec": 600.0, "granted_repair_turns": 80,
                                "phase_a_elapsed_sec": 9000.0, "repair_eligible": True})
    _w(mcr / "task_prompt.txt", f"Design the layout. {marker}")
    _w(mcr / "trusted_task" / "eda_env.sh", "export PDK=sky130A\n")
    _w(mcr / "trusted_task" / "task" / "circuit.spice", f".subckt top a b\n.ends\n* {marker}\n")
    _w(mcr / "trusted_task" / "task" / "testbench" / "tb.spice", "* tb\n")
    _w(mcr / "checks" / "20260101T000000_a" / "lvs.log", "OLDEST CHECK")
    _w(mcr / "checks" / "20260102T000000_b" / "drc" / "out.log", "A" * 70_000 + "TAILEND")
    _w(mcr / "checks" / "20260103T000000_c" / "notes.txt", f"key upstream-literal-key {marker}")
    _w(mcr / "checks" / "20260103T000000_c" / "big.gds", b"\x00\x06\x00\x02")
    _w(ws / "submission" / "layout.gds", b"\x00\x06\x00\x02\x00\x07GDS")
    _w(snap / "artifact_summary.json", {"submission_gds_sha256": "ab", "marker": marker})
    _w(snap / "post_repair" / "best" / "cert.json", {"m": [1] * 6})
    _w(snap / "repair_trace.json", {"ratchet_promoted": True})
    _w(snap / "phase_b_stream.jsonl", '{"type":"result"}\n')
    _w(trial / "agent" / "claude-code.txt", "x" * 40_000 + f" /localdata/alice/state {marker} END")
    _w(trial / "verifier" / "evaluation" / "metrics.json", {"hidden": "VERIFIER-ONLY"})
    return snap


def evidence_state(tmp_path: Path) -> tuple[Path, list[dict]]:
    state = tmp_path / "state"
    archive = [{"task_id": t, "seed": 0, "snapshot_dir": str(make_snapshot(state, t, 0))} for t in VIS[:2]]
    archive += [{"task_id": t, "seed": 0, "snapshot_dir": str(make_snapshot(state, t, 0, "BLINDMARK"))}
                for t in BL[:2]]
    _w(state / "loop" / "round_1" / "archive.json", archive)
    runs = [{"task_id": a["task_id"], "seed": 0, "Y_star": 0.4, "C_star": 0, "arm": "H_base"}
            for a in archive]
    _w(state / "runs" / "dev_H_base.jsonl", "".join(json.dumps(r) + "\n" for r in runs))
    diag = {"taxonomy": {"runs": [{"task_id": a["task_id"], "seed": 0, "class": "public_mirror_execution_gap"}
                                  for a in archive]},
            "checkpoint_gap": {"runs": [{"task_id": a["task_id"], "seed": 0, "gap": 0.1} for a in archive]},
            "infra_errors": [{"task_id": BL[0], "seed": 1, "category": "oom"}],
            "module_switches": {"capability_bound_tasks": [BL[1], VIS[0]]}}
    _w(state / "diagnostics" / "dev_diagnostics.json", diag)
    return state, archive


def test_export_visible_evidence(tmp_path, monkeypatch):
    monkeypatch.setenv("SSAP_UPSTREAM_KEY", "upstream-literal-key")
    state, archive = evidence_state(tmp_path)
    seen = []
    real = ps._export_run
    monkeypatch.setattr(ps, "_export_run", lambda ex, prefix, snap, run: (seen.append(snap),
                                                                          real(ex, prefix, snap, run)))
    out = tmp_path / "evidence"
    s = ps.export_visible_evidence(state, out, catalog=CATALOG, split=SPLIT, round_id=1)
    assert s["n_visible_runs"] == 2 and s["blind_aggregate"]["n_runs_withheld"] == 2
    assert all(not any(b in str(p) for b in BL) for p in seen) and len(seen) == 2
    texts = {p.relative_to(out).as_posix(): p.read_bytes().decode(errors="replace")
             for p in out.rglob("*") if p.is_file()}
    blob = "\n".join(list(texts) + list(texts.values()))
    assert not leaks(blob) and "BLINDMARK" not in blob and "VERIFIER-ONLY" not in blob
    assert "upstream-literal-key" not in blob and "alice" not in blob and "/home/<USER>" in blob
    r = f"runs/{VIS[0]}__s0"
    for rel in ("run_summary.json", "artifact_summary.json", ".mcr/gate_report.json", ".mcr/handoff.json",
                ".mcr/best/cert.json", ".mcr/task_prompt.txt", ".mcr/trusted_task/eda_env.sh",
                ".mcr/trusted_task/task/circuit.spice", ".mcr/trusted_task/task/testbench/tb.spice",
                "post_repair/best/cert.json", "post_repair/repair_trace.json", "sample_layout.gds",
                "agent_tail.txt", ".mcr/checks/20260103T000000_c/notes.txt"):
        assert f"{r}/{rel}" in texts, rel
    assert not any("20260101T000000_a" in k or k.endswith("big.gds") or "fork_meta" in k for k in texts)
    big = texts[f"{r}/.mcr/checks/20260102T000000_b/drc/out.log"]
    assert "bytes omitted" in big and big.endswith("TAILEND") and len(big) < 66_000
    tail = texts[f"{r}/agent_tail.txt"]
    assert "END" in tail and len(tail) < 2 * ps.TAIL_CAP + 1024 and "/localdata/<USER>" in tail
    diag = json.loads(texts["diagnostics_summary.json"])
    assert {x["task_id"] for x in diag["taxonomy"]["runs"]} == set(VIS[:2])
    assert diag["blind_aggregate"] == {"n_not_passed_runs": 2, "class_counts": {
        "public_mirror_execution_gap": 2}, "n_infra_errors": 1, "n_capability_bound_tasks": 1}
    assert diag["capability_bound_tasks"] == [VIS[0]]
    assert len(json.loads(texts["visible_catalog.json"])["tasks"]) == len(VIS) == 8
    assert json.loads(texts["round_history.json"]) == {"rounds": []}

    with pytest.raises(PrivacyViolation):
        ps._assert_visible(BL[0], CLUSTER, BLIND)
    ex = ps._Exporter(tmp_path / "x", BlindBoundary(BLIND), [])
    with pytest.raises(PrivacyViolation):
        ex.json("bad.json", {"t": BL[0]})


# ------------------------------------------------------------------------------------------
# proposer container
# ------------------------------------------------------------------------------------------

class FakeProxy:
    port = 8791

    def __init__(self):
        self.revoked, self.trials = [], []

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return None

    def new_trial(self, name, idx=None):
        self.trials.append(name)
        return "ssap-trial-TOKEN123"

    def revoke(self, tok):
        self.revoked.append(tok)

    def stats(self, name):
        return {"proxy_requests": 7, "input_tokens": 100, "output_tokens": 50, "cached_tokens": 3,
                "unrecovered_gateway_error": False}


def test_run_proposer_docker(tmp_path, monkeypatch):
    ws = tmp_path / "ws"
    (ws / "evidence").mkdir(parents=True)
    (ws / "PROPOSER_TASK.md").write_text(ps.build_proposer_prompt(1))
    proxy, calls = FakeProxy(), []
    monkeypatch.setattr(ps, "_proxy_from_config", lambda cfg: proxy)
    monkeypatch.setattr(ps, "_chown_back", lambda *a: None)

    def fake_run(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[:2] == ["docker", "run"]:
            (ws / "proposal.json").write_text("{}")
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(ps.subprocess, "run", fake_run)
    cost = tmp_path / "ledgers" / "offline_cost.jsonl"
    res = ps.run_proposer_docker(ws, config(), out_dir=tmp_path / "out", offline_cost_path=cost,
                                 cost_meta={"round": 1, "attempt": 1})
    cmd, kw = calls[0]
    for a in ("--cpus", "4", "--memory", "16g", "--add-host", "host.docker.internal:host-gateway",
              f"{ws.resolve()}:/proposer_ws", f"{ws.resolve()}/evidence:/proposer_ws/evidence:ro",
              "/opt/claude-code/bin/claude", "--permission-mode=bypassPermissions", "-p",
              "--no-session-persistence", "--verbose", "--output-format=stream-json"):
        assert a in cmd, a
    assert cmd[cmd.index("--max-turns") + 1] == "80" and cmd[cmd.index("--model") + 1] == "test-model"
    assert "ssap-trial-TOKEN123" not in " ".join(cmd)
    with_value = [cmd[i + 1] for i, a in enumerate(cmd) if a == "-e" and "=" in cmd[i + 1]]
    assert with_value == [f"PATH={ps.CONTAINER_PATH}", f"PYTHONPATH={ps.CONTAINER_PYTHONPATH}"]
    assert "/opt/analog-layout/python/bin" in ps.CONTAINER_PATH
    assert ps.CONTAINER_PYTHONPATH.startswith("/proposer_ws/candidate_src/src")
    env = kw["env"]
    assert env["PATH"] == __import__("os").environ.get("PATH", "/usr/bin:/bin")   # host PATH finds docker
    from ssap.targets.alw.agent_wrapper import MODEL_SLOTS
    assert all(env[s] == "test-model" for s in MODEL_SLOTS)
    assert env["IS_SANDBOX"] == "1" and env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] == "1"
    assert env["CLAUDE_CONFIG_DIR"] == "/tmp/proposer_sessions"
    assert env["ANTHROPIC_BASE_URL"] == "http://host.docker.internal:8791"
    assert env["ANTHROPIC_API_KEY"] == "ssap-trial-TOKEN123" and "SSAP_UPSTREAM_KEY" not in env
    assert kw["input"] == (ws / "PROPOSER_TASK.md").read_bytes()
    assert proxy.revoked == ["ssap-trial-TOKEN123"] and res["proposal_present"]
    row = json.loads(cost.read_text().splitlines()[-1])
    assert row["kind"] == "proposer" and row["input_tokens"] == 100 and row["round"] == 1
    assert "TOKEN123" not in (tmp_path / "out" / "proposer_command.json").read_text()

    def slow(cmd, **kw):
        calls.append((cmd, kw))
        if cmd[:2] == ["docker", "run"]:
            raise subprocess.TimeoutExpired(cmd, 1)
        return SimpleNamespace(returncode=0)
    monkeypatch.setattr(ps.subprocess, "run", slow)
    res = ps.run_proposer_docker(ws, config(), out_dir=tmp_path / "out2", offline_cost_path=cost)
    assert res["timed_out"] and calls[-1][0][:2] == ["docker", "kill"]
    assert len(cost.read_text().splitlines()) == 2


# ------------------------------------------------------------------------------------------
# Stage 1 Path 1
# ------------------------------------------------------------------------------------------

FORK = {"granted_repair_budget_sec": 600.0, "granted_repair_turns": 80, "repair_eligible": True}


def path1_state(tmp_path, hbase, text="then stop and report the remaining failures.", old="then stop."):
    state = tmp_path / "state"
    archive = [{"task_id": VIS[0], "seed": 0, "fork_meta": FORK, "Y_inc_final": 0.5, "Y_pre": 0.5,
                "snapshot_dir": str(tmp_path / "snapA")},
               {"task_id": VIS[1], "seed": 0, "fork_meta": FORK, "Y_inc_final": 0.6, "Y_pre": None,
                "snapshot_dir": str(tmp_path / "snapB")},
               {"task_id": BL[0], "seed": 0, "fork_meta": FORK, "Y_inc_final": 0.3, "Y_pre": 0.3,
                "snapshot_dir": str(tmp_path / "snapC")}]
    _w(state / "loop" / "round_1" / "archive.json", archive)
    cand_src = copy(hbase, tmp_path / "cand_src")
    edit(cand_src, REPAIR, old, text)
    cand = {"candidate_id": "c1", "mutation_class": 4, "evaluation_path": "path1",
            "cand_src": str(cand_src), "checker": None}
    return state, cand


def fake_gate(divergent_task):
    def gate_fn(which, t):
        sha = "B" if (which == "cand" and t["task_id"] == divergent_task) else "A"
        return {"gate": {"gate_triggered": True}, "triggered": True,
                "reasons": ["m_best_not_all_certified"], "prompt_sha256": sha}
    return gate_fn


def test_stage1_path1_promotes_and_redacts(tmp_path, hbase):
    state, cand = path1_state(tmp_path, hbase)
    calls, verified = [], []

    def repair(t, r, budget):
        calls.append((t["task_id"], r, budget))
        return {"Y_post": 0.8, "ratchet_promoted": True, "promote_reason": "promoted",
                "llm_calls": 5, "verifier_calls": 1}

    def verifier(task, sub, out):
        verified.append((task, str(sub)))
        return SimpleNamespace(Y_star=0.55)
    res = al.run_stage1_path1(config(), state, cand, round_id=1, split=SPLIT, verify_cmd=None,
                              alw_repo=None, gate_fn=fake_gate(VIS[1]), repair_fn=repair,
                              verifier_fn=verifier)
    assert res["n_trials"] == 3 and res["n_divergent"] == 1 and res["promoted"], res
    assert [c[0] for c in calls] == [VIS[1]] * 3 and {c[2] for c in calls} == {600.0}
    assert verified == [(VIS[1], str(tmp_path / "snapB" / "pre_repair" / "workspace" / "submission"))]
    same = [t for t in res["trials"] if not t["divergent"]]
    assert len(same) == 2 and all(t["llm_calls"] == 0 and t["verifier_calls"] == 0 and t["delta_Y"] == 0.0
                                  for t in same)
    assert res["llm_calls"] == 15 and abs(res["sum_delta_Y"] - 0.2) < 1e-9
    assert res["single_atomic_mutation"] and res["mutations"] == ["class4:c1"]
    rep = al.finish_round(state, 1, blind=BLIND, h_inc_before=al.load_h_inc(state),
                          attempts_info=[{"attempt": 1, "stage": "passed"}], cand=cand, result=res)
    h = json.loads((state / "loop" / "h_inc.json").read_text())
    assert h["version_id"] == "H_base+c1" and h["round"] == 1 and rep["h_inc_after"] == "H_base+c1"
    assert "remaining failures" in (state / "loop" / "h_inc_harness" / REPAIR).read_text()
    assert al.inc_source(state, ROOT) == state / "loop" / "h_inc_harness"
    red = (state / "loop" / "round_1" / "round_report_redacted.json").read_text()
    assert not leaks(red) and json.loads(red)["evaluation"]["blind_aggregate"]["n_trials"] == 1
    assert (state / "loop" / "round_1" / "stage1" / "stage1_result.json").is_file()
    assert al.current_round(state) == 2
    nxt = json.loads((state / "loop" / "round_2" / "archive.json").read_text())
    by = {t["task_id"]: t for t in nxt}
    assert abs(by[VIS[1]]["Y_inc_final"] - 0.8) < 1e-9 and by[VIS[1]]["refreshed_by"] == "c1"
    assert by[VIS[1]]["inc_gate_signature"][-1] == "prompt:B"
    assert by[VIS[0]]["Y_inc_final"] == 0.5 and "refreshed_by" not in by[VIS[0]]
    assert ps.h_inc_archive_path(state, 2) == state / "loop" / "round_2" / "archive.json"


def test_stage1_path1_unresolved_y_pre_blocks_promotion(tmp_path, hbase):
    state, cand = path1_state(tmp_path, hbase)
    res = al.run_stage1_path1(config(), state, cand, round_id=1, split=SPLIT, verify_cmd=None,
                              alw_repo=None, gate_fn=fake_gate(VIS[1]),
                              repair_fn=lambda *a: {"Y_post": 0.9, "ratchet_promoted": True},
                              verifier_fn=lambda *a: SimpleNamespace(Y_star=None))
    assert not res["promoted"] and res["reason"] == "unresolved_infra_error"
    al.finish_round(state, 1, blind=BLIND, h_inc_before=al.load_h_inc(state), attempts_info=[],
                    cand=cand, result=res)
    assert not (state / "loop" / "h_inc_harness").exists()
    assert json.loads((state / "loop" / "h_inc.json").read_text())["version_id"] == "H_base"


def real_snapshot(base: Path, name: str) -> str:
    ws = base / name / "pre_repair" / "workspace"
    _w(ws / ".mcr" / "fork_meta.json", FORK | {"phase_a_elapsed_sec": 9000.0})
    _w(ws / ".mcr" / "handoff.json", {"summary": "s"})
    (ws / "submission").mkdir(parents=True)
    return str(base / name)


@pytest.mark.parametrize("old,text,divergent", [
    ('WORKSPACE = "/alb-workspace"', 'WORKSPACE = "/alb-workspace"  # comment only', 0),
    ("then stop.", "then stop and summarise.", 2)])
def test_stage1_path1_real_terminal_gate(tmp_path, hbase, old, text, divergent):
    state, cand = path1_state(tmp_path, hbase, text, old)
    arch = [{"task_id": t, "seed": 0, "fork_meta": FORK, "Y_inc_final": 0.4, "Y_pre": 0.4,
             "snapshot_dir": real_snapshot(tmp_path, t)} for t in VIS[:2]]
    _w(state / "loop" / "round_1" / "archive.json", arch)
    res = al.run_stage1_path1(config(), state, cand, round_id=1, split=SPLIT, verify_cmd=None,
                              alw_repo=None, dry_run=True)
    assert res["n_trials"] == 2 and res["n_divergent"] == divergent
    v = al.local_gate_view(state / "loop" / "round_1" / "stage1" / "inc_bundle", arch[0],
                           tmp_path / "gt", harness_src=al.inc_source(state, ROOT))
    assert v["triggered"] and "m_best_not_all_certified" in v["reasons"] and len(v["prompt_sha256"]) == 64


# ------------------------------------------------------------------------------------------
# Stage 1 Path 2
# ------------------------------------------------------------------------------------------

def _run(task, seed, y, arm="H_star", infra=False):
    r = {"run_id": f"{arm}-{task}-s{seed}", "task_id": task, "seed": seed, "arm": arm, "Y_star": y,
         "C_star": 0, "d_star": {}, "proxy_requests": 10, "gateway_wait_seconds": 1.0, "wall_sec": 100.0,
         "infra_error": infra, "m": [1] * 6}
    return r


def path2_fakes(y_cand=0.7, infra_tasks=(), retry_ok=True):
    staged, plan = {}, []

    def stage_fn(cfg, state, *, arm, tasks, seeds, tag, timeout_s, harness_root):
        assert arm == "H_star" and harness_root is not None
        plan.append((tag, list(tasks)))
        staged[tag] = [(t, s) for t in tasks for s in seeds]
        return staged[tag]

    def launch_fn(cfg, jobs, template, state, tag):
        assert template == "harbor run {job_config}"
        return []

    def harvest_fn(job_dir):
        tag = Path(job_dir).name
        retry = "-retry-" in tag
        return [Harvested(run=_run(t, s, None if (t in infra_tasks and not (retry and retry_ok)) else y_cand,
                                   infra=t in infra_tasks and not (retry and retry_ok)),
                          job_root=Path(job_dir)) for t, s in staged.get(tag, [])]
    return stage_fn, launch_fn, harvest_fn, plan


def test_stage1_path2_nested_batches_and_promotion(tmp_path, hbase):
    state = tmp_path / "state"
    _w(state / "runs" / "dev_H_base.jsonl", "".join(json.dumps(_run(t, 0, 0.5, "H_base")) + "\n" for t in DEV))
    cand = {"candidate_id": "c2", "mutation_class": 2, "evaluation_path": "path2",
            "cand_src": str(copy(hbase, tmp_path / "cand_src"))}
    st, la, hv, plan = path2_fakes(infra_tasks=(VIS[0],))
    res = al.run_stage1_path2(config(), state, cand, round_id=1, split=SPLIT, blind=BLIND,
                              harbor_cmd="harbor run {job_config}", stage_fn=st, launch_fn=la,
                              harvest_fn=hv)
    tags = [p[0] for p in plan]
    assert plan[0][1] == VIS[:2] and all(t in BLIND.visible_task_ids for t in plan[0][1])
    assert "-retry-" in tags[1] and plan[1][1] == [VIS[0]]
    assert sorted(plan[2][1]) == sorted(set(DEV) - set(VIS[:2])) and len(plan) == 3
    assert res["B1"]["unresolved_infra"] == 0 and res["promoted"], res
    assert res["path"] == "path2" and res["leakage_audit"]["untriggered_tasks"] == []   # backfilled
    assert res["n_B1"] + res["n_B2"] == 12
    runs = (state / "loop" / "h_inc_runs.jsonl").read_text().splitlines()
    assert len(runs) == 12 and (state / "loop" / "round_2" / "archive.json").is_file()
    assert len((state / "bank" / "b_dev.jsonl").read_text().splitlines()) == 12
    assert ps.h_inc_runs_path(state) == state / "loop" / "h_inc_runs.jsonl"

    st, la, hv, plan = path2_fakes(infra_tasks=(VIS[1],), retry_ok=False)
    res = al.run_stage1_path2(config(), tmp_path / "s2", cand, round_id=1, split=SPLIT, blind=BLIND,
                              harbor_cmd="harbor run {job_config}", stage_fn=st, launch_fn=la,
                              harvest_fn=hv)
    assert not res["promoted"] and res["reason"] == "b1_infra_or_crash" and len(plan) == 2
    st, la, hv, plan = path2_fakes(y_cand=0.4)
    res = al.run_stage1_path2(config(), tmp_path / "s3", cand, round_id=1, split=SPLIT, blind=BLIND,
                              harbor_cmd="harbor run {job_config}", stage_fn=st, launch_fn=la,
                              harvest_fn=hv, inc_job_dir=str(tmp_path / "incjob"))
    assert res["missing_tasks"] and not res["promoted"]                  # no H_inc runs harvested
    with pytest.raises(ValueError):
        al.run_stage1_path2(config(), tmp_path / "s4", cand, round_id=1, split=SPLIT, blind=BLIND,
                            harbor_cmd=None)


# ------------------------------------------------------------------------------------------
# alw-loop --auto / --propose / --screen-candidate / --eval-candidate
# ------------------------------------------------------------------------------------------

def loop_cli(capsys, state, *extra):
    rc = cli.main(["alw-loop", "--config", str(CFG), "--state", str(state), "--model", "test-model",
                   *extra])
    cap = capsys.readouterr()
    out = cap.out
    return rc, (json.loads(out) if out.strip().startswith("{") else out + cap.err)


def e2e_state(tmp_path) -> Path:
    state = tmp_path / "state"
    arch = [{"task_id": t, "seed": 0, "fork_meta": FORK, "Y_inc_final": 0.4, "Y_pre": 0.4,
             "snapshot_dir": real_snapshot(tmp_path / "snaps", t)} for t in VIS[:2] + BL[:1]]
    _w(state / "loop" / "round_1" / "archive.json", arch)
    write_bank(state / "bank" / "b_dev.jsonl")
    return state


def fake_proposer(script):
    seen = []

    def run(ws, cfg, *, out_dir, max_turns, offline_cost_path, cost_meta):
        ws = Path(ws)
        seen.append({"task": (ws / "PROPOSER_TASK.md").read_text(), "max_turns": max_turns, **cost_meta})
        script[len(seen) - 1](ws / "candidate_src")
        (ws / "proposal.json").write_text(json.dumps({"candidate_id": f"c{len(seen)}",
                                                      "mutation_class": 4, "evaluation_path": "path1"}))
        ps.append_offline_cost(offline_cost_path, {"kind": "proposer", **cost_meta})
        return {"returncode": 0, "proposal_present": True}
    run.seen = seen
    return run


def test_alw_loop_auto_dry_run(tmp_path, capsys, monkeypatch):
    monkeypatch.setenv("SSAP_UPSTREAM_KEY", "upstream-literal-key")
    state, _ = evidence_state(tmp_path)
    rc, out = loop_cli(capsys, state, "--auto", "--dry-run")
    assert rc == 0 and out["round"] == 1 and out["propose"][0]["proposer"] == {"planned": True}
    assert out["propose"][0]["evidence"]["n_visible_runs"] == 2
    ws = state / "loop" / "round_1" / "attempt_1" / "proposer_ws"
    task = (ws / "PROPOSER_TASK.md").read_text()
    assert not [w for w in ps.FORBIDDEN_HINT_WORDS if w in task]
    assert ps.tree_hash(ws / "harness_src") == ps.tree_hash(ws / "candidate_src")
    assert not (ws / "harness_src" / "src" / "ssap" / "checkers" / "alw" / "pex.py").exists()
    assert not (state / "loop" / "h_inc.json").exists() and not leaks(json.dumps(out))
    rc, out = loop_cli(capsys, state, "--dry-run")                    # legacy loop untouched
    assert rc == 0 and out["dry_run"] and out["rounds"][0]["round"] == 1 and "propose" not in out


def test_alw_loop_auto_retries_with_feedback_then_evaluates(tmp_path, capsys, monkeypatch):
    state = e2e_state(tmp_path)
    prop = fake_proposer([
        lambda c: edit(c, "src/ssap/kernel/ratchet.py", '"""', '"""edited '),
        lambda c: edit(c, REPAIR, 'WORKSPACE = "/alb-workspace"', 'WORKSPACE = "/alb-workspace"  # c'),
    ])
    monkeypatch.setattr(ps, "run_proposer_docker", prop)
    monkeypatch.setattr(ps, "_default_smoke", smoke_ok)
    rc, out = loop_cli(capsys, state, "--auto", "--max-propose-attempts", "3",
                       "--proposer-max-turns", "40")
    assert rc == 0, out
    assert [s["ok"] for s in out["screen"]] == [False, True] and len(prop.seen) == 2
    assert out["screen"][0]["reason"] == "kernel_sha256_changed"
    assert "Previous attempt" not in prop.seen[0]["task"] and "kernel_sha256_changed" in prop.seen[1]["task"]
    assert prop.seen[1]["max_turns"] == 40 and prop.seen[1]["attempt"] == 2
    rep = out["round_report"]
    assert rep["evaluation"]["path"] == "path1" and rep["evaluation"]["n_divergent"] == 0
    assert rep["evaluation"]["promoted"] is False and rep["evaluation"]["llm_calls"] == 0
    assert [a["stage"] for a in rep["attempts"]] == ["kernel", "passed"]
    assert not leaks(json.dumps(out))
    assert (state / "loop" / "round_1" / "round_report_redacted.json").is_file()
    assert json.loads((state / "loop" / "h_inc.json").read_text())["round"] == 1
    kinds = [json.loads(l)["kind"] for l in (state / "ledgers" / "offline_cost.jsonl").read_text().splitlines()]
    assert kinds.count("proposer") == 2 and kinds.count("stage0_screen") == 2


def test_alw_loop_split_stages(tmp_path, capsys, monkeypatch):
    state = e2e_state(tmp_path)
    prop = fake_proposer([lambda c: edit(c, REPAIR, "then stop.", "then stop and summarise.")])
    monkeypatch.setattr(ps, "run_proposer_docker", prop)
    monkeypatch.setattr(ps, "_default_smoke", smoke_ok)
    rc, out = loop_cli(capsys, state, "--propose")
    assert rc == 0 and "screen" not in out and len(prop.seen) == 1
    rc, out = loop_cli(capsys, state, "--screen-candidate")
    assert rc == 0 and out["screen"][0]["ok"]
    cand = json.loads((state / "loop" / "round_1" / "candidate.json").read_text())
    assert cand["evaluation_path"] == "path1" and cand["mutation_class"] == 4
    rc, out = loop_cli(capsys, state, "--eval-candidate")                # divergent: needs verifier
    assert rc == 2 and "--verify-cmd" in str(out)
    rc, out = loop_cli(capsys, state, "--eval-candidate", "--dry-run")
    assert rc == 0 and out["round_report"]["evaluation"]["n_divergent"] == 3
    assert out["round_report"]["evaluation"]["blind_aggregate"]["n_trials"] == 0   # dry run: no trials
    _w(state / "loop" / "h_inc_harness" / "container" / "alw" / "settings.json", "{}")
    rc, out = loop_cli(capsys, state, "--eval-candidate", "--dry-run")
    assert rc == 2                                                        # incumbent changed since screen


# ------------------------------------------------------------------------------------------
# server-runtime follow-ups (#13)
# ------------------------------------------------------------------------------------------

def test_gate_view_runs_in_docker_when_image_present(tmp_path, hbase, monkeypatch):
    snap = real_snapshot(tmp_path, "snapD")
    trusted = Path(snap) / "pre_repair" / "workspace" / ".mcr" / "trusted_task"
    _w(trusted / "eda_env.sh", "# env\n")
    bundle = al._bundle(hbase, tmp_path / "bundle", config())
    real, calls = subprocess.run, []

    def fake(cmd, **kw):
        if cmd[0] != "docker":
            return real(cmd, **kw)
        calls.append((cmd, kw))
        if "--gate" in cmd:
            out = json.dumps({"gate_triggered": True, "trigger_reasons": ["m_best_not_all_certified"]})
        else:
            assert json.loads(kw["input"])["fork"]["granted_repair_budget_sec"] == 600.0
            out = json.dumps("f" * 64)
        return SimpleNamespace(returncode=0, stdout=out, stderr="")
    monkeypatch.setattr(al.subprocess, "run", fake)
    v = al.gate_view(bundle, {"snapshot_dir": snap}, tmp_path / "gw", harness_src=hbase, image="img:1")
    assert v["triggered"] and v["prompt_sha256"] == "f" * 64 and len(calls) == 2
    gate_cmd, sha_cmd = calls[0][0], calls[1][0]
    for a in ("--network", "none", "--cpus", "2", "--memory", "4g", f"{bundle.resolve()}:/opt/harness:ro",
              "img:1", "/opt/harness/bin/mcr_check", "--gate", "9000.0"):
        assert a in gate_cmd, a
    assert any(x.endswith(":/alb-workspace/.mcr/trusted_task:ro") for x in gate_cmd)
    assert al.PROMPT_SHA in sha_cmd and sha_cmd[-1] == "/opt/harness/python"
    monkeypatch.setattr(al.shutil, "which", lambda name: None)
    assert al.gate_image(config()) is None


def test_inner_cert_keeps_notes_and_terminal_m(tmp_path, hbase, monkeypatch):
    from ssap.bank import phase2
    repo = tmp_path / "alw"
    _w(repo / "tasks" / DEV[0] / "environment" / "task" / "circuit.spice", "* c\n")
    _w(repo / "tasks" / DEV[0] / "environment" / "eda_env.sh", "# env\n")
    gds = tmp_path / "x.gds"
    gds.write_bytes(b"\x00\x06")
    monkeypatch.setattr(phase2.shutil, "which", lambda name: "/usr/bin/docker")
    outputs, calls = [], []

    def fake(cmd, **kw):
        calls.append(cmd)
        return SimpleNamespace(returncode=0, stdout=json.dumps(outputs.pop(0)), stderr="")
    monkeypatch.setattr(phase2.subprocess, "run", fake)
    fn = phase2.make_docker_inner_cert_fn(None, hbase, repo, tmp_path / "w", "img:1")
    outputs.append({"cert": {"m": [1] * 6, "u": [1] * 6, "notes": {"lvs": "ok"},
                             "terminal_m": {"m_extra": 1}}, "mirror_names": MIRRORS})
    res = fn(DEV[0], "top", gds, tmp_path / "c1")
    assert res["notes"] == {"lvs": "ok"} and res["terminal_m"] == {"m_extra": 1} and len(calls) == 1
    outputs.append({"cert": {"m": [1] * 6}, "terminal_m": {"m_extra": 0}})
    assert fn(DEV[0], "top", gds, tmp_path / "c2")["terminal_m"] == {"m_extra": 0}
    fb = phase2.make_docker_inner_cert_fn(None, hbase, repo, tmp_path / "w2", "img:1", terminal_fallback=True)
    outputs += [{"cert": {"m": [1] * 6}}, {"gate_triggered": False, "terminal_m": {"m_extra": 1}}]
    res = fb(DEV[0], "top", gds, tmp_path / "c3")
    assert res["terminal_m"] == {"m_extra": 1} and "mcr_promote" in calls[-1][-1]
    assert "mcr_check --gate" in calls[-1][-1] and "--network" in calls[-1]
    outputs.append({"cert": {"m": [1] * 6}})
    assert "terminal_m" not in fn(DEV[0], "top", gds, tmp_path / "c4")       # no fallback requested
    assert "terminal_m" in ps.build_proposer_prompt(1)


def test_propose_and_screen_retry_without_auto(tmp_path, capsys, monkeypatch):
    state = e2e_state(tmp_path)
    prop = fake_proposer([
        lambda c: edit(c, "src/ssap/kernel/ratchet.py", '"""', '"""edited '),
        lambda c: edit(c, REPAIR, 'WORKSPACE = "/alb-workspace"', 'WORKSPACE = "/alb-workspace"  # c'),
    ])
    monkeypatch.setattr(ps, "run_proposer_docker", prop)
    monkeypatch.setattr(ps, "_default_smoke", smoke_ok)
    rc, out = loop_cli(capsys, state, "--propose", "--screen-candidate")
    assert rc == 0 and [s["ok"] for s in out["screen"]] == [False, True] and len(prop.seen) == 2
    assert "round_report" not in out and (state / "loop" / "round_1" / "candidate.json").is_file()


def test_harvest_sets_hook_triggered(tmp_path):
    from ssap.targets.alw.harvest import harvest_trial
    job = tmp_path / "job"
    for name, cert in (("j-star", True), ("j-none", False), ("j-raw", True)):
        snap = job / "harbor" / name / "trial" / "agent" / "mcr_snapshot"
        _w(snap / "artifact_summary.json", {"ratchet_promoted": False})
        if cert:
            _w(snap / "pre_repair" / "workspace" / ".mcr" / "best" / "cert.json", {"m": [1] * 6})
    star = harvest_trial(job, {"job_name": "j-star", "arm": "H_star", "task_id": DEV[0], "seed": 0})
    none = harvest_trial(job, {"job_name": "j-none", "arm": "H_base", "task_id": DEV[0], "seed": 0})
    raw = harvest_trial(job, {"job_name": "j-raw", "arm": "H_raw", "task_id": DEV[0], "seed": 0})
    assert star.run["hook_triggered"] is True and none.run["hook_triggered"] is False
    assert "hook_triggered" not in raw.run
