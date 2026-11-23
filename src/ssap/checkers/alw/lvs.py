"""``m_2`` (``m_lvs``): container-local LVS mirror and the run-time task contract.

Contract (top cell, ordered ports, exact-case substrate) is derived from public inputs only:
* ``top``: the ``with top cell `<top>` `` phrase of the captured task prompt, when present, must
  name a defined subcircuit; otherwise the unique call-graph root of ``circuit.spice``;
* ``ports``: the declared pins of ``top`` (exact case, declaration order);
* ``substrate``: the majority NMOS 4th-terminal (bulk) net, followed through every hierarchy
  level back to a ``top`` port (exact case).
* ``floating_reference_ports``: always computed on the unmodified ``circuit.spice``.

Backends:
* ``klayout``: the installed ``sky130.lvs`` deck with ``ignore_extraction_errors(true)`` prepended
  and the ``flag_missing_ports`` / ``ports.json`` epilogue appended, flags ``run_mode=deep``,
  ``convert_subckts``, ``scale``, ``schematic_simplify``, ``spice_net_names``, ``spice_comments``,
  ``verbose`` (all ``true``), ``lvs_sub``, ``thr=2``. Pass = return code 0, ``ports.json`` =
  ``{"completed": true, "top_ports_valid": true}``, no ``Error`` / ``must-connect`` log entry,
  exactly one top pair, top pins matched (floating reference ports exempt), every circuit pair
  ``Match`` and both device counts ``> 0``.
* ``magic_netgen`` (``ALB_NETGEN_BIN`` exported by ``eda_env.sh``): geometry-only flattened GDS,
  Magic ``ext2spice lvs`` extraction, Netgen ``-json`` comparison and the strict report reader.

Only the original deck is run. The outer verifier may retry a comparison mismatch with a private
same-net purge deck, so inner ``m_2 = 1`` implies outer LVS pass (one-sided, conservative).
"""
from __future__ import annotations

import json
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path

from . import spice
from .runtime import (ToolUnavailable, eda_env_exports, load_runtime, require_file, run,
                      tool_env)

PROMPT_TOP = re.compile(r"with top cell `([^`]+)`")
IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*\Z")


class ContractError(ValueError):
    pass


def detect_lvs_backend(eda_env_path: str | Path) -> str:
    """``"magic_netgen"`` iff the frozen ``eda_env.sh`` exports ``ALB_NETGEN_BIN``."""
    return "magic_netgen" if "ALB_NETGEN_BIN" in eda_env_exports(eda_env_path) else "klayout"


@dataclass(frozen=True)
class TaskContract:
    top: str
    ports: list[str]
    substrate: str
    top_source: str = "call_graph_root"
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"top": self.top, "ports": list(self.ports), "substrate": self.substrate,
                "top_source": self.top_source, "notes": list(self.notes)}


def prompt_top(prompt_text: str) -> str | None:
    names = set(PROMPT_TOP.findall(prompt_text or ""))
    return next(iter(names)) if len(names) == 1 else None


def resolve_task_contract(spice_path: str | Path, prompt_path: str | Path | None = None
                          ) -> TaskContract:
    text = Path(spice_path).read_text()
    circuits = spice.parse(text, strict=False)
    roots = spice.call_graph_roots(circuits)
    notes: list[str] = []
    top = None
    source = "call_graph_root"
    if prompt_path is not None and Path(prompt_path).is_file():
        named = prompt_top(Path(prompt_path).read_text(errors="replace"))
        if named is not None and named.casefold() in circuits:
            top, source = circuits[named.casefold()].name, "prompt"
            if roots != [top]:
                notes.append(f"call-graph roots {roots} differ from prompt top {top!r}")
        elif named is not None:
            notes.append(f"prompt top {named!r} is not a defined subcircuit")
    if top is None:
        if len(roots) != 1:
            raise ContractError(f"ambiguous or missing top subcircuit: roots={roots}")
        top = roots[0]
    circuit = circuits[top.casefold()]
    ports = list(circuit.ports)
    counts = spice.nmos_bulk_counts(circuits, top)
    by_key = {p.casefold(): p for p in ports}
    ranked = [(n, c) for n, c in counts.most_common() if n.casefold() in by_key]
    if ranked:
        best = max(c for _, c in ranked)
        winners = sorted(n for n, c in ranked if c == best)
        substrate = by_key[winners[0].casefold()]
        if len(winners) > 1:
            notes.append(f"substrate tie {winners}; chose {substrate!r}")
    else:
        fallback = [p for p in ports if p.casefold() in ("vss", "gnd", "avss", "vssa")]
        if not fallback:
            raise ContractError("no NMOS bulk connects to a top port; substrate unknown")
        substrate = fallback[0]
        notes.append("no NMOS bulk on a top port; ground-named port used")
    return TaskContract(top, ports, substrate, source, notes)


