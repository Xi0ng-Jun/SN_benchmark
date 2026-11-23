"""Deterministic 0-LLM-token synthetic bank variants (pure GDSII binary edits).

Each mutator takes the bytes of a source layout (a ``dev`` artifact with known labels) and the
exact top-cell name and returns a ``SyntheticVariant``: new bytes, the target mirror checker,
polarity, the expected verifier sub-criterion for that checker and the parameters used.

Negatives (expected ``d*_k = 0``):
* ``m_gds``: truncated stream, renamed top cell;
* ``m_lvs``: a port label removed;
* ``m_shape``: aspect ratio ``q > 4.2``;
* ``m_fill``: outer met1 ring that lowers ``rho = A_eff / A_bbox`` below ``0.08``;
* ``m_drc``: an isolated met1 sliver below minimum width.
Advisory: ``3.2 < q < 3.8`` is a true solution of ``q_max = 4.0`` tasks and a failure of
``q_max = 3.0`` tasks, tagged ``advisory_only_on_q3=True`` (positive for ``m_shape``).
Positive equivalents (labels inherited from the source): on-grid shift, ``90°`` rotation.

``m_morph`` has no geometric mutator here: a meaningless placement requires an electrical
re-placement, not an edit of the stream. Output is written by a pluggable ``writer(path, bytes)``.
"""
from __future__ import annotations

import math
import struct
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable

from ..checkers.alw import gds_geom

# GDSII record types
HEADER, UNITS, ENDLIB, BGNSTR, STRNAME, ENDSTR = 0x00, 0x03, 0x04, 0x05, 0x06, 0x07
BOUNDARY, PATH, SREF, AREF, TEXT, LAYER, DATATYPE, XY, ENDEL = (
    0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x0D, 0x0E, 0x10, 0x11)
SNAME, STRING, STRANS, MAG, ANGLE, BOX = 0x12, 0x19, 0x1A, 0x1B, 0x1C, 0x2D
ELEMENT_START = (BOUNDARY, PATH, SREF, AREF, TEXT, BOX, 0x15)
MET1 = (68, 20)
GRID_UM = 0.005
SLIVER_WIDTH_UM = 0.05          # sky130 met1 minimum width is 0.14 um
STRIP_UM = 0.2

Rec = tuple[int, int, bytes]
Writer = Callable[[Path, bytes], None]


def default_writer(path: Path, data: bytes) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_bytes(data)


@dataclass
class SyntheticVariant:
    name: str
    checker: str
    polarity: str                       # "positive" | "negative" | "equivalent"
    expected: dict                      # {d_key: 0 | 1} or {"inherit": True}
    data: bytes
    advisory_only_on_q3: bool = False
    params: dict = field(default_factory=dict)

    @property
    def source_kind(self) -> str:
        return "synthetic_neg" if self.polarity == "negative" else "synthetic_pos"

    def write(self, path: str | Path, writer: Writer = default_writer) -> Path:
        writer(Path(path), self.data)
        return Path(path)


# ---------------------------------------------------------------------------------------------
# record level
# ---------------------------------------------------------------------------------------------

def parse_records(data: bytes) -> list[Rec]:
    out, pos = [], 0
    while pos + 4 <= len(data):
        length, rtype, dtype = struct.unpack(">HBB", data[pos:pos + 4])
        if length < 4 or pos + length > len(data):
            raise ValueError("corrupt GDS record")
        out.append((rtype, dtype, data[pos + 4:pos + length]))
        pos += length
        if rtype == ENDLIB:
            return out
    raise ValueError("GDS stream has no ENDLIB")


def pack_records(recs: Iterable[Rec]) -> bytes:
    out = []
    for rtype, dtype, payload in recs:
        if len(payload) % 2:
            payload += b"\0"
        out.append(struct.pack(">HBB", 4 + len(payload), rtype, dtype) + payload)
    return b"".join(out)


def _real8(b: bytes) -> float:
    sign = -1.0 if b[0] & 0x80 else 1.0
    return sign * int.from_bytes(b[1:8], "big") / float(1 << 56) * 16.0 ** ((b[0] & 0x7F) - 64)


