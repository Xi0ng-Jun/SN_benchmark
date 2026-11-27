"""scripts/gds_dump.py on a hand-built hierarchical GDSII stream (no layout library needed)."""
from __future__ import annotations

import importlib.util
import struct
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("gds_dump", ROOT / "scripts" / "gds_dump.py")
gd = importlib.util.module_from_spec(spec)
spec.loader.exec_module(gd)


def rec(rtype: int, dtype: int = 0, body: bytes = b"") -> bytes:
    if len(body) % 2:
        body += b"\0"
    return struct.pack(">HBB", len(body) + 4, rtype, dtype) + body


def i2(*v: int) -> bytes:
    return struct.pack(f">{len(v)}h", *v)


def i4(*v: int) -> bytes:
    return struct.pack(f">{len(v)}i", *v)


def r8(x: float) -> bytes:
    if x == 0:
        return b"\0" * 8
    sign, x, e = (0x80 if x < 0 else 0), abs(x), 64
    while x >= 1:
        x, e = x / 16, e + 1
    while x < 1 / 16:
        x, e = x * 16, e - 1
    return bytes([sign | e]) + int(x * (1 << 56)).to_bytes(7, "big")


def cell(name: str, *elements: bytes) -> bytes:
    return rec(gd.BGNSTR, 2, i2(*[0] * 12)) + rec(gd.STRNAME, 6, name.encode()) + b"".join(elements) + rec(gd.ENDSTR)


def boundary(layer: int, pts: list[tuple[int, int]]) -> bytes:
    flat = [c for p in pts + pts[:1] for c in p]
    return (rec(gd.BOUNDARY) + rec(gd.LAYER, 2, i2(layer)) + rec(gd.DATATYPE, 2, i2(20))
            + rec(gd.XY, 3, i4(*flat)) + rec(gd.ENDEL))


def sref(name: str, at: tuple[int, int], mirror: bool = False, angle: float = 0.0) -> bytes:
    out = rec(gd.SREF) + rec(gd.SNAME, 6, name.encode())
    if mirror or angle:
        out += rec(gd.STRANS, 1, bytes([0x80 if mirror else 0, 0])) + rec(gd.ANGLE, 5, r8(angle))
    return out + rec(gd.XY, 3, i4(*at)) + rec(gd.ENDEL)


def write_gds(path: Path) -> None:
    leaf = cell("leaf", boundary(65, [(0, 0), (2000, 0), (2000, 1000), (0, 1000)]))
    aref = (rec(gd.AREF) + rec(gd.SNAME, 6, b"leaf") + rec(gd.COLROW, 2, i2(2, 1))
            + rec(gd.XY, 3, i4(0, 20000, 6000, 20000, 0, 21000)) + rec(gd.ENDEL))
    path_el = (rec(gd.PATH) + rec(gd.LAYER, 2, i2(68)) + rec(gd.DATATYPE, 2, i2(20)) + rec(gd.WIDTH, 3, i4(200))
               + rec(gd.XY, 3, i4(0, 0, 1000, 0)) + rec(gd.ENDEL))
    text = (rec(gd.TEXT) + rec(gd.LAYER, 2, i2(68)) + rec(gd.TEXTTYPE, 2, i2(5)) + rec(gd.XY, 3, i4(500, 0))
            + rec(gd.STRING, 6, b"out") + rec(gd.ENDEL))
    top = cell("top", sref("leaf", (10000, 0)), sref("leaf", (0, 0), angle=90.0),
               sref("leaf", (0, 10000), mirror=True), aref, path_el, text)
    path.write_bytes(rec(0x00, 2, i2(600)) + rec(0x02, 6, b"lib") + rec(gd.UNITS, 5, r8(1e-3) + r8(1e-9))
                     + leaf + top + rec(0x04))


def test_real8_roundtrip():
    for v in (1e-3, 1e-9, 90.0, 270.0, 1.0):
        assert abs(gd.real8(r8(v)) - v) < 1e-12 * max(1.0, v)


def test_flatten_transforms(tmp_path, capsys):
    gds = tmp_path / "t.gds"
    write_gds(gds)
    assert gd.main([str(gds)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "# cells=2 dbu_um=0.001 unreferenced=['top']"
    assert "# top | 1 | 1 | leaf x4" in lines
    body = lines[lines.index("== 65/20 n=5") + 1: lines.index("== 68/20 n=1")]
    assert sorted(body) == sorted([
        "10 0 12 1",      # translated
        "-1 0 0 2",       # rotated 90 degrees counter-clockwise
        "0 9 2 10",       # mirrored about the x axis, then moved up
        "0 20 2 21",      # array column 0
        "3 20 5 21",      # array column 1, pitch 3 um
    ])
    assert lines[lines.index("== 68/20 n=1") + 1] == "0 -0.1 1 0.1"
    assert lines[-1] == "T 68/5 0.5 0 out"


def test_layer_filter_and_cells_only(tmp_path, capsys):
    gds = tmp_path / "t.gds"
    write_gds(gds)
    gd.main([str(gds), "--layers", "68/20"])
    out = capsys.readouterr().out
    assert "== 68/20 n=1" in out and "== 65/20" not in out and "T 68/5" not in out
    gd.main([str(gds), "--cells"])
    assert "==" not in capsys.readouterr().out
