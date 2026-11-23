"""Milestone 2: ALW mirror checkers against the read-only ALW checkout, and an end-to-end
simulation of ``mcr_entrypoint.py`` / ``mcr_hook.py`` with a fake ``claude`` binary.

The ALW checkout is read as data only (``$ALW_REPO``, default the sibling checkout); nothing from
it is imported. EDA tools are not required: where they are absent the LVS / DRC mirror reports
``m = 0`` (tool unavailable), which is exactly the short-circuit path under test.
"""
from __future__ import annotations

import ast
import json
import os
import shutil
import subprocess
import sys
import textwrap
import time
from pathlib import Path

import pytest

from ssap.checkers.alw import AlwContext, AlwSuite, alw_registry, area_fill, drc, gds_geom, lvs
from ssap.checkers.alw import morphology as morph
from ssap.kernel.promote import sha256_file

ROOT = Path(__file__).resolve().parents[1]
BIN = ROOT / "container" / "alw" / "bin"
ALW = Path(os.environ.get("ALW_REPO") or (ROOT.parents[2] / "external" / "AnalogLayoutWorld" / "repo"))
needs_alw = pytest.mark.skipif(not (ALW / "tasks").is_dir(), reason="ALW checkout not available")
SMALL_TASK = "sky130-adb-ota-5t-gain40-pm60-noise50uv-pvt-layout"


def _tasks() -> list[Path]:
    return sorted(p for p in (ALW / "tasks").iterdir() if (p / "task.toml").is_file())


def _constraints(t: Path) -> dict:
    return json.loads((t / "tests" / "task" / "constraints.json").read_text())


# ------------------------------------------------------------------------------------------
# task contract over all 40 tasks
# ------------------------------------------------------------------------------------------

@needs_alw
def test_all_40_tasks_backend_contract_and_reference_copy():
    tasks = _tasks()
    assert len(tasks) == 40
    rows = []
    for t in tasks:
        c = _constraints(t)
        env = t / "environment"
        spice = env / "task" / "circuit.spice"
        assert spice.read_bytes() == (t / "tests" / "task" / "circuit.spice").read_bytes(), t.name
        assert lvs.detect_lvs_backend(env / "eda_env.sh") == c.get("lvs_backend", "klayout"), t.name
        expected = (c["top"], c["ports"], c.get("substrate", "vss"))
        with_prompt = lvs.resolve_task_contract(spice, t / "instruction.md")
        spice_only = lvs.resolve_task_contract(spice)
        assert (with_prompt.top, with_prompt.ports, with_prompt.substrate) == expected, t.name
        assert (spice_only.top, spice_only.ports, spice_only.substrate) == expected, t.name
        assert with_prompt.top_source == "prompt" and spice_only.top_source == "call_graph_root"
        rows.append((t.name, c))
    backends = sorted(t for t, c in rows if c.get("lvs_backend") == "magic_netgen")
    assert len(backends) == 2
    substrates = {c.get("substrate", "vss") for _, c in rows}
    assert {"sourceNmos", "vssana", "AVSS", "dvss", "gnd", "VSS", "vss"} <= substrates
    assert sum(c.get("substrate") == "sourceNmos" for _, c in rows) == 3


@needs_alw
def test_hierarchical_bulk_and_nested_scope():
    div2 = ALW / "tasks" / "sky130-adb-transistor-divide-by-2-layout" / "environment/task/circuit.spice"
    from ssap.checkers.alw import spice
    circuits = spice.parse(div2.read_text(), strict=False)
    top = lvs.resolve_task_contract(div2)
    # every top-level statement is a subcircuit call: the bulk net is found only through hierarchy
    assert all(d.card == "X" and d.model.casefold() in circuits for d in circuits[top.top.casefold()].devices)
    assert spice.nmos_bulk_counts(circuits, top.top).most_common(1)[0][0] == "vss"
    nested = ALW / "tasks" / "sky130-local-bandgap-layout" / "environment/task/circuit.spice"
    assert lvs.resolve_task_contract(nested).top == "bandgap"            # bgr_ota nested inside
    assert circuits_parent(nested, "bgr_ota") == "bandgap"


