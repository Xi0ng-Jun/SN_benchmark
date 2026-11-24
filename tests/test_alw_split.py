"""Milestone 1: ALW catalog, cluster-isolated split, leak guards, metrics reader, paper numbers.

Deterministic; reads only the committed configs/alw/*.json. Tests that re-read the ALW checkout
or the paper run only when $ALW_REPO / $ALW_PAPER_DIR are set.
"""
from __future__ import annotations

import copy
import getpass
import hashlib
import importlib.util
import json
import os
import re
import statistics
from pathlib import Path

import pytest

from ssap import cli
from ssap.config import interpolate, load_config
from ssap.targets.alw import catalog as cat
from ssap.targets.alw import splits as sp
from ssap.targets.alw.metrics_reader import BOTTOM, D_KEYS, parse_metrics, read_metrics

ROOT = Path(__file__).resolve().parents[1]
CFG = ROOT / "configs/alw"
DEV = [2, 7, 11, 12, 13, 15, 16, 18, 19, 21, 27, 38]
VAL = [1, 3, 5, 6, 8, 17, 20, 22, 25, 26, 34, 35, 36, 37]
TEST = [4, 9, 10, 14, 23, 24, 28, 29, 30, 31, 32, 33, 39, 40]


def _load(name: str) -> dict:
    return json.loads((CFG / name).read_text())


@pytest.fixture(scope="module")
def catalog() -> dict:
    return _load("catalog.json")


@pytest.fixture(scope="module")
def clusters() -> dict:
    return _load("clusters.json")


@pytest.fixture(scope="module")
def split() -> dict:
    return _load("split.json")


def _by_num(catalog: dict) -> dict[int, dict]:
    return {t["number"]: t for t in catalog["tasks"]}


# ------------------------------------------------------------------------------------------
# catalog.json
# ------------------------------------------------------------------------------------------

def test_catalog_has_40_tasks_and_no_local_paths(catalog):
    assert len(catalog["tasks"]) == 40
    assert [t["number"] for t in catalog["tasks"]] == list(range(1, 41))
    assert catalog["commit"] == "d5236c886fefb5f5cedb0c755f3b3a949f1f2423"
    text = (CFG / "catalog.json").read_text()
    assert "/home/" not in text and "/localdata/" not in text and not re.search(r"\b[A-Z]:\\", text)


@pytest.mark.parametrize("part,nums,median,lo,hi", [
    ("dev", DEV, 26.5, 6, 59), ("val", VAL, 21.5, 7, 50), ("test", TEST, 23.0, 8, 118),
    ("all", list(range(1, 41)), 24.0, 6, 118)])
def test_device_medians_and_ranges(catalog, part, nums, median, lo, hi):
    t = _by_num(catalog)
    devs = [t[n]["devices"] for n in nums]
    assert (statistics.median(devs), min(devs), max(devs)) == (median, lo, hi)


def test_profiles_backends_and_qmax_per_split(catalog):
    t = _by_num(catalog)
    binned = {k: sum(t[n]["runtime_profile"] == "binned" for n in v)
              for k, v in (("dev", DEV), ("val", VAL), ("test", TEST))}
    assert binned == {"dev": 3, "val": 3, "test": 3}
    magic = sorted(n for n, x in t.items() if x["lvs_backend"] == "magic_netgen")
    assert magic == [2, 10] and 2 in DEV and 10 in TEST
    q3 = {k: sum(t[n]["q_max"] == 3.0 for n in v) for k, v in (("dev", DEV), ("val", VAL), ("test", TEST))}
    assert q3 == {"dev": 8, "val": 8, "test": 7} and sum(q3.values()) == 23
    assert sum(x["q_max"] == 4.0 for x in t.values()) == 17
    assert {x["u_min"] for x in t.values()} == {0.1}


