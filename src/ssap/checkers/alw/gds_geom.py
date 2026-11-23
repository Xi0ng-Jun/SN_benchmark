"""``m_1`` (``m_gds``): layout readability, exact-case top, positive manufacturing bounding box.

Mirror of the outer geometry stage: the layout must be a regular (non-symlink) non-empty file that
the GDS reader accepts; the requested top must be in ``top_cells()`` (exact case); ``dbu > 0``;
and the recursive union of the 38 manufacturing layers below must have a non-empty, positive-area
bounding box. Texts, pin/label layers and auxiliary boundaries are excluded.

Backends: ``klayout.db`` when importable (container), else a pure-Python GDSII stream reader
(BOUNDARY / PATH / BOX elements, SREF / AREF with STRANS / MAG / ANGLE). Class-2 advisories:
``advisory_missing_src_py`` and ``advisory_task_modified`` (live task inputs differ from the
T=0 frozen copy). Neither enters ``m``.
"""
from __future__ import annotations

import hashlib
import math
import os
import stat
import struct
from dataclasses import dataclass, field
from pathlib import Path

MANUFACTURING_LAYERS: dict[str, tuple[int, int]] = {
    "dnwell": (64, 18), "nwell": (64, 20), "pwell": (64, 44),
    "diff": (65, 20), "tap": (65, 44), "poly": (66, 20),
    "pwbm": (19, 44), "pwde": (124, 20), "hvtr": (18, 20),
    "hvtp": (78, 44), "ldntm": (11, 44), "hvi": (75, 20),
    "tunm": (80, 20), "lvtn": (125, 44), "hvntm": (125, 20),
    "nsdm": (93, 44), "psdm": (94, 20), "rpm": (86, 20),
    "urpm": (79, 20), "npc": (95, 20), "licon1": (66, 44),
    "li1": (67, 20), "mcon": (67, 44),
    **{f"met{i}": (67 + i, 20) for i in range(1, 6)},
    **{("via" if i == 1 else f"via{i}"): (67 + i, 44) for i in range(1, 5)},
    "capm": (89, 44), "cap2m": (97, 44), "pad": (76, 20),
    "nsm": (61, 20), "rdl": (74, 20), "rdlcon": (13, 0),
}
_LAYER_SET = frozenset(MANUFACTURING_LAYERS.values())


class GeometryError(ValueError):
    """The layout violates the geometry contract (``m_1 = 0``)."""


@dataclass
class Geometry:
    top: str
    top_cells: list[str]
    dbu_um: float
    bbox_um: tuple[float, float, float, float]
    width_um: float
    height_um: float
    area_um2: float
    present_layers: dict[str, list[int]] = field(default_factory=dict)
    backend: str = ""

    def as_dict(self) -> dict:
        return {"top": self.top, "top_cells": self.top_cells, "dbu_um": self.dbu_um,
                "bbox_um": list(self.bbox_um), "width_um": self.width_um,
                "height_um": self.height_um, "area_um2": self.area_um2,
                "present_manufacturing_layers": self.present_layers, "backend": self.backend}


def regular_nonempty_file(path: str | Path) -> bool:
    """Tier-0 ``file_present``: a regular, non-symlink, non-empty file."""
    try:
        st = os.lstat(path)
    except OSError:
        return False
    return stat.S_ISREG(st.st_mode) and st.st_size > 0


# ---------------------------------------------------------------------------------------------
# Pure GDSII reader
# ---------------------------------------------------------------------------------------------

_HEADER, _BGNLIB, _UNITS, _ENDLIB, _BGNSTR, _STRNAME, _ENDSTR = 0x00, 0x01, 0x03, 0x04, 0x05, 0x06, 0x07
_BOUNDARY, _PATH, _SREF, _AREF, _TEXT, _LAYER, _DATATYPE, _WIDTH, _XY, _ENDEL = (
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x0F, 0x10, 0x11)
_SNAME, _COLROW, _NODE, _BOX, _BOXTYPE, _PATHTYPE, _STRANS, _MAG, _ANGLE = (
    0x12, 0x13, 0x15, 0x2D, 0x2E, 0x21, 0x1A, 0x1B, 0x1C)
_BGNEXTN, _ENDEXTN = 0x30, 0x31