def circuits_parent(path: Path, name: str) -> str | None:
    from ssap.checkers.alw import spice
    return spice.parse(path.read_text(), strict=False)[name].parent


@needs_alw
def test_floating_reference_ports_on_unmodified_circuit():
    found = {}
    for t in _tasks():
        c = _constraints(t)
        fl = lvs.floating_reference_ports(t / "environment/task/circuit.spice", c["top"], c["ports"])
        assert set(fl) <= set(c["ports"])
        if fl:
            found[t.name] = fl
    name = "sky130-adb-gmr-degenerated-diff-amp-gain2-bw70m-power1p2mw-sdr60-layout"
    assert found == {name: ["VCM"]}
    # independent text check: VCM is declared on the top but used by no statement inside it
    text = (ALW / "tasks" / name / "environment/task/circuit.spice").read_text()
    body = text[text.lower().index(".subckt gmr_degen"):].split("\n")
    body = body[1:next(i for i, line in enumerate(body) if line.lower().startswith(".ends"))]
    assert "vcm" in text.lower().split(".subckt gmr_degen")[1].split("\n")[0]
    assert not any("vcm" in line.lower().split() for line in body if not line.startswith("*"))


def test_quote_aware_parameters_do_not_leak_into_nodes(tmp_path):
    p = tmp_path / "c.spice"
    p.write_text(textwrap.dedent("""\
        * quoted parameters with spaces and comment markers
        .subckt amp vdd out in VSS
        XM1 out in VSS VSS sky130_fd_pr__nfet_01v8 L=0.15 W=1 nf=2 ad='int((nf+1)/2) * W/nf * 0.29'
        + as={W ; 2} m=1 ; trailing comment
        XM2 out in vdd vdd sky130_fd_pr__pfet_01v8 L=0.15 W=2
        .ends amp
        """))
    c = lvs.resolve_task_contract(p)
    assert (c.top, c.ports, c.substrate) == ("amp", ["vdd", "out", "in", "VSS"], "VSS")
    assert lvs.floating_reference_ports(p, "amp", c.ports) == []
    p.write_text(".subckt amp a b n params: w=1\nXM1 a b n n sky130_fd_pr__nfet_01v8\n.ends\n")
    assert lvs.floating_reference_ports(p, "amp", ["a", "b", "n"]) == []   # strict parse fails


# ------------------------------------------------------------------------------------------
# geometry, shape, fill, morphology, DRC, Netgen readers
# ------------------------------------------------------------------------------------------

@needs_alw
def test_manufacturing_layers_match_outer_verifier_exactly():
    tree = ast.parse((ALW / "src/eda/verify.py").read_text())
    node = next(n for n in tree.body if isinstance(n, ast.Assign)
                and any(getattr(t, "id", None) == "MANUFACTURING_LAYERS" for t in n.targets))
    outer = eval(compile(ast.Expression(node.value), "verify.py", "eval"), {"__builtins__": {"range": range}})
    assert len(outer) == 38 and gds_geom.MANUFACTURING_LAYERS == outer


