#!/usr/bin/env python3
"""Read-only text dump of a GDSII layout: cell hierarchy and flattened geometry in micrometres.
Standard library only; writes nothing.

    gds_dump.py FILE.gds                 header, cell table, flattened shapes and labels of the top cell
    gds_dump.py FILE.gds --cells         header and cell table only
    gds_dump.py FILE.gds --layers 65/20,66/20,68/20     keep only these layer/datatype pairs
    gds_dump.py FILE.gds --top NAME      flatten this cell instead of the unreferenced one

Output lines after the cell table (coordinates in um, top-cell frame):
    == L/D n=COUNT            start of one layer/datatype
    x1 y1 x2 y2               an axis-aligned rectangle
    P x,y x,y ...             any other polygon
    T L/D x y TEXT            a label

Paths are converted to one rectangle per Manhattan segment (other segments are reported as
``# skipped``). Array references are expanded.
"""
from __future__ import annotations

import argparse
import collections
import math
import struct
import sys

BOUNDARY, PATH, SREF, AREF, TEXT, BOX = 0x08, 0x09, 0x0A, 0x0B, 0x0C, 0x2D
UNITS, BGNSTR, STRNAME, ENDSTR, ENDEL = 0x03, 0x05, 0x06, 0x07, 0x11
LAYER, DATATYPE, WIDTH, XY, SNAME, COLROW = 0x0D, 0x0E, 0x0F, 0x10, 0x12, 0x13
TEXTTYPE, STRANS, MAG, ANGLE, PATHTYPE, STRING, BOXTYPE = 0x16, 0x1A, 0x1B, 0x1C, 0x21, 0x19, 0x2E


def real8(b: bytes) -> float:
    """GDSII 8-byte real: sign bit, excess-64 base-16 exponent, 56-bit mantissa."""
    if not any(b):
        return 0.0
    sign = -1.0 if b[0] & 0x80 else 1.0
    return sign * int.from_bytes(b[1:8], "big") / float(1 << 56) * 16.0 ** ((b[0] & 0x7F) - 64)


def records(data: bytes):
    pos = 0
    while pos + 4 <= len(data):
        size, rtype, dtype = struct.unpack(">HBB", data[pos:pos + 4])
        if size < 4:
            break
        body = data[pos + 4:pos + size]
        if dtype == 2:
            val = list(struct.unpack(f">{len(body) // 2}h", body))
        elif dtype == 3:
            val = list(struct.unpack(f">{len(body) // 4}i", body))
        elif dtype == 5:
            val = [real8(body[i:i + 8]) for i in range(0, len(body), 8)]
        elif dtype == 6:
            val = body.rstrip(b"\0").decode("latin-1")
        else:
            val = body
        yield rtype, val
        pos += size


def parse(data: bytes):
    """Return (user_unit_in_um_per_dbu, {cell: [element dict, ...]})."""
    dbu_um, cells, cur, el = 0.001, {}, None, None
    for rtype, val in records(data):
        if rtype == UNITS:
            dbu_um = val[1] * 1e6
        elif rtype == BGNSTR:
            cur = []
        elif rtype == STRNAME:
            cells[val] = cur
        elif rtype == ENDSTR:
            cur = None
        elif rtype in (BOUNDARY, PATH, SREF, AREF, TEXT, BOX):
            el = {"kind": rtype, "layer": 0, "dtype": 0, "mirror": False, "mag": 1.0, "angle": 0.0}
        elif el is None:
            continue
        elif rtype == LAYER:
            el["layer"] = val[0]
        elif rtype in (DATATYPE, TEXTTYPE, BOXTYPE):
            el["dtype"] = val[0]
        elif rtype == WIDTH:
            el["width"] = val[0]
        elif rtype == PATHTYPE:
            el["pathtype"] = val[0]
        elif rtype == XY:
            el["xy"] = list(zip(val[0::2], val[1::2]))
        elif rtype == SNAME:
            el["sname"] = val
        elif rtype == COLROW:
            el["colrow"] = (val[0], val[1])
        elif rtype == STRANS:
            el["mirror"] = bool(val[0] & 0x8000) if isinstance(val, list) else bool(val[0] & 0x80)
        elif rtype == MAG:
            el["mag"] = val[0]
        elif rtype == ANGLE:
            el["angle"] = val[0]
        elif rtype == STRING:
            el["text"] = val
        elif rtype == ENDEL:
            if cur is not None:
                cur.append(el)
            el = None
    return dbu_um, cells


def compose(outer, inner):
    """Affine maps as (a, b, c, d, tx, ty): x' = a x + b y + tx, y' = c x + d y + ty."""
    a, b, c, d, tx, ty = outer
    e, f, g, h, ux, uy = inner
    return (a * e + b * g, a * f + b * h, c * e + d * g, c * f + d * h, a * ux + b * uy + tx, c * ux + d * uy + ty)


def ref_map(el, origin):
    """GDSII order: mirror about the x axis, magnify, rotate counter-clockwise, translate."""
    th = math.radians(el["angle"])
    cs, sn, m = math.cos(th), math.sin(th), el["mag"]
    my = -1.0 if el["mirror"] else 1.0
    return (m * cs, -m * sn * my, m * sn, m * cs * my, origin[0], origin[1])


def apply(t, p):
    return (t[0] * p[0] + t[1] * p[1] + t[4], t[2] * p[0] + t[3] * p[1] + t[5])


