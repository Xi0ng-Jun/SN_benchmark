"""Build configs/alw/paper_reference.json from the AnalogLayoutWorld paper (arXiv 2611.16991v1).

    python scripts/make_paper_reference.py --paper-dir "$ALW_PAPER_DIR" \
        --out configs/alw/paper_reference.json

Inputs: ``paper.html`` (the arXiv HTML rendering) and ``paper.txt`` (its text conversion with
table cells separated by ``|``). Every number is parsed, none is typed:

* Tables 1-3 are read from ``paper.html`` with ``html.parser``; Table 1 is parsed a second time
  from ``paper.txt`` and the two must agree cell for cell.
* Score weights, gamma, rho_min, the q_max set and the rho* range are captured by regular
  expressions from Appendix C; prose statistics (abstract, sections 5.1-5.3, 6.1, appendix E)
  are captured by regular expressions, and each record keeps the matched sentence.
"""
import argparse
import json
import re
import sys
from html.parser import HTMLParser
from pathlib import Path

T1_COLUMNS = ("drc", "lvs", "sim", "acc", "area", "quality", "tool_calls", "tokens_m", "score")
WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6}


class _Tables(HTMLParser):
    """Rows of every ``<table>``: cell text, MathML replaced by its ``alttext``."""

    def __init__(self):
        super().__init__()
        self.tables, self._figs = [], []
        self._table = self._row = self._cell = None
        self._skip = 0

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "figure":
            self._figs.append(a.get("id"))
        elif tag == "table":
            self._table = {"id": a.get("id"), "figure": self._figs[-1] if self._figs else None, "rows": []}
        elif tag == "tr" and self._table is not None:
            self._row = []
        elif tag in ("td", "th") and self._row is not None:
            self._cell = []
        elif tag in ("annotation", "annotation-xml"):
            self._skip += 1
        elif tag == "math" and self._cell is not None:
            self._cell.append(" " + a.get("alttext", "") + " ")
            self._skip += 1

    def handle_endtag(self, tag):
        if tag in ("annotation", "annotation-xml"):
            self._skip -= 1
        elif tag == "math" and self._cell is not None:
            self._skip -= 1
        elif tag in ("td", "th") and self._cell is not None:
            self._row.append(re.sub(r"\s+", " ", "".join(self._cell)).strip())
            self._cell = None
        elif tag == "tr" and self._row is not None:
            self._table["rows"].append(self._row)
            self._row = None
        elif tag == "table" and self._table is not None:
            self.tables.append(self._table)
            self._table = None
        elif tag == "figure" and self._figs:
            self._figs.pop()

    def handle_data(self, data):
        if self._cell is not None and self._skip == 0:
            self._cell.append(data)


def html_tables(html: str) -> dict[str, list[list[str]]]:
    p = _Tables()
    p.feed(html)
    return {t["figure"]: t["rows"] for t in p.tables if t["figure"]}


def _num(c: str):
    c = c.strip()
    return None if c in ("—", "-", "–", "") else float(c)


def _t1_row(cells: list[str]) -> dict:
    m = re.match(r"(.+?)\s*\[(.+?)\]$", cells[0])
    if not m:
        raise ValueError(f"unexpected Table 1 cell {cells[0]!r}")
    system, setting = m.groups()
    rest = cells[1:]
    harness = None
    if len(rest) == len(T1_COLUMNS) + 1:
        harness, rest = rest[0], rest[1:]
    if len(rest) != len(T1_COLUMNS):
        raise ValueError(f"Table 1 row {cells!r} has {len(rest)} value cells")
    return dict(system=system, setting=setting, harness=harness,
                **{k: _num(v) for k, v in zip(T1_COLUMNS, rest)})


def table1_from_html(rows: list[list[str]]) -> list[dict]:
    if not rows[0][0].startswith("Model / System"):
        raise ValueError("Table 1 header not found")
    return [_t1_row(r) for r in rows[1:]]


