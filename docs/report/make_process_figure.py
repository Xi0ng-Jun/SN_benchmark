#!/usr/bin/env python3
"""Generate ``process_figure.tex``: one 16:9 page on how the agent produces a layout in the
benchmark, how the result is scored, and the three conditions compared.

Geometry, device list, scores and measurements are read from files copied back from the
evaluation server; nothing numeric is typed in except the run timeline (see TIMELINE).

    python3 docs/report/make_process_figure.py

Inputs (relative to the repository root):
    docs/server-info/analysis/fig2.txt   gds_dump.py output, generator source, extracted netlist
    docs/server-info/analysis/fig3.txt   official score parts, post-layout measurements, agent self-test
    docs/server-info/test_export/test_runs.jsonl, hraw_probe.txt
    configs/alw/catalog.json, ../method-v5/experiments/alw/paper_reference.json
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SI = ROOT / "docs" / "server-info"
OUT = Path(__file__).resolve().parent / "process_figure.tex"
TASK = "sky130-internet-ajc-schmitt-layout"

# Phases of the example run (minutes), from the single-run analysis report
# docs/server-info/analysis/run_test-H_base_ajc-schmitt.md (sections 2-4); the key moments
# (146, 152, 155-157, 161) were checked against the trajectory (spot.txt, fig1.txt, lvs_digest).
TIMELINE = [
    (0, 3, "read"), (3, 39, "probe"), (39, 63, "read"), (63, 89, "think"),
    (89, 137, "probe"), (137, 146, "think"), (146, 161, "full"), (161, 165, "sim"),
]
PHASE = {
    "read": ("读题面、文档与规则文件", "cRead"),
    "probe": ("单器件试验（摸清规则如何识别器件）", "cProbe"),
    "think": ("长思考，回复超出长度上限被截断，没有产出操作", "cThink"),
    "full": ("写整版脚本并按 DRC / LVS 修改", "cFull"),
    "sim": ("寄生提取与后仿真", "cSim"),
}
T_FIRST_FULL, T_LVS_OK, T_SUBMIT, T_END, T_BUDGET = 152, 156, 157, 165, 180

# layer/datatype -> (TikZ style, legend); drawing order bottom to top. Contacts, li1 and implant
# layers are left out for legibility.
DRAW = [
    ((64, 20), "lyNwell", "n 阱"),
    ((65, 20), "lyNdiff", "n 有源"),
    ((65, 20, "p"), "lyPdiff", "p 有源"),
    ((65, 44), "lyTap", "体接触"),
    ((66, 20), "lyPoly", "多晶硅"),
    ((68, 20), "lyM1", "金属1"),
    ((69, 20), "lyM2", "金属2"),
    ((68, 44), "lyVia", "通孔"),
]


def read_gds_dump(text: str):
    layers, labels, cur = {}, [], None
    for line in text.splitlines():
        if line.startswith("#### "):
            break
        if line.startswith("== "):
            l, d = line.split()[1].split("/")
            cur = layers.setdefault((int(l), int(d)), [])
        elif line.startswith("T "):
            _, ld, x, y, name = line.split(maxsplit=4)
            labels.append((ld, float(x), float(y), name))
        elif cur is not None and line and line[0] in "-0123456789":
            cur.append(tuple(float(v) for v in line.split()))
    bbox = [float(v) for v in re.search(r"bbox_um=(\S+) (\S+) (\S+) (\S+)", text).groups()]
    cells = dict(re.findall(r"(sky130_fd_pr__\w+?_01v8_GW\w+?)_GL\S+ x(\d+)", text))
    return layers, labels, bbox, cells


def section(text: str, name: str) -> str:
    part = text.split(f"#### {name}\n", 1)[1]
    return part.split("\n#### ", 1)[0]


def esc(s: str) -> str:
    out = []
    for ch in s:
        out.append({"\\": "\\textbackslash{}", "_": "\\_", "{": "\\{", "}": "\\}", "#": "\\#", "%": "\\%",
                    "&": "\\&", "$": "\\$", "^": "\\^{}", "~": "\\~{}", '"': "\\textquotedbl{}"}.get(ch, ch))
    return "".join(out)


def code_lines(src: str) -> list[str]:
    """Verbatim lines of the generator (only re-wrapped inside brackets); '...' marks omissions."""
    def find(prefix: str) -> str:
        hits = [l for l in src.splitlines() if l.strip().startswith(prefix)]
        assert hits, prefix
        return hits[0]

    add_ref = find("ref = c.add_ref(")
    assert add_ref.strip() == "ref = c.add_ref(fac(gate_width=w, gate_length=0.5, nf=1, guard_ring=False))"
    riser = " ".join(l.strip() for l in src.split("            _rect(c, x - SD_RISER_W / 2, y - 0.17,")[1]
                     .split(")\n", 1)[0].splitlines())
    assert riser == "x + SD_RISER_W / 2, 0.13, L.met1drawing"
    label = find("c.add_label(")
    assert label.strip() == "c.add_label(net, position=(tx, up_y[net]), layer=L.met2label)"
    return [
        find("DEVICES = [").strip(),
        find('("M1",'),
        find('("M3",'),
        "    ...]",
        find("for i, (name, kind, w, left, right, gate)").strip(),
        "    ref = c.add_ref(fac(gate_width=w, gate_length=0.5,",
        "                        nf=1, guard_ring=False))",
        "    ...",
        "    _rect(c, x - SD_RISER_W / 2, y - 0.17,",
        "          x + SD_RISER_W / 2, 0.13, L.met1drawing)",
        "...",
        "c.add_label(net, position=(tx, up_y[net]),",
        "            layer=L.met2label)",
    ]


def netlist_rows(spice: str):
    rows = []
    for line in spice.splitlines():
        m = re.match(r"X(\w+) (\S+) (\S+) (\S+) (\S+) sky130_fd_pr__(\w)fet_01v8 L=(\S+) W=(\S+) nf=(\d+) m=(\d+)", line)
        if m:
            rows.append(dict(name=m[1], d=m[2], g=m[3], s=m[4], kind=m[6], L=m[7], W=m[8], nf=m[9], m=m[10]))
    return rows


def stop_counts(runs: list[dict]) -> list[int]:
    """Runs ending at: no layout | layout, LVS failed | LVS passed, not fully accepted | fully accepted."""
    c = [0, 0, 0, 0]
    for r in runs:
        d = r["d_star"]
        c[0 if d["gds"] != 1 else 1 if d["lvs"] != 1 else 3 if r["C_star"] == 1 else 2] += 1
    return c


def main() -> None:
    fig2 = (SI / "analysis" / "fig2.txt").read_text()
    fig3 = (SI / "analysis" / "fig3.txt").read_text()
    layers, labels, bbox, cells = read_gds_dump(fig2)
    src = section(fig2, "snapshot/submission/src/schmitt_lib.py")
    n_src = len(src.splitlines())
    devices = re.findall(r'\("(\w+)", "([np])", ([\d.]+), "(\w+)", "(\w+)", "(\w+)"\),', src)
    pitch = float(re.search(r"^PITCH = ([\d.]+)", src, re.M).group(1))
    extracted = section(fig2, "stages/lvs/extracted.spice")
    n_ext = len(re.findall(r"^XM\$", extracted, re.M))
    w_ext = sorted(int(w) // 1000000 for w in re.findall(r" W=(\d+)", extracted))

    score = json.JSONDecoder().raw_decode(fig3)[0]
    sc, si = score["score_components"], score["scoring_inputs"]
    meas = {m[1]: float(m[2]) for m in re.finditer(r'"(\w+)": \{\s+"value": ([\d.eE+-]+),\s+"passed": true', fig3)}
    cov = re.search(r'"source_analysis_count": (\d+),\s+"evaluation_analysis_count": (\d+)', fig3)
    n_src_cases, n_eval_cases = int(cov[1]), int(cov[2])
    eval_case = re.search(r'"evaluation_case_ids": \[\s+"(\w+?)_([\d.]+)V_(-?\d+)C"', fig3)
    pex = re.search(r'"resistors": (\d+),\s+"capacitors": (\d+)', fig3)
    self_pass = re.search(r"\n(\d)/(\d) PASS\. Failures", fig3)

    cat = json.loads((ROOT / "configs" / "alw" / "catalog.json").read_text())
    task = next(t for t in cat["tasks"] if t["task_id"] == TASK)
    paper = json.loads((ROOT.parent / "method-v5" / "experiments" / "alw" / "paper_reference.json").read_text())
    geo, wts = paper["geometry"][str(task["number"])], paper["weights"]
    assert abs(wts["area"] * min(1, si["raw_area_ratio"] / geo["rho_star"]) ** wts["gamma"] - sc["area"]) < 1e-6

    runs = [json.loads(l) for l in (SI / "test_export" / "test_runs.jsonl").read_text().splitlines()]
    runs = [r for r in runs if not r["infra_error"]]
    arms = {a: [r for r in runs if r["arm"] == a] for a in ("H_raw", "H_base", "H_star")}
    this = next(r for r in arms["H_base"] if r["task_id"] == TASK)
    assert abs(this["Y_star"] - score["score"]) < 1e-9
    probe = re.search(r"RUN H_base sky130-internet-ajc-schmitt\S* steps=(\d+) calls=(\d+)",
                      (SI / "test_export" / "hraw_probe.txt").read_text())

    netlist = netlist_rows((ROOT.parents[2] / "external" / "AnalogLayoutWorld" / "repo" / "tasks" / TASK /
                            "environment" / "task" / "circuit.spice").read_text())
    assert len(netlist) == len(devices) == n_ext == 8
    assert sorted(int(float(r["W"]) * int(r["m"])) for r in netlist) == w_ext == sorted(int(float(d[2])) for d in devices)

    # ---------------------------------------------------------------- layout drawing
    LX, LY, LW = 19.45, 11.0, 9.2            # lower-left corner and width (cm) of the layout drawing
    s = LW / (bbox[2] - bbox[0])

    def X(x):
        return LX + (x - bbox[0]) * s

    def Y(y):
        return LY + (y - bbox[1]) * s

    wells = layers[(64, 20)]

    def in_well(r):
        return any(w[0] <= r[0] and w[1] <= r[1] and r[2] <= w[2] and r[3] <= w[3] for w in wells)

    def shapes(key):
        rects = layers.get(key[:2], [])
        if key[:2] == (65, 20):      # active area: p-type inside the n-well, n-type outside
            rects = [r for r in rects if in_well(r) == (len(key) == 3)]
        return rects

    lay = []
    for key, style, _ in DRAW:
        for x1, y1, x2, y2 in shapes(key):
            lay.append(f"\\path[{style}] ({X(x1):.3f},{Y(y1):.3f}) rectangle ({X(x2):.3f},{Y(y2):.3f});")
    ytxt = LY - 0.32
    wmax = max(float(d[2]) for d in devices)

    def netlist_name(kind, w, left, right, gate):
        hits = [r["name"] for r in netlist if r["kind"] == kind and float(r["W"]) * int(r["m"]) == float(w)
                and r["g"] == gate and {r["d"], r["s"]} == {left, right}]
        assert len(hits) == 1, (kind, w, left, right, gate, hits)
        return hits[0]

    for i, (_agent_name, kind, w, left, right, gate) in enumerate(devices):
        name = netlist_name(kind, w, left, right, gate)
        hot = ",text=cHot" if float(w) == wmax else ""
        if hot:
            lay.append(f"\\draw[cHot,line width=1pt] ({X(i * pitch - 0.72):.3f},{Y(-wmax / 2 - 0.62):.3f}) "
                       f"rectangle ({X(i * pitch + 0.72):.3f},{Y(wmax / 2 + 0.62):.3f});")
        lay.append(f"\\node[devlab{hot}] at ({X(i * pitch):.3f},{ytxt:.3f}) {{{esc(name)}}};")
        lay.append(f"\\node[devw] at ({X(i * pitch):.3f},{ytxt - 0.38:.3f}) {{{float(w):g}}};")
    n_n = sum(d[1] == "n" for d in devices)
    lay.append(f"\\node[devw,anchor=east] at ({X(0) - 0.45:.3f},{ytxt - 0.38:.3f}) {{$W$/µm}};")
    for a, b, text in ((0, n_n - 1, "NMOS"), (n_n, len(devices) - 1, "PMOS（n 阱内）")):
        xa, xb = X(a * pitch) - 0.42, X(b * pitch) + 0.42
        lay.append(f"\\draw[gray,line width=0.5pt] ({xa:.3f},{ytxt - 0.68:.3f}) -- ({xb:.3f},{ytxt - 0.68:.3f});")
        lay.append(f"\\node[devw] at ({(xa + xb) / 2:.3f},{ytxt - 0.95:.3f}) {{{text}}};")
    # Bus -> net from the generator's own constants, checked against the drawn metal-2 rectangles
    # and against the four pin labels in the GDS.
    above = re.findall(r'"(\w+)"', re.search(r"^ABOVE = \[(.*?)\]", src, re.M).group(1))
    below = re.findall(r'"(\w+)"', re.search(r"^BELOW = \[(.*?)\]", src, re.M).group(1))
    bus_pitch = float(re.search(r"^BUS_PITCH = ([\d.]+)", src, re.M).group(1))
    assert "bus0_up = tallest / 2 + 0.30" in src and "bus0_dn = -(tallest / 2 + 0.30)" in src
    buses = [(wmax / 2 + 0.30 + i * bus_pitch, n) for i, n in enumerate(above)]
    buses += [(-(wmax / 2 + 0.30) - i * bus_pitch, n) for i, n in enumerate(below)]
    horiz = [r for r in layers[(69, 20)] if r[2] - r[0] > r[3] - r[1]]
    pins = {name: y for ld, _x, y, name in labels if ld == "69/5"}
    assert set(pins) == set(task["ports"])
    for group in (sorted(b for b in buses if b[0] > 0), sorted((b for b in buses if b[0] < 0), reverse=True)):
        ys = [Y(y) for y, _ in group]
        mid = (ys[0] + ys[-1]) / 2
        for k, (y, name) in enumerate(group):
            bar = [r for r in horiz if abs((r[1] + r[3]) / 2 - y) < 1e-3]
            assert len(bar) == 1, (name, y)
            assert name not in pins or y < 0 or abs(pins[name] - y) < 1e-3
            ylab = mid + (k - (len(group) - 1) / 2) * 0.31 * (1 if y > 0 else -1)
            lay.append(f"\\draw[gray,line width=0.4pt] ({X(bar[0][0]):.3f},{Y(y):.3f}) -- ({LX - 0.18:.3f},{ylab:.3f});")
            lay.append(f"\\node[portlab,anchor=east] at ({LX - 0.18:.3f},{ylab:.3f}) {{{esc(name)}}};")
    sb = LX + LW - 2 * s
    lay.append(f"\\draw[line width=1pt] ({sb:.3f},{LY - 0.25:.3f}) -- ({LX + LW:.3f},{LY - 0.25:.3f});")
    lay.append(f"\\node[devw,anchor=north] at ({sb + s:.3f},{LY - 0.27:.3f}) {{2 µm}};")
    lx, yl = LX - 0.9, ytxt - 1.42
    for _key, style, text in DRAW:
        lay.append(f"\\path[{style}] ({lx:.3f},{yl - 0.12:.3f}) rectangle ({lx + 0.3:.3f},{yl + 0.12:.3f});")
        lay.append(f"\\node[devw,anchor=west] at ({lx + 0.36:.3f},{yl:.3f}) {{{text}}};")
        lx += 0.52 + 0.27 * len(text)
    lay.append(f"\\node[anchor=west,inner sep=0pt,font=\\footnotesize,text=cHot] at ({LX - 0.9:.3f},{yl - 0.44:.3f}) "
               f"{{网表中 $W$=1 µm、$m$={max(int(r['m']) for r in netlist)} 的管子画成 1 个 $W$={wmax:g} µm 的单指管，"
               f"官方 LVS 判为匹配。}};")

    # ---------------------------------------------------------------- netlist table
    net = ["\\begin{tabular}{@{}l@{\\hspace{5pt}}c@{\\hspace{5pt}}c@{\\hspace{5pt}}c@{\\hspace{7pt}}l@{}}",
           "\\textcolor{gray}{器件} & \\textcolor{gray}{类型} & \\textcolor{gray}{$W/L$} & \\textcolor{gray}{$m$} & "
           "\\textcolor{gray}{D, G, S} \\\\[1pt]"]
    for r in netlist:
        hot = "\\bfseries\\color{cHot}" if r["m"] == max(n["m"] for n in netlist) else ""
        net.append(f"{{{hot}{esc(r['name'])}}} & {{{hot}{r['kind']}}} & {{{hot}{r['W']}/{r['L']}}} & {{{hot}{r['m']}}} & "
                   f"{{{hot}{esc(r['d'])}, {esc(r['g'])}, {esc(r['s'])}}} \\\\")
    net.append("\\end{tabular}")

    # ---------------------------------------------------------------- code excerpt
    code = []
    for line in code_lines(src):
        body = esc(line).replace(" ", "~")
        if '"M3"' in line:
            body = f"\\textcolor{{cHot}}{{{body}}}"
        code.append(body + "\\\\")

    # ---------------------------------------------------------------- timeline
    TX0, TX1, TY = 2.2, 32.3, 7.4
    ts = (TX1 - TX0) / T_BUDGET

    def T(m):
        return TX0 + m * ts

    before = 100 * T_FIRST_FULL / T_END
    tl = [f"\\path[fill=black!6] ({T(0):.3f},{TY}) rectangle ({T(T_BUDGET):.3f},{TY + 0.5});"]
    for a, b, kind in TIMELINE:
        tl.append(f"\\path[fill={PHASE[kind][1]}] ({T(a):.3f},{TY}) rectangle ({T(b):.3f},{TY + 0.5});")
    for m in range(0, T_BUDGET + 1, 30):
        tl.append(f"\\draw[gray,line width=0.4pt] ({T(m):.3f},{TY}) -- ({T(m):.3f},{TY - 0.1});")
        tl.append(f"\\node[tick] at ({T(m):.3f},{TY - 0.12}) {{{m}}};")
    tl.append(f"\\node[tick,anchor=north west] at ({T(T_BUDGET) + 0.3:.3f},{TY - 0.12}) {{min}};")
    tl.append(f"\\draw[cHot,line width=1pt] ({T(T_FIRST_FULL):.3f},{TY - 0.05}) -- ({T(T_FIRST_FULL):.3f},{TY + 0.85});")
    tl.append(f"\\node[anchor=south east,inner sep=1.5pt,text=cHot,font=\\small] at ({T(T_FIRST_FULL) + 0.05:.3f},{TY + 0.5}) "
              f"{{第 {T_FIRST_FULL} 分钟（已用去 {before:.0f}\\% 的时间）才第一次生成包含全部器件的版图}};")
    tl.append(f"\\node[anchor=south west,inner sep=1.5pt,font=\\small] at ({T(T_FIRST_FULL) + 0.1:.3f},{TY + 0.5}) "
              f"{{{T_LVS_OK} LVS 通过，{T_SUBMIT} 提交，{T_END} 结束}};")
    lx = TX0
    for kind in ("read", "probe", "think", "full", "sim"):
        text, col = PHASE[kind]
        tl.append(f"\\path[fill={col}] ({lx:.3f},{TY - 0.95}) rectangle ({lx + 0.32:.3f},{TY - 0.68});")
        tl.append(f"\\node[anchor=west,font=\\footnotesize,inner sep=1pt] at ({lx + 0.4:.3f},{TY - 0.815}) {{{text}}};")
        lx += 0.4 + 0.335 * len(text) + 0.55

    # ---------------------------------------------------------------- scoring values
    rho, rho_star, q, q_max = si["raw_area_ratio"], geo["rho_star"], si["shape_ratio"], geo["q_max"]
    w, h = bbox[2] - bbox[0], bbox[3] - bbox[1]
    corner, temp = eval_case[1], eval_case[3]

    tex = TEMPLATE
    rep = {
        "@LAYOUT@": "\n".join(lay), "@NETLIST@": "\n".join(net), "@CODE@": "\n".join(code),
        "@TIMELINE@": "\n".join(tl), "@NSRC@": str(n_src), "@SCORE@": f"{score['score']:.3f}",
        "@CALLS@": probe[2],
        "@W@": f"{w:.1f}", "@H@": f"{h:.1f}", "@RHO@": f"{rho:.3f}", "@RHOSTAR@": f"{rho_star:g}",
        "@RHOMIN@": f"{wts['rho_min']:g}",
        "@Q@": f"{q:.2f}", "@QMAX@": f"{q_max:g}", "@QUAL@": f"{si['quality_rate']:.2f}",
        "@SG@": f"{sc['gds']:.2f}", "@SL@": f"{sc['lvs']:.2f}", "@SD@": f"{sc['drc']:.2f}",
        "@SA@": f"{sc['area']:.3f}", "@SQ@": f"{sc['quality']:.3f}", "@SS@": f"{sc['postsim']:.2f}",
        "@WG@": f"{wts['gds']:.2f}", "@WL@": f"{wts['lvs']:.2f}", "@WD@": f"{wts['drc']:.2f}",
        "@WA@": f"{wts['area']:.2f}", "@WQ@": f"{wts['quality']:.2f}", "@WS@": f"{wts['postsim']:.2f}",
        "@GAMMA@": f"{wts['gamma']:g}",
        "@NEVAL@": str(n_eval_cases), "@NCASE@": str(n_src_cases), "@CORNER@": corner, "@TEMP@": temp,
        "@NR@": pex[1], "@NC@": pex[2], "@SELFP@": self_pass[1],
    }
    for k, v in rep.items():
        assert k in tex, k
        tex = tex.replace(k, v)
    assert not re.findall(r"@[A-Z0-9]+@", tex), re.findall(r"@[A-Z0-9]+@", tex)
    OUT.write_text(tex)
    print(f"wrote {OUT} ({len(tex)} chars); layout rects drawn: "
          f"{sum(len(shapes(k)) for k, _, _ in DRAW)}; PCells: {cells}")


TEMPLATE = r"""% Generated by docs/report/make_process_figure.py -- do not edit by hand.
\documentclass[fontset=none,11pt,linespread=1.0]{ctexart}
\usepackage[paperwidth=33.867cm,paperheight=19.05cm,margin=0cm]{geometry}
\usepackage{tikz,booktabs,array,amssymb}
\usetikzlibrary{arrows.meta,positioning}
\IfFileExists{/mnt/c/Windows/Fonts/msyh.ttc}{%
  \setCJKmainfont{msyh.ttc}[Path=/mnt/c/Windows/Fonts/,BoldFont=msyhbd.ttc,ItalicFont=msyh.ttc]
  \setCJKsansfont{msyh.ttc}[Path=/mnt/c/Windows/Fonts/,BoldFont=msyhbd.ttc,ItalicFont=msyh.ttc]
  \setCJKmonofont{msyh.ttc}[Path=/mnt/c/Windows/Fonts/]
  \setmainfont{msyh.ttc}[Path=/mnt/c/Windows/Fonts/,BoldFont=msyhbd.ttc,ItalicFont=msyh.ttc]
}{}
\IfFileExists{/mnt/c/Windows/Fonts/consola.ttf}{%
  \setmonofont{consola.ttf}[Path=/mnt/c/Windows/Fonts/,BoldFont=consolab.ttf]
}{}
\pagestyle{empty}\parindent=0pt\renewcommand{\arraystretch}{1.18}
\definecolor{cHot}{HTML}{C0392B}
\definecolor{cInk}{HTML}{1F2D3D}
\definecolor{cEx}{HTML}{1F5FA8}
\definecolor{cRead}{HTML}{B8C4D0}
\definecolor{cProbe}{HTML}{E3B878}
\definecolor{cThink}{HTML}{C9A6C8}
\definecolor{cFull}{HTML}{5B9BD5}
\definecolor{cSim}{HTML}{6BB58C}
\begin{document}
\begin{tikzpicture}[x=1cm,y=1cm,
  lyNwell/.style={fill=black!7,draw=black!45,line width=0.3pt,dash pattern=on 1.5pt off 1pt},
  lyNdiff/.style={fill={rgb,255:red,110;green,185;blue,110}},
  lyPdiff/.style={fill={rgb,255:red,222;green,178;blue,105}},
  lyTap/.style={fill={rgb,255:red,95;green,95;blue,70}},
  lyPoly/.style={fill={rgb,255:red,205;green,60;blue,50}},
  lyM1/.style={fill={rgb,255:red,70;green,120;blue,200},fill opacity=0.55},
  lyM2/.style={fill={rgb,255:red,165;green,85;blue,190},fill opacity=0.5},
  lyVia/.style={fill=black},
  box/.style={draw=cInk,line width=0.6pt,rounded corners=2pt,inner sep=4pt,align=center},
  hd/.style={font=\bfseries,text=cInk,anchor=south west,inner sep=0pt},
  arr/.style={-{Stealth[length=6pt,width=5pt]},line width=0.9pt,draw=cInk},
  devlab/.style={font=\scriptsize\ttfamily,inner sep=0pt},
  devw/.style={font=\scriptsize,inner sep=0pt,text=black!70},
  portlab/.style={font=\scriptsize\ttfamily,anchor=west,inner sep=1.5pt},
  tick/.style={font=\scriptsize,anchor=north,inner sep=1pt,text=black!70},
  sbox/.style={draw=cInk,line width=0.6pt,rounded corners=2pt,minimum width=2.75cm,minimum height=1.55cm,
               inner sep=2pt,align=center,font=\small},
  ex/.style={text=cEx,font=\small,align=center,inner sep=1pt},
]
\useasboundingbox (0,0) rectangle (33.867,19.05);

