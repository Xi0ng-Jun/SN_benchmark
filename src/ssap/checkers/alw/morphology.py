"""``m_5`` (``m_morph``): annotation-free "meaningless layout" screen.

``magic_netgen`` tasks are exempt by public policy (``m_5 = 1``). For ``klayout`` tasks with
``m_2 = 1`` the layout is rejected (``m_5 = 0``) iff all four features meet their thresholds:

* ``bus_nets >= 5``: distinct signal nets owning a long straight met1..met5 trapezoid in the
  dominant direction (``>= 0.5`` of the layout extent, ``>= 3x`` the median device size, and
  ``>= 10x`` its own thickness);
* ``drops >= 5``: perpendicular segments of those nets ``>= max(5 um, 0.15 x)`` the other extent;
* ``largest_row_fraction >= 0.7``: share of devices in the largest row along the bus direction
  (rows grouped by ``>= 30 %`` extent overlap);
* ``row_elongation >= 4.0``: length / thickness of that row.

Signal nets are LVS-referenced nets except body-tie rails (a net that is the bulk terminal of
``>= half`` of the NMOS or of the PMOS devices). The pure feature core (``bus_analysis``,
``device_rows``, ``judge``) is backend-free; ``features_from_lvsdb`` builds its inputs from a
saved ``report.lvsdb`` with ``klayout.db``.
"""
from __future__ import annotations

import re
import statistics
from dataclasses import dataclass, field
from pathlib import Path

THRESHOLDS = {"bus_lines": 5, "drops": 5, "row_fraction": 0.7, "row_elongation": 4.0}
LINE_DEV_X = 3.0
METALS = ["li1", "met1", "met2", "met3", "met4", "met5"]
VIAS = ["mcon", "via", "via2", "via3", "via4"]
GDS = {"licon": "66/44", "mcon": "67/44", "via": "68/44", "via2": "69/44", "via3": "70/44",
       "via4": "71/44", "capm": "89/44", "cap2m": "97/44"}
LABEL = {"li1": "67/5", "met1": "68/5", "met2": "69/5", "met3": "70/5", "met4": "71/5",
         "met5": "72/5"}


@dataclass
class Dev:
    bbox: tuple[float, float, float, float]          # union of finger boxes (um)
    polarity: str | None = None                        # "n" / "p"
    bulk: str | None = None                            # canonical net key of the B terminal
    is_mos: bool = True


@dataclass
class NetGeom:
    key: str
    segments: list[tuple[str, tuple[float, float, float, float]]] = field(default_factory=list)


def supply_nets(devices: list[Dev]) -> set[str]:
    rails = set()
    for pol in ("n", "p"):
        devs = [d for d in devices if d.is_mos and d.polarity == pol]
        if not devs:
            continue
        cnt: dict[str, int] = {}
        for d in devs:
            if d.bulk:
                cnt[d.bulk] = cnt.get(d.bulk, 0) + 1
        rails |= {n for n, c in cnt.items() if c >= 0.5 * len(devs)}
    return rails