def floating_reference_ports(circuit_spice_path: str | Path, top: str, ports: list[str]
                             ) -> list[str]:
    """Contract ports with no device connection inside ``top`` of the unmodified netlist.
    Without a parsable reference nothing is exempt (the strict pairing rule remains)."""
    try:
        names = {n.casefold() for n in spice.floating_ports(Path(circuit_spice_path).read_text(), top)}
    except (OSError, ValueError):
        return []
    return [p for p in ports if p.casefold() in names]


def layout_port_name(token: str) -> str:
    return token.replace(r"\<", "<").replace(r"\>", ">")


def port_keys(ports: list[str]) -> list[str]:
    return [layout_port_name(p).casefold() for p in ports]


@dataclass
class LvsOutcome:
    passed: bool
    backend: str
    reason: str | None = None
    details: dict = field(default_factory=dict)
    out_dir: str | None = None
    report: str | None = None

    def as_dict(self) -> dict:
        return {"passed": self.passed, "backend": self.backend, "reason": self.reason,
                "details": self.details, "out_dir": self.out_dir, "report": self.report}


# ---------------------------------------------------------------------------------------------
# KLayout backend
# ---------------------------------------------------------------------------------------------

PORTS_EPILOGUE = '''
# Required external-port binding check, using KLayout's official API.
require "json"
alb_ports_valid = flag_missing_ports
File.write($ports_result, JSON.generate({completed: true, top_ports_valid: alb_ports_valid}))
exit(2) unless alb_ports_valid
'''


def klayout_lvs_flags(gds: Path, top: str, schematic: Path, out: Path, substrate: str) -> dict:
    return {"input": str(gds), "top_cell": top, "thr": "2", "report": str(out / "report.lvsdb"),
            "schematic": str(schematic), "target_netlist": str(out / "extracted.spice"),
            "run_mode": "deep", "convert_subckts": "true", "scale": "true",
            "schematic_simplify": "true", "spice_net_names": "true", "spice_comments": "true",
            "verbose": "true", "lvs_sub": layout_port_name(substrate),
            "ports_result": str(out / "ports.json")}