def path_rects(el):
    pts, hw = el["xy"], el.get("width", 0) / 2.0
    ext = hw if el.get("pathtype", 0) == 2 else 0.0
    out, skipped = [], 0
    for i, ((x0, y0), (x1, y1)) in enumerate(zip(pts, pts[1:])):
        e0 = ext if i == 0 else hw
        e1 = ext if i == len(pts) - 2 else hw
        if y0 == y1:
            lo, hi = (x0 - e0, x1 + e1) if x0 <= x1 else (x1 - e1, x0 + e0)
            out.append([(lo, y0 - hw), (hi, y0 - hw), (hi, y0 + hw), (lo, y0 + hw)])
        elif x0 == x1:
            lo, hi = (y0 - e0, y1 + e1) if y0 <= y1 else (y1 - e1, y0 + e0)
            out.append([(x0 - hw, lo), (x0 + hw, lo), (x0 + hw, hi), (x0 - hw, hi)])
        else:
            skipped += 1
    return out, skipped


def flatten(cells, name, t, shapes, labels, stats, depth=0):
    if depth > 32:
        sys.exit("reference depth above 32: recursive hierarchy?")
    for el in cells[name]:
        kind = el["kind"]
        if kind in (BOUNDARY, BOX):
            shapes[(el["layer"], el["dtype"])].append([apply(t, p) for p in el["xy"]])
        elif kind == PATH:
            rects, skipped = path_rects(el)
            stats["skipped_path_segments"] += skipped
            for r in rects:
                shapes[(el["layer"], el["dtype"])].append([apply(t, p) for p in r])
        elif kind == TEXT:
            labels.append(((el["layer"], el["dtype"]), apply(t, el["xy"][0]), el.get("text", "")))
        elif kind == SREF:
            if el["sname"] not in cells:
                stats["missing_cells"] += 1
                continue
            flatten(cells, el["sname"], compose(t, ref_map(el, el["xy"][0])), shapes, labels, stats, depth + 1)
        elif kind == AREF:
            if el["sname"] not in cells:
                stats["missing_cells"] += 1
                continue
            cols, rows = el["colrow"]
            o, pc, pr = el["xy"]
            dc = ((pc[0] - o[0]) / cols, (pc[1] - o[1]) / cols)
            dr = ((pr[0] - o[0]) / rows, (pr[1] - o[1]) / rows)
            for i in range(cols):
                for j in range(rows):
                    org = (o[0] + i * dc[0] + j * dr[0], o[1] + i * dc[1] + j * dr[1])
                    flatten(cells, el["sname"], compose(t, ref_map(el, org)), shapes, labels, stats, depth + 1)


def num(v: float) -> str:
    s = f"{v:.3f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def as_rect(poly):
    pts = poly[:-1] if len(poly) > 1 and poly[0] == poly[-1] else poly
    xs, ys = sorted({round(p[0], 6) for p in pts}), sorted({round(p[1], 6) for p in pts})
    if len(pts) == 4 and len(xs) == 2 and len(ys) == 2:
        return xs[0], ys[0], xs[1], ys[1]
    return None


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("gds")
    ap.add_argument("--top")
    ap.add_argument("--cells", action="store_true")
    ap.add_argument("--layers", default="")
    args = ap.parse_args(argv)
    with open(args.gds, "rb") as fh:
        dbu, cells = parse(fh.read())
    referenced = {el["sname"] for els in cells.values() for el in els if el["kind"] in (SREF, AREF)}
    tops = [c for c in cells if c not in referenced]
    top = args.top or (tops[0] if len(tops) == 1 else None)
    print(f"# cells={len(cells)} dbu_um={dbu:g} unreferenced={tops}")
    print("# cell table: name | shapes | labels | references (name x count)")
    for name, els in cells.items():
        refs = collections.Counter(el["sname"] for el in els if el["kind"] in (SREF, AREF))
        n_shape = sum(el["kind"] in (BOUNDARY, BOX, PATH) for el in els)
        n_text = sum(el["kind"] == TEXT for el in els)
        print(f"# {name} | {n_shape} | {n_text} | " + ", ".join(f"{k} x{v}" for k, v in refs.items()))
    if args.cells:
        return 0
    if top is None or top not in cells:
        sys.exit(f"give --top NAME; unreferenced cells: {tops}")
    shapes, labels, stats = collections.defaultdict(list), [], collections.Counter()
    flatten(cells, top, (dbu, 0.0, 0.0, dbu, 0.0, 0.0), shapes, labels, stats)
    keep = {tuple(int(v) for v in s.split("/")) for s in args.layers.split(",") if s}
    pts = [p for polys in shapes.values() for poly in polys for p in poly]
    if pts:
        print(f"# top={top} bbox_um={num(min(p[0] for p in pts))} {num(min(p[1] for p in pts))} "
              f"{num(max(p[0] for p in pts))} {num(max(p[1] for p in pts))}")
    print("# layers: " + " ".join(f"{l}/{d}:{len(v)}" for (l, d), v in sorted(shapes.items())))
    if stats:
        print("# " + " ".join(f"{k}={v}" for k, v in stats.items()))
    for (l, d), polys in sorted(shapes.items()):
        if keep and (l, d) not in keep:
            continue
        print(f"== {l}/{d} n={len(polys)}")
        for poly in polys:
            r = as_rect(poly)
            if r:
                print(" ".join(num(v) for v in r))
            else:
                body = poly[:-1] if poly[0] == poly[-1] else poly
                print("P " + " ".join(f"{num(x)},{num(y)}" for x, y in body))
    for (l, d), (x, y), text in labels:
        if not keep or (l, d) in keep:
            print(f"T {l}/{d} {num(x)} {num(y)} {text}")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
