"""``m_3`` (``m_shape``), ``m_4`` (``m_fill``) and the Class-2 ``advisory_aspect_gt_3``.

``m_3``: ``q = max(W, H) / min(W, H)`` of the manufacturing bounding box; raw margin
``4.0 - q`` against the global necessary bound ``q <= 4.0`` (every task has ``q_max`` in
``{3.0, 4.0}`` and the per-task value is not disclosed in the workspace). ``eps_3`` is locked at
``0.0``: calibrating it from ``q_max = 3.0`` tasks would push it to ~1.0 and reject true solutions
of ``q_max = 4.0`` tasks. ``3.0 < q <= 4.0`` raises the advisory, which has no overwrite authority.

``m_4``: ``rho = A_eff / A_bbox`` with raw margin ``rho - 0.10``; ``A_eff`` is the extracted
electrical recognition area of the LVS-matched devices:
* KLayout ``report.lvsdb`` (``scale=true``: lengths x1e6, areas x1e12): MOS ``W*L + AS + AD``,
  R/C native ``A``, BJT union of C/B/E recognition regions per physical abstract; unsupported
  device classes / models and unmatched devices fail closed (no partial area);
* Magic ``extracted.spice`` (``scale off`` / ``merge none``, already um / um^2): MOS
  ``w*l + as + ad``, MIM / poly resistor ``w*l``; one statement per physical device.
When ``m_2 = 0`` no device correspondence exists: the fill margin is undefined (``None``,
``bottom`` in the bank) and ``m_4 = 0``.
"""
from __future__ import annotations

import math
import re
from pathlib import Path

Q_BOUND = 4.0
Q_ADVISORY = 3.0
RHO_MIN = 0.10
EPS_SHAPE = 0.0
EPS_FILL_DEFAULT = 1e-4


def aspect_ratio(width_um: float, height_um: float) -> float:
    lo = min(width_um, height_um)
    if not (math.isfinite(width_um) and math.isfinite(height_um)) or lo <= 0:
        raise ValueError("aspect ratio needs a positive finite bounding box")
    return max(width_um, height_um) / lo


def shape_raw_margin(width_um: float, height_um: float) -> float:
    return Q_BOUND - aspect_ratio(width_um, height_um)


def advisory_aspect_gt_3(width_um: float, height_um: float) -> bool:
    q = aspect_ratio(width_um, height_um)
    return Q_ADVISORY < q <= Q_BOUND


def validate_shape_eps(eps_3: float) -> float:
    """``eps_3`` is locked at ``0.0`` (no calibration from ``q_max = 3.0`` tasks)."""
    if eps_3 != EPS_SHAPE:
        raise ValueError(f"eps_3 is locked at 0.0 (got {eps_3!r}); mismatches of q_max=3.0 tasks "
                         "in (3.0, 4.0] belong to advisory_aspect_gt_3, never to eps_3")
    return eps_3


def fill_raw_margin(effective_area_um2: float | None, bbox_area_um2: float,
                    lvs_passed: bool) -> float | None:
    if not lvs_passed or effective_area_um2 is None:
        return None
    if not math.isfinite(bbox_area_um2) or bbox_area_um2 <= 0:
        raise ValueError("bounding-box area must be positive")
    rho = effective_area_um2 / bbox_area_um2
    if not math.isfinite(rho) or rho <= 0:
        raise ValueError("device-area ratio must be positive and finite")
    return rho - RHO_MIN


# ---------------------------------------------------------------------------------------------
# Magic extracted.spice
# ---------------------------------------------------------------------------------------------

MAGIC_MOS = frozenset({"sky130_fd_pr__nfet_01v8", "sky130_fd_pr__pfet_01v8",
                       "sky130_fd_pr__nfet_01v8_lvt", "sky130_fd_pr__pfet_01v8_lvt",
                       "sky130_fd_pr__nfet_05v0_nvt"})
MAGIC_MIM = frozenset({"sky130_fd_pr__cap_mim_m3_1", "sky130_fd_pr__cap_mim_m3_2"})
MAGIC_RES = frozenset({"sky130_fd_pr__res_generic_po"})
_NUMBER = re.compile(r"([+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?)([a-z]*)", re.I)
_SCALE = {"": 1., "t": 1e12, "g": 1e9, "meg": 1e6, "k": 1e3,
          "m": 1e-3, "u": 1e-6, "n": 1e-9, "p": 1e-12, "f": 1e-15}