def lvs_report(path: Path, top: str, ports: list[str], floating_ports: list[str] = ()) -> dict:
    """Mirror of the outer report reader (requires ``klayout.db``)."""
    import klayout.db as kdb

    database = kdb.LayoutVsSchematic()
    database.read(str(path))
    entries = list(database.each_log_entry())
    extraction_passed = not any(e.severity == kdb.LogEntryData.Error
                                or e.category_name == "must-connect" for e in entries)
    xref = database.xref()
    if xref is None:
        raise RuntimeError("LVS report has no comparison cross-reference")
    pairs = list(xref.each_circuit_pair())
    if not pairs:
        raise RuntimeError("LVS report contains no compared circuits")
    circuits, top_pairs = [], []
    for pair in pairs:
        a, b = pair.first(), pair.second()
        circuits.append({"layout": a.name if a else None, "reference": b.name if b else None,
                         "status": str(pair.status())})
        if a and b and a.name.casefold() == top.casefold() and b.name.casefold() == top.casefold():
            top_pairs.append(pair)
    top_pair = top_pairs[0] if len(top_pairs) == 1 else None
    if top_pair is None:
        ref_tops = [p for p in pairs if p.second() and p.second().name.casefold() == top.casefold()]
        if not (not top_pairs and len(ref_tops) == 1 and ref_tops[0].first() is None
                and ref_tops[0].status() == kdb.NetlistCrossReference.Mismatch):
            raise RuntimeError("LVS report lacks one unambiguous requested top comparison")
    pin_pairs = [{"layout": p.first().name() if p.first() else None,
                  "reference": p.second().name() if p.second() else None,
                  "status": str(p.status())}
                 for p in xref.each_pin_pair(top_pair)] if top_pair is not None else []
    floating = {layout_port_name(p).casefold() for p in floating_ports}
    expected = {layout_port_name(p).casefold() for p in ports} - floating

    def floating_pair(pp):
        names = {pp[s].casefold() for s in ("layout", "reference") if pp[s] is not None}
        return len(names) == 1 and names <= floating

    checked = [pp for pp in pin_pairs if not floating_pair(pp)]
    ports_match = top_pair is not None and len(checked) == len(expected) and all(
        pp["layout"] is not None and pp["reference"] is not None
        and pp["layout"].casefold() == pp["reference"].casefold() and pp["status"] == "Match"
        for pp in checked) and {pp["layout"].casefold() for pp in checked
                                if pp["layout"] is not None} == expected
    counts = [sum(1 for c in nl.each_circuit() for _ in c.each_device())
              for nl in (xref.netlist_a(), xref.netlist_b())]
    return {"circuits": circuits, "pin_pairs": pin_pairs, "layout_devices": counts[0],
            "reference_devices": counts[1], "top_ports_match": ports_match,
            "extraction_passed": extraction_passed,
            "extraction_diagnostics": [{"severity": str(e.severity), "category": e.category_name,
                                        "message": e.message} for e in entries][:50],
            "matches": extraction_passed and ports_match
                       and all(c["status"] == "Match" for c in circuits)
                       and all(n > 0 for n in counts)}


def run_klayout_lvs(gds: Path, contract: TaskContract, schematic: Path, floating: list[str],
                    out: Path, runtime: dict, timeout: float) -> LvsOutcome:
    deck_src = require_file(runtime["lvs_deck"], "LVS deck")
    if shutil.which(runtime["klayout_bin"]) is None and not Path(runtime["klayout_bin"]).is_file():
        raise ToolUnavailable("klayout binary not found")
    out.mkdir(parents=True, exist_ok=True)
    deck = out / "run.lvs"
    deck.write_text("ignore_extraction_errors(true)\n" + deck_src.read_text() + "\n" + PORTS_EPILOGUE)
    flags = klayout_lvs_flags(gds, contract.top, schematic, out, contract.substrate)
    cmd = [runtime["klayout_bin"], "-b", "-r", str(deck)]
    for k, v in flags.items():
        cmd += ["-rd", f"{k}={v}"]
    ex = run(cmd, cwd=out, timeout=timeout, env=tool_env(out))
    res = LvsOutcome(False, "klayout", out_dir=str(out), report=str(out / "report.lvsdb"))
    res.details["returncode"] = ex.returncode
    if ex.returncode not in (0, 1, 2):
        res.reason = f"klayout exited {ex.returncode}"
        return res
    report = out / "report.lvsdb"
    if not report.is_file() or report.stat().st_size == 0:
        res.reason = "missing report.lvsdb"
        return res
    try:
        res.details.update(lvs_report(report, contract.top, contract.ports, floating))
    except Exception as exc:  # noqa: BLE001 - corrupt report = not certified
        res.reason = f"{type(exc).__name__}: {exc}"
        return res
    ports_ok = False
    if ex.returncode == 0:
        try:
            ports_ok = json.loads((out / "ports.json").read_text()) == {
                "completed": True, "top_ports_valid": True}
        except (OSError, ValueError):
            ports_ok = False
    res.details["ports_json_valid"] = ports_ok
    res.passed = bool(ex.returncode == 0 and ports_ok and res.details.get("matches"))
    if not res.passed:
        res.reason = "LVS mismatch" if ex.returncode == 0 else f"klayout returned {ex.returncode}"
    return res


