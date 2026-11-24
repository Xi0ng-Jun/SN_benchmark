"""Milestone 3a: B_dev referee bank, blind-cluster privacy, synthetic variants, guard-band
calibration, Stage 0 Class I/II/III contracts and the offline harness smoke."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from ssap.bank import synthesize as syn
from ssap.bank.blind_clusters import (BlindBoundary, PrivacyViolation, aggregate,
                                      freeze_blind_clusters)
from ssap.bank.concordance import (calibrate_epsilon, offline_smoke_container, screen_class1,
                                   screen_class2, screen_class3)
from ssap.bank.referee_bank import BOTTOM, BankSample, RefereeBank
from ssap.checkers.alw import area_fill, gds_geom

ROOT = Path(__file__).resolve().parents[1]
SPLIT = json.loads((ROOT / "configs" / "alw" / "split.json").read_text())
BLIND = freeze_blind_clusters(SPLIT)
DEV = list(BLIND.dev_clusters)
VISIBLE = [c for c in DEV if c not in BLIND.blind]


def task_of(cluster: str) -> str:
    for i, n in enumerate(SPLIT["splits"]["dev"]):
        if SPLIT["cluster_of"][str(n)] == cluster:
            return SPLIT["task_ids"]["dev"][i]
    raise KeyError(cluster)


def S(sid, cluster, *, C=1, d=None, m=None, kind="trajectory", raw_u=None) -> BankSample:
    d = d or {"gds": 1, "lvs": 1, "shape": 1, "fill": 1, "morph": 1, "drc": 1}
    cert = {}
    if m is not None:
        cert["m"] = m
    if raw_u is not None:
        cert["raw_u"] = raw_u
    return BankSample(sid, task_of(cluster), cluster, kind, 1.0 if C else 0.0, C, d, cert,
                      f"/state/artifacts/{sid}/layout.gds")


# ------------------------------------------------------------------------------------------
# blind clusters / privacy
# ------------------------------------------------------------------------------------------

def test_blind_clusters_frozen_consistently_with_split():
    assert BLIND.g_dev == SPLIT["G_dev"] == 10
    assert list(BLIND.blind) == SPLIT["blind_clusters"] and len(BLIND.blind) == 3
    assert set(BLIND.blind) <= set(DEV)
    assert len(BLIND.blind_task_ids) + len(BLIND.visible_task_ids) == len(SPLIT["splits"]["dev"])
    tampered = {**SPLIT, "blind_clusters": sorted(VISIBLE[:3])}
    with pytest.raises(ValueError):
        freeze_blind_clusters(tampered)


def test_blind_privacy_boundary_exports_aggregates_only():
    b = BlindBoundary(BLIND)
    recs = [{"sample_id": f"s{i}", "task_id": task_of(c), "cluster_id": c, "flagged": f, "label": l,
             "artifact_path": f"/state/x/{c}/s{i}.gds"}
            for i, (c, f, l) in enumerate([(BLIND.blind[0], True, 0), (BLIND.blind[1], True, 1),
                                          (BLIND.blind[1], False, 1), (VISIBLE[0], True, 0)])]
    out = b.export_for_proposer(recs)
    assert out["blind_aggregate"] == {"n_samples": 3, "n_pass": 1, "n_fp": 1, "n_tp": 1,
                                      "clusters_covered": 2}
    assert [r["sample_id"] for r in out["visible"]] == ["s3"]
    text = json.dumps(out)
    for tid in BLIND.blind_task_ids:
        assert tid not in text
    for c in BLIND.blind:
        assert c not in text
    with pytest.raises(PrivacyViolation):
        b.guard({"leak": next(iter(BLIND.blind_task_ids))})
    with pytest.raises(PrivacyViolation):
        b.guard({"log": "/state/x/" + BLIND.blind[0] + "/s0.gds"})
    with pytest.raises(PrivacyViolation):
        b.guard({"ids": ["s0"]}, blind_records=recs[:1])
    with pytest.raises(PrivacyViolation):           # raw blind rows never pass the guard
        b.guard({"visible": [recs[0]]}, blind_records=recs[:1])
    assert set(aggregate([])) == {"n_samples", "n_pass", "n_fp", "n_tp", "clusters_covered"}


# ------------------------------------------------------------------------------------------
# referee bank
# ------------------------------------------------------------------------------------------

def test_bank_bottom_exclusion_and_index_sets(tmp_path):
    bank = RefereeBank(tmp_path / "bank.jsonl", dev_clusters=DEV)
    bank.add(S("a", VISIBLE[0]))
    bank.add(S("b", VISIBLE[1], C=0, d={"gds": 1, "lvs": 0, "fill": BOTTOM, "morph": BOTTOM, "drc": 0},
               m=[1, 1, 1, 0, 0, 1]))                       # incumbent misses LVS and DRC
    bank.add(S("c", VISIBLE[2], C=0, d={"gds": 1, "lvs": 1, "fill": 0, "drc": 0},
               m=[1, 1, 1, 0, 1, 0]))                       # incumbent flags fill and drc
    bank.add(S("d", VISIBLE[3], C=0, d={"gds": 1, "lvs": 1, "fill": BOTTOM, "drc": 1}))
    assert [s.sample_id for s in bank.s_plus()] == ["a"]
    assert {s.sample_id for s in bank.s_plus_k("m_fill")} == {"a"}
    assert {s.sample_id for s in bank.bottom_k("fill")} == {"b", "d"}
    assert bank.s_minus_k("m_fill") == []                   # c is flagged; b, d are bottom
    assert [s.sample_id for s in bank.s_minus_k("m_lvs")] == ["b"]
    assert [s.sample_id for s in bank.s_minus_k("m_drc")] == ["b"]
    assert {s.sample_id for s in bank.s_minus_k("m_fill", incumbent_flags={})} == {"c"}
    for k in ("m_fill", "m_morph"):
        ids = {s.sample_id for s in bank.s_plus_k(k) + bank.s_minus_k(k, incumbent_flags={})}
        assert not ids & {s.sample_id for s in bank.bottom_k(k)}
    # persistence, idempotent duplicates, conflicts, sealed splits
    again = RefereeBank(tmp_path / "bank.jsonl", dev_clusters=DEV)
    assert len(again) == 4 and again.get("b").d_star["fill"] == BOTTOM
    assert again.add(S("a", VISIBLE[0])) is False
    with pytest.raises(ValueError):
        again.add(S("a", VISIBLE[1]))
    with pytest.raises(ValueError, match="dev cluster"):
        again.add(S("z", VISIBLE[0]).__class__(**{**S("z", VISIBLE[0]).as_dict(),
                                                  "cluster_id": "cl_not_dev"}))
    with pytest.raises(ValueError):
        S("bad", VISIBLE[0], d={"lvs": 2})


def test_bank_coverage_counts_distinct_dev_clusters_capped():
    bank = RefereeBank(dev_clusters=DEV)
    for i in range(40):
        bank.add(S(f"s{i}", DEV[i % len(DEV)]))
    cov = bank.coverage(bank.s_plus())
    assert cov == {"clusters_covered": 10, "G_dev": 10}
    few = RefereeBank(dev_clusters=DEV)
    for i in range(6):
        few.add(S(f"s{i}", DEV[0]))
    assert few.coverage(few.s_plus())["clusters_covered"] == 1
    with pytest.raises(ValueError):
        RefereeBank(dev_clusters=[f"cl_{i}" for i in range(11)])
    rep = bank.coverage_report("m_lvs")
    assert rep["S_plus_k"]["clusters_covered"] <= 10 and rep["n_S_plus_k"] == 40


# ------------------------------------------------------------------------------------------
# synthesize
# ------------------------------------------------------------------------------------------

TOP = "toy_top"


def _base(tmp_path) -> bytes:
    p = gds_geom.write_gds(tmp_path / "base.gds", {
        "child": {"boxes": [(68, 20, 0, 0, 1, 2)]},
        TOP: {"boxes": [(65, 20, 0, 0, 20, 10), (68, 20, 1, 1, 3, 3)],
              "texts": [(68, 5, 1, 1, "vdd"), (68, 5, 2, 2, "out")],
              "refs": [("child", 5, 5)]}})
    return p.read_bytes()


def _geo(tmp_path, data: bytes, name: str, top: str = TOP):
    p = tmp_path / f"{name}.gds"
    p.write_bytes(data)
    return gds_geom.check_geometry(p, top, backend="pure")


def test_synthesize_negative_mutations(tmp_path):
    base = _base(tmp_path)
    g0 = _geo(tmp_path, base, "base")
    assert g0.bbox_um == (0.0, 0.0, 20.0, 10.0)
    adv = syn.shape_advisory_q3(base, TOP)
    g = _geo(tmp_path, adv.data, "adv")
    q = area_fill.aspect_ratio(g.width_um, g.height_um)
    assert 3.2 < q < 3.8 and adv.advisory_only_on_q3 and adv.polarity == "positive"
    assert area_fill.shape_raw_margin(g.width_um, g.height_um) >= 0          # m_shape keeps it
    assert area_fill.advisory_aspect_gt_3(g.width_um, g.height_um)
    neg = syn.shape_negative(base, TOP)
    g = _geo(tmp_path, neg.data, "neg")
    assert area_fill.aspect_ratio(g.width_um, g.height_um) > 4.2 and neg.expected == {"shape": 0}
    with pytest.raises(ValueError):
        syn.shape_advisory_q3(base, TOP, q=4.0)
    with pytest.raises(ValueError):
        syn.shape_negative(base, TOP, q=4.1)
    ring = syn.fill_low_ring(base, TOP, effective_area_um2=20.0)
    g = _geo(tmp_path, ring.data, "ring")
    assert 20.0 / g.area_um2 < 0.08 and ring.expected == {"fill": 0}
    assert abs(area_fill.aspect_ratio(g.width_um, g.height_um) - 2.0) < 0.01
    sliver = syn.drc_sliver(base, TOP)
    x1, _, x2, _ = sliver.params["box"][2:6]
    assert x2 - x1 < 0.14 and sliver.expected == {"drc": 0}
    assert _geo(tmp_path, sliver.data, "sliver").bbox_um[2] > 20.0
    lab = syn.lvs_missing_label(base, TOP, "vdd")
    assert lab.params["removed"] == 1 and b"vdd" not in lab.data and b"out" in lab.data
    with pytest.raises(ValueError):
        syn.lvs_missing_label(base, TOP, "nope")
    trunc = syn.gds_truncated(base)
    (tmp_path / "t.gds").write_bytes(trunc.data)
    with pytest.raises(gds_geom.GeometryError):
        gds_geom.check_geometry(tmp_path / "t.gds", TOP, backend="pure")
    ren = syn.gds_renamed_top(base, TOP)
    with pytest.raises(gds_geom.GeometryError):
        _geo(tmp_path, ren.data, "ren")
    assert all(v.source_kind == "synthetic_neg" for v in (neg, ring, sliver, lab, trunc, ren))


def test_synthesize_positive_equivalents(tmp_path):
    base = _base(tmp_path)
    sh = syn.grid_shift(base, TOP, dx_um=10.0, dy_um=-5.0)
    assert _geo(tmp_path, sh.data, "sh").bbox_um == (10.0, -5.0, 30.0, 5.0)
    rot = syn.rotate90(base, TOP)
    g = _geo(tmp_path, rot.data, "rot")
    assert g.bbox_um == (-10.0, 0.0, 0.0, 20.0)           # child box rotated with its placement
    assert set(g.present_layers) == set(_geo(tmp_path, base, "b2").present_layers)
    full = syn.transform_top(base, TOP, rot90=4)
    assert _geo(tmp_path, full, "r4").bbox_um == (0.0, 0.0, 20.0, 10.0)
    # rotated child: ANGLE record present on the SREF
    recs = syn.parse_records(rot.data)
    assert any(r[0] == syn.ANGLE and abs(syn._real8(r[2]) - 90.0) < 1e-9 for r in recs)
    assert sh.expected == {"inherit": True} and rot.source_kind == "synthetic_pos"
    with pytest.raises(ValueError):
        syn.grid_shift(base, TOP, dx_um=0.0031)
    out = rot.write(tmp_path / "w" / "rot.gds", writer=syn.default_writer)
    assert out.read_bytes() == rot.data
    names = [v.name for v in syn.synthesize_all(base, TOP, ports=["nope", "out"], effective_area_um2=20)]
    assert names == ["shape_q_advisory", "shape_q_over_4", "drc_sliver", "gds_truncated",
                     "gds_renamed_top", "grid_shift", "rotate90", "fill_low_ring", "lvs_missing_label"]
    assert syn.synthesize_all(base, TOP)[0].data == syn.synthesize_all(base, TOP)[0].data  # deterministic


# ------------------------------------------------------------------------------------------
# epsilon calibration
# ------------------------------------------------------------------------------------------

def test_calibrate_eps4_fill():
    samples = [{"raw_u": 0.004, "d_star_k": 0}, {"raw_u": 0.012, "d_star_k": 0},
               {"raw_u": 0.05, "d_star_k": 1}, {"raw_u": -0.02, "d_star_k": 0},
               {"raw_u": 0.3, "d_star_k": BOTTOM}, {"raw_u": None, "d_star_k": 0}]
    assert calibrate_epsilon("m_fill", samples) == pytest.approx(0.012 + 1e-6)
    assert calibrate_epsilon("m_fill", [{"raw_u": -0.1, "d_star_k": 0}]) == 0.0
    assert calibrate_epsilon("m_fill", []) == 0.0


def test_eps3_shape_forbidden_on_q_max_3():
    q3 = [{"raw_u": 0.5, "d_star_k": 0, "q_max": 3.0}]
    with pytest.raises(ValueError, match="q_max == 4.0"):
        calibrate_epsilon("m_shape", q3)
    with pytest.raises(ValueError):
        calibrate_epsilon("m_shape", q3 + [{"raw_u": 0.1, "d_star_k": 0, "q_max": 4.0}])
    with pytest.raises(ValueError):
        calibrate_epsilon("m_shape", [{"raw_u": 0.1, "d_star_k": 0}])          # q_max unknown
    assert calibrate_epsilon("m_shape", [{"raw_u": 0.1, "d_star_k": 1, "q_max": 4.0}]) == 0.0
    assert calibrate_epsilon("m_shape", [{"raw_u": 0.1, "d_star_k": 0, "q_max": 4.0}]) == \
        pytest.approx(0.1 + 1e-6)


# ------------------------------------------------------------------------------------------
# Stage 0
# ------------------------------------------------------------------------------------------

def _stage0_bank() -> RefereeBank:
    bank = RefereeBank(dev_clusters=DEV)
    bank.add(S("p1", VISIBLE[0]))
    bank.add(S("p2", BLIND.blind[0]))
    bank.add(S("n1", VISIBLE[1], C=0, d={"gds": 1, "lvs": 1, "drc": 0, "fill": 1}, m=[1, 1, 1, 1, 1, 1]))
    bank.add(S("n2", BLIND.blind[1], C=0, d={"gds": 1, "lvs": 1, "drc": 0, "fill": 0},
               m=[1, 1, 1, 1, 1, 1]))
    bank.add(S("b1", VISIBLE[2], C=0, d={"gds": 1, "lvs": 1, "drc": BOTTOM}, m=[1, 1, 1, 1, 1, 1]))
    return bank


def test_stage0_class1_directional_and_bidirectional():
    bank, bd = _stage0_bank(), BlindBoundary(BLIND)
    good = {"p1": 1, "p2": 1, "n1": 0, "n2": 0, "b1": 1}
    r = screen_class1("m_drc", bank, lambda s: good[s.sample_id], bidirectional=True, boundary=bd)
    assert r.ok and r.checks["certified_on_bottom_with_lvs_pass"] == 1
    assert r.checks["n_S_plus_k"] == 2 and r.checks["n_S_minus_k"] == 2
    leaky = {**good, "n2": 1}                         # misses a blind negative
    r = screen_class1("m_drc", bank, lambda s: leaky[s.sample_id], bidirectional=True, boundary=bd)
    assert not r.ok and r.checks["bidirectional_violations"] == 1
    assert r.visible["violating_sample_ids"] == []     # blind violation shown only in aggregate
    assert screen_class1("m_drc", bank, lambda s: leaky[s.sample_id], boundary=bd).ok   # directional
    harsh = {**good, "p2": 0}                          # de-certifies a true solution
    r = screen_class1("m_drc", bank, lambda s: harsh[s.sample_id], boundary=bd)
    assert not r.ok and r.checks["directional_violations"] == 1
    for tid in BLIND.blind_task_ids:
        assert tid not in json.dumps(r.as_dict())


def test_stage0_class2_probe_contract():
    bank, bd = _stage0_bank(), BlindBoundary(BLIND)
    flags = {"p1": False, "p2": False, "n1": True, "n2": True, "b1": False}
    r = screen_class2("probe_x", bank, lambda s: flags[s.sample_id], boundary=bd)
    assert r.ok and r.checks["k_FP_S_plus"] == 0 and r.checks["k_TP_blind"] == 1
    assert r.blind_aggregate["n_tp"] == 1
    r = screen_class2("probe_x", bank, lambda s: flags[s.sample_id], boundary=bd, overwrite_authority=True)
    assert not r.ok
    fp = {**flags, "p2": True}
    r = screen_class2("probe_x", bank, lambda s: fp[s.sample_id], boundary=bd)
    assert not r.ok and r.checks["k_FP_S_plus"] == 1 and r.visible["violating_sample_ids"] == []
    no_tp = {**flags, "n2": False}
    assert not screen_class2("probe_x", bank, lambda s: no_tp[s.sample_id], boundary=bd).ok
    by_key = screen_class2("probe_x", bank, lambda s: flags[s.sample_id], boundary=bd,
                           negative_key="m_fill")
    assert by_key.ok and by_key.checks["k_TP_blind"] == 1


def test_stage0_class3_root_cause_retention_and_byte_cap():
    log = "\n".join([f"info line {i}" for i in range(400)] + ["ERROR: met1 width 0.05 < 0.14"])
    cases = [(log, ["ERROR: met1 width 0.05 < 0.14"])]
    keep_errors = lambda t: "\n".join(line for line in t.splitlines() if "ERROR" in line)  # noqa: E731
    assert screen_class3("errs", keep_errors, cases).ok
    head = lambda t: t[:500]                                                          # noqa: E731
    r = screen_class3("head", head, cases)
    assert not r.ok and r.checks["root_cause_lines_lost"] == 1
    r = screen_class3("all", lambda t: t, cases)
    assert not r.ok and r.checks["over_byte_cap"] == 1 and r.checks["max_output_bytes"] > 2048
    assert not screen_class3("none", keep_errors, []).ok


# ------------------------------------------------------------------------------------------
# offline smoke
# ------------------------------------------------------------------------------------------

def test_offline_smoke_container_local(tmp_path):
    rep = offline_smoke_container(None, root=ROOT, work_dir=tmp_path / "smoke", use_docker=False)
    assert rep["ok"], json.dumps(rep, indent=1)
    exp = rep["local"]["expectations"]
    assert exp["good_promoted"] and exp["bad_rejected"] and exp["junk_rejected"]
    assert exp["ssap_from_bundle"] and exp["submission_is_good"] and rep["docker"] is None
    assert rep["local"]["steps"]["promote_good"]["reason"] == "tier0_bootstrap"


# ------------------------------------------------------------------------------------------
# Phase 2: synthetic bank builder and the Stage 0 H_base audit
# ------------------------------------------------------------------------------------------

from ssap.bank import phase2                                         # noqa: E402
from ssap.bank.referee_bank import MIRROR_ORDER                     # noqa: E402
from ssap.targets.alw.metrics_reader import MetricsReading          # noqa: E402

ALL1 = {"gds": 1, "lvs": 1, "shape": 1, "fill": 1, "morph": 1, "drc": 1}


def T(sid, cluster, path, *, C=0, d=None, raw_fill=None, kind="terminal") -> BankSample:
    cert = {"raw_u": [0.0, 0.0, 1.0, raw_fill, 0.0, 0.0]} if raw_fill is not None else {}
    return BankSample(sid, task_of(cluster), cluster, kind, 1.0 if C else 0.2, C,
                      dict(d or ALL1), cert, str(path) if path else None)


def _source_bank(tmp_path) -> tuple[RefereeBank, Path]:
    gds = tmp_path / "src" / "layout.gds"
    gds.parent.mkdir(parents=True)
    gds.write_bytes(_base(tmp_path))
    bank = RefereeBank(dev_clusters=DEV)
    for s in (T("tb_plus", BLIND.blind[0], gds, C=1, raw_fill=0.3),
              T("tb_low", BLIND.blind[0], gds, d={**ALL1, "lvs": 0, "drc": 0}),
              T("tb_lvs", BLIND.blind[1], gds, d={**ALL1, "drc": 0}, raw_fill=0.3),
              T("tv_drc", VISIBLE[1], gds, d={"gds": 1, "lvs": 0, "drc": 1}),
              T("tv_gds", VISIBLE[0], gds, d={"gds": 1, "lvs": 0, "drc": 0}),
              T("tv_gone", VISIBLE[2], tmp_path / "missing.gds", C=1),
              T("tv_traj", VISIBLE[3], gds, C=1, kind="trajectory")):
        bank.add(s)
    return bank, gds


def test_phase2_select_mutation_sources(tmp_path):
    bank, _ = _source_bank(tmp_path)
    ids = [s.sample_id for s in phase2.select_mutation_sources(bank, BLIND, max_sources=3)]
    assert ids == ["tb_plus", "tv_drc", "tb_lvs"]           # S+, reserved visible, S+_lvs
    ids = [s.sample_id for s in phase2.select_mutation_sources(bank, BLIND, max_sources=2)]
    assert ids == ["tb_plus", "tv_drc"]                     # visible kept over a blind S+_lvs
    ids = [s.sample_id for s in phase2.select_mutation_sources(bank, BLIND, max_sources=10)]
    assert ids == ["tb_plus", "tv_drc", "tb_lvs", "tv_gds"]  # one per cluster, files only


def test_phase2_effective_area_from_sample(tmp_path):
    data = _base(tmp_path)
    s = T("x", VISIBLE[0], None, raw_fill=0.3)
    assert phase2.effective_area_from_sample(s, data, TOP) == pytest.approx(0.4 * 200.0)
    assert phase2.effective_area_from_sample(T("y", VISIBLE[0], None), data, TOP) is None
    no_lvs = T("z", VISIBLE[0], None, d={**ALL1, "lvs": 0}, raw_fill=0.3)
    assert phase2.effective_area_from_sample(no_lvs, data, TOP) is None


NEG_KEY = {"shape_q_over_4": "shape", "drc_sliver": "drc", "gds_truncated": "gds",
           "gds_renamed_top": "gds", "fill_low_ring": "fill", "lvs_missing_label": "lvs"}


def _variant_name(p: Path) -> str:
    return p.name.split("_", 2)[2]


def _mock_verifier(task_id, submission_dir, out_dir):
    name = _variant_name(Path(submission_dir).parent)
    if name == "rotate90":
        return MetricsReading(None, None, {k: BOTTOM for k in ALL1}, {"infra_error": True})
    d = dict(ALL1)
    if name in NEG_KEY:
        d[NEG_KEY[name]] = 0
    c = int(all(v == 1 for v in d.values()))
    return MetricsReading(1.0 if c else 0.3, c, d, {})


def _mock_inner(task_id, top, gds_path, work_dir):
    name = _variant_name(Path(gds_path).parent.parent)
    m = [0 if NEG_KEY.get(name) == MIRROR_ORDER[i][2:] else 1 for i in range(6)]
    return {"m": m, "raw_u": [0.0] * 6, "mirror_names": list(MIRROR_ORDER),
            "advisory_flags": {"advisory_aspect_gt_3": name == "shape_q_advisory"}}


def _catalog():
    return {task_of(c): {"top": TOP, "ports": ["nope", "vdd"], "q_max": 4.0} for c in DEV}


def test_phase2_synthesize_bank_variants_blind_safe(tmp_path):
    bank, _ = _source_bank(tmp_path)
    work = tmp_path / "work"
    dry = phase2.synthesize_bank_variants(bank, _catalog(), BLIND, work_dir=work, dry_run=True,
                                          verifier_fn=_mock_verifier)
    assert dry["n_sources"] == 3 and dry["n_written"] == 0 and dry["n_added"] == 0
    assert not work.exists() and len(bank) == 7
    out = phase2.synthesize_bank_variants(bank, _catalog(), BLIND, work_dir=work, parallel=2,
                                          verifier_fn=_mock_verifier, inner_cert_fn=_mock_inner)
    n_rot = out["by_variant_name"]["rotate90"]["planned"]
    assert out["n_sources"] == 3 and out["n_sources_blind_clusters"] == 2
    assert out["n_variants_planned"] == dry["n_variants_planned"] == out["n_written"]
    assert out["n_skipped_infra"] == n_rot == 3
    assert out["n_verified"] == out["n_added"] == out["n_variants_planned"] - n_rot
    assert out["n_inner_cert"] == out["n_verified"]
    assert out["by_variant_name"]["fill_low_ring"]["added"] == 2       # LVS-passing sources only
    assert out["by_variant_name"]["drc_sliver"]["agree"] == 3
    text = json.dumps(out)
    for needle in [*BLIND.blind, *BLIND.blind_task_ids, "tb_plus", "tb_lvs", str(work)]:
        assert needle not in text
    BlindBoundary(BLIND).guard(out, blind_records=list(bank))
    v = bank.get("tb_plus:drc_sliver")
    assert v.source_kind == "synthetic_neg" and v.d("drc") == 0 and v.cluster_id == BLIND.blind[0]
    assert v.meta == {"variant": "drc_sliver", "checker": "m_drc", "polarity": "negative",
                      "q_max": 4.0, "advisory_only_on_q3": False}
    assert Path(v.artifact_path).is_file() and v.m("m_drc") == 0 and v.m("m_gds") == 1
    assert bank.get("tv_drc:grid_shift").source_kind == "synthetic_pos"
    again = phase2.synthesize_bank_variants(bank, _catalog(), BLIND, work_dir=work,
                                            verifier_fn=_mock_verifier, inner_cert_fn=_mock_inner)
    assert again["n_added"] == 0 and again["n_existing"] == out["n_added"]


def _hbase_bank() -> RefereeBank:
    bank = RefereeBank(dev_clusters=DEV)
    for sid, cl, C, d, m, adv, raw in (
            ("vp1", VISIBLE[0], 1, ALL1, [1] * 6, False, 0.2),
            ("bp1", BLIND.blind[0], 1, ALL1, [1] * 6, False, 0.2),
            ("vn1", VISIBLE[1], 0, {**ALL1, "drc": 0}, [1, 1, 1, 1, 1, 0], False, 0.2),
            ("bn1", BLIND.blind[1], 0, {**ALL1, "shape": 0}, [1, 1, 0, 1, 1, 1], True, 0.2),
            ("vf1", VISIBLE[3], 0, {**ALL1, "fill": 0}, [1, 1, 1, 0, 1, 1], False, 0.02),
            ("vu1", VISIBLE[2], 1, ALL1, None, False, None)):        # no incumbent certificate
        cert = {} if m is None else {"m": m, "raw_u": [0.0, 0.0, 0.0, raw, 0.0, 0.0],
                                     "advisory_flags": {"advisory_aspect_gt_3": adv}}
        bank.add(BankSample(sid, task_of(cl), cl, "terminal", float(C), C, dict(d), cert,
                            f"/state/{sid}/layout.gds"))
    return bank


def test_phase2_screen_hbase_on_bank():
    bank = _hbase_bank()
    rep = phase2.screen_hbase_on_bank(bank, BLIND)
    assert rep["class1_ok"] and set(rep["class1"]) == set(MIRROR_ORDER)
    drc = rep["class1"]["m_drc"]
    assert drc["n_checked"] == 5 and drc["n_unchecked"] == 1          # vu1 is not an m != 1
    assert drc["checks"]["n_S_plus_k"] == 4 and drc["checks"]["n_S_minus_k"] == 0   # vn1 caught
    assert rep["class1"]["m_lvs"]["checks"]["n_S_minus_k"] is None     # directional only
    assert rep["class2_ok"]
    adv = rep["class2"]["advisory_aspect_gt_3"]
    assert adv["checks"]["k_FP_S_plus"] == 0 and adv["checks"]["k_TP_blind"] == 1
    assert rep["eps"]["m_fill"] == pytest.approx(0.02 + 1e-6)
    cov = rep["coverage"]
    assert cov["n_total"] == 6 and cov["n_S_plus"] == 3 and cov["S_plus"]["clusters_covered"] == 3
    assert cov["per_criterion"]["m_drc"]["n_S_minus_k"] == 0           # vn1 flagged by m_drc
    bank.add(BankSample("bv1", task_of(BLIND.blind[2]), BLIND.blind[2], "terminal", 1.0, 1,
                        dict(ALL1), {"m": [1, 1, 1, 1, 1, 0]}, "/state/bv1/layout.gds"))
    rep = phase2.screen_hbase_on_bank(bank, BLIND)
    drc = rep["class1"]["m_drc"]
    assert not rep["class1_ok"] and drc["checks"]["directional_violations"] == 1
    assert drc["visible"]["violating_sample_ids"] == [] and drc["blind_aggregate"]["n_fp"] == 1
    text = json.dumps(rep)
    for needle in [*BLIND.blind, *BLIND.blind_task_ids, "bp1", "bn1", "bv1", "/state/b"]:
        assert needle not in text
    BlindBoundary(BLIND).guard(rep, blind_records=list(bank))