REQUIRED_MAGIC_COMMANDS = (
    "extract style {ngspice()}", "ext2spice lvs", "ext2spice format ngspice",
    "ext2spice scale off", "ext2spice merge none", "ext2spice hierarchy off",
    "ext2spice subcircuit top on", "ext2spice short none", "ext2spice extresist off",
    "ext2spice rthresh infinite", "ext2spice cthresh infinite",
)


def _lines(text: str):
    current, number = "", 0
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("*"):
            continue
        if line.startswith("+"):
            if not current:
                raise ValueError(f"Orphan continuation at line {n}")
            current += " " + line[1:].strip()
        else:
            if current:
                yield number, current
            current, number = line, n
    if current:
        yield number, current


def _native_number(text: str, label: str, *, zero: bool = False) -> float:
    m = _NUMBER.fullmatch(text)
    if m is None or m[2].casefold() not in _SCALE:
        raise ValueError(f"Unsupported native numeric value for {label}: {text!r}")
    value = float(m[1]) * _SCALE[m[2].casefold()]
    if not math.isfinite(value) or value < 0 or (not zero and value == 0):
        raise ValueError(f"Invalid native value for {label}: {text!r}")
    return value


def qualify_magic_script(script_text: str) -> None:
    lines = [" ".join(s.strip().split()) for s in script_text.splitlines()
             if s.strip() and not s.lstrip().startswith("#")]
    for required in REQUIRED_MAGIC_COMMANDS:
        prefix = required.rsplit(" ", 1)[0] + " "
        matching = ([s for s in lines if s == required] if len(required.split()) == 2
                    else [s for s in lines if s.startswith(prefix)])
        if matching != [required]:
            raise ValueError(f"Magic extraction configuration is not qualified: {required}")
    outputs = [s for s in lines if s.startswith("ext2spice -o ")]
    if len(outputs) != 1 or len(outputs[0].split()) != 4 or outputs[0].split()[2] != "extracted.spice":
        raise ValueError("Magic extraction has no unique native extracted.spice output")


