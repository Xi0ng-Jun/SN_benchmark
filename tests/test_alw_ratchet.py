"""Milestone 2: generic MCR kernel -- ratchet order, atomic promotion, direct-write rescue,
handoff, watermarks / gate, snapshots. Deterministic; no EDA tools, no network."""
from __future__ import annotations

import json
import os
import re
from pathlib import Path

import pytest

from ssap.checkers.alw import alw_registry
from ssap.kernel import handoff, snapshot
from ssap.kernel.promote import (Workspace, draft_needs_evaluation, handle_direct_submission_write,
                                 load_best, promote, sha256_file, tree_sha256)
from ssap.kernel.ratchet import Cert, TierSpec, certify, decide, is_strictly_better
from ssap.kernel.watermarks import Watermarks, compute_gate

ROOT = Path(__file__).resolve().parents[1]
SPEC = TierSpec.from_lists([[0], [1], [2, 3, 4, 5]], [0.0, 0.0, 0.0, 1e-4, 0.0, 0.0],
                           ["m_gds", "m_lvs", "m_shape", "m_fill", "m_morph", "m_drc"])
ALL_PASS = [0.0, 0.0, 2.0, 0.30, 0.0, 0.0]


def C(raw, present=True, **kw) -> Cert:
    return Cert.build(present, raw, SPEC, **kw)


# ------------------------------------------------------------------------------------------
# ratchet order
# ------------------------------------------------------------------------------------------

def test_certify_saturates_certified_margins_and_keeps_uncertified_continuous():
    m, u = certify([0.0, -1.0, 2.5, 0.05, None, -7.0], SPEC.eps)
    assert m == (1, 0, 1, 1, 0, 0)
    assert u[:4] == (0.0, -1.0, 0.0, 0.0) and u[4] == float("-inf") and u[5] == -7.0
    m, _ = certify([0.0, 0.0, 0.0, 5e-5, 0.0, 0.0], SPEC.eps)       # 5e-5 < eps_4
    assert m[3] == 0


def test_tier0_bootstrap_weak_dominance():
    uncertified = C([-1.0, None, None, None, None, None])
    assert is_strictly_better(uncertified, None, SPEC) == (True, "tier0_bootstrap")
    assert is_strictly_better(uncertified, C([None] * 6, present=False), SPEC)[0]
    # an absent / empty candidate never replaces a present best, even an uncertified one
    assert is_strictly_better(C([None] * 6, present=False), uncertified, SPEC) == (False, "tier0_regression")
    assert is_strictly_better(C([None] * 6, present=False), None, SPEC)[0] is False


def test_tier1_tier2_lexicographic():
    gds_only = C([0.0, -1.0, 2.0, None, None, 0.0])
    gds_bad = C([-1.0, None, None, None, None, None])
    lvs = C([0.0, 0.0, -3.0, -0.05, -1.0, -40.0])        # LVS passes, all of tier 3 fails
    assert decide(gds_only, gds_bad, SPEC).reason == "tier_lex"
    d = decide(lvs, gds_only, SPEC)
    assert d.promote and d.tier == 2                      # tier 2 dominates tier-3 losses
    assert decide(gds_only, lvs, SPEC) == decide(gds_only, lvs, SPEC)
    assert not decide(gds_only, lvs, SPEC).promote and decide(gds_only, lvs, SPEC).reason == "tier_regression"


def test_tier3_pareto_never_overwrites_incomparable():
    drc_ok_fill_bad = C([0.0, 0.0, 2.0, -0.02, 0.0, 0.0])     # DRC=1, E=0
    drc_bad_fill_ok = C([0.0, 0.0, 2.0, 0.20, 0.0, -3.0])     # DRC=0, E=1
    assert decide(drc_ok_fill_bad, drc_bad_fill_ok, SPEC).reason == "tier_incomparable"
    assert decide(drc_bad_fill_ok, drc_ok_fill_bad, SPEC).reason == "tier_incomparable"
    assert decide(C(ALL_PASS), drc_ok_fill_bad, SPEC).reason == "tier_pareto"


def test_rule2b_ge_lets_post_sim_fix_with_slightly_larger_bbox_promote():
    best = C(ALL_PASS)
    larger_bbox = C([0.0, 0.0, 1.9, 0.27, 0.0, 0.0])          # q and rho slightly worse, still certified
    d = decide(larger_bbox, best, SPEC)
    assert d.promote and d.reason == "same_tier_margin" and d.thin_margin


def test_rule2b_uncertified_margin_must_improve_without_regression():
    best = C([0.0, 0.0, 2.0, 0.2, 0.0, -5.0])
    assert decide(C([0.0, 0.0, 2.0, 0.2, 0.0, -2.0]), best, SPEC).reason == "same_tier_margin"
    assert decide(C([0.0, 0.0, 2.0, 0.2, 0.0, -5.0]), best, SPEC).reason == "same_tier_no_improvement"
    assert decide(C([0.0, 0.0, 2.0, 0.2, 0.0, -9.0]), best, SPEC).reason == "same_tier_margin_regression"