def test_geometry_mirror_pure_backend(tmp_path):
    p = gds_geom.write_gds(tmp_path / "l.gds", {
        "cell": {"boxes": [(68, 20, 0, 0, 4, 1)]},
        "TOP": {"boxes": [(65, 20, 0, 0, 10, 10), (235, 4, -50, -50, 50, 50), (68, 5, 0, 0, 99, 99)],
                "refs": [("cell", 10, 0)], "texts": [(68, 5, 1, 1, "vdd")]}})
    g = gds_geom.check_geometry(p, "TOP", backend="pure")
    assert g.bbox_um == (0.0, 0.0, 14.0, 10.0) and set(g.present_layers) == {"diff", "met1"}
    with pytest.raises(gds_geom.GeometryError):
        gds_geom.check_geometry(p, "top", backend="pure")              # exact-case top
    only_aux = gds_geom.write_gds(tmp_path / "aux.gds", {"TOP": {"boxes": [(235, 4, 0, 0, 5, 5)]}})
    with pytest.raises(gds_geom.GeometryError, match="manufacturing"):
        gds_geom.check_geometry(only_aux, "TOP", backend="pure")
    (tmp_path / "junk.gds").write_bytes(b"not a gds stream at all")
    with pytest.raises(gds_geom.GeometryError):
        gds_geom.check_geometry(tmp_path / "junk.gds", "TOP", backend="pure")
    (tmp_path / "ln.gds").symlink_to(p)
    assert not gds_geom.regular_nonempty_file(tmp_path / "ln.gds")
    assert gds_geom.gds_header_ok(p) and not gds_geom.gds_header_ok(tmp_path / "junk.gds")


def test_class2_artifact_advisories(tmp_path):
    assert gds_geom.advisory_missing_src_py(None)
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "README").write_text("x")
    assert gds_geom.advisory_missing_src_py(tmp_path / "src")
    (tmp_path / "src" / "gen.py").write_text("pass\n")
    assert not gds_geom.advisory_missing_src_py(tmp_path / "src")
    for d in ("live", "trusted"):
        (tmp_path / d).mkdir()
        (tmp_path / d / "circuit.spice").write_text("* same\n")
    assert not gds_geom.advisory_task_modified(tmp_path / "live", tmp_path / "trusted")
    (tmp_path / "live" / "circuit.spice").write_text("* edited\n")
    assert gds_geom.advisory_task_modified(tmp_path / "live", tmp_path / "trusted")


def test_shape_margin_eps3_lock_and_aspect_advisory():
    assert area_fill.shape_raw_margin(30.0, 10.0) == pytest.approx(1.0)
    assert area_fill.shape_raw_margin(10.0, 45.0) < 0
    assert area_fill.advisory_aspect_gt_3(35.0, 10.0)
    assert not area_fill.advisory_aspect_gt_3(30.0, 10.0)
    assert not area_fill.advisory_aspect_gt_3(41.0, 10.0)            # q > 4: Class-1 m_3 = 0
    with pytest.raises(ValueError, match="locked"):
        area_fill.validate_shape_eps(0.5)
    with pytest.raises(ValueError, match="locked"):
        alw_registry(eps_shape=1.0)
    reg = alw_registry()
    assert reg.get("m_shape").eps == 0.0 and reg.get("m_fill").eps == 1e-4
    assert reg.gate_triggers() == ["advisory_aspect_gt_3"]
    pex = reg.get("m_pex")
    assert pex.scope == "terminal_only" and not pex.enabled_in_hbase
    assert reg.evaluation_path(reg.with_change("m_pex", enabled_in_hbase=True)) == "path1"
    assert reg.evaluation_path(reg.with_change("m_pex", enabled_in_hbase=True, scope="trajectory")) == "path2"
    assert area_fill.fill_raw_margin(None, 100.0, lvs_passed=False) is None
    assert area_fill.fill_raw_margin(20.0, 100.0, lvs_passed=True) == pytest.approx(0.10)