def measure_extracted_area(extracted_path: str | Path, *, extraction_script: str | Path | None = None,
                           expected_top: str | None = None, allowed_models=None) -> dict:
    """Complete native area terms of a flat Magic extraction, or ``ValueError`` (fail closed)."""
    if extraction_script is not None:
        qualify_magic_script(Path(extraction_script).read_text())
    text = Path(extracted_path).read_text()
    first = text.splitlines()[0] if text else ""
    if not text.startswith("* NGSPICE file created from ") or "technology: sky130A" not in first:
        raise ValueError("Not a native SKY130 Magic extraction")
    allowed = {s.casefold() for s in allowed_models} if allowed_models is not None else None
    rows, names, top, ports, ended = [], set(), None, None, False
    for number, line in _lines(text):
        tokens = line.split()
        head = tokens[0].casefold()
        if head == ".subckt":
            if top is not None or len(tokens) < 3 or any("=" in x for x in tokens):
                raise ValueError("Magic area requires exactly one flat nonparameterized top")
            top, ports = tokens[1], tokens[2:]
            if expected_top is not None and top.casefold() != expected_top.casefold():
                raise ValueError("Extracted top differs from the expected top")
            continue
        if head == ".ends":
            if top is None or ended or len(tokens) > 2 or (len(tokens) > 1 and tokens[1].casefold() != top.casefold()):
                raise ValueError("Malformed extracted top terminator")
            ended = True
            continue
        if top is None or ended or head.startswith(".") or not head.startswith("x"):
            raise ValueError(f"Unsupported native statement at line {number}: {head}")
        if head in names:
            raise ValueError(f"Duplicate extracted device: {tokens[0]}")
        names.add(head)
        cut = next((i for i, t in enumerate(tokens) if "=" in t), len(tokens))
        if cut < 4:
            raise ValueError(f"Missing native model/terminals: line {number}")
        model, nodes = tokens[cut - 1].casefold(), tokens[1:cut - 1]
        if model not in (MAGIC_MOS | MAGIC_MIM | MAGIC_RES) or (allowed is not None and model not in allowed):
            raise ValueError(f"Unsupported/unapproved native model: {model}")
        params: dict[str, str] = {}
        for token in tokens[cut:]:
            if token.count("=") != 1:
                raise ValueError(f"Unsupported native parameter syntax: {token}")
            k, v = token.split("=")
            k = k.casefold()
            if k in params:
                raise ValueError(f"Duplicate native parameter: {k}")
            params[k] = v
        kind = "MOS" if model in MAGIC_MOS else "C" if model in MAGIC_MIM else "R"
        required = {"w", "l", "as", "ad"} if kind == "MOS" else {"w", "l"}
        permitted = required | ({"ps", "pd"} if kind == "MOS" else set())
        if set(params) - permitted or not required <= set(params):
            raise ValueError(f"Incomplete or unqualified {kind} geometry parameters: {tokens[0]}")
        if len(nodes) != (4 if kind == "MOS" else 2):
            raise ValueError(f"Unexpected {kind} terminal count: {tokens[0]}")
        native = {k: _native_number(v, f"{tokens[0]}.{k}", zero=k in {"as", "ad", "ps", "pd"})
                  for k, v in params.items()}
        terms = ({"channel_um2": native["w"] * native["l"], "source_um2": native["as"],
                  "drain_um2": native["ad"]} if kind == "MOS"
                 else {"plate_um2" if kind == "C" else "body_um2": native["w"] * native["l"]})
        area = math.fsum(terms.values())
        if not math.isfinite(area) or area <= 0:
            raise ValueError("Native device area is not finite and positive")
        rows.append({"instance": tokens[0], "model": model, "kind": kind, "area_um2": area,
                     "area_terms_um2": terms})
    if top is None or not ended or not rows:
        raise ValueError("Incomplete or empty extracted top")
    totals = {n: math.fsum(r["area_um2"] for r in rows if r["kind"] == k)
              for n, k in (("mos", "MOS"), ("resistor", "R"), ("capacitor", "C"), ("bjt", "BJT"))}
    return {"status": "complete", "area_um2": math.fsum(totals.values()), "kind_totals_um2": totals,
            "device_rows": rows, "top": top, "ports": ports, "backend": "magic_netgen"}


# ---------------------------------------------------------------------------------------------
# KLayout report.lvsdb
# ---------------------------------------------------------------------------------------------

_MIM_ALIASES = {"sky130_fd_pr__cap_mim_m3_1": "sky130_fd_pr__model__cap_mim",
                "sky130_fd_pr__cap_mim_m3_2": "sky130_fd_pr__model__cap_mim_m4"}
_CLASS_KIND = {"DeviceClassMOS3Transistor": "MOS", "DeviceClassMOS4Transistor": "MOS",
               "DeviceClassResistor": "R", "DeviceClassResistorWithBulk": "R",
               "DeviceClassCapacitor": "C", "DeviceClassCapacitorWithBulk": "C",
               "DeviceClassBJT3Transistor": "BJT", "DeviceClassBJT4Transistor": "BJT"}
LENGTH_SCALE = 1e6          # sky130.lvs with scale=true: device_scaling(1000000)


def _kind(device) -> str:
    name = type(device.device_class()).__name__
    if name not in _CLASS_KIND:
        raise ValueError(f"Unsupported extracted device class {name}")
    return _CLASS_KIND[name]


def _params(device) -> dict[str, float]:
    return {p.name: float(device.parameter(p.id()))
            for p in device.device_class().parameter_definitions()}


def parameter_area(kind: str, p: dict[str, float], length_scale: float = LENGTH_SCALE) -> float:
    """Native merged parameter area in um^2 (MOS ``W*L + AS + AD``; R/C ``A``)."""
    a2 = length_scale * length_scale
    need = {"MOS": ("W", "L", "AS", "AD"), "R": ("A",), "C": ("A",)}[kind]
    if not all(k in p for k in need):
        raise ValueError(f"Missing {kind} parameters: {need}")
    for k in need:
        v = p[k]
        if not math.isfinite(v) or (v < 0 if k in ("AS", "AD") else v <= 0):
            raise ValueError(f"Invalid {kind}.{k}: {v}")
    if kind == "MOS":
        return (p["W"] * p["L"] + p["AS"] + p["AD"]) / a2
    return p["A"] / a2