% ------------------------------------------------------------------ title
\node[anchor=west,font=\Large\bfseries,text=cInk,inner sep=0pt] at (0.7,18.45)
  {基准中 agent 如何生成版图、结果如何评分};
\node[anchor=east,font=\small,text=black!65,inner sep=0pt] at (33.2,18.45)
  {实例：八管施密特触发器（SKY130），DeepSeek V4.1 Flash + Claude Code，带辅助流程的一次运行，官方得分 @SCORE@};
\draw[cInk,line width=0.8pt] (0.7,18.05) -- (33.2,18.05);

% ------------------------------------------------------------------ A: how the layout is produced
\node[hd] at (0.7,17.45) {A\quad 画版图的方式：agent 写程序生成版图，再用检查工具的报告修改程序};

\node[hd,font=\small\bfseries] at (0.7,16.55) {① 题目给定};
\node[anchor=north west,inner sep=0pt,font=\footnotesize] at (0.7,16.4) {%
@NETLIST@
};
\node[anchor=north west,inner sep=0pt,font=\footnotesize,align=left,text=black!75] at (0.7,11.9)
  {另有：电气指标、器件库文档、\\[2pt]DRC / LVS 规则文件、3 小时时限};

\node[hd,font=\small\bfseries] at (7.0,16.55) {② agent 编写的生成脚本（摘录，全文 @NSRC@ 行 Python）};
\node[anchor=north west,inner sep=5pt,fill=black!4,draw=black!25,line width=0.4pt,rounded corners=2pt,
      font=\fontsize{8.6}{11.2}\selectfont\ttfamily,align=left] (code) at (7.0,16.4) {%
@CODE@
};
\node[anchor=north west,inner sep=0pt,font=\footnotesize,align=left,text=black!75] at (7.0,10.55)
  {器件：调用器件库的参数化单元；连线：逐个写金属矩形和通孔的坐标；\\[2pt]本例没有用图形界面或自动布线，全部坐标由脚本算出。};