def test_magic_extracted_area_wl_plus_as_ad(tmp_path):
    p = tmp_path / "extracted.spice"
    p.write_text(textwrap.dedent("""\
        * NGSPICE file created from ota_lvs.ext - technology: sky130A

        .subckt ota_lvs vdd vss out
        X0 out vss vss vss sky130_fd_pr__nfet_01v8 ad=0.29 pd=2.58 as=0.29 ps=2.58 w=1 l=0.15
        X1 out vdd vdd vdd sky130_fd_pr__pfet_01v8 ad=0.58 pd=4.58 as=0.58 ps=4.58 w=2 l=1.5k
        X2 out vss sky130_fd_pr__cap_mim_m3_1 l=10 w=10
        .ends
        """))
    script = tmp_path / "extract.tcl"
    script.write_text(lvs.EXTRACT_TCL)
    area = area_fill.measure_extracted_area(p, extraction_script=script, expected_top="ota_lvs")
    assert area["area_um2"] == pytest.approx((0.15 + 0.58) + (2 * 1500 + 1.16) + 100.0)
    assert area["kind_totals_um2"]["capacitor"] == pytest.approx(100.0)
    bad = tmp_path / "bad.spice"
    bad.write_text(p.read_text().replace("sky130_fd_pr__cap_mim_m3_1", "sky130_fd_pr__res_high_po"))
    with pytest.raises(ValueError, match="Unsupported"):
        area_fill.measure_extracted_area(bad)
    pex_tcl = tmp_path / "pex.tcl"
    from ssap.checkers.alw import pex
    pex_tcl.write_text(pex.PEX_TCL)
    with pytest.raises(ValueError, match="not qualified"):          # C/RC PEX is not LVS evidence
        area_fill.measure_extracted_area(p, extraction_script=pex_tcl)


def test_klayout_parameter_area_scale_true():
    p = {"W": 1.0e6, "L": 0.15e6, "AS": 0.29e12, "AD": 0.29e12}
    assert area_fill.parameter_area("MOS", p) == pytest.approx(0.15 + 0.58)
    assert area_fill.parameter_area("C", {"A": 100e12}) == pytest.approx(100.0)
    with pytest.raises(ValueError):
        area_fill.parameter_area("MOS", {"W": 1e6, "L": 1e5})


def test_drc_report_parser(tmp_path):
    clean = tmp_path / "clean.lyrdb"
    clean.write_text("<report-database><top-cell>ota</top-cell><categories><category><name>m1.1"
                     "</name></category></categories><cells/><items/></report-database>")
    assert drc.drc_report(clean, "ota") == {"markers": 0, "categories": {}, "matches": True}
    dirty = tmp_path / "dirty.lyrdb"
    dirty.write_text("<report-database><top-cell>ota</top-cell><categories><category><name>m1.1"
                     "</name></category></categories><items><item><category>'m1.1'</category></item>"
                     "<item><category>'m1.1'</category></item><item><category>'li.3'</category>"
                     "</item></items></report-database>")
    r = drc.drc_report(dirty, "ota")
    assert r["markers"] == 3 and r["categories"] == {"'m1.1'": 2, "'li.3'": 1} and not r["matches"]
    with pytest.raises(RuntimeError):
        drc.drc_report(dirty, "OTA")
    cmd = drc.drc_command({"klayout_bin": "klayout", "drc_deck": "/d/sky130A_mr.drc"},
                          Path("/w/l.gds"), "ota", Path("/w/r.lyrdb"))
    flags = dict(cmd[i + 1].split("=", 1) for i in range(len(cmd)) if cmd[i] == "-rd")
    assert flags == {"input": "/w/l.gds", "top_cell": "ota", "thr": "2", "report": "/w/r.lyrdb",
                     "feol": "true", "beol": "true", "offgrid": "true", "floating_met": "true",
                     "sram_exclude": "false", "seal": "false", "info": "false"}