def test_rule2a_quality_mirror():
    best = C(ALL_PASS, y_hat_mir=0.5)
    better = C([0.0, 0.0, 1.0, 0.11, 0.0, 0.0], y_hat_mir=0.7)
    d = decide(better, best, SPEC)
    assert d.promote and d.reason == "same_tier_quality_mirror" and d.thin_margin
    assert decide(C(ALL_PASS, y_hat_mir=0.4), best, SPEC).reason == "same_tier_quality_lower"
    assert decide(C(ALL_PASS, y_hat_mir=0.5), best, SPEC).reason == "same_tier_margin"


def test_class2_advisory_flags_have_zero_overwrite_authority():
    best = C([0.0, 0.0, 2.0, 0.2, 0.0, -5.0])
    for cand_raw in (ALL_PASS, [0.0, 0.0, 2.0, 0.2, 0.0, -5.0], [0.0, -1.0, 2.0, None, None, 0.0]):
        plain = decide(C(cand_raw), best, SPEC)
        flagged = decide(C(cand_raw, advisory_flags={"advisory_aspect_gt_3": True,
                                                     "advisory_missing_src_py": True}), best, SPEC)
        assert plain == flagged
    assert C(ALL_PASS, advisory_flags={"advisory_aspect_gt_3": True}).m(SPEC) == (1,) * 6
    reg = alw_registry()
    assert all(s.klass == 1 and s.overwrite_authority for s in reg.mirror())
    assert all(not s.overwrite_authority for s in reg.advisories())
    assert [s.name for s in reg.mirror()] == list(SPEC.names)


def test_timeout_is_no_decision():
    best = C([0.0, -1.0, 2.0, None, None, 0.0])
    assert is_strictly_better(C(ALL_PASS, check_timed_out=True), best, SPEC) == (
        False, "check_timeout_no_decision")


# ------------------------------------------------------------------------------------------
# promotion on a real file system
# ------------------------------------------------------------------------------------------

def _evaluator(path: Path, aux):
    """Fake mirror: the artifact is JSON ``{"raw_u": [...], "timeout": bool}``."""
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return Cert.build(Path(path).is_file() and Path(path).stat().st_size > 0, [-1.0] + [None] * 5, SPEC)
    return Cert.build(True, doc["raw_u"], SPEC, check_timed_out=doc.get("timeout", False),
                      advisory_flags=doc.get("advisory", {}))


def _put(path: Path, raw, **extra) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"raw_u": raw, **extra}))
    return path


@pytest.fixture()
def ws(tmp_path) -> Workspace:
    w = Workspace(tmp_path / "workspace", "layout.gds", "src")
    (w.root / "submission").mkdir(parents=True)
    w.draft_dir.mkdir(parents=True)
    return w


def _promote(ws, art, aux=None):
    return promote(ws, art, aux, _evaluator, SPEC)


def test_bootstrap_never_reverts_regular_file_to_empty(ws):
    r = _promote(ws, _put(ws.draft_artifact, [-1.0, None, None, None, None, None]))
    assert r.promoted and r.reason == "tier0_bootstrap"
    first = sha256_file(ws.submission_artifact)
    ws.draft_artifact.write_bytes(b"")
    r = _promote(ws, ws.draft_artifact)
    assert not r.promoted and r.reason == "tier0_regression"
    assert sha256_file(ws.submission_artifact) == first and ws.submission_artifact.stat().st_size > 0


def test_promotion_writes_best_and_submission_atomically(ws):
    r = _promote(ws, _put(ws.draft_artifact, [0.0, 0.0, 2.0, 0.2, 0.0, -3.0]))
    assert r.promoted and r.submission_written
    best = load_best(ws)
    assert best.artifact_sha256 == sha256_file(ws.submission_artifact) == sha256_file(ws.draft_artifact)
    cert = json.loads((ws.best_dir / "cert.json").read_text())
    assert cert["m"] == [1, 1, 1, 1, 1, 0] and cert["raw_u"][5] == -3.0 and cert["u"][2] == 0.0
    leftovers = [p.name for p in ws.submission_dir.iterdir() if p.name not in ("layout.gds",)]
    assert leftovers == []                                 # no temp files left in submission/
    assert not any(ws.staging_dir.iterdir())
    # a worse candidate leaves everything untouched; the draft is never rolled back
    worse = _put(ws.draft_artifact, [0.0, -1.0, 2.0, None, None, 0.0])
    r = _promote(ws, worse)
    assert not r.promoted and r.reason == "tier_regression"
    assert sha256_file(ws.submission_artifact) == best.artifact_sha256
    assert json.loads(worse.read_text())["raw_u"][1] == -1.0