def _real8(b: bytes) -> float:
    if len(b) != 8:
        raise GeometryError("bad GDS real")
    sign = -1.0 if b[0] & 0x80 else 1.0
    exponent = (b[0] & 0x7F) - 64
    mantissa = int.from_bytes(b[1:], "big") / float(1 << 56)
    return sign * mantissa * (16.0 ** exponent)


def _to_real8(x: float) -> bytes:
    if x == 0:
        return bytes(8)
    sign = 0x80 if x < 0 else 0
    x = abs(x)
    exponent = 64
    while x >= 1:
        x /= 16.0
        exponent += 1
    while x < 1 / 16.0:
        x *= 16.0
        exponent -= 1
    mantissa = int(round(x * (1 << 56)))
    if mantissa >= (1 << 56):
        mantissa >>= 4
        exponent += 1
    return bytes([sign | exponent]) + mantissa.to_bytes(7, "big")


def _records(data: bytes):
    pos, n = 0, len(data)
    while pos + 4 <= n:
        length, rtype, _dtype = struct.unpack(">HBB", data[pos:pos + 4])
        if length < 4 or pos + length > n:
            raise GeometryError("truncated or corrupt GDS record")
        yield rtype, data[pos + 4:pos + length]
        pos += length
        if rtype == _ENDLIB:
            return
    raise GeometryError("GDS stream has no ENDLIB")


@dataclass
class _Ref:
    sname: str
    xy: list[tuple[int, int]]
    reflect: bool = False
    mag: float = 1.0
    angle: float = 0.0
    colrow: tuple[int, int] | None = None


@dataclass
class _Cell:
    name: str
    boxes: list[tuple[tuple[int, int], float, tuple[float, float, float, float]]] = field(default_factory=list)
    refs: list[_Ref] = field(default_factory=list)


def _poly_area(pts: list[tuple[int, int]]) -> float:
    if len(pts) < 3:
        return 0.0
    s = 0.0
    for (x1, y1), (x2, y2) in zip(pts, pts[1:] + pts[:1]):
        s += x1 * y2 - x2 * y1
    return abs(s) / 2.0


def _path_outline(pts, width: float, pathtype: int, bext: float, eext: float):
    hw = abs(width) / 2.0
    if hw == 0 or len(pts) < 2:
        return [], 0.0
    corners, area = [], 0.0
    last = len(pts) - 2
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:])):
        dx, dy = x1 - x0, y1 - y0
        length = math.hypot(dx, dy)
        if length == 0:
            continue
        ux, uy = dx / length, dy / length
        nx, ny = -uy * hw, ux * hw
        e0 = (hw if pathtype in (1, 2) else bext if pathtype == 4 else 0.0) if i == 0 else 0.0
        e1 = (hw if pathtype in (1, 2) else eext if pathtype == 4 else 0.0) if i == last else 0.0
        ax, ay = x0 - ux * e0, y0 - uy * e0
        bx, by = x1 + ux * e1, y1 + uy * e1
        corners += [(ax + nx, ay + ny), (ax - nx, ay - ny), (bx + nx, by + ny), (bx - nx, by - ny)]
        area += 2 * hw * (length + e0 + e1)
    return corners, area


def _bbox(points) -> tuple[float, float, float, float]:
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return (min(xs), min(ys), max(xs), max(ys))