# ---------------------------------------------------------------------------------------------
# Magic + Netgen backend
# ---------------------------------------------------------------------------------------------

EXTRACT_TCL = """# Inputs arrive as literal environment variables, not Tcl source.
if {[catch {
    drc off
    gds readonly true
    gds rescale false
    gds read $::env(LAYOUT_GDS)
    load $::env(LAYOUT_TOP)
    select top cell
    flatten $::env(LAYOUT_FLAT_TOP)
    load $::env(LAYOUT_FLAT_TOP)
    select top cell
    expand
    set i 1
    foreach pin [split $::env(LAYOUT_PORTS) "\\n"] {
        if {[port $pin exists]} {
            port $pin index $i
        } else {
            port $pin make $i
        }
        incr i
    }
    extract style {ngspice()}
    extract do local
    extract no capacitance
    extract no resistance
    extract no coupling
    extract no adjust
    extract all
    ext2spice lvs
    ext2spice format ngspice
    ext2spice scale off
    ext2spice merge none
    ext2spice hierarchy off
    ext2spice subcircuit top on
    ext2spice global off
    ext2spice short none
    ext2spice extresist off
    ext2spice rthresh infinite
    ext2spice cthresh infinite
    ext2spice -o extracted.spice $::env(LAYOUT_FLAT_TOP).ext
} problem]} {
    puts stderr "ALB_LVS_EXTRACTION_ERROR: $problem"
} else {
    puts "ALB_LVS_EXTRACTION_COMPLETE"
}
quit -noprompt
"""

COMPARE_TCL = """# Native Netgen interface; Tcl lists preserve paths literally.
if {[catch {
    lvs [list $::env(LVS_LAYOUT) $::env(LVS_LAYOUT_TOP)] \\
        [list $::env(LVS_REFERENCE) $::env(LVS_REFERENCE_TOP)] \\
        $::env(LVS_SETUP) report.out -json
} problem]} {
    puts stderr "ALB_NETGEN_ERROR: $problem"
} else {
    puts "ALB_NETGEN_COMPLETE"
}
quit
"""


def geometry_only_gds(source: Path, destination: Path, top: str) -> None:
    """Flatten geometry/text into a fresh database without submitted properties."""
    import klayout.db as kdb

    original = kdb.Layout()
    original.read(str(source))
    cell = original.cell(top)
    if cell is None:
        raise ValueError("Requested extraction top is absent")
    cell.flatten(-1, True)
    clean = kdb.Layout()
    clean.dbu = original.dbu
    flat = clean.create_cell(top)
    for index in original.layer_indices():
        target = clean.layer(original.get_info(index))
        for shape in cell.shapes(index).each():
            flat.shapes(target).insert(shape).clear_properties()
    clean.write(str(destination))


def _pair(value, what: str) -> list:
    if not isinstance(value, list) or len(value) != 2:
        raise ValueError(f"Netgen {what} must contain both compared circuits")
    return value


def _pins(value) -> tuple[list[str], list[str]]:
    sides = _pair(value, "pins")
    if any(not isinstance(p, list) or not p or any(not isinstance(x, str) or not x for x in p)
           for p in sides):
        raise ValueError("Netgen pin comparison is missing or malformed")
    return sides[0], sides[1]


def _devices(value) -> dict:
    if not isinstance(value, list):
        raise ValueError("Netgen device summary must be a list")
    counts: dict[str, int] = {}
    for entry in value:
        if (not isinstance(entry, list) or len(entry) != 2 or not isinstance(entry[0], str)
                or not entry[0] or type(entry[1]) is not int or entry[1] < 0):
            raise ValueError("Netgen device count is missing or malformed")
        counts[entry[0].casefold()] = counts.get(entry[0].casefold(), 0) + entry[1]
    return counts


