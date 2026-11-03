"""Publication figure of a method-v3 run's sealed curve: round x sealed score.

Draws ``curve_view.read_curve`` with the encoding of the workbench chart
(method-v3 §6): main path solid, certified points filled, provisional hollow,
rolled-back points dashed and joined to their parent by a dashed segment, a
vertical whisker per point for the multiplicity-corrected interval, the seed
score as a faint reference line. Sized for a single paper column.

    python -m rag_eval.optimizer.cli curve --state-dir <run dir> --out fig.svg

matplotlib is imported here only, so the loop and the workbench never need it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from typing import Optional

# Print-safe versions of the workbench tokens (apps/sn-workbench/src/ui.tsx, light theme).
INK, MUTED, GRID, SEED_LINE = "#1b1b1f", "#6b6f76", "#e6e6e8", "#b9bbbf"
CERTIFIED, PROVISIONAL, ROLLED = "#1f8a3b", "#4b57c2", "#8a8f98"
SEED_FILL = "#6b6f76"

# Chinese first (the paper), then English; a missing CJK font falls back to English.
_CJK_NAMES = ("Noto Sans CJK SC", "Noto Sans SC", "Source Han Sans SC", "Microsoft YaHei", "DengXian", "SimHei",
              "WenQuanYi Micro Hei", "PingFang SC")
_CJK_FILES = ("/mnt/c/Windows/Fonts/msyh.ttc", "/mnt/c/Windows/Fonts/Deng.ttf", "/mnt/c/Windows/Fonts/simhei.ttf",
              "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc", "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
              "/System/Library/Fonts/PingFang.ttc")

LABELS = {
    "zh": dict(x="轮（0 = 种子）", y="封存集 {metric}", nk="n = {n}，k = {k}", certified="已确认", provisional="暂定",
               rolled_back="已回滚", interval="多重校正区间{m}", m="（m = {m}）", seed="种子"),
    "en": dict(x="Round (0 = seed)", y="Sealed {metric}", nk="n = {n}, k = {k}", certified="Certified",
               provisional="Provisional", rolled_back="Rolled back", interval="Corrected interval{m}", m=" (m = {m})",
               seed="Seed"),
}


def _cjk_font(explicit: Optional[str]):
    from matplotlib import font_manager
    if explicit:
        font_manager.fontManager.addfont(explicit)
        return font_manager.FontProperties(fname=explicit).get_name()
    installed = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_NAMES:
        if name in installed:
            return name
    for path in _CJK_FILES:
        if Path(path).is_file():
            font_manager.fontManager.addfont(path)
            return font_manager.FontProperties(fname=path).get_name()
    return None


def _nice_step(span: float, target: int = 4) -> float:
    import math
    raw = span / max(1, target)
    mag = 10 ** math.floor(math.log10(raw)) if raw > 0 else 1
    return next(s * mag for s in (1, 2, 2.5, 5, 10) if s * mag >= raw)


def x_positions(curve: dict, spread: float = 0.24) -> dict[int, float]:
    """index -> x: the round, offset left to right among the nodes of one round."""
    return {p["index"]: p["round"] + (p["slot"] - (p["slots"] - 1) / 2) * spread for p in curve["points"]}


def render(curve: dict, out: str | Path, *, lang: str = "zh", font: Optional[str] = None,
           width: float = 3.4, height: float = 2.5) -> str:
    """Write the figure to *out* (format from the suffix: .svg, .pdf, .png); returns the language used."""
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.lines import Line2D
    from matplotlib.patches import Circle
    from matplotlib.legend_handler import HandlerPatch
    from matplotlib.ticker import MultipleLocator
    from matplotlib.transforms import ScaledTranslation

    family = None
    if lang == "zh":
        family = _cjk_font(font)
        if family is None:
            print("curve: no CJK font found (pass --font); writing English labels", file=sys.stderr)
            lang = "en"
    text = LABELS[lang]
    rc = {"font.size": 7.5, "axes.labelsize": 7.5, "xtick.labelsize": 7, "ytick.labelsize": 7, "legend.fontsize": 6.8,
          "svg.fonttype": "path", "axes.unicode_minus": False}
    if family:
        rc["font.family"] = [family, "DejaVu Sans"]
    points = curve["points"]
    xs = x_positions(curve)
    radius = 3.3 / 72  # marker radius in inches (6.6 pt across)

    with plt.rc_context(rc):
        fig, ax = plt.subplots(figsize=(width, height), dpi=200)

        def marker(x, y, state, seed=False):
            offset = fig.dpi_scale_trans + ScaledTranslation(x, y, ax.transData)
            if seed:
                kw = dict(facecolor=SEED_FILL, edgecolor=SEED_FILL, linewidth=1.0)
            elif state == "rolled_back":
                kw = dict(facecolor="white", edgecolor=ROLLED, linewidth=1.0, linestyle=(0, (1.6, 1.1)))
            elif state == "provisional":
                kw = dict(facecolor="white", edgecolor=PROVISIONAL, linewidth=1.1)
            else:
                kw = dict(facecolor=CERTIFIED, edgecolor=CERTIFIED, linewidth=1.0)
            ax.add_patch(Circle((0, 0), radius, transform=offset, zorder=5, clip_on=False, **kw))

        tone = {"certified": CERTIFIED, "provisional": PROVISIONAL, "rolled_back": ROLLED}
        values = [v for p in points for v in (p["score"], p["y_lower"], p["y_upper"]) if v is not None]
        seed = curve.get("seed_score")
        if seed is not None:
            ax.axhline(seed, color=SEED_LINE, linewidth=0.8, zorder=1)
        for p in points:
            if p["score"] is None or p["index"] == 0 or p["y_lower"] is None:
                continue
            ax.plot([xs[p["index"]]] * 2, [p["y_lower"], p["y_upper"]], color=tone.get(p["state"], MUTED),
                    linewidth=1.0, alpha=0.55, solid_capstyle="butt", zorder=2)
            for y in (p["y_lower"], p["y_upper"]):
                ax.plot([xs[p["index"]] - 0.05, xs[p["index"]] + 0.05], [y, y], color=tone.get(p["state"], MUTED),
                        linewidth=0.8, alpha=0.55, zorder=2)
        by_index = {p["index"]: p for p in points}
        path = [by_index[i] for i in curve["path"] if by_index[i]["score"] is not None]
        ax.plot([xs[p["index"]] for p in path], [p["score"] for p in path], color=INK, linewidth=1.2,
                solid_joinstyle="round", solid_capstyle="round", zorder=3)
        for p in points:
            parent = by_index.get(p["parent_index"]) if p["parent_index"] is not None else None
            if p["on_path"] or parent is None or p["score"] is None or parent["score"] is None:
                continue
            ax.plot([xs[parent["index"]], xs[p["index"]]], [parent["score"], p["score"]], color=ROLLED,
                    linewidth=1.0, linestyle=(0, (3, 2)) if p["state"] == "rolled_back" else "-", zorder=3)
        for p in points:
            if p["score"] is not None:
                marker(xs[p["index"]], p["score"], p["state"], seed=p["index"] == 0)

        lo, hi = (min(values), max(values)) if values else (0.0, 1.0)
        pad = max((hi - lo) * 0.12, 0.005)
        ax.set_ylim(lo - pad, hi + pad)
        ax.yaxis.set_major_locator(MultipleLocator(_nice_step(hi - lo + 2 * pad)))
        rounds = curve["max_round"]
        # Room left of the seed point for its label, ~26 pt whatever the number of rounds.
        plot_pt = (width - 0.6) * 72
        ax.set_xlim(-max(0.5, 26 * (rounds + 0.5) / (plot_pt - 26)), rounds + 0.5)
        ax.xaxis.set_major_locator(MultipleLocator(max(1, round(rounds / 8 + 0.49))))
        ax.grid(axis="y", color=GRID, linewidth=0.6, zorder=0)
        ax.set_axisbelow(True)
        for side in ("top", "right"):
            ax.spines[side].set_visible(False)
        for side in ("left", "bottom"):
            ax.spines[side].set_color("#9a9da3")
            ax.spines[side].set_linewidth(0.6)
        ax.tick_params(colors=MUTED, length=2.5, width=0.6, labelcolor=INK)
        ax.set_xlabel(text["x"], color=INK, labelpad=2)
        metric = curve.get("metric") or "score"
        ax.set_ylabel(text["y"].format(metric=metric), color=INK, labelpad=3)
        if seed is not None:
            # Left of the seed point: round 0 holds the seed alone, so nothing else is drawn there.
            ax.annotate(text["seed"], xy=(0, seed), xycoords=("axes fraction", "data"), xytext=(2, -2),
                        textcoords="offset points", ha="left", va="top", fontsize=6.5, color=MUTED)

        class _Dot(HandlerPatch):
            def __init__(self, **kw):
                self.kw = kw
                super().__init__()

            def create_artists(self, legend, orig, xdescent, ydescent, w, h, fontsize, trans):
                c = Circle((w / 2 - xdescent, h / 2 - ydescent), radius * 72, transform=trans, **self.kw)
                return [c]

        proxies = {k: Line2D([], []) for k in ("certified", "provisional", "rolled_back")}
        handler = {proxies["certified"]: _Dot(facecolor=CERTIFIED, edgecolor=CERTIFIED, linewidth=1.0),
                   proxies["provisional"]: _Dot(facecolor="white", edgecolor=PROVISIONAL, linewidth=1.1),
                   proxies["rolled_back"]: _Dot(facecolor="white", edgecolor=ROLLED, linewidth=1.0,
                                                linestyle=(0, (1.6, 1.1)))}
        m = curve.get("m")
        handles = [proxies["certified"], proxies["provisional"], proxies["rolled_back"],
                   Line2D([], [], color=MUTED, linewidth=1.0)]
        labels = [text["certified"], text["provisional"], text["rolled_back"],
                  text["interval"].format(m=text["m"].format(m=m) if m else "")]

        class _Whisker:
            def legend_artist(self, legend, orig, fontsize, box):
                x = box.xdescent + box.width / 2
                art = [Line2D([x, x], [-box.ydescent - 1, box.height - box.ydescent + 1], color=MUTED, linewidth=1.0)]
                for y in (-box.ydescent - 1, box.height - box.ydescent + 1):
                    art.append(Line2D([x - 2, x + 2], [y, y], color=MUTED, linewidth=0.8))
                for a in art:
                    a.set_transform(box.get_transform())
                    box.add_artist(a)
                return art[0]

        handler[handles[3]] = _Whisker()
        legend = fig.legend(handles, labels, handler_map=handler, loc="lower center", ncol=4, frameon=False,
                            handlelength=1.2, handletextpad=0.4, columnspacing=1.0, borderaxespad=0.2)
        for t in legend.get_texts():
            t.set_color(INK)
        nk = text["nk"].format(n=curve.get("n") if curve.get("n") is not None else "—", k=curve.get("k") or "—")
        ax.text(0.0, 1.02, nk, transform=ax.transAxes, ha="left", va="bottom", fontsize=6.5, color=MUTED)
        fig.tight_layout(rect=(0, 0.07, 1, 1), pad=0.3)
        out = Path(out)
        out.parent.mkdir(parents=True, exist_ok=True)
        fig.savefig(out, bbox_inches="tight", pad_inches=0.02, metadata={"Date": None} if out.suffix == ".svg" else None)
        plt.close(fig)
    return lang