def _to_real8(x: float) -> bytes:
    if x == 0:
        return bytes(8)
    sign = 0x80 if x < 0 else 0
    x, exp = abs(x), 64
    while x >= 1:
        x, exp = x / 16.0, exp + 1
    while x < 1 / 16.0:
        x, exp = x * 16.0, exp - 1
    mant = int(round(x * (1 << 56)))
    if mant >= 1 << 56:
        mant, exp = mant >> 4, exp + 1
    return bytes([sign | exp]) + mant.to_bytes(7, "big")


def dbu_um(recs: list[Rec]) -> float:
    for rtype, _, payload in recs:
        if rtype == UNITS:
            return _real8(payload[8:16]) * 1e6
    raise ValueError("GDS stream has no UNITS record")


def _name(payload: bytes) -> str:
    return payload.rstrip(b"\0").decode("ascii", "replace")


def top_range(recs: list[Rec], top: str) -> tuple[int, int]:
    """Indices ``(i_bgnstr, i_endstr)`` of cell *top* (exact name)."""
    start = None
    for i, (rtype, _, payload) in enumerate(recs):
        if rtype == BGNSTR:
            start = i
        elif rtype == STRNAME and start is not None and _name(payload) == top:
            for j in range(i, len(recs)):
                if recs[j][0] == ENDSTR:
                    return start, j
    raise ValueError(f"cell {top!r} not found")


def _elements(recs: list[Rec], lo: int, hi: int) -> list[tuple[int, int]]:
    """Element ``(start, end_inclusive)`` index spans between *lo* and *hi*."""
    spans, i = [], lo
    while i < hi:
        if recs[i][0] in ELEMENT_START:
            j = i
            while recs[j][0] != ENDEL:
                j += 1
            spans.append((i, j))
            i = j + 1
        else:
            i += 1
    return spans


def _xy_map(payload: bytes, fn) -> bytes:
    vals = struct.unpack(f">{len(payload) // 4}i", payload)
    out = []
    for k in range(0, len(vals) - 1, 2):
        out += fn(vals[k], vals[k + 1])
    return struct.pack(f">{len(out)}i", *out)


def transform_top(data: bytes, top: str, *, dx_um: float = 0.0, dy_um: float = 0.0,
                  rot90: int = 0) -> bytes:
    """Rotate the top cell by ``rot90 * 90°`` (counter-clockwise, about the origin) then shift it.
    Child placements get the rotation added to their ``ANGLE`` (reflection is applied first in
    GDSII, so ``R_90 * R_a * M = R_(a+90) * M``)."""
    recs = parse_records(data)
    unit = dbu_um(recs)
    dx, dy, k = int(round(dx_um / unit)), int(round(dy_um / unit)), rot90 % 4

    def pt(x: int, y: int) -> list[int]:
        for _ in range(k):
            x, y = -y, x
        return [x + dx, y + dy]

    lo, hi = top_range(recs, top)
    out = recs[:lo]
    cell = recs[lo:hi + 1]
    spans = {s: e for s, e in _elements(cell, 0, len(cell))}
    i = 0
    while i < len(cell):
        if i not in spans:
            out.append(cell[i])
            i += 1
            continue
        el = cell[i:spans[i] + 1]
        kind = el[0][0]
        if kind in (SREF, AREF) and k:
            head = [r for r in el if r[0] not in (STRANS, MAG, ANGLE, XY, ENDEL)]
            strans = next((r for r in el if r[0] == STRANS), (STRANS, 1, b"\0\0"))
            mag = [r for r in el if r[0] == MAG]
            ang = next((_real8(r[2]) for r in el if r[0] == ANGLE), 0.0)
            new_ang = (ang + 90.0 * k) % 360.0
            xy = [(XY, r[1], _xy_map(r[2], pt)) for r in el if r[0] == XY]
            el = head + [strans] + mag + [(ANGLE, 5, _to_real8(new_ang))] + xy + [(ENDEL, 0, b"")]
        else:
            el = [(r[0], r[1], _xy_map(r[2], pt)) if r[0] == XY else r for r in el]
        out += el
        i = spans[i] + 1
    return pack_records(out + recs[hi + 1:])