def read_gds_cells(data: bytes) -> tuple[float, dict[str, _Cell]]:
    """Parse a GDSII stream: ``(dbu_um, cells)``; only manufacturing-layer geometry is kept."""
    recs = _records(data)
    try:
        first = next(recs)
    except StopIteration as exc:
        raise GeometryError("empty GDS stream") from exc
    if first[0] != _HEADER:
        raise GeometryError("not a GDSII stream (no HEADER record)")
    dbu_um = None
    cells: dict[str, _Cell] = {}
    cur: _Cell | None = None
    el: dict | None = None
    for rtype, body in recs:
        if rtype == _UNITS:
            if len(body) != 16:
                raise GeometryError("bad UNITS record")
            db_in_m = _real8(body[8:16])
            dbu_um = db_in_m / 1e-6
        elif rtype == _BGNSTR:
            cur = None
        elif rtype == _STRNAME:
            name = body.rstrip(b"\0").decode("ascii", "replace")
            if name in cells:
                raise GeometryError(f"duplicate cell {name}")
            cur = cells[name] = _Cell(name)
        elif rtype == _ENDSTR:
            cur = None
        elif rtype in (_BOUNDARY, _PATH, _BOX, _SREF, _AREF, _TEXT, _NODE):
            if cur is None:
                raise GeometryError("element outside a structure")
            el = {"kind": rtype, "width": 0, "pathtype": 0, "bext": 0, "eext": 0,
                  "reflect": False, "mag": 1.0, "angle": 0.0}
        elif el is not None:
            if rtype == _LAYER:
                el["layer"] = struct.unpack(">h", body[:2])[0]
            elif rtype in (_DATATYPE, _BOXTYPE):
                el["datatype"] = struct.unpack(">h", body[:2])[0]
            elif rtype == _WIDTH:
                el["width"] = struct.unpack(">i", body[:4])[0]
            elif rtype == _PATHTYPE:
                el["pathtype"] = struct.unpack(">h", body[:2])[0]
            elif rtype == _BGNEXTN:
                el["bext"] = struct.unpack(">i", body[:4])[0]
            elif rtype == _ENDEXTN:
                el["eext"] = struct.unpack(">i", body[:4])[0]
            elif rtype == _XY:
                vals = struct.unpack(f">{len(body) // 4}i", body)
                el["xy"] = list(zip(vals[0::2], vals[1::2]))
            elif rtype == _SNAME:
                el["sname"] = body.rstrip(b"\0").decode("ascii", "replace")
            elif rtype == _STRANS:
                el["reflect"] = bool(body[0] & 0x80)
            elif rtype == _MAG:
                el["mag"] = _real8(body)
            elif rtype == _ANGLE:
                el["angle"] = _real8(body)
            elif rtype == _COLROW:
                el["colrow"] = struct.unpack(">hh", body[:4])
            elif rtype == _ENDEL:
                _finish(cur, el)
                el = None
    if dbu_um is None:
        raise GeometryError("GDS has no UNITS record")
    return dbu_um, cells


def _finish(cell: _Cell, el: dict) -> None:
    kind = el["kind"]
    xy = el.get("xy") or []
    if kind in (_SREF, _AREF):
        if "sname" not in el or not xy:
            raise GeometryError("reference without SNAME/XY")
        cell.refs.append(_Ref(el["sname"], xy, el["reflect"], el["mag"], el["angle"],
                              el.get("colrow")))
        return
    if kind in (_TEXT, _NODE):
        return
    key = (el.get("layer", -1), el.get("datatype", 0))
    if key not in _LAYER_SET or not xy:
        return
    if kind == _PATH:
        corners, area = _path_outline(xy, el["width"], el["pathtype"], el["bext"], el["eext"])
        if corners and area > 0:
            cell.boxes.append((key, area, _bbox(corners)))
        return
    pts = xy[:-1] if len(xy) > 1 and xy[0] == xy[-1] else xy
    area = _poly_area(pts)
    if area > 0:
        cell.boxes.append((key, area, _bbox(pts)))


def _transform_box(box, ref: _Ref, offset: tuple[float, float]):
    x1, y1, x2, y2 = box
    pts = [(x1, y1), (x1, y2), (x2, y1), (x2, y2)]
    a = math.radians(ref.angle)
    ca, sa = math.cos(a), math.sin(a)
    out = []
    for x, y in pts:
        if ref.reflect:
            y = -y
        x, y = x * ref.mag, y * ref.mag
        x, y = x * ca - y * sa, x * sa + y * ca
        out.append((x + offset[0], y + offset[1]))
    return _bbox(out)


def _ref_offsets(ref: _Ref) -> list[tuple[float, float]]:
    if ref.colrow is None:
        return [tuple(map(float, ref.xy[0]))]
    cols, rows = ref.colrow
    if len(ref.xy) < 3 or cols <= 0 or rows <= 0:
        raise GeometryError("bad AREF")
    (ox, oy), (cx, cy), (rx, ry) = ref.xy[:3]
    dcx, dcy = (cx - ox) / cols, (cy - oy) / cols
    drx, dry = (rx - ox) / rows, (ry - oy) / rows
    return [(ox + i * dcx + j * drx, oy + i * dcy + j * dry)
            for i in range(cols) for j in range(rows)]