def table1_from_txt(txt: str) -> list[dict]:
    t1 = txt[txt.index("Table 1: Main results"):txt.index("#### 5 Analysis")]
    t1 = t1[t1.index("Avg.\nScore"):]
    t1 = t1[t1.index("|") + 1:]
    cells = [re.sub(r"\s+", " ", c).strip() for c in t1.split("|")]
    cells = [c for c in cells if c]
    rows, i = [], 0
    while i < len(cells):
        if not re.search(r"\[.+\]$", cells[i]):
            raise ValueError(f"unexpected Table 1 text cell {cells[i]!r}")
        has_harness = not re.match(r"^[\d.]+$|^—$", cells[i + 1])
        n = 1 + has_harness + len(T1_COLUMNS)
        rows.append(_t1_row(cells[i:i + n]))
        i += n
    return rows


def table2(rows: list[list[str]]) -> list[dict]:
    head = [h.lower() for h in rows[0]]
    if head[:2] != ["submission", "gds"]:
        raise ValueError(f"Table 2 header {rows[0]!r}")
    return [{"submission": r[0], **{h: float(v) for h, v in zip(head[1:], r[1:])}} for r in rows[1:]]


def table3(rows: list[list[str]]) -> dict[str, dict]:
    out = {}
    for r in rows[1:]:
        for k in range(0, len(r) - 2, 3):
            if r[k].strip():
                out[str(int(r[k]))] = {"q_max": float(r[k + 1]), "rho_star": float(r[k + 2])}
    return dict(sorted(out.items(), key=lambda kv: int(kv[0])))


def _capture(flat: str, pattern: str, where: str, names: tuple[str, ...], conv=float) -> dict:
    """First match of *pattern*; *conv* is one converter or one per group."""
    m = re.search(pattern, flat)
    if not m:
        raise ValueError(f"not found in the paper text ({where}): {pattern}")
    start = flat.rfind(". ", 0, m.start()) + 2
    end = flat.find(". ", m.end())
    sentence = flat[max(start, 0):end + 1 if end > 0 else m.end()].strip()
    convs = conv if isinstance(conv, tuple) else (conv,) * len(names)
    vals = {n: c(g) for n, c, g in zip(names, convs, m.groups())}
    return {"where": where, "values": vals, "quote": sentence[:400]}