def test_klayout_lvs_flags_and_netgen_report(tmp_path):
    c = lvs.TaskContract("ota", ["vdd", "vss", "out"], "vss")
    f = lvs.klayout_lvs_flags(Path("/w/l.gds"), c.top, Path("/t/circuit.spice"), tmp_path, c.substrate)
    assert {k: f[k] for k in ("run_mode", "convert_subckts", "scale", "schematic_simplify",
                               "spice_net_names", "spice_comments", "verbose", "lvs_sub", "thr")} == {
        "run_mode": "deep", "convert_subckts": "true", "scale": "true", "schematic_simplify": "true",
        "spice_net_names": "true", "spice_comments": "true", "verbose": "true", "lvs_sub": "vss",
        "thr": "2"}
    assert "flag_missing_ports" in lvs.PORTS_EPILOGUE and "ports_result" in lvs.PORTS_EPILOGUE
    rows = [{"name": ["ota_lvs", "ota"], "devices": [[["nfet", 2]], [["nfet", 2]]], "nets": [5, 5],
             "badnets": [], "badelements": [], "pins": [["vdd", "vss", "out"], ["vdd", "vss", "out"]]},
            {"pins": [["a"], ["a"]]}]
    (tmp_path / "report.json").write_text(json.dumps(rows))
    (tmp_path / "report.out").write_text("...\nFinal result: Circuits match uniquely.\n")
    assert lvs.netgen_report(tmp_path / "report.json", tmp_path / "report.out", "ota_lvs", "ota",
                             c.ports)["matches"]
    rows[0]["properties"] = [["w mismatch"]]
    (tmp_path / "report.json").write_text(json.dumps(rows))
    assert not lvs.netgen_report(tmp_path / "report.json", tmp_path / "report.out", "ota_lvs", "ota",
                                 c.ports)["matches"]


def test_morphology_judge_and_features():
    devs = [morph.Dev((x * 12.0, 0.0, x * 12.0 + 10.0, 5.0), "n", "vss") for x in range(8)]
    nets = [morph.NetGeom(f"n{i}", [("met2", (0.0, 10.0 + i, 95.0, 10.3 + i)),
                                    ("met1", (5.0 * i, 0.0, 5.0 * i + 0.3, 12.0))]) for i in range(6)]
    nets.append(morph.NetGeom("vss", [("met1", (0.0, -2.0, 95.0, -1.5))]))
    f = morph.features(nets, devs, (0.0, -2.0, 96.0, 16.0))
    assert f["bus_direction"] == "H" and f["bus_nets"] == 6 and f["drops"] == 6
    assert f["largest_row_fraction"] == 1.0 and f["row_elongation"] >= 4.0 and "vss" in f["supply_nets"]
    assert morph.judge(f)["meaningless"]
    assert not morph.judge({**f, "drops": 4})["meaningless"]
    assert not morph.judge({**f, "row_elongation": 3.9})["meaningless"]
    assert morph.check_morphology("magic_netgen", True, None, (0, 0, 1, 1))[0] == 0.0
    assert morph.check_morphology("klayout", False, None, (0, 0, 1, 1))[0] is None


# ------------------------------------------------------------------------------------------
# suite: m_2 = 0 short-circuits m_4 / m_5
# ------------------------------------------------------------------------------------------

def _trusted_copy(tmp_path: Path, task: str = SMALL_TASK) -> Path:
    t = ALW / "tasks" / task / "environment"
    ws = tmp_path / "alb"
    shutil.copytree(t / "task", ws / "task")
    shutil.copy2(t / "eda_env.sh", ws / "eda_env.sh")
    (ws / ".mcr" / "trusted_task").mkdir(parents=True)
    shutil.copytree(t / "task", ws / ".mcr" / "trusted_task" / "task")
    shutil.copy2(t / "eda_env.sh", ws / ".mcr" / "trusted_task" / "eda_env.sh")
    return ws


def _toy_layout(path: Path, top: str, w: float, h: float) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    return gds_geom.write_gds(path, {top: {"boxes": [(65, 20, 0, 0, w, h), (68, 20, 0, 0, 1, 1)]}})