def test_timeout_keeps_best(ws):
    _promote(ws, _put(ws.draft_artifact, [0.0, -1.0, 2.0, None, None, 0.0]))
    before = sha256_file(ws.submission_artifact)
    r = _promote(ws, _put(ws.draft_artifact, ALL_PASS, timeout=True))
    assert r.reason == "check_timeout_no_decision" and sha256_file(ws.submission_artifact) == before
    assert draft_needs_evaluation(ws)                     # no decision -> still pending


def test_nonempty_src_tree_swap_and_preservation(ws):
    src1 = ws.draft_aux
    (src1 / "pkg").mkdir(parents=True)
    (src1 / "gen.py").write_text("print(1)\n")
    (src1 / "pkg" / "old_only.py").write_text("x = 1\n")
    _promote(ws, _put(ws.draft_artifact, [0.0, -1.0, 2.0, None, None, -4.0]), src1)
    assert (ws.submission_aux / "pkg" / "old_only.py").is_file()
    src2 = ws.root / "work" / "v2" / "src"
    src2.mkdir(parents=True)
    (src2 / "gen.py").write_text("print(2)\n")
    r = _promote(ws, _put(ws.root / "work/v2/layout.gds", [0.0, -1.0, 2.0, None, None, -1.0]), src2)
    assert r.promoted
    assert (ws.submission_aux / "gen.py").read_text() == "print(2)\n"
    assert not (ws.submission_aux / "pkg").exists()       # full replacement of a non-empty tree
    assert tree_sha256(ws.submission_aux) == tree_sha256(ws.best_dir / "src") == tree_sha256(src2)
    assert not [p for p in ws.submission_dir.iterdir() if ".old." in p.name or ".new." in p.name]
    # a candidate without src/ keeps the certified src/
    r = _promote(ws, _put(ws.root / "work/v3/layout.gds", [0.0, -1.0, 2.0, None, None, 0.0]))
    assert r.promoted and (ws.submission_aux / "gen.py").read_text() == "print(2)\n"
    assert (ws.best_dir / "src" / "gen.py").is_file()


def test_symlink_candidate_rejected(ws):
    target = _put(ws.root / "elsewhere.gds", ALL_PASS)
    ws.draft_artifact.symlink_to(target)
    r = _promote(ws, ws.draft_artifact)
    assert not r.promoted and r.reason == "rejected_symlink"
    assert not ws.submission_artifact.exists()


def test_last_evaluated_draft_dedup(ws):
    art = _put(ws.draft_artifact, [0.0, -1.0, 2.0, None, None, -2.0])
    assert draft_needs_evaluation(ws)
    _promote(ws, art)
    assert not draft_needs_evaluation(ws)
    _put(art, [0.0, -1.0, 2.0, None, None, -9.0])          # a rejected draft is also recorded
    _promote(ws, art)
    rec = json.loads(ws.last_draft_path.read_text())
    assert rec["promoted"] is False and rec["sha256"] == sha256_file(art) and "mtime_ns" in rec
    assert not draft_needs_evaluation(ws)
    (ws.draft_aux).mkdir()
    (ws.draft_aux / "a.py").write_text("pass\n")
    assert draft_needs_evaluation(ws)


def test_direct_write_rescue_versioned_snapshots_and_restore(ws):
    _promote(ws, _put(ws.draft_artifact, [0.0, 0.0, 2.0, 0.2, 0.0, -2.0]))
    certified = sha256_file(ws.submission_artifact)
    assert handle_direct_submission_write(ws, _evaluator, SPEC) is None
    notices = []
    for k, raw in enumerate(([0.0, -1.0, 2.0, None, None, 0.0], [-1.0, None, None, None, None, None])):
        _put(ws.submission_artifact, raw, n=k)
        out = handle_direct_submission_write(ws, _evaluator, SPEC)
        assert out["rescued"] and not out["promoted"] and out["restored"]
        assert sha256_file(ws.submission_artifact) == certified
        notices.append(out)
    snaps = sorted(p.name for p in ws.direct_snapshot_dir.iterdir())
    assert len(snaps) == 2 and re.fullmatch(r"0001_[0-9a-f]{8}", snaps[0]) and snaps[1].startswith("0002_")
    for out, name in zip(notices, snaps):
        assert name in out["additionalContext"] and "restored" in out["additionalContext"]
        saved = json.loads((ws.direct_snapshot_dir / name / "layout.gds").read_text())
        assert "n" in saved                                # the agent's write itself is preserved
    # a better direct write is certified instead of reverted
    _put(ws.submission_artifact, [0.0, 0.0, 2.0, 0.2, 0.0, 0.0])
    out = handle_direct_submission_write(ws, _evaluator, SPEC)
    assert out["promoted"] and load_best(ws).artifact_sha256 == sha256_file(ws.submission_artifact)
    # a symlinked submission is restored to the certified best
    ws.submission_artifact.unlink()
    ws.submission_artifact.symlink_to(ws.root / "nowhere.gds")
    out = handle_direct_submission_write(ws, _evaluator, SPEC)
    assert out["restored"] and not ws.submission_artifact.is_symlink()