def build(html: str, txt: str) -> dict:
    tables = html_tables(html)
    rows = table1_from_html(tables["S4.T1"])
    rows_txt = table1_from_txt(txt)
    if rows != rows_txt:
        raise ValueError("Table 1 differs between paper.html and paper.txt")
    flat = re.sub(r"\s+", " ", txt).replace("–", "-").replace("--", "-")

    w = re.search(r"ordered as GDS, LVS, DRC, area, quality, and simulation, are "
                  r"\$\(([\d.,\s]+)\)\$ ; \$\\gamma=(\d+)\$", flat)
    if not w:
        raise ValueError("score weights not found")
    wv = [float(x) for x in w.group(1).split(",")]
    q = re.search(r"\$q_\{t\}\^\{\\max\}\\in\\\{(\d+),(\d+)\\\}\$", flat)
    rs = re.search(r"\$\\rho_\{t\}\^\{\\star\}\\in\[([\d.]+),([\d.]+)\]\$", flat)
    rmin = re.search(r"all \$\\rho_\{t\}\^\{\\min\}=([\d.]+)\$", flat)
    if not (q and rs and rmin):
        raise ValueError("geometry parameters not found")
    weights = dict(zip(("gds", "lvs", "drc", "area", "quality", "postsim"), wv),
                   gamma=int(w.group(2)), rho_min=float(rmin.group(1)),
                   q_max_values=[float(q.group(1)), float(q.group(2))],
                   rho_star_range=[float(rs.group(1)), float(rs.group(2))])

    word = lambda g: WORDS[g] if g in WORDS else int(g)   # noqa: E731
    stats = {
        "abstract_tasks": _capture(flat, r"with (\d+) expert-reviewed tasks", "abstract", ("tasks",), int),
        "abstract_submission_acceptance": _capture(
            flat, r"Across (\d+) model-harness configurations, ([\d.]+)% of attempts produce GDS "
                  r"layouts, but only ([\d.]+)% satisfy", "abstract",
            ("configurations", "gds_produced_pct", "accepted_pct"), (int, float, float)),
        "trials_per_task": _capture(flat, r"we run (\w+) independent", "section 4.1", ("trials",), word),
        "agent_budget_hours": _capture(flat, r"Each attempt has a (\w+)-hour wall-clock budget",
                                       "section 3.2", ("hours",), word),
        "repeated_verification": _capture(
            flat, r"([\d.]+)% of DRC first passes and ([\d.]+)% of LVS first passes occur after the "
                  r"first verification call", "section 5.1", ("drc_first_pass_after_first_call_pct",
                                                               "lvs_first_pass_after_first_call_pct")),
        "difficulty_vs_size": _capture(flat, r"\( \$\\rho_\{s\}=(-?[\d.]+)\$ \), indicating that larger",
                                       "section 5.2", ("spearman_score_vs_devices",)),
        "time_limit_hits": _capture(flat, r"GLM-5\.3 Flash reaches the agent time limit in (\d+) of (\d+) "
                                          r"attempts", "section 5.3", ("glm53_flash_hits", "attempts"), int),
        "aspect_failure_task9": _capture(flat, r"ratios range from ([\d.]+) to ([\d.]+), exceeding the "
                                               r"evaluation bound of (\d+)", "section 6.1",
                                         ("q_low", "q_high", "q_bound")),
        "task40_comparison": _capture(flat, r"\(([\d.]+) versus ([\d.]+)\)", "appendix E.1",
                                      ("gpt61_sol", "deepseek_v41_flash")),
        "task38_score": _capture(flat, r"scoring ([\d.]+):", "appendix E.2", ("gpt61_sol",)),
    }
    return {
        "schema_version": 1,
        "source": {"arxiv": "2611.16991", "version": "v1",
                   "title": "AnalogLayoutWorld: An Open Benchmark for Agentic Analog Layout Design",
                   "parsed_from": ["paper.html (Tables 1-3)", "paper.txt (Table 1 cross-check, prose)"],
                   "note": "Table 1 rows are means over 40 tasks x 3 attempts; percentages as printed"},
        "weights": weights,
        "table1": rows,
        "table2_ldo_decomposition": table2(tables["S6.T2"]),
        "table3_geometry": table3(tables["A3.T3"]),
        "statistics": stats,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--paper-dir", default=None,
                    help="directory holding paper.html and paper.txt (default: $ALW_PAPER_DIR)")
    ap.add_argument("--out", default="configs/alw/paper_reference.json")
    a = ap.parse_args(argv)
    import os
    d = Path(a.paper_dir or os.environ.get("ALW_PAPER_DIR", ""))
    if not (d / "paper.html").is_file() or not (d / "paper.txt").is_file():
        ap.error("--paper-dir (or $ALW_PAPER_DIR) must hold paper.html and paper.txt")
    ref = build((d / "paper.html").read_text(), (d / "paper.txt").read_text())
    if len(ref["table1"]) != 14 or len(ref["table3_geometry"]) != 40:
        sys.exit(f"unexpected table sizes: {len(ref['table1'])} rows, {len(ref['table3_geometry'])} tasks")
    Path(a.out).write_text(json.dumps(ref, indent=1, ensure_ascii=False) + "\n")
    for r in ref["table1"]:
        print(f"{r['system']:22s} {str(r['harness']):12s} " +
              " ".join(f"{str(r[k]):>6s}" for k in T1_COLUMNS))
    print(json.dumps(ref["weights"]))
    for k, v in ref["statistics"].items():
        print(f"{k}: {v['values']}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