def _pure_geometry(path: Path, top: str) -> Geometry:
    dbu_um, cells = read_gds_cells(path.read_bytes())
    referenced = {r.sname for c in cells.values() for r in c.refs}
    tops = [n for n in cells if n not in referenced]
    if top not in tops:
        raise GeometryError(f"Required top-level cell {top!r} is missing")
    if not math.isfinite(dbu_um) or dbu_um <= 0:
        raise GeometryError("Invalid GDS database unit")
    memo: dict[str, tuple[tuple | None, dict]] = {}

    def visit(name: str, stack: tuple[str, ...]):
        if name in memo:
            return memo[name]
        if name in stack:
            raise GeometryError(f"recursive cell hierarchy at {name}")
        cell = cells.get(name)
        if cell is None:
            raise GeometryError(f"reference to undefined cell {name}")
        bb, present = None, {}
        for key, area, box in cell.boxes:
            present[key] = present.get(key, 0.0) + area
            bb = box if bb is None else _union(bb, box)
        for ref in cell.refs:
            cbb, cpresent = visit(ref.sname, stack + (name,))
            if cbb is None:
                continue
            for k, a in cpresent.items():
                present[k] = present.get(k, 0.0) + a
            for off in _ref_offsets(ref):
                tb = _transform_box(cbb, ref, off)
                bb = tb if bb is None else _union(bb, tb)
        memo[name] = (bb, present)
        return memo[name]

    bb, present = visit(top, ())
    names = {v: k for k, v in MANUFACTURING_LAYERS.items()}
    present_named = {names[k]: list(k) for k, a in present.items() if a > 0}
    return _finalize(top, tops, dbu_um, bb, present_named, "pure_gdsii")


def _union(a, b):
    return (min(a[0], b[0]), min(a[1], b[1]), max(a[2], b[2]), max(a[3], b[3]))


def _finalize(top, tops, dbu_um, bb, present, backend) -> Geometry:
    if bb is None or not present:
        raise GeometryError("Top cell has no nonempty manufacturing geometry")
    width, height = (bb[2] - bb[0]) * dbu_um, (bb[3] - bb[1]) * dbu_um
    area = width * height
    if not all(math.isfinite(v) and v > 0 for v in (width, height, area)):
        raise GeometryError("Manufacturing geometry has invalid area")
    return Geometry(top, sorted(tops), dbu_um, tuple(c * dbu_um for c in bb), width, height, area,
                    dict(sorted(present.items())), backend)


def _klayout_geometry(path: Path, top: str) -> Geometry:
    import klayout.db as kdb

    layout = kdb.Layout()
    try:
        layout.read(str(path))
    except Exception as exc:  # noqa: BLE001
        raise GeometryError(f"Cannot read submitted GDS: {type(exc).__name__}") from exc
    tops = [c.name for c in layout.top_cells()]
    if top not in tops:
        raise GeometryError(f"Required top-level cell {top!r} is missing")
    if not math.isfinite(layout.dbu) or layout.dbu <= 0:
        raise GeometryError("Invalid GDS database unit")
    cell = layout.cell(top)
    bounds = kdb.Box()
    present = {}
    for name, layer in MANUFACTURING_LAYERS.items():
        index = layout.find_layer(*layer)
        if index is None:
            continue
        region = kdb.Region(cell.begin_shapes_rec(index))
        if not region.is_empty() and region.area() > 0:
            bounds += region.bbox()
            present[name] = list(layer)
    if bounds.empty() or not present:
        raise GeometryError("Top cell has no nonempty manufacturing geometry")
    bb = (bounds.left, bounds.bottom, bounds.right, bounds.top)
    return _finalize(top, tops, layout.dbu, bb, present, "klayout")


def klayout_available() -> bool:
    try:
        import klayout.db  # noqa: F401
    except ImportError:
        return False
    return True


def check_geometry(path: str | Path, top: str, backend: str | None = None) -> Geometry:
    """Raise ``GeometryError`` unless the layout passes the outer geometry contract."""
    p = Path(path)
    if not regular_nonempty_file(p):
        raise GeometryError("layout is missing, empty, or not a regular file")
    backend = backend or ("klayout" if klayout_available() else "pure")
    if backend == "klayout":
        return _klayout_geometry(p, top)
    try:
        return _pure_geometry(p, top)
    except (struct.error, IndexError, UnicodeError) as exc:
        raise GeometryError(f"Cannot read submitted GDS: {type(exc).__name__}") from exc