# ------------------------------------------------------------------------------------------
# handoff, watermarks, gate, snapshots, kernel hygiene
# ------------------------------------------------------------------------------------------

def test_handoff_roundtrip_and_deterministic_truncation(tmp_path):
    rec = {"top_cell": "ota", "status": "iterating", "known_blockers": ["x" * 3000] * 10,
           "next_steps": [f"step {i}" for i in range(400)]}
    a = handoff.write(tmp_path / "h.json", rec)
    b = handoff.truncate({"schema_version": 1, **rec})
    assert a == b and len((tmp_path / "h.json").read_bytes()) <= handoff.MAX_BYTES
    assert a["_truncated"]["dropped_items"] > 0
    assert handoff.validate(a) == []
    assert handoff.read(tmp_path / "h.json") == a
    assert "status must be one of" in " ".join(handoff.validate({"schema_version": 1, "status": "?"}))


def test_watermarks_fork_meta_and_gate():
    cfg = json.loads((ROOT / "configs/alw/config.server.json").read_text())["watermarks"]
    wm = Watermarks.from_mapping(cfg)
    assert (wm.t_warn_sec, wm.t_soft_sec, wm.tail_buffer_sec) == (8400, 9900, 120)
    assert wm.delta_s_rep_turns == 80 and wm == Watermarks()      # config locks the defaults
    fm = wm.fork_meta(9900.0)
    assert fm["granted_repair_budget_sec"] == 780.0 and fm["repair_eligible"]
    assert fm["granted_repair_turns"] == 80
    assert not wm.warn_due(8399.9) and wm.warn_due(8400.0)
    assert wm.fork_meta(4000)["granted_repair_budget_sec"] == 900.0
    late = wm.fork_meta(10700)
    assert late["remaining_sec"] == 100 and not late["repair_eligible"]
    assert wm.fork_meta(9900.0) == wm.fork_meta(9900.0004)  # deterministic rounding
    g = compute_gate(fm, [1, 1, 1, 1, 1, 1], unevaluated_draft=False, advisory_triggers=[])
    assert not g["gate_triggered"] and g["trigger_reasons"] == []
    g = compute_gate(fm, [1, 1, 1, 1, 1, 1], unevaluated_draft=False,
                     advisory_triggers=["advisory_aspect_gt_3"])
    assert g["gate_triggered"] and g["trigger_reasons"] == ["advisory:advisory_aspect_gt_3"]
    g = compute_gate(late, [1, 0, 1, 0, 0, 0], unevaluated_draft=True)
    assert not g["gate_triggered"] and len(g["trigger_reasons"]) == 2
    with pytest.raises(ValueError):
        Watermarks(t_warn_sec=9950, t_soft_sec=9900)


def test_pre_repair_snapshot_full_tree_without_recursion_or_transients(tmp_path):
    w = tmp_path / "w"
    (w / "work" / "lvs-01").mkdir(parents=True)
    (w / "work" / "lvs-01" / "report.lvsdb").write_text("db")
    (w / ".mcr" / "staging" / "x").mkdir(parents=True)
    (w / ".mcr" / "promote.lock").write_text("")
    (w / ".mcr" / "handoff.json").write_text("{}")
    (w / "link").symlink_to("/etc/hostname")
    dest = w / "snap" / "pre_repair"
    m = snapshot.archive_pre_repair(w, dest, exclude_paths=[w / ".mcr" / "staging"])
    tree = dest / "workspace"
    assert (tree / "work/lvs-01/report.lvsdb").read_text() == "db"
    assert (tree / ".mcr/handoff.json").is_file() and (tree / "link").is_symlink()
    assert not (tree / ".mcr/staging").exists() and not (tree / ".mcr/promote.lock").exists()
    assert not (tree / "snap" / "pre_repair").exists()        # no recursion into the archive
    assert json.loads((dest / "manifest.json").read_text())["files"]["work/lvs-01/report.lvsdb"] == m[
        "work/lvs-01/report.lvsdb"]


def test_kernel_is_benchmark_agnostic():
    banned = re.compile(r"sky130|klayout|magic|netgen|\.gds", re.I)
    hits = [f"{p.name}:{i}" for p in sorted((ROOT / "src/ssap/kernel").glob("*.py"))
            for i, line in enumerate(p.read_text().splitlines(), 1) if banned.search(line)]
    assert hits == []