def netgen_report(path: Path, transcript: Path, layout_top: str, reference_top: str,
                  ports: list[str]) -> dict:
    """Strict Netgen JSON + transcript reader (properties, every port, unique final match)."""
    rows = json.loads(Path(path).read_text())
    if not isinstance(rows, list) or not rows or any(not isinstance(r, dict) or not r for r in rows):
        raise ValueError("Netgen report contains no valid comparisons")
    expected = [layout_top.casefold(), reference_top.casefold()]
    tops = [r for r in rows if isinstance(r.get("name"), list)
            and [str(n).casefold() for n in r["name"]] == expected]
    if len(tops) != 1:
        raise ValueError("Netgen report lacks one unambiguous requested top comparison")
    top = tops[0]
    errors = []
    for index, row in enumerate(rows):
        for key in ("badnets", "badelements", "properties"):
            if key in row:
                if not isinstance(row[key], list):
                    raise ValueError(f"Netgen {key} field is malformed")
                if row[key]:
                    errors.append({"comparison": index, "kind": key})
        if "pins" in row:
            left, right = _pins(row["pins"])
            if port_keys(left) != port_keys(right):
                errors.append({"comparison": index, "kind": "pins"})
        if row is not top and "name" in row:
            raise ValueError("Unexpected hierarchical circuit in flat LVS report")
        if row is not top and "pins" not in row:
            raise ValueError("Incomplete primitive comparison in Netgen report")
    for key in ("devices", "nets", "badnets", "badelements", "pins"):
        if key not in top:
            raise ValueError(f"Netgen top comparison is missing {key}")
    devices = [_devices(side) for side in _pair(top["devices"], "devices")]
    nets = _pair(top["nets"], "nets")
    if any(type(n) is not int or n <= 0 for n in nets):
        raise ValueError("Netgen net counts must be positive integers")
    first, second = _pins(top["pins"])
    ports_match = (len(first) == len(second) == len(ports)
                   and port_keys(first) == port_keys(second)
                   and set(port_keys(first)) == set(port_keys(ports)))
    text = Path(transcript).read_text(errors="replace")
    final = bool(re.search(r"^Final result:\s*Circuits match uniquely\.\s*$", text, re.M))
    textual = bool(re.search(r"Property errors were found|following cells had property errors|"
                             r"following subcells failed to match", text, re.I))
    matches = (not errors and not textual and final and ports_match and nets[0] == nets[1]
               and devices[0] == devices[1] and all(sum(s.values()) > 0 for s in devices))
    return {"matches": matches, "top_ports_match": ports_match,
            "layout_devices": sum(devices[0].values()), "reference_devices": sum(devices[1].values()),
            "nets": nets, "comparison_errors": errors, "final_unique_match": final,
            "textual_property_or_subcell_error": textual}


def extracted_top(path: Path) -> dict:
    """Flat top of a Magic ``extracted.spice``: name, ordered ports, models (structural only)."""
    top, ports, models = None, [], {}
    for _, line in spice.logical_lines(Path(path).read_text()):
        words = line.split()
        head = words[0].casefold()
        if head == ".subckt":
            if top is not None:
                raise ValueError("more than one subcircuit in flat extraction")
            top = words[1]
            ports = [w for w in words[2:] if "=" not in w and w.casefold() != "params:"]
        elif head.startswith("x") and top is not None:
            cut = next((i for i, w in enumerate(words) if "=" in w), len(words))
            models[words[cut - 1].casefold()] = models.get(words[cut - 1].casefold(), 0) + 1
    if top is None:
        raise ValueError("no subcircuit in extraction")
    return {"top": top, "ports": ports, "models": models}