def bus_analysis(nets: list[NetGeom], devices: list[Dev], w: float, h: float) -> dict:
    dw = sorted(d.bbox[2] - d.bbox[0] for d in devices) or [0.0]
    dh = sorted(d.bbox[3] - d.bbox[1] for d in devices) or [0.0]
    min_h = max(0.5 * w, LINE_DEV_X * dw[len(dw) // 2])
    min_v = max(0.5 * h, LINE_DEV_X * dh[len(dh) // 2])
    segs = {"H": [], "V": []}
    perp = {"H": [], "V": []}
    for net in nets:
        for metal, (x1, y1, x2, y2) in net.segments:
            if metal not in METALS[1:]:
                continue
            bw, bh = x2 - x1, y2 - y1
            if bw >= bh:
                if bw >= min_h and bw >= 10 * bh:
                    segs["H"].append((net.key, bw))
                perp["V"].append((net.key, bw))
            else:
                if bh >= min_v and bh >= 10 * bw:
                    segs["V"].append((net.key, bh))
                perp["H"].append((net.key, bh))
    out = {}
    for o in ("H", "V"):
        bus = {n for n, _ in segs[o]}
        drop_min = max(5.0, 0.15 * (h if o == "H" else w))
        out[o] = {"bus_nets": len(bus),
                  "drops": sum(1 for n, length in perp[o] if n in bus and length >= drop_min),
                  "long_len_um": round(sum(length for _, length in segs[o]), 1)}
    return out


def device_rows(devices: list[Dev], o: str) -> dict:
    boxes = [d.bbox for d in devices]
    if not boxes:
        return {"rows": 0, "largest_row_fraction": None, "row_elongation": None}
    k = 1 if o == "H" else 0
    rows: list[dict] = []
    for b in sorted(boxes, key=lambda b: (b[k] + b[k + 2]) / 2):
        lo, hi = b[k], b[k + 2]
        for r in rows:
            if min(hi, r["hi"]) - max(lo, r["lo"]) >= 0.3 * min(hi - lo, r["hi"] - r["lo"]):
                r["n"] += 1
                r["lo"], r["hi"] = min(lo, r["lo"]), max(hi, r["hi"])
                break
        else:
            rows.append({"lo": lo, "hi": hi, "n": 1})
    big = max(rows, key=lambda r: r["n"])
    members = [b for b in boxes
               if min(b[k + 2], big["hi"]) - max(b[k], big["lo"]) >= 0.3 * (b[k + 2] - b[k])]
    j = 1 - k
    length = max(b[j + 2] for b in members) - min(b[j] for b in members)
    thick = big["hi"] - big["lo"] or 1.0
    return {"rows": len(rows), "largest_row_fraction": big["n"] / len(boxes),
            "row_elongation": round(length / thick, 2)}


def features(nets: list[NetGeom], devices: list[Dev], bbox: tuple[float, float, float, float]) -> dict:
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    rails = supply_nets(devices)
    signal = [n for n in nets if n.key not in rails]
    bus = bus_analysis(signal, devices, w, h)
    o = max(("H", "V"), key=lambda d: (bus[d]["bus_nets"], bus[d]["long_len_um"]))
    rows = device_rows(devices, o)
    return {"bus_direction": o, "bus_nets": bus[o]["bus_nets"], "drops": bus[o]["drops"],
            "largest_row_fraction": rows["largest_row_fraction"],
            "row_elongation": rows["row_elongation"], "device_rows": rows["rows"],
            "supply_nets": sorted(rails), "bus": bus}


def judge(f: dict) -> dict:
    c = THRESHOLDS
    sl, dr = f.get("bus_nets") or 0, f.get("drops") or 0
    row = f.get("largest_row_fraction")
    one_row = (row is not None and row >= c["row_fraction"]
               and (f.get("row_elongation") or 0) >= c["row_elongation"])
    hit = sl >= c["bus_lines"] and dr >= c["drops"] and one_row
    return {"meaningless": bool(hit), "thresholds": dict(c),
            "features": {k: f.get(k) for k in ("bus_nets", "drops", "largest_row_fraction",
                                               "row_elongation")}}


# ---------------------------------------------------------------------------------------------
# lvsdb metal-layer inference (text header) and KLayout adapter
# ---------------------------------------------------------------------------------------------

_L = re.compile(r"^\s*L\((l\d+)(?:\s+'(\d+/\d+)')?\)")
_C = re.compile(r"^\s*C\(([^)]*)\)")


def read_header(path: str | Path):
    layers: dict[str, str | None] = {}
    conn: dict[str, set[str]] = {}
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            m = _L.match(line)
            if m:
                layers[m.group(1)] = m.group(2)
                continue
            m = _C.match(line)
            if m:
                items = m.group(1).split()
                conn.setdefault(items[0], set()).update(items[1:])
                continue
            if line.lstrip().startswith(("K(", "D(", "X(", "H(")) and conn:
                break
    return layers, conn


def infer_metal_layers(path: str | Path) -> dict:
    """Map li1/met1..met5 (and vias) to anonymous lvsdb layer names."""
    gds, conn = read_header(path)
    by_gds = {v: k for k, v in gds.items() if v}
    nbr = {k: set(v) - {k} for k, v in conn.items()}
    selfc = {k for k, v in conn.items() if k in v}
    out: dict[str, str] = {}
    caps = {by_gds.get(GDS["capm"]), by_gds.get(GDS["cap2m"])} - {None}

    def cap_like(x, via):
        return x in caps or nbr.get(x, set()) <= {via}

    def label_conductor(metal):
        lab = by_gds.get(LABEL[metal])
        if not lab:
            return None
        cands = [x for x in nbr.get(lab, ()) if x in selfc and x not in out.values()]
        vias = {by_gds.get(GDS[v]) for v in VIAS + ["licon"]} - {None}
        good = [x for x in cands if nbr.get(x, set()) & vias]
        return good[0] if len(good) == 1 else None

    mcon = by_gds.get(GDS["mcon"])
    if mcon:
        pair = sorted(nbr.get(mcon, set()) - caps)
        out["mcon"] = mcon
        licon = by_gds.get(GDS["licon"])
        li = [x for x in pair if licon and x in nbr.get(licon, set())]
        if len(pair) == 2 and len(li) == 1:
            out["li1"] = li[0]
            out["met1"] = next(x for x in pair if x != li[0])
        elif len(pair) == 2:
            lab = label_conductor("li1")
            if lab in pair:
                out["li1"] = lab
                out["met1"] = next(x for x in pair if x != lab)
    for k in range(1, 5):
        lo, hi, vname = METALS[k], METALS[k + 1], VIAS[k]
        if lo not in out:
            break
        via = by_gds.get(GDS[vname])
        if via:
            out[vname] = via
            rest = [x for x in nbr.get(via, set()) - {out[lo]} if not cap_like(x, via)]
            rest = [x for x in rest if x not in out.values()]
            if len(rest) == 1:
                out[hi] = rest[0]
                continue
        lab = label_conductor(hi)
        if lab:
            out[hi] = lab
            vs = [x for x in nbr.get(out[lo], set()) & nbr.get(lab, set()) if x in selfc]
            if len(vs) == 1 and vname not in out:
                out[vname] = vs[0]
            continue
        cands = []
        known = {out.get(v) for v in VIAS} - {None}
        for x in nbr.get(out[lo], set()):
            if x in known or x not in selfc:
                continue
            ups = [y for y in nbr.get(x, set()) - {out[lo]} if not cap_like(y, x)]
            if len(ups) == 1 and ups[0] in selfc and ups[0] not in out.values() and len(nbr.get(ups[0], set())) >= 2:
                cands.append((x, ups[0]))
        if len(cands) == 1:
            out[vname], out[hi] = cands[0]
    return {"layers": out, "unresolved": [m for m in METALS + VIAS if m not in out]}


def _polarity(model: str) -> str | None:
    m = model.lower()
    if "pfet" in m or "pnp" in m:
        return "p"
    if "nfet" in m or "npn" in m:
        return "n"
    return None


def features_from_lvsdb(lvsdb_path: str | Path, bbox_um: tuple[float, float, float, float]) -> dict:
    """Build devices / signal-net metal trapezoids from a passed ``report.lvsdb``."""
    import klayout.db as kdb

    layer_info = infer_metal_layers(lvsdb_path)
    lv = kdb.LayoutVsSchematic()
    lv.read(str(lvsdb_path))
    xref = lv.xref()
    ly = lv.internal_layout()
    dbu = ly.dbu
    cpairs = {cp.first().cell_index: cp for cp in xref.each_circuit_pair() if cp.first()}
    top = next(lv.netlist().each_circuit_top_down())
    parent: dict = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    occurrences: dict = {}
    raw_devices: list = []

    def walk(circ, trans, path):
        cp = cpairs.get(circ.cell_index)
        matched, net_ref = set(), {}
        if cp is not None:
            for dp in xref.each_device_pair(cp):
                if dp.first() and dp.second():
                    matched.add(dp.first().id())
            for np_ in xref.each_net_pair(cp):
                if np_.first() and np_.second():
                    net_ref[np_.first().cluster_id] = True
        for n in circ.each_net():
            node = (path, n.cluster_id)
            find(node)
            occurrences[node] = (n, trans, net_ref.get(n.cluster_id, False))
        for d in circ.each_device():
            if d.id() not in matched:
                continue
            dc = d.device_class()
            parts = [(d.device_abstract, d.trans)] + [(r.device_abstract, d.trans * r.trans)
                                                      for r in d.each_combined_abstract()]
            box = None
            for ab, t in parts:
                if ab is None:
                    continue
                cell = ly.cell(ab.cell_index())
                for li in ly.layer_indexes():
                    for s in cell.shapes(li).each():
                        b = (trans * t) * s.dbbox()
                        box = b if box is None else box + b
            if box is None:
                continue
            bulk = None
            for td in dc.terminal_definitions():
                if td.name == "B":
                    n = d.net_for_terminal(td.id())
                    if n is not None:
                        bulk = (path, n.cluster_id)
            raw_devices.append(((box.left, box.bottom, box.right, box.top), _polarity(dc.name),
                                bulk, "MOS" in type(dc).__name__))
        for sc in circ.each_subcircuit():
            cpath = path + (sc.id(),)
            for cn in sc.circuit_ref().each_net():
                for pin in cn.each_pin():
                    pn = sc.net_for_pin(pin.pin_id())
                    if pn is not None:
                        union((path, pn.cluster_id), (cpath, cn.cluster_id))
            walk(sc.circuit_ref(), trans * sc.trans, cpath)

    walk(top, kdb.DCplxTrans(), ())
    groups: dict = {}
    for node in occurrences:
        groups.setdefault(find(node), []).append(node)
    key_of = {node: str(root) for root, nodes in groups.items() for node in nodes}
    devices = [Dev(b, pol, key_of.get(bulk) if bulk else None, mos) for b, pol, bulk, mos in raw_devices]
    nets: list[NetGeom] = []
    regions = {m: lv.layer_by_name(layer_info["layers"][m]) for m in METALS[1:]
               if m in layer_info["layers"]}
    for root, nodes in groups.items():
        if not any(occurrences[n][2] for n in nodes):
            continue
        ng = NetGeom(str(root))
        roots = []
        for node in sorted(nodes, key=lambda x: len(x[0])):
            if any(len(a) < len(node[0]) and node[0][:len(a)] == a for a in roots):
                continue
            roots.append(node[0])
            net, trans, _ = occurrences[node]
            t = kdb.ICplxTrans(trans, dbu) if not trans.is_unity() else kdb.ICplxTrans()
            for metal, lr in regions.items():
                region = lv.shapes_of_net(net, lr, True, t).merged()
                for poly in region.each_merged():
                    for tz in poly.decompose_trapezoids():
                        b = tz.bbox()
                        ng.segments.append((metal, (b.left * dbu, b.bottom * dbu,
                                                    b.right * dbu, b.top * dbu)))
        nets.append(ng)
    f = features(nets, devices, bbox_um)
    f["unresolved_layers"] = layer_info["unresolved"]
    return f


def check_morphology(backend: str, lvs_passed: bool, lvsdb_path: str | Path | None,
                     bbox_um: tuple[float, float, float, float]) -> tuple[float | None, dict]:
    """``(raw_u_5, details)``: ``0.0`` pass, ``-1.0`` meaningless, ``None`` not evaluable."""
    if backend == "magic_netgen":
        return (0.0 if lvs_passed else None), {"morphology_policy_exempt": True}
    if not lvs_passed or lvsdb_path is None:
        return None, {"reason": "requires m_2 = 1"}
    f = features_from_lvsdb(lvsdb_path, bbox_um)
    verdict = judge(f)
    return (-1.0 if verdict["meaningless"] else 0.0), {**verdict, "bus_direction": f["bus_direction"]}