def _bjt_area(database, device) -> float:
    import klayout.db as kdb

    abstracts = [device.device_abstract] + [r.device_abstract for r in device.each_combined_abstract()]
    mags = [device.trans.mag] + [(device.trans * r.trans).mag for r in device.each_combined_abstract()]
    dbu = database.internal_layout().dbu
    terms = {t.name: t.id() for t in device.device_class().terminal_definitions()}
    if not {"C", "B", "E"} <= terms.keys():
        raise ValueError("BJT class lacks C/B/E terminals")
    total = 0.0
    for abstract, mag in zip(abstracts, mags):
        if abstract is None:
            raise ValueError("BJT physical abstract missing")
        nl = kdb.Netlist()
        circ = kdb.Circuit()
        circ.name = "abstract_query"
        circ.cell_index = abstract.cell_index()
        nl.add(circ)
        union = kdb.Region()
        for name in ("C", "B", "E"):
            cluster = abstract.cluster_id_for_terminal(terms[name])
            if cluster <= 0:
                raise ValueError(f"BJT abstract has no {name} terminal cluster")
            net = circ.create_net(name)
            net.cluster_id = cluster
            for index in database.layer_indexes():
                part = database.shapes_of_net(net, database.layer_by_index(index), False)
                if not part.is_empty():
                    union += part
        if union.is_empty():
            raise ValueError("Empty BJT recognition region")
        total += union.merged().area() * dbu * dbu * mag * mag
    return total


def klayout_extracted_area(lvsdb_path: str | Path, top: str) -> dict:
    """Area of every LVS-matched extracted device reachable from ``top`` (requires klayout.db)."""
    import klayout.db as kdb

    database = kdb.LayoutVsSchematic()
    database.read(str(lvsdb_path))
    if any(e.severity == kdb.LogEntryData.Error or e.category_name == "must-connect"
           for e in database.each_log_entry()):
        raise ValueError("LVSdb contains extraction errors")
    xref = database.xref()
    if xref is None:
        raise ValueError("LVSdb has no comparison cross-reference")
    pairs = list(xref.each_circuit_pair())
    if not pairs or not all(p.first() and p.second() and str(p.status()) == "Match" for p in pairs):
        raise ValueError("LVS cross-reference has unmatched circuits")
    tops = [p for p in pairs if p.first().name.casefold() == top.casefold()
            and p.second().name.casefold() == top.casefold()]
    if len(tops) != 1:
        raise ValueError("LVS cross-reference lacks an unambiguous top")
    device_map = {}
    for pair in pairs:
        dps = list(xref.each_device_pair(pair))
        if not all(d.first() and d.second() and str(d.status()) == "Match" for d in dps):
            raise ValueError("LVS cross-reference has unmatched/ignored devices")
        if not (len(dps) == len(list(pair.first().each_device())) == len(list(pair.second().each_device()))):
            raise ValueError("LVS cross-reference does not cover every compared device")
        device_map[pair.first().name] = [d.first() for d in dps]

    rows = []

    def walk(circuit, ancestors, mag2):
        if circuit.name in ancestors:
            raise ValueError("Recursive extracted circuit hierarchy")
        for device in device_map.get(circuit.name, []):
            kind, p = _kind(device), _params(device)
            model = device.device_class().name.casefold()
            model = _MIM_ALIASES.get(model, model)
            if device.device_abstract is None:
                raise ValueError("Extracted device is missing physical geometry")
            area = _bjt_area(database, device) if kind == "BJT" else parameter_area(kind, p)
            if not math.isfinite(area) or area <= 0:
                raise ValueError("Invalid extracted device area")
            rows.append({"kind": kind, "model": model, "area_um2": area * mag2})
        for sub in circuit.each_subcircuit():
            walk(sub.circuit_ref(), ancestors + (circuit.name,), mag2 * sub.trans.mag ** 2)

    walk(tops[0].first(), (), 1.0)
    if not rows:
        raise ValueError("No reachable extracted devices")
    totals = {k: math.fsum(r["area_um2"] for r in rows if r["kind"] == k) for k in ("MOS", "R", "C", "BJT")}
    return {"status": "complete", "area_um2": math.fsum(totals.values()),
            "kind_totals_um2": totals, "devices": len(rows), "backend": "klayout"}