def test_flattener_is_quote_aware_and_hierarchical():
    net = """* test
.subckt inv a y vdd vss
XM1 y a vdd vdd sky130_fd_pr__pfet_01v8 L=0.15 W=1 ad='int((nf+1)/2) * W/nf * 0.29' as = { 2 * W }
XM2 y a vss vss sky130_fd_pr__nfet_01v8 L=0.15
+ W=0.5 $ inline comment with words
.ends inv
.subckt top in out vdd vss
X1 in mid vdd vss inv
X2 mid out vdd vss inv m=2
R1 out vss 1k ; comment
C1 mid vss 'cval * 2'
.ends top
"""
    assert cat.circuit_scale(net, "top", "vss") == (6, 3)        # nets: in, x1.? -> in, mid, out
    assert cat.child_subckts(net, "top") == ["inv"]
    assert cat.root_subckts(net) == ["top"]
    prims = cat.flatten(net, "top")
    assert ("sky130_fd_pr__pfet_01v8", ("mid", "in", "vdd", "vdd")) in prims
    assert cat.tokenize("a w = 1 ad='x * y' b={ 1 + 2 }") == ["a", "w=1", "ad='x * y'", "b={ 1 + 2 }"]


@pytest.mark.skipif(not os.environ.get("ALW_REPO"), reason="needs $ALW_REPO")
def test_catalog_matches_checkout(catalog):
    repo = os.environ["ALW_REPO"]
    fresh = cat.catalog_document(cat.load_catalog(repo), cat.repo_commit(repo))
    assert fresh["tasks"] == catalog["tasks"]


# ------------------------------------------------------------------------------------------
# clusters.json / split.json
# ------------------------------------------------------------------------------------------

def test_cluster_counts_and_merges(catalog, clusters):
    assert sp.derive_clusters(catalog["tasks"])["hard"] == clusters["hard"]
    assert (len(clusters["hard"]), len(clusters["conservative"])) == (35, 32)
    merged_hard = {k: v["tasks"] for k, v in clusters["hard"].items() if len(v["tasks"]) > 1}
    assert merged_hard == {"cl_ajc_por": [32, 33], "cl_adb_cp_10mhz": [24, 31],
                           "cl_adb_sampling_fb_ota": [11, 12], "cl_adb_miller_opamp": [29, 30],
                           "cl_adb_gmr_degen": [15, 16]}
    merged_cons = {k: v["tasks"] for k, v in clusters["conservative"].items() if len(v["tasks"]) > 1}
    assert merged_cons == {**merged_hard, "cl_fc_ota_name": [14, 39], "cl_loadbench_batch": [34, 35, 36]}
    assert clusters["hard_of"]["7"] == "cl_hard_07" and clusters["conservative_of"]["7"] == "cl_task_07"
    assert clusters["conservative_of"]["13"] == "cl_task_13"


def test_split_is_the_locked_seed_split(catalog, clusters, split):
    assert split["seed"] == "ssap-alw-v2-19"
    assert split["splits"] == {"dev": DEV, "val": VAL, "test": TEST}
    assert sp.make_split(catalog, clusters)["splits"] == split["splits"]
    s = split["stats"]
    assert [(s[k]["hard_clusters"], s[k]["conservative_clusters"]) for k in ("dev", "val", "test")] == \
        [(10, 10), (14, 12), (11, 10)]
    assert split["G_dev"] == 10 >= 8 and split["blind_cluster_mode"] is True
    assert len(split["blind_clusters"]) == 3
    blind_tasks = [n for n in DEV if split["cluster_of"][str(n)] in split["blind_clusters"]]
    assert 3 <= len(blind_tasks) <= 4


def test_zero_cross_split_overlap(catalog, clusters, split):
    for key in ("hard_of", "conservative_of"):
        assert sp.cross_split_overlap(split, {int(n): [c] for n, c in clusters[key].items()}) == {}


def test_all_invariants_hold_on_the_repository(catalog, clusters, split):
    rep = sp.check_split_invariants(catalog, clusters, split, code_root=ROOT)
    assert rep.ok, [c.__dict__ for c in rep.failures()]
    assert {c.rule for c in rep.checks} == {"R1", "R2", "R3", "R4", "R5"}


