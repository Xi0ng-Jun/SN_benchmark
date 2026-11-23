"""``m_6`` (``m_drc``): the installed ``sky130A_mr.drc`` deck with the outer flags.

Flags: ``feol=true beol=true offgrid=true floating_met=true sram_exclude=false seal=false
info=false thr=2``. The ``report.lyrdb`` must be a report database for the requested top with an
``items`` element and at least one category; ``raw_u_6 = -markers`` (``0.0`` when clean).
"""
from __future__ import annotations

import shutil
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path

from .runtime import ToolUnavailable, load_runtime, require_file, run, tool_env

DRC_FLAGS = {"feol": "true", "beol": "true", "offgrid": "true", "floating_met": "true",
             "sram_exclude": "false", "seal": "false", "info": "false"}


def drc_report(path: str | Path, top: str) -> dict:
    root = ET.parse(path).getroot()
    if (root.tag != "report-database" or root.findtext("top-cell") != top
            or root.find("items") is None or not root.findall(".//categories/category")):
        raise RuntimeError("DRC report is incomplete or belongs to a different top cell")
    items = root.findall("./items/item")
    return {"markers": len(items),
            "categories": dict(Counter(i.findtext("category", "") for i in items)),
            "matches": not items}


def drc_command(runtime: dict, gds: Path, top: str, report: Path) -> list[str]:
    flags = {"input": str(gds), "top_cell": top, "thr": "2", "report": str(report), **DRC_FLAGS}
    cmd = [runtime["klayout_bin"], "-b", "-r", str(runtime["drc_deck"])]
    for k, v in flags.items():
        cmd += ["-rd", f"{k}={v}"]
    return cmd


def check_drc(gds: Path, top: str, out: Path, timeout: float, runtime: dict | None = None
              ) -> tuple[float | None, dict]:
    """``(raw_u_6, details)``; ``raw_u_6 is None`` when the run did not produce a valid report."""
    runtime = runtime or load_runtime()
    require_file(runtime["drc_deck"], "DRC deck")
    if shutil.which(runtime["klayout_bin"]) is None and not Path(runtime["klayout_bin"]).is_file():
        raise ToolUnavailable("klayout binary not found")
    out.mkdir(parents=True, exist_ok=True)
    report = out / "report.lyrdb"
    ex = run(drc_command(runtime, gds, top, report), cwd=out, timeout=timeout, env=tool_env(out))
    details = {"returncode": ex.returncode, "report": str(report)}
    if ex.returncode != 0 or not report.is_file():
        details["reason"] = f"klayout DRC exited {ex.returncode}"
        return None, details
    try:
        details.update(drc_report(report, top))
    except (ET.ParseError, RuntimeError, OSError) as exc:
        details["reason"] = str(exc)
        return None, details
    return -float(details["markers"]), details