def run_magic_netgen_lvs(gds: Path, contract: TaskContract, reference: Path, out: Path,
                         runtime: dict, timeout: float) -> LvsOutcome:
    import time

    started = time.monotonic()
    if not IDENTIFIER.fullmatch(contract.top):
        raise ContractError("Magic LVS requires a literal valid top name")
    for key in ("magic_bin", "magic_rcfile", "netgen_bin", "netgen_setup"):
        require_file(runtime[key], key)
    out.mkdir(parents=True, exist_ok=True)
    res = LvsOutcome(False, "magic_netgen", out_dir=str(out))
    clean = out / "geometry.gds"
    try:
        geometry_only_gds(gds, clean, contract.top)
    except ImportError as exc:
        raise ToolUnavailable("klayout.db is required for geometry-only GDS") from exc
    except Exception as exc:  # noqa: BLE001
        res.reason = f"geometry: {exc}"
        return res
    flat_top = contract.top + "_lvs"
    mdir, ndir = out / "magic", out / "netgen"
    mdir.mkdir(exist_ok=True)
    ndir.mkdir(exist_ok=True)
    (mdir / "extract.tcl").write_text(EXTRACT_TCL)
    rc = Path(runtime["magic_rcfile"])
    env = tool_env(out)
    env.update(LAYOUT_GDS=str(clean), LAYOUT_TOP=contract.top, LAYOUT_FLAT_TOP=flat_top,
               LAYOUT_PORTS="\n".join(layout_port_name(p) for p in contract.ports),
               PDK_ROOT=str(rc.parents[3]))
    ex = run([runtime["magic_bin"], "-dnull", "-noconsole", "-rcfile", str(rc),
              str(mdir / "extract.tcl")], cwd=mdir, timeout=timeout, env=env)
    log = ex.log_path.read_text(errors="replace")
    if ex.returncode != 0 or "ALB_LVS_EXTRACTION_COMPLETE" not in log:
        res.reason = f"magic extraction failed (exit {ex.returncode})"
        return res
    extracted = mdir / "extracted.spice"
    try:
        info = extracted_top(extracted)
        if info["top"].casefold() != flat_top.casefold() or port_keys(info["ports"]) != port_keys(contract.ports):
            raise ValueError(f"extracted top/ports differ: {info['top']} {info['ports']}")
    except (OSError, ValueError) as exc:
        res.reason = f"extracted netlist: {exc}"
        return res
    res.details["extracted_netlist"] = info
    (ndir / "compare.tcl").write_text(COMPARE_TCL)
    env.update(LVS_LAYOUT=str(extracted), LVS_LAYOUT_TOP=flat_top, LVS_REFERENCE=str(reference),
               LVS_REFERENCE_TOP=contract.top, LVS_SETUP=str(runtime["netgen_setup"]))
    remaining = timeout - (time.monotonic() - started)
    ex = run([runtime["netgen_bin"], "-batch", "source", str(ndir / "compare.tcl")], cwd=ndir,
             timeout=max(1.0, remaining), env=env)
    nlog = ex.log_path.read_text(errors="replace")
    if ex.returncode != 0 or "ALB_NETGEN_COMPLETE" not in nlog:
        res.reason = f"netgen failed (exit {ex.returncode})"
        return res
    if re.search(r"errors reading the setup file|Cannot read the setup file|"
                 r"Setup file .+ does not exist|No setup file specified|Error .+:\d+ \(ignoring\)",
                 nlog, re.I):
        res.reason = "netgen setup not loaded"
        return res
    try:
        res.details.update(netgen_report(ndir / "report.json", ndir / "report.out", flat_top,
                                         contract.top, contract.ports))
    except (OSError, ValueError) as exc:
        res.reason = f"netgen report: {exc}"
        return res
    res.report = str(ndir / "report.json")
    res.passed = bool(res.details.get("matches"))
    if not res.passed:
        res.reason = "LVS mismatch"
    return res


def lvs_reference_path(task_dir: Path) -> Path:
    """``lvs_reference.spice`` when supplied (reader-syntax view of the same devices)."""
    alt = Path(task_dir) / "lvs_reference.spice"
    return alt if alt.is_file() else Path(task_dir) / "circuit.spice"


def check_lvs(gds: Path, contract: TaskContract, task_dir: Path, backend: str, out: Path,
              timeout: float, runtime: dict | None = None) -> LvsOutcome:
    runtime = runtime or load_runtime()
    task_dir = Path(task_dir)
    if backend == "magic_netgen":
        return run_magic_netgen_lvs(gds, contract, task_dir / "circuit.spice", out, runtime, timeout)
    floating = floating_reference_ports(task_dir / "circuit.spice", contract.top, contract.ports)
    res = run_klayout_lvs(gds, contract, lvs_reference_path(task_dir), floating, out, runtime, timeout)
    res.details["floating_reference_ports"] = floating
    return res