def _naive_task_level_split(tasks: list[dict], seed: str) -> dict:
    """method-v5 style: single tasks dealt by (device tercile, source, sha256(seed:task_id))."""
    sizes = {"dev": 12, "val": 14, "test": 14}
    by_dev = sorted(tasks, key=lambda t: (t["devices"], t["number"]))
    terc = {t["task_id"]: min(2, 3 * i // len(tasks)) for i, t in enumerate(by_dev)}
    order = sorted(tasks, key=lambda t: (terc[t["task_id"]], t["source"],
                                         hashlib.sha256(f"{seed}:{t['task_id']}".encode()).hexdigest()))
    left, parts = dict(sizes), {k: [] for k in sizes}
    for t in order:
        k = max(left, key=lambda k: (left[k] / sizes[k], -list(sizes).index(k)))
        parts[k].append(t["number"])
        left[k] -= 1
    return {k: sorted(v) for k, v in parts.items()}


def test_naive_task_level_split_leaks_clusters_and_is_caught(catalog, clusters, split):
    naive = copy.deepcopy(split)
    naive["splits"] = _naive_task_level_split(catalog["tasks"], "method-v5-alw")
    leaks = sp.cross_split_overlap(naive, {int(n): [c] for n, c in clusters["hard_of"].items()})
    assert leaks, "a task-level split is expected to separate members of some hard cluster"
    rep = sp.check_split_invariants(catalog, clusters, naive)
    assert "zero cross-split hard cluster overlap" in {c.name for c in rep.failures()}


def test_synthetic_cluster_leak_is_caught(catalog, clusters, split):
    leaky = copy.deepcopy(split)
    leaky["splits"]["dev"] = sorted(set(DEV) - {12} | {1})     # #12 (with #11 in dev) -> val
    leaky["splits"]["val"] = sorted(set(VAL) - {1} | {12})
    rep = sp.check_split_invariants(catalog, clusters, leaky)
    failed = {c.name for c in rep.failures()}
    assert {"zero cross-split hard cluster overlap", "zero cross-split conservative cluster overlap",
            "zero cross-split non-PDK .subckt name overlap"} <= failed
    detail = next(c.detail for c in rep.failures() if c.name == "zero cross-split hard cluster overlap")
    assert detail == {"cl_adb_sampling_fb_ota": ["dev", "val"]}


def test_static_scan_catches_task_ids_tests_paths_and_imports(tmp_path, catalog):
    tid = catalog["tasks"][0]["task_id"]
    (tmp_path / "src/ssap/kernel").mkdir(parents=True)
    (tmp_path / "src/ssap/kernel/bad.py").write_text(
        f"BLACKLIST = ['{tid}']\nCONS = '/alb-workspace/tests/task/constraints.json'\nimport eda.verify\n")
    (tmp_path / "container/alw").mkdir(parents=True)
    (tmp_path / "container/alw/settings.json").write_text(json.dumps({"x": f"tasks/{tid}"}))
    (tmp_path / "src/ssap/other.py").write_text(
        "import importlib\nfrom harbor.agents import x\nm = importlib.import_module('analoglayoutworld')\n"
        f"OK_HOST_SIDE = '{tid}'\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests/test_x.py").write_text("__import__('eda')\nS = 'import eda'  # a string, fine\n")
    scan = sp.scan_code(tmp_path, [t["task_id"] for t in catalog["tasks"]])
    got = {(v["path"], v["kind"], v["detail"][:40]) for v in scan["violations"]}
    assert ("src/ssap/kernel/bad.py", "task_id_literal", tid[:40]) in got
    assert any(p == "src/ssap/kernel/bad.py" and k == "tests_path" for p, k, _ in got)
    assert ("src/ssap/kernel/bad.py", "forbidden_import", "eda.verify") in got
    assert ("container/alw/settings.json", "task_id_literal", tid[:40]) in got
    assert ("src/ssap/other.py", "forbidden_import", "harbor.agents") in got
    assert ("src/ssap/other.py", "forbidden_import", "analoglayoutworld") in got
    assert ("tests/test_x.py", "forbidden_import", "eda") in got
    # Host-side code outside the proposer-visible roots may name tasks; only imports are banned there.
    assert not any(p == "src/ssap/other.py" and k == "task_id_literal" for p, k, _ in got)
    assert len([v for v in scan["violations"] if v["path"] == "tests/test_x.py"]) == 1


def test_repository_has_no_forbidden_imports():
    scan = sp.scan_code(ROOT, [])
    assert scan["violations"] == []


# ------------------------------------------------------------------------------------------
# Proposer export (Disposition C1)
# ------------------------------------------------------------------------------------------

def test_export_proposer_catalog_strips_val_test_and_blind(catalog, split):
    blind = split["blind_clusters"]
    out = cat.export_proposer_catalog(catalog, split, blind)
    nums = {t["number"] for t in out["tasks"]}
    blind_tasks = {n for n in DEV if split["cluster_of"][str(n)] in blind}
    assert nums == set(DEV) - blind_tasks
    text = json.dumps(out)
    by = _by_num(catalog)
    for n in VAL + TEST + sorted(blind_tasks):
        assert by[n]["task_id"] not in text
    assert out["withheld"] == {"dev_blind_tasks": len(blind_tasks), "dev_blind_clusters": 3,
                               "non_dev_tasks": 28}
    assert {t["number"] for t in cat.export_proposer_catalog(catalog, split)["tasks"]} == set(DEV)
    with pytest.raises(ValueError):
        cat.export_proposer_catalog(catalog, split, ["cl_no_such_cluster"])


# ------------------------------------------------------------------------------------------
# metrics.json reader
# ------------------------------------------------------------------------------------------

def _metrics(**over) -> dict:
    """A passed evaluation; keyword overrides replace top-level keys, `si` / `stages` merge."""
    m = {
        "status": "evaluated", "evaluation_error": False, "tool_error": False,
        "submission_present": True, "score_valid": True, "score": 1.0, "design_passed": True,
        "score_is_lower_bound": False, "bonuses_eligible": True,
        "stages": {"drc": {"status": "passed"}, "lvs": {"status": "passed"},
                   "area": {"status": "measured"}, "quality": {"status": "completed"},
                   "pex": {"status": "completed"}, "postsim": {"status": "completed"}},
        "scoring_inputs": {"submission_present": True, "geometry_valid": True, "shape_ok": True,
                           "shape_ratio": 1.4, "raw_area_ratio": 0.5, "low_fill": False,
                           "meaningless": False, "lvs_pass": True, "drc_pass": True,
                           "pex_success": True, "postsim_case_fraction": 1.0, "postsim_coverage": 1.0,
                           "postsim_all_passed": True, "morphology_policy_exempt": False},
        "postsim_cases": {"required_case_count": 4, "passed_case_count": 4, "failed_case_count": 0,
                          "completed_case_count": 4, "coverage": 1.0, "all_passed": True},
    }
    for k, v in over.items():
        if k == "si":
            m["scoring_inputs"].update(v)
        elif k == "stages":
            m["stages"].update(v)
        elif k == "cases":
            m["postsim_cases"].update(v)
        else:
            m[k] = v
    return m


def test_metrics_passed():
    r = parse_metrics(_metrics())
    assert (r.Y_star, r.C_star) == (1.0, 1)
    assert r.d_star == {k: 1 for k in D_KEYS}
    assert r.meta["infra_error"] is False and r.meta["task_failure"] is False


def test_metrics_lvs_failed_short_circuits_to_bottom():
    r = parse_metrics(_metrics(
        score=0.1, design_passed=False, bonuses_eligible=False,
        stages={"lvs": {"status": "failed"}, "quality": {"status": "skipped"},
                "pex": {"status": "skipped"}, "postsim": {"status": "skipped"}},
        si={"lvs_pass": False, "pex_success": False, "low_fill": None, "meaningless": None,
            "postsim_coverage": 0.0, "postsim_all_passed": False, "postsim_case_fraction": 0.0},
        cases={"passed_case_count": 0, "completed_case_count": 0, "coverage": 0.0, "all_passed": False}))
    assert (r.Y_star, r.C_star) == (0.1, 0)
    assert r.d_star == {"gds": 1, "drc": 1, "lvs": 0, "shape": 1, "fill": BOTTOM, "morph": BOTTOM,
                        "pex": BOTTOM, "postsim": BOTTOM}
    # Even a stale low_fill / meaningless value is not read when LVS failed.
    r2 = parse_metrics(_metrics(stages={"lvs": {"status": "failed"}},
                                si={"lvs_pass": False, "low_fill": True, "meaningless": True}))
    assert (r2.d_star["fill"], r2.d_star["morph"], r2.d_star["pex"]) == (BOTTOM, BOTTOM, BOTTOM)


def test_metrics_pex_failed():
    r = parse_metrics(_metrics(
        score=0.5, design_passed=False,
        stages={"pex": {"status": "failed", "error_kind": "design"}, "postsim": {"status": "skipped"}},
        si={"pex_success": False, "postsim_coverage": 0.0, "postsim_all_passed": False}))
    assert r.d_star["pex"] == 0 and r.d_star["postsim"] == BOTTOM
    assert r.d_star["lvs"] == 1 and r.d_star["fill"] == 1 and r.C_star == 0


def test_metrics_pex_budget_exhausted_is_bottom():
    r = parse_metrics(_metrics(status="budget_exhausted", score_is_lower_bound=True,
                               stages={"pex": {"status": "failed", "error_kind": "verifier_budget"}},
                               si={"pex_success": False}))
    assert r.d_star["pex"] == BOTTOM and r.meta["infra_error"] is False


def test_metrics_lower_bound_postsim():
    partial = dict(score=0.8, design_passed=False, score_is_lower_bound=True,
                   stages={"postsim": {"status": "measurement_failed", "error_kind": "verifier_budget"}},
                   si={"postsim_coverage": 0.5, "postsim_all_passed": False, "postsim_case_fraction": 0.5})
    r = parse_metrics(_metrics(**partial, cases={"passed_case_count": 2, "failed_case_count": 0,
                                                 "completed_case_count": 2, "coverage": 0.5}))
    assert r.d_star["postsim"] == BOTTOM and r.Y_star == 0.8 and r.C_star == 0
    assert r.meta["score_is_lower_bound"] is True
    r = parse_metrics(_metrics(**partial, cases={"passed_case_count": 1, "failed_case_count": 1,
                                                 "completed_case_count": 2, "coverage": 0.5}))
    assert r.d_star["postsim"] == 0
    r = parse_metrics(_metrics(score=0.9, design_passed=False,
                               si={"postsim_all_passed": False, "postsim_case_fraction": 0.75},
                               cases={"passed_case_count": 3, "failed_case_count": 1}))
    assert r.d_star["postsim"] == 0


def test_metrics_missing_submission_is_a_scored_task_failure():
    r = parse_metrics({
        "status": "missing_submission", "evaluation_error": False, "tool_error": False,
        "submission_present": False, "score_valid": True, "score": 0.0, "design_passed": False,
        "stages": {k: {"status": "skipped"} for k in ("drc", "lvs", "area", "quality", "pex", "postsim")},
        "scoring_inputs": {"submission_present": False, "lvs_pass": False, "pex_success": False}})
    assert (r.Y_star, r.C_star) == (0.0, 0)
    assert r.meta["infra_error"] is False and r.meta["task_failure"] is True
    assert r.d_star == {"gds": 0, **{k: BOTTOM for k in D_KEYS if k != "gds"}}


def test_metrics_invalid_submission_scores_zero():
    r = parse_metrics({"status": "invalid_submission", "evaluation_error": False, "score_valid": True,
                       "score": 0.0, "design_passed": False, "stages": {}, "scoring_inputs": {}})
    assert (r.Y_star, r.C_star, r.d_star["gds"], r.meta["infra_error"]) == (0.0, 0, 0, False)
    # Regular file snapshotted (Tier 0 submission_present=True, 0.10) that fails geometry_valid
    # (truncated GDS or wrong top cell) has d*_gds = 0 and short-circuits d*_shape to bottom.
    r2 = parse_metrics(_metrics(
        score=0.1, design_passed=False,
        stages={"drc": {"status": "failed"}, "lvs": {"status": "failed"},
                "area": {"status": "failed"}, "quality": {"status": "skipped"},
                "pex": {"status": "skipped"}, "postsim": {"status": "skipped"}},
        si={"submission_present": True, "geometry_valid": False, "shape_ok": False,
            "lvs_pass": False, "drc_pass": False, "pex_success": False}))
    assert (r2.Y_star, r2.d_star["gds"], r2.d_star["shape"]) == (0.1, 0, BOTTOM)


def test_metrics_evaluation_error_is_infra():
    r = parse_metrics({"status": "tool_error", "evaluation_error": True, "tool_error": True,
                       "submission_present": False, "score_valid": False, "score": None,
                       "stages": {}, "scoring_inputs": {}})
    assert r.Y_star is None and r.C_star is None
    assert r.meta["infra_error"] is True and r.meta["infra_reason"] == "evaluation_error"
    assert r.d_star["gds"] == BOTTOM
    r = parse_metrics(_metrics(status="interrupted", score_valid=False, score=None))
    assert r.meta["infra_error"] is True and r.Y_star is None


def test_metrics_files(tmp_path):
    assert read_metrics(tmp_path / "absent.json").meta["infra_reason"] == "metrics_missing"
    (tmp_path / "bad.json").write_text("{not json")
    assert read_metrics(tmp_path / "bad.json").meta["infra_error"] is True
    (tmp_path / "ok.json").write_text(json.dumps(_metrics()))
    assert read_metrics(tmp_path / "ok.json").Y_star == 1.0


def test_metrics_magic_policy_exempt_morph():
    r = parse_metrics(_metrics(si={"meaningless": False, "morphology_policy_exempt": True}))
    assert r.d_star["morph"] == 1


# ------------------------------------------------------------------------------------------
# paper_reference.json
# ------------------------------------------------------------------------------------------

def test_paper_reference_numbers(catalog):
    ref = _load("paper_reference.json")
    rows = {(r["system"], r["harness"]): r for r in ref["table1"]}
    assert len(ref["table1"]) == 14
    r = rows[("DeepSeek V4.1 Flash", "Claude Code")]
    assert (r["score"], r["tokens_m"], r["acc"], r["drc"], r["lvs"], r["sim"], r["tool_calls"]) == \
        (0.1974, 13.2, 8.3, 41.7, 29.2, 17.5, 134.0)
    assert rows[("DeepSeek V4 Pro", "Claude Code")]["score"] == 0.1504
    assert rows[("Novara Agent System", None)]["tool_calls"] is None
    w = ref["weights"]
    assert [w[k] for k in ("gds", "lvs", "drc", "area", "quality", "postsim")] == [0.1, 0.1, 0.1, 0.2, 0.1, 0.4]
    assert (w["gamma"], w["rho_min"], w["q_max_values"], w["rho_star_range"]) == (2, 0.1, [3.0, 4.0], [0.6, 1.1])
    st = {k: v["values"] for k, v in ref["statistics"].items()}
    assert st["abstract_submission_acceptance"] == {"configurations": 13, "gds_produced_pct": 91.1,
                                                     "accepted_pct": 21.3}
    assert st["repeated_verification"] == {"drc_first_pass_after_first_call_pct": 90.3,
                                           "lvs_first_pass_after_first_call_pct": 66.8}
    assert st["time_limit_hits"] == {"glm53_flash_hits": 51, "attempts": 120}
    assert st["trials_per_task"] == {"trials": 3} and st["agent_budget_hours"] == {"hours": 3}
    # Table 3 (paper) agrees with the benchmark's own per-task parameters (catalog).
    for t in catalog["tasks"]:
        assert ref["table3_geometry"][str(t["number"])] == {"q_max": t["q_max"], "rho_star": t["u_target"]}


@pytest.mark.skipif(not os.environ.get("ALW_PAPER_DIR"), reason="needs $ALW_PAPER_DIR")
def test_paper_reference_regenerates():
    spec = importlib.util.spec_from_file_location("mpr", ROOT / "scripts/make_paper_reference.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    d = Path(os.environ["ALW_PAPER_DIR"])
    assert mod.build((d / "paper.html").read_text(), (d / "paper.txt").read_text()) == _load("paper_reference.json")


# ------------------------------------------------------------------------------------------
# Config hygiene and CLI
# ------------------------------------------------------------------------------------------

_LOCAL_USER = getpass.getuser()
SECRETISH = re.compile(r"sk-[A-Za-z0-9]{8,}|\b\d{1,3}(\.\d{1,3}){3}\b(?<!127\.0\.0\.1)(?<!0\.0\.0\.0)(?<!172\.17\.0\.1)"
                       r"|://[^\s/@\"']+:[^\s/@\"']+@|"
                       + (rf"\b{re.escape(_LOCAL_USER)}\b" if len(_LOCAL_USER) >= 4 and _LOCAL_USER != "root" else r"(?!)"))


def test_configs_and_sources_hold_no_secrets_or_local_names():
    files = [*CFG.glob("*"), *(ROOT / "src").rglob("*.py"), *(ROOT / "scripts").glob("*")]
    for f in files:
        if f.is_file() and "__pycache__" not in f.parts:
            hits = [m.group(0) for m in SECRETISH.finditer(f.read_text())]
            assert not hits, (f, hits)


def test_server_config_schema():
    raw = json.loads((CFG / "config.server.json").read_text())
    assert raw["alw"]["repo"] == "${ALW_REPO}" and raw["state"] == "${SSAP_STATE}"
    assert raw["agent"]["model_name"] == "${SSAP_MODEL}"
    g = raw["gateway"]
    assert (g["upstream_base"], g["upstream_key_env"], g["upstream_keys_env"]) == \
        ("${SSAP_GATEWAY}", "SSAP_UPSTREAM_KEY", "SSAP_UPSTREAM_KEYS")
    assert {"upstream_auth_header", "upstream_auth_prefix", "proxy_bind", "proxy_port",
            "proxy_advertise"} <= set(g) and g["theta_gw_max"] == 0.35
    wm = raw["watermarks"]
    assert (wm["t_max_sec"], wm["t_warn_sec"], wm["t_soft_sec"], wm["delta_t_rep_sec"],
            wm["delta_s_rep_turns"], wm["min_remaining_for_repair_sec"], wm["tail_buffer_sec"],
            wm["kill_grace_sec"]) == (10800, 8400, 9900, 900, 80, 180, 120, 5)
    assert raw["images"]["agent_image"] == "${SSAP_AGENT_IMAGE:-}"
    assert re.fullmatch(r"ghcr\.io/.+@sha256:[0-9a-f]{64}", raw["images"]["eda_base_image"])
    slots = raw["model_homogeneity"]["slots"]
    assert len(slots) == 8 and "CLAUDE_CODE_SUBAGENT_MODEL" in slots
    assert "parallel" in raw["run"]
    cfg = load_config(CFG / "config.server.json", env={"SSAP_MODEL": "m", "ALW_REPO": "/r"})
    assert cfg["agent"]["model_name"] == "m" and cfg["run"]["parallel"] == "4"
    assert cfg["gateway"]["upstream_auth_header"] == "x-api-key"
    assert interpolate("${A:-d}/${B}", {"A": "", "B": "b"}) == "d/b"


def test_cli_alw_check_split(capsys, monkeypatch):
    monkeypatch.delenv("ALW_REPO", raising=False)
    rc = cli.main(["alw-check-split", "--config", str(CFG / "config.server.json")])
    out = capsys.readouterr().out
    assert rc == 0, out
    assert "ALL INVARIANTS HOLD" in out
