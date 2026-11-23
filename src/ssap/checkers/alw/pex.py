"""Phase 2/3 candidate ``m_pex``: Magic ``ngspice()`` RC extraction + netlist inspection.

Registered with ``enabled_in_hbase=False`` and ``scope="terminal_only"``: it is not part of
``H_base``'s trajectory mirror vector. Enabling it as a terminal gate keeps evaluation on Path 1;
enabling it mid-trajectory changes Phase A (Path 2). It must pass the Stage 0 concordance audit
on the dev bank before it may hold overwrite authority.

Pass = Magic completes with the ``PEX_COMPLETE`` marker, ``extracted.spice`` has exactly one top
whose ordered ports equal the contract (floating reference ports may be absent), and every
reachable device model is a known PDK primitive.
"""
from __future__ import annotations

from pathlib import Path

from . import spice
from .lvs import TaskContract, layout_port_name, port_keys
from .runtime import load_runtime, require_file, run, tool_env

PEX_TCL = """# Input GDS and installed PDK are read-only; extraction uses an in-memory copy.
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
extract no corners
extract no resistance
extract do capacitance
extract do coupling
extract do adjust
if {$::env(PEX_MODE) eq "rc"} {
    extresist threshold 0
    extresist minres 1
    extresist mindelay 0
    extresist simplify on
    extresist extout on
    extresist silent off
    extract do resistance
}
extract all
ext2spice default
ext2spice format ngspice
ext2spice scale off
ext2spice merge none
ext2spice hierarchy off
ext2spice subcircuit top on
ext2spice global off
if {$::env(PEX_MODE) eq "c"} {
    ext2spice short resistor
} else {
    ext2spice short none
}
ext2spice rthresh infinite
ext2spice cthresh 0
if {$::env(PEX_MODE) eq "rc"} {
    ext2spice extresist on
} else {
    ext2spice extresist off
}
ext2spice -o extracted.spice $::env(LAYOUT_FLAT_TOP).ext
save $::env(LAYOUT_FLAT_TOP)
puts "PEX_COMPLETE $::env(PEX_MODE)"
quit -noprompt
"""

PRIMITIVE_PREFIX = "sky130_fd_pr__"


def inspect_pex_netlist(path: Path, contract: TaskContract, floating: list[str],
                        flat_top: str) -> dict:
    circuits = spice.parse(Path(path).read_text(), strict=False)
    top = circuits.get(flat_top.casefold())
    if top is None:
        raise ValueError(f"missing extracted top {flat_top}")
    required = [p for p in contract.ports if p not in floating or
                layout_port_name(p).casefold() in port_keys(top.ports)]
    if set(port_keys(top.ports)) != set(port_keys(required)):
        raise ValueError(f"top-level pin contract differs: {top.ports}")
    models = {}
    for d in top.devices:
        if d.card in ("X", "M", "Q", "D"):
            models[d.model.casefold()] = models.get(d.model.casefold(), 0) + 1
    unknown = sorted(m for m in models if not m.startswith(PRIMITIVE_PREFIX)
                     and m not in circuits)
    if unknown:
        raise ValueError(f"unknown device models: {unknown}")
    if not models:
        raise ValueError("no devices in the extracted netlist")
    return {"top": top.name, "ports": top.ports, "models": models}


def check_pex(gds: Path, contract: TaskContract, floating: list[str], out: Path, timeout: float,
              mode: str = "rc", runtime: dict | None = None) -> tuple[float | None, dict]:
    runtime = runtime or load_runtime()
    for key in ("magic_bin", "magic_rcfile"):
        require_file(runtime[key], key)
    out.mkdir(parents=True, exist_ok=True)
    flat_top = contract.top + "_pex"
    (out / "pex.tcl").write_text(PEX_TCL)
    rc = Path(runtime["magic_rcfile"])
    env = tool_env(out)
    env.update(LAYOUT_GDS=str(gds), LAYOUT_TOP=contract.top, LAYOUT_FLAT_TOP=flat_top,
               LAYOUT_PORTS="\n".join(layout_port_name(p) for p in contract.ports),
               PEX_MODE=mode, PDK_ROOT=str(rc.parents[3]))
    ex = run([runtime["magic_bin"], "-dnull", "-noconsole", "-rcfile", str(rc), str(out / "pex.tcl")],
             cwd=out, timeout=timeout, env=env)
    details: dict = {"returncode": ex.returncode, "mode": mode}
    if ex.returncode != 0 or f"PEX_COMPLETE {mode}" not in ex.log_path.read_text(errors="replace"):
        details["reason"] = "magic PEX did not complete"
        return -1.0, details
    try:
        details.update(inspect_pex_netlist(out / "extracted.spice", contract, floating, flat_top))
    except (OSError, ValueError) as exc:
        details["reason"] = str(exc)
        return -1.0, details
    return 0.0, details