\node[hd,font=\small\bfseries] at (18.55,16.55) {③ 提交的版图\quad{\normalfont\footnotesize @W@ µm × @H@ µm；未画接触孔、局部互连、注入层}};
@LAYOUT@

\node[hd,font=\small\bfseries] at (30.35,16.55) {④ 自检};
\node[box,font=\footnotesize,minimum width=2.9cm] (drc) at (31.85,15.75) {KLayout DRC};
\node[box,font=\footnotesize,minimum width=2.9cm] (lvs) at (31.85,14.65) {KLayout LVS};
\node[box,font=\footnotesize,minimum width=2.9cm] (pex) at (31.85,13.55) {Magic 寄生提取};
\node[box,font=\footnotesize,minimum width=2.9cm] (sim) at (31.85,12.45) {ngspice 后仿真};
\draw[arr] (drc) -- (lvs); \draw[arr] (lvs) -- (pex); \draw[arr] (pex) -- (sim);
\node[anchor=north,inner sep=0pt,font=\footnotesize,align=center,text=black!75] at (31.85,11.75)
  {$\circlearrowleft$ 读报告，改脚本，\\[2pt]重新生成\\[6pt]本次运行共 @CALLS@ 次\\[2pt]工具调用};
\draw[arr] (6.35,14.0) -- (code.west |- 0,14.0);
\draw[arr] (code.east |- 0,14.0) -- ++(0.45,0);
\draw[arr] (29.15,14.0) -- (30.2,14.0);