@needs_alw
def test_suite_lvs_failure_short_circuits_fill_and_morph(tmp_path):
    ws = _trusted_copy(tmp_path)
    no_tools = {"klayout_bin": str(tmp_path / "none/klayout"), "lvs_deck": str(tmp_path / "none.lvs"),
                "drc_deck": str(tmp_path / "none.drc")}
    suite = AlwSuite(AlwContext.for_workspace(ws, runtime={**no_tools}))
    cert = suite.evaluate(_toy_layout(tmp_path / "l.gds", "ota_5t", 35.0, 10.0), None)
    assert cert.raw_u[0] == 0.0 and cert.raw_u[1] == -1.0
    assert cert.raw_u[3] is None and cert.raw_u[4] is None          # bottom: not evaluated
    assert cert.m() == (1, 0, 1, 0, 0, 0) and not cert.check_timed_out
    assert cert.advisory_flags == {"advisory_missing_src_py": True, "advisory_task_modified": False,
                                   "advisory_aspect_gt_3": True}
    assert suite.gate_advisories(cert) == ["advisory_aspect_gt_3"]
    wrong_top = suite.evaluate(_toy_layout(tmp_path / "w.gds", "OTA_5T", 10.0, 10.0), None)
    assert wrong_top.m() == (0,) * 6 and wrong_top.file_present


# ------------------------------------------------------------------------------------------
# end-to-end: entrypoint + hook with a fake claude
# ------------------------------------------------------------------------------------------

FAKE_CLAUDE = r'''#!{python}
import json, os, subprocess, sys, time
from pathlib import Path
argv = sys.argv[1:]
log = Path(os.environ["FAKE_LOG_DIR"])
if "--version" in argv:
    print("2.1.302 (Claude Code)")
    sys.exit(0)
phase = os.environ.get("MCR_PHASE", "?")
prompt = sys.stdin.read()
(log / f"argv_{{phase}}.json").write_text(json.dumps({{"argv": argv, "prompt": prompt,
    "config_dir": os.environ.get("CLAUDE_CONFIG_DIR")}}))
ws = Path(os.environ["MCR_WORKSPACE"])
sys.path.insert(0, os.environ["MCR_HARNESS_PYTHON"])
from ssap.checkers.alw.gds_geom import write_gds
draft = ws / "work" / "draft"
(draft / "src").mkdir(parents=True, exist_ok=True)
binr = os.environ["FAKE_MCR_BIN"]
def promote():
    out = subprocess.run([sys.executable, binr + "/mcr_promote"], capture_output=True, text=True, cwd=ws)
    return json.loads(out.stdout)
def hook(tool="Bash"):
    ev = {{"hook_event_name": "PostToolUse", "tool_name": tool, "tool_input": {{}}}}
    out = subprocess.run([sys.executable, binr + "/mcr_hook.py"], input=json.dumps(ev),
                         capture_output=True, text=True, cwd=ws)
    return out.stdout
if phase == "A":
    print(json.dumps({{"type": "system", "subtype": "init", "phase": "A"}}), flush=True)
    write_gds(draft / "layout.gds", {{"ota_5t": {{"boxes": [(65, 20, 0, 0, 50, 10)]}}}})   # q = 5
    (draft / "src" / "gen.py").write_text("print('v1')\n")
    (log / "promote_A.json").write_text(json.dumps(promote()))
    (ws / "submission" / "layout.gds").write_bytes(b"agent overwrote this directly")
    (log / "hook_A.txt").write_text(hook("Write"))
    stray = subprocess.Popen(["sleep", "300"], cwd=ws)
    (log / "stray.pid").write_text(str(stray.pid))
    write_gds(draft / "layout.gds", {{"ota_5t": {{"boxes": [(65, 20, 0, 0, 60, 10)]}}}})   # unevaluated
    print(json.dumps({{"type": "result", "subtype": "success"}}), flush=True)
else:
    print(json.dumps({{"type": "system", "phase": phase}}), flush=True)
    write_gds(draft / "layout.gds", {{"ota_5t": {{"boxes": [(65, 20, 0, 0, 20, 10)]}}}})   # q = 2
    (draft / "src" / "gen.py").write_text("print('v2')\n")
    (log / "promote_B.json").write_text(json.dumps(promote()))
    (log / "hook_B.txt").write_text(hook())
'''