def add_boxes_top(data: bytes, top: str, boxes_um: Iterable[tuple]) -> bytes:
    """Append rectangles ``(layer, datatype, x1, y1, x2, y2)`` (um) to the top cell."""
    recs = parse_records(data)
    unit = dbu_um(recs)
    _, hi = top_range(recs, top)
    new: list[Rec] = []
    for layer, dt, x1, y1, x2, y2 in boxes_um:
        a, b, c, d = (int(round(v / unit)) for v in (x1, y1, x2, y2))
        new += [(BOUNDARY, 0, b""), (LAYER, 2, struct.pack(">h", layer)),
                (DATATYPE, 2, struct.pack(">h", dt)),
                (XY, 3, struct.pack(">10i", a, b, c, b, c, d, a, d, a, b)), (ENDEL, 0, b"")]
    return pack_records(recs[:hi] + new + recs[hi:])


def drop_label(data: bytes, top: str, label: str) -> tuple[bytes, int]:
    """Remove every TEXT element of the top cell whose string equals *label*."""
    recs = parse_records(data)
    lo, hi = top_range(recs, top)
    drop: set[int] = set()
    for s, e in _elements(recs, lo, hi):
        if recs[s][0] == TEXT and any(r[0] == STRING and _name(r[2]) == label for r in recs[s:e + 1]):
            drop.update(range(s, e + 1))
    return pack_records(r for i, r in enumerate(recs) if i not in drop), len({s for s in drop
                                                                             if recs[s][0] == TEXT})


def rename_cell(data: bytes, old: str, new: str) -> bytes:
    recs = parse_records(data)
    out = []
    for rtype, dtype, payload in recs:
        if rtype in (STRNAME, SNAME) and _name(payload) == old:
            payload = new.encode()
        out.append((rtype, dtype, payload))
    return pack_records(out)


def bbox_um(data: bytes, top: str) -> tuple[float, float, float, float]:
    with tempfile.TemporaryDirectory() as d:
        p = Path(d) / "l.gds"
        p.write_bytes(data)
        return gds_geom.check_geometry(p, top, backend="pure").bbox_um


# ---------------------------------------------------------------------------------------------
# mutators
# ---------------------------------------------------------------------------------------------

def _q_strip(data: bytes, top: str, q: float) -> tuple[bytes, dict]:
    x1, y1, x2, y2 = bbox_um(data, top)
    w, h = x2 - x1, y2 - y1
    if w >= h:
        if w / h >= q:
            box = (*MET1, x1, y1, x1 + STRIP_UM, y1 + w / q)
        else:
            box = (*MET1, x1, y1, x1 + q * h, y1 + STRIP_UM)
    else:
        if h / w >= q:
            box = (*MET1, x1, y1, x1 + h / q, y1 + STRIP_UM)
        else:
            box = (*MET1, x1, y1, x1 + STRIP_UM, y1 + q * w)
    return add_boxes_top(data, top, [box]), {"q_target": q, "strip": list(box)}


def shape_advisory_q3(data: bytes, top: str, q: float = 3.5) -> SyntheticVariant:
    if not 3.2 < q < 3.8:
        raise ValueError("advisory aspect variant needs 3.2 < q < 3.8")
    out, params = _q_strip(data, top, q)
    return SyntheticVariant("shape_q_advisory", "m_shape", "positive", {"shape": 1}, out,
                            advisory_only_on_q3=True, params=params)


def shape_negative(data: bytes, top: str, q: float = 4.5) -> SyntheticVariant:
    if not q > 4.2:
        raise ValueError("negative aspect variant needs q > 4.2")
    out, params = _q_strip(data, top, q)
    return SyntheticVariant("shape_q_over_4", "m_shape", "negative", {"shape": 0}, out, params=params)