% ------------------------------------------------------------------ timeline of the run
\node[anchor=east,font=\small\bfseries,text=cInk,inner sep=0pt] at (1.95,7.65) {用时};
@TIMELINE@

\draw[black!30,line width=0.5pt] (0.7,6.15) -- (33.2,6.15);

% ------------------------------------------------------------------ B: scoring
\node[hd] at (0.7,5.7) {B\quad 评分：对提交的版图独立验收，逐级计分};
\node[sbox] (g) at (2.2,3.7) {提交版图\\$+@WG@$};
\node[sbox] (l) at (5.6,3.7) {LVS 通过\\$+@WL@$};
\node[sbox] (d) at (9.4,4.65) {DRC 通过\\$+@WD@$};
\node[sbox,dashed,minimum width=3.3cm,minimum height=1.75cm] (e) at (9.65,2.8)
  {计分资格\\[2pt]\footnotesize 长宽比 $q \le q_{\max}$\\[1pt]\footnotesize 利用率 $\rho \ge @RHOMIN@$，非退化形态};
\node[sbox] (a) at (13.75,2.8) {面积\\$@WA@\min(1,\rho/\rho^{*})^{@GAMMA@}$};
\node[sbox] (qq) at (16.95,2.8) {工程质量\\$@WQ@\,Q$};
\node[sbox] (s) at (20.15,2.8) {后仿真\\$@WS@\,F$};
\draw[arr] (g) -- (l);
\draw[arr] (l.east) -- ++(0.35,0) |- (d.west);
\draw[arr] (l.east) -- ++(0.35,0) |- (e.west);
\draw[arr] (e) -- (a);
\node[ex] at (2.2,2.65) {@SG@};
\node[ex] at (5.6,2.65) {@SL@};
\node[ex,anchor=west] at (10.95,4.65) {@SD@};
\node[ex] at (9.65,1.6) {$q$=@Q@（$q_{\max}$=@QMAX@）};
\node[ex] at (13.75,1.6) {@SA@\\[1pt]\footnotesize $\rho$=@RHO@，$\rho^{*}$=@RHOSTAR@};
\node[ex] at (16.95,1.6) {@SQ@\\[1pt]\footnotesize $Q$=@QUAL@};
\node[ex] at (20.15,1.6) {@SS@\\[1pt]\footnotesize $F$=@NEVAL@/@NEVAL@};
\node[anchor=west,font=\small,text=cEx,inner sep=0pt] at (0.7,0.7) {蓝色：本例取值，合计 @SCORE@};
\node[anchor=west,font=\footnotesize,inner sep=0pt,align=left,text=black!80] at (12.3,4.7)
  {$\rho$ = 器件有效面积 / 版图外框面积；$Q$：匹配、走线、供电的几何指标；\\[3pt]
   $F$：Magic 提取寄生（本例 @NR@ 个 R、@NC@ 个 C）后，通过的工况比例；\\[3pt]
   全部通过：LVS、DRC、计分资格和全部工况都通过。};