def gds_header_ok(path: str | Path) -> bool:
    """Integrity callback: regular non-empty file starting with a GDSII HEADER record."""
    p = Path(path)
    if not regular_nonempty_file(p):
        return False
    with p.open("rb") as fh:
        head = fh.read(4)
    return len(head) == 4 and head[2] == _HEADER


# ---------------------------------------------------------------------------------------------
# Class-2 advisories
# ---------------------------------------------------------------------------------------------

def advisory_missing_src_py(src_dir: str | Path | None) -> bool:
    """True when the submission source tree would be an outer ``artifact_issues`` entry:
    missing / symlinked directory, a symlink inside, or no ``*.py`` file anywhere."""
    if src_dir is None:
        return True
    d = Path(src_dir)
    if d.is_symlink() or not d.is_dir():
        return True
    found = False
    for root, dirs, files in os.walk(d):
        for name in dirs + files:
            if (Path(root) / name).is_symlink():
                return True
        found = found or any(f.endswith(".py") for f in files)
    return not found


def tree_digest(root: str | Path) -> dict[str, str]:
    root = Path(root)
    out: dict[str, str] = {}
    if not root.is_dir():
        return out
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root).as_posix()
        if p.is_symlink():
            out[rel] = "symlink:" + os.readlink(p)
        elif p.is_file():
            out[rel] = hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def advisory_task_modified(live_task_dir: str | Path, trusted_task_dir: str | Path) -> bool:
    """True when the live task inputs differ from the T=0 frozen copy (advisory only: every
    checker reads the frozen copy, and the outer verifier uses a fresh task copy)."""
    return tree_digest(live_task_dir) != tree_digest(trusted_task_dir)


# ---------------------------------------------------------------------------------------------
# Minimal GDSII writer (synthetic test / bank variants)
# ---------------------------------------------------------------------------------------------

def _rec(rtype: int, dtype: int, payload: bytes = b"") -> bytes:
    if len(payload) % 2:
        payload += b"\0"
    return struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload


def write_gds(path: str | Path, cells: dict[str, dict], *, dbu_um: float = 0.001,
              libname: str = "LIB") -> Path:
    """Write ``cells = {name: {"boxes": [(layer, dt, x1, y1, x2, y2)], "refs": [(child, x, y)],
    "texts": [(layer, dt, x, y, "label")]}}`` with coordinates in micrometres."""
    def i32(*v):
        return struct.pack(f">{len(v)}i", *v)

    def dbu(v):
        return int(round(v / dbu_um))

    out = [_rec(_HEADER, 2, struct.pack(">h", 600)), _rec(_BGNLIB, 2, bytes(24)),
           _rec(0x02, 6, libname.encode()),
           _rec(_UNITS, 5, _to_real8(dbu_um) + _to_real8(dbu_um * 1e-6))]
    for name, spec in cells.items():
        out += [_rec(_BGNSTR, 2, bytes(24)), _rec(_STRNAME, 6, name.encode())]
        for layer, dt, x1, y1, x2, y2 in spec.get("boxes", []):
            a, b, c, d = dbu(x1), dbu(y1), dbu(x2), dbu(y2)
            out += [_rec(_BOUNDARY, 0), _rec(_LAYER, 2, struct.pack(">h", layer)),
                    _rec(_DATATYPE, 2, struct.pack(">h", dt)),
                    _rec(_XY, 3, i32(a, b, c, b, c, d, a, d, a, b)), _rec(_ENDEL, 0)]
        for layer, dt, x, y, label in spec.get("texts", []):
            out += [_rec(_TEXT, 0), _rec(_LAYER, 2, struct.pack(">h", layer)),
                    _rec(0x16, 2, struct.pack(">h", dt)), _rec(_XY, 3, i32(dbu(x), dbu(y))),
                    _rec(0x19, 6, label.encode()), _rec(_ENDEL, 0)]
        for child, x, y in spec.get("refs", []):
            out += [_rec(_SREF, 0), _rec(_SNAME, 6, child.encode()),
                    _rec(_XY, 3, i32(dbu(x), dbu(y))), _rec(_ENDEL, 0)]
        out.append(_rec(_ENDSTR, 0))
    out.append(_rec(_ENDLIB, 0))
    p = Path(path)
    p.write_bytes(b"".join(out))
    return p