def fill_low_ring(data: bytes, top: str, effective_area_um2: float,
                  rho_target: float = 0.07) -> SyntheticVariant:
    if not 0 < rho_target < 0.08:
        raise ValueError("low-fill variant needs rho < 0.08")
    x1, y1, x2, y2 = bbox_um(data, top)
    w, h = x2 - x1, y2 - y1
    s = math.sqrt(effective_area_um2 / rho_target / (w * h))
    if s <= 1.0:
        raise ValueError("source fill is already below the target")
    cx, cy, W, H = (x1 + x2) / 2, (y1 + y2) / 2, s * w, s * h
    X1, Y1, X2, Y2 = cx - W / 2, cy - H / 2, cx + W / 2, cy + H / 2
    t = STRIP_UM
    ring = [(*MET1, X1, Y1, X2, Y1 + t), (*MET1, X1, Y2 - t, X2, Y2),
            (*MET1, X1, Y1, X1 + t, Y2), (*MET1, X2 - t, Y1, X2, Y2)]
    return SyntheticVariant("fill_low_ring", "m_fill", "negative", {"fill": 0},
                            add_boxes_top(data, top, ring),
                            params={"rho_target": rho_target, "bbox": [X1, Y1, X2, Y2],
                                    "effective_area_um2": effective_area_um2})


def drc_sliver(data: bytes, top: str, width_um: float = SLIVER_WIDTH_UM) -> SyntheticVariant:
    _, y1, x2, _ = bbox_um(data, top)
    box = (*MET1, x2 + 1.0, y1, x2 + 1.0 + width_um, y1 + 1.0)
    return SyntheticVariant("drc_sliver", "m_drc", "negative", {"drc": 0},
                            add_boxes_top(data, top, [box]), params={"box": list(box)})


def lvs_missing_label(data: bytes, top: str, port: str) -> SyntheticVariant:
    out, n = drop_label(data, top, port)
    if n == 0:
        raise ValueError(f"no label {port!r} in {top!r}")
    return SyntheticVariant("lvs_missing_label", "m_lvs", "negative", {"lvs": 0}, out,
                            params={"port": port, "removed": n})


def gds_truncated(data: bytes, keep_fraction: float = 0.5) -> SyntheticVariant:
    n = max(4, int(len(data) * keep_fraction)) & ~1
    return SyntheticVariant("gds_truncated", "m_gds", "negative", {"gds": 0}, data[:n],
                            params={"bytes": n})


def gds_renamed_top(data: bytes, top: str, new: str | None = None) -> SyntheticVariant:
    new = new or (top.swapcase() if top.swapcase() != top else top + "_x")
    return SyntheticVariant("gds_renamed_top", "m_gds", "negative", {"gds": 0},
                            rename_cell(data, top, new), params={"top": new})


def grid_shift(data: bytes, top: str, dx_um: float = 10.0, dy_um: float = -5.0) -> SyntheticVariant:
    for v in (dx_um, dy_um):
        if abs(v / GRID_UM - round(v / GRID_UM)) > 1e-9:
            raise ValueError("shift must be a multiple of the manufacturing grid")
    return SyntheticVariant("grid_shift", "*", "equivalent", {"inherit": True},
                            transform_top(data, top, dx_um=dx_um, dy_um=dy_um),
                            params={"dx_um": dx_um, "dy_um": dy_um})


def rotate90(data: bytes, top: str, turns: int = 1) -> SyntheticVariant:
    return SyntheticVariant("rotate90", "*", "equivalent", {"inherit": True},
                            transform_top(data, top, rot90=turns), params={"rot90": turns % 4})


def synthesize_all(data: bytes, top: str, *, ports: Iterable[str] = (),
                   effective_area_um2: float | None = None) -> list[SyntheticVariant]:
    """All applicable variants of one source layout, in a fixed order."""
    out = [shape_advisory_q3(data, top), shape_negative(data, top), drc_sliver(data, top),
           gds_truncated(data), gds_renamed_top(data, top), grid_shift(data, top), rotate90(data, top)]
    if effective_area_um2:
        try:
            out.append(fill_low_ring(data, top, effective_area_um2))
        except ValueError:
            pass
    for p in ports:
        try:
            out.append(lvs_missing_label(data, top, p))
            break
        except ValueError:
            continue
    return out