\node[anchor=west,font=\footnotesize,inner sep=0pt,align=left,text=cHot] at (6.3,0.7)
  {官方后仿真只测 @NEVAL@ 个工况（@CORNER@，@TEMP@ °C）；题面要求 @NCASE@ 个工艺角与温度组合，agent 自测通过其中 @SELFP@ 个。};

% ------------------------------------------------------------------ C: auxiliary flow and what is optimised
\draw[black!30,line width=0.5pt] (22.35,0.4) -- (22.35,6.0);
\node[hd] at (22.75,5.7) {C\quad 辅助流程，以及自动优化改的是哪里};
\path[draw=cInk,line width=0.7pt,dashed,rounded corners=3pt,fill=cFull!8] (22.75,2.8) rectangle (33.2,5.45);
\node[anchor=north west,inner sep=0pt,font=\small\bfseries,text=cInk] at (22.95,5.27) {辅助流程（初版人工编写）};
\node[box,fill=white,font=\small,minimum width=2.9cm,minimum height=1.6cm] (ag) at (24.55,3.8)
  {agent\\[2pt]\footnotesize 模型 + Claude Code\\[2pt]\footnotesize 始终不改};
\node[anchor=west,inner sep=0pt,font=\footnotesize,align=left] at (26.35,3.84)
  {·\,草稿可随时送检，只保留通过检查更多的版本\\[3pt]
   ·\,第 140 分钟提醒剩余时间\\[3pt]
   ·\,第 165 分钟停止；仍有检查未过时，\\[1pt]
   \phantom{·\,}另起一个会话修补，最多 15 分钟};
\node[box,font=\small,minimum width=10.45cm,minimum height=1.1cm,anchor=south west] (opt) at (22.75,0.45)
  {自动优化：另一个 agent 读开发集的运行结果，提出修改；\\[2pt]
   \footnotesize 修改后在开发集上与原版对比，更好才采纳};
\draw[arr] (27.0,1.57) -- (27.0,2.8);
\node[anchor=west,inner sep=0pt,font=\footnotesize,text=black!75,align=left] at (27.25,2.18)
  {只改辅助流程：检查、提示、时间安排\\[2pt]本次采纳的两处修改都在“修补”的提示上};
\end{tikzpicture}
\end{document}
"""


if __name__ == "__main__":
    main()