def _alive(pid: int) -> bool:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text()
    except OSError:
        return False
    return stat[stat.rfind(")") + 2] != "Z"


@needs_alw
def test_entrypoint_and_hook_end_to_end_with_fake_claude(tmp_path):
    ws = tmp_path / "alb"
    t = ALW / "tasks" / SMALL_TASK
    shutil.copytree(t / "environment" / "task", ws / "task")
    shutil.copy2(t / "environment" / "eda_env.sh", ws / "eda_env.sh")
    logs, snap = tmp_path / "fake_logs", tmp_path / "logs_agent" / "mcr_snapshot"
    logs.mkdir()
    fake = tmp_path / "bin" / "claude"
    fake.parent.mkdir()
    fake.write_text(FAKE_CLAUDE.format(python=sys.executable))
    fake.chmod(0o755)
    none = tmp_path / "no-eda"
    (none / "runtime.json").parent.mkdir()
    env = {k: v for k, v in os.environ.items() if not k.startswith(("ANTHROPIC_", "CLAUDE_", "MCR_"))}
    env.update(MCR_WORKSPACE=str(ws), MCR_SNAPSHOT_DIR=str(snap), MCR_REAL_CLAUDE=str(fake),
               MCR_HARNESS_PYTHON=str(ROOT / "src"), MCR_CONFIG=str(tmp_path / "absent.json"),
               FAKE_LOG_DIR=str(logs), FAKE_MCR_BIN=str(BIN), PATH=f"{none}:/usr/bin:/bin",
               MCR_T_WARN_SEC="0.01", MCR_T_SOFT_SEC="60", MCR_T_MAX_SEC="1000",
               MCR_DELTA_T_REP_SEC="60", MCR_DELTA_S_REP_TURNS="7", MCR_TAIL_BUFFER_SEC="10",
               MCR_MIN_REMAINING_FOR_REPAIR_SEC="5", MCR_KILL_GRACE_SEC="1",
               SSAP_UPSTREAM_KEY="sk-live-SECRETVALUE123", ANTHROPIC_API_KEY="trial-token-XYZ987")

    version = subprocess.run([sys.executable, str(BIN / "mcr_entrypoint.py"), "--version"],
                             env=env, capture_output=True, text=True, timeout=60)
    assert version.returncode == 0 and version.stdout.strip() == "2.1.302 (Claude Code)"

    prompt = "Design the layout with top cell `ota_5t` and submit it.\n"
    argv = ["--verbose", "--output-format=stream-json", "--settings", "/tmp/s.json",
            "--resume", "abc", "--print"]
    started = time.monotonic()
    run = subprocess.run([sys.executable, str(BIN / "mcr_entrypoint.py"), *argv], input=prompt,
                         env=env, capture_output=True, text=True, timeout=300)
    assert run.returncode == 0, run.stderr
    assert time.monotonic() - started < 120
    out_lines = [json.loads(x) for x in run.stdout.splitlines() if x.strip()]
    assert [x.get("phase") for x in out_lines if x.get("type") == "system"] == ["A"]   # B not on stdout

    mcr = ws / ".mcr"
    assert (mcr / "task_prompt.txt").read_text() == prompt
    trusted = mcr / "trusted_task"
    assert (trusted / "task" / "circuit.spice").read_bytes() == (ws / "task" / "circuit.spice").read_bytes()
    assert (trusted / "eda_env.sh").is_file() and not os.access(trusted / "task" / "circuit.spice", os.W_OK)
    assert "task/circuit.spice" in json.loads((mcr / "input_hashes.json").read_text())["files"]

    a = json.loads((logs / "argv_A.json").read_text())
    assert a["argv"] == argv and a["prompt"] == prompt
    pa = json.loads((logs / "promote_A.json").read_text())
    assert pa["promoted"] and pa["reason"] == "tier0_bootstrap"
    assert pa["candidate_cert"]["m"] == [1, 0, 0, 0, 0, 0]
    hook_a = json.loads((logs / "hook_A.txt").read_text())
    ctx = hook_a["hookSpecificOutput"]["additionalContext"]
    assert hook_a["hookSpecificOutput"]["hookEventName"] == "PostToolUse"
    assert "_direct_snapshot/0001_" in ctx and "restored to the certified best" in ctx
    assert "Time warning" in ctx
    snaps = sorted((ws / "work/draft/_direct_snapshot").iterdir())
    assert len(snaps) == 1 and (snaps[0] / "layout.gds").read_bytes() == b"agent overwrote this directly"
    assert not _alive(int((logs / "stray.pid").read_text()))

    gate = json.loads((mcr / "gate_report.json").read_text())
    fork = json.loads((mcr / "fork_meta.json").read_text())
    assert gate["gate_triggered"] and "m_best_not_all_certified" in gate["trigger_reasons"]
    assert fork["granted_repair_budget_sec"] == 60.0 and fork["granted_repair_turns"] == 7
    # the last Phase A draft (q = 6) was evaluated at exit and refused against the best (q = 5)
    assert fork["pending_promotion"] == {"promoted": False, "reason": "same_tier_margin_regression"}
    assert fork["phase_a"]["stray_processes_terminated"] >= 1 and not fork["phase_a"]["timed_out"]
    assert gate["trigger_reasons"] == ["m_best_not_all_certified"]       # refused draft: no re-trigger
    pre = snap / "pre_repair" / "workspace"
    assert (pre / ".mcr" / "gate_report.json").is_file() and (pre / "work/draft/layout.gds").is_file()
    assert (pre / "task" / "circuit.spice").is_file() and (pre / "submission" / "layout.gds").is_file()

    b = json.loads((logs / "argv_B.json").read_text())
    assert "--no-session-persistence" in b["argv"] and "--resume" not in b["argv"]
    assert "abc" not in b["argv"] and "--print" not in b["argv"]
    assert b["argv"] == ["--verbose", "--output-format=stream-json", "--settings", "/tmp/s.json",
                         "-p", "--no-session-persistence", "--max-turns", "7"]
    assert "--settings" in b["argv"] and b["config_dir"] == str(mcr / "phase_b_sessions")
    assert "gate_triggered" in b["prompt"] and "mcr_promote" in b["prompt"]
    pb = json.loads((logs / "promote_B.json").read_text())
    assert pb["promoted"] and pb["candidate_cert"]["m"] == [1, 0, 1, 0, 0, 0]
    assert "Time warning" not in (logs / "hook_B.txt").read_text()          # T_warn off in Phase B
    stream = (snap / "phase_b_stream.jsonl").read_text()
    assert '"phase": "B"' in stream
    trace = json.loads((snap / "repair_trace.json").read_text())
    assert trace["ratchet_promoted"] and (snap / "post_repair" / "manifest.json").is_file()
    assert (snap / "post_repair" / "submission" / "src" / "gen.py").read_text() == "print('v2')\n"

    summary = json.loads((snap / "artifact_summary.json").read_text())
    assert summary["repair_ran"] and summary["phase_b_invoked"] and summary["ratchet_promoted"]
    assert summary["gate_triggered"] and summary["m_post"] == [1, 0, 1, 0, 0, 0]
    assert summary["submission_gds_sha256"] == sha256_file(ws / "submission" / "layout.gds")
    assert summary["post_repair_gds_sha256"] == summary["submission_gds_sha256"]
    assert summary["pre_repair_gds_sha256"] != summary["post_repair_gds_sha256"]
    text = (snap / "artifact_summary.json").read_text() + (snap / "artifact_summary.md").read_text()
    assert "SECRETVALUE123" not in text and "XYZ987" not in text
