#!/usr/bin/env python3
"""Generate ``result_tables.tex`` (three LaTeX tables for the ALW experiment report).

Every number is read from the files below; nothing is typed in. Standard library only.

    python3 docs/report/make_result_tables.py

Inputs (paths relative to the repository root):
    docs/server-info/test_export/test_runs.jsonl      per-run results on the 14 test tasks
    docs/server-info/round1.txt                       round 1 report (terminal paste with JSON)
    docs/server-info/round_report_redacted.json       round 2 report
    docs/server-info/phase1c_sync.json                development-set baseline
    configs/alw/catalog.json                          task titles and device counts
    ../method-v5/experiments/alw/paper_reference.json paper Table 1 (parsed from arXiv HTML)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SI = ROOT / "docs" / "server-info"
PAPER = ROOT.parent / "method-v5" / "experiments" / "alw" / "paper_reference.json"
PAPER_TXT = ROOT.parents[2] / "external" / "AnalogLayoutWorld" / "paper" / "paper.txt"
OUT = Path(__file__).resolve().parent / "result_tables.tex"

ARMS = ["H_raw", "H_base", "H_star"]
ARM_LABEL = {
    "H_raw": "原始 agent",
    "H_base": "\\quad + 人工编写的辅助流程",
    "H_star": "\\quad\\quad + 两轮自动优化",
}
# Plain-language descriptions of the two accepted changes (text, not data).
ROUND_TEXT = {
    1: "修补环节的提示中加入各项检查的失败原因",
    2: "再次改写修补环节的提示（针对 LVS）",
}


def tex(s: str) -> str:
    for a, b in (("\\", "\\textbackslash{}"), ("&", "\\&"), ("%", "\\%"), ("_", "\\_"), ("#", "\\#")):
        s = s.replace(a, b)
    return s


def load_runs() -> dict[str, dict[str, dict]]:
    runs: dict[str, dict[str, dict]] = {a: {} for a in ARMS}
    for line in (SI / "test_export" / "test_runs.jsonl").read_text().splitlines():
        r = json.loads(line)
        if r["infra_error"]:
            continue
        assert r["task_id"] not in runs[r["arm"]], "duplicate scored run"
        runs[r["arm"]][r["task_id"]] = r
    assert all(len(v) == 14 for v in runs.values())
    return runs


def passed(r: dict, key: str) -> bool:
    return r["d_star"].get(key) == 1


def count(rs: dict[str, dict], key: str) -> int:
    return sum(passed(r, key) for r in rs.values())


def mean(rs: dict[str, dict]) -> float:
    return sum(r["Y_star"] for r in rs.values()) / len(rs)


def frac(k: int, n: int) -> str:
    return f"{k}/{n}"


def pct(k: int, n: int) -> str:
    return f"{100 * k / n:.1f}\\%"


def round_report(n: int) -> dict:
    if n == 1:
        text = (SI / "round1.txt").read_text()
        text = text[text.index("{"):].rstrip()
        # The paste lost its final closing brace; everything before it is intact.
        return json.loads(text if text.endswith("}") else text + "\n}")
    return json.loads((SI / "round_report_redacted.json").read_text())


def raw_tool_calls() -> list[int]:
    """Tool calls per scored run of the unassisted agent, from the read-only trajectory probe.

    The probe also lists the one run that was discarded for an infrastructure fault and re-run;
    of the two entries for that task the earlier one is dropped.
    """
    rows = [json.loads(line) for line in (SI / "test_export" / "test_runs.jsonl").read_text().splitlines()]
    redone = [r["task_id"] for r in rows if r["arm"] == "H_raw" and r["infra_error"]]
    assert len(redone) == 1
    frag = redone[0].removeprefix("sky130-")[:20]
    found = []
    for line in (SI / "test_export" / "hraw_probe.txt").read_text().splitlines():
        m = re.match(r"RUN H_raw (\S+) steps=\d+ calls=(\d+) .* t0=(\S+) ", line)
        if m:
            found.append((m[3], m[1], int(m[2])))
    dup = sorted(f for f in found if frag in f[1])
    assert len(dup) == 2
    return [c for f in found if f != dup[0] for c in [f[2]]]


def table1(runs, paper) -> str:
    rows = paper["rows"]
    same = next(r for r in rows if r["system"] == "DeepSeek V4.1 Flash" and r["harness"] == "Claude Code")
    best = max((r for r in rows if r["harness"]), key=lambda r: r["score"])
    special = max((r for r in rows if not r["harness"]), key=lambda r: r["score"])
    raw = runs["H_raw"]
    n = len(raw)
    # Tokens as the benchmark records them: prompt tokens including cache reads, plus output tokens.
    tok = sum(r["cost"]["input_tokens"] + r["cost"]["cached_tokens"] + r["cost"]["output_tokens"]
              for r in raw.values()) / n / 1e6
    calls = raw_tool_calls()
    assert len(calls) == n
    gds_rate = re.search(r"([\d.]+)% of attempts produce GDS", PAPER_TXT.read_text()).group(1)

    ks = [sum(r["C_star"] for r in raw.values())] + [count(raw, key) for key in ("gds", "drc", "lvs", "postsim")]

    def prow(label: str, r: dict) -> str:
        t = f"{r['tokens_m']:.1f}" if r["tokens_m"] is not None else "--"
        c = f"{r['tool_calls']:.0f}" if r["tool_calls"] is not None else "--"
        return (f"{label} & {r['score']:.4f} & {r['acc']:.1f}\\% & -- & {r['drc']:.1f}\\% & "
                f"{r['lvs']:.1f}\\% & {r['sim']:.1f}\\% & {c} & {t} \\\\")

    n_blank = sum(r["status"] == "missing_submission" for r in raw.values())
    lines = [
        "\\begin{table}[t]",
        "\\centering\\small",
        "\\caption{模拟版图设计任务上现成 agent 的表现：论文报告值与本实验复现值。"
        "“完全通过”指全部检查项通过；“有效版图”指提交了可读取的版图文件；各项为通过的运行占全部运行的比例。}",
        "\\label{tab:task}",
        "\\vspace{3pt}",
        "\\footnotesize\\setlength{\\tabcolsep}{3.5pt}",
        "\\begin{tabular}{@{}lcccccccc@{}}",
        "\\toprule",
        "模型 + agent 工具 & 总分 & 完全通过 & 有效版图 & DRC & LVS & 后仿真 & 工具调用 & Token (M) \\\\",
        "\\midrule",
        "\\multicolumn{9}{@{}l}{\\textit{论文报告（40 题 $\\times$ 3 次）}\\textsuperscript{a}} \\\\",
        prow(f"{tex(same['system'])} + {tex(same['harness'])}", same),
        prow(f"{tex(best['system'])} + {tex(best['harness'])}\\textsuperscript{{d}}", best),
        prow(f"{tex(special['system'])}\\textsuperscript{{d}}", special),
        "\\midrule",
        f"\\multicolumn{{9}}{{@{{}}l}}{{\\textit{{本实验（{n} 题 $\\times$ 1 次）}}}} \\\\",
        (f"DeepSeek V4.1 Flash + Claude Code\\textsuperscript{{b}} & {mean(raw):.4f} & "
         + " & ".join(pct(k, n) for k in ks)
         + f" & {sum(calls) / n:.0f} & {tok:.1f}\\textsuperscript{{c}} \\\\"),
        ("\\quad（通过次数/运行次数） & & "
         + " & ".join(f"({k}/{n})" for k in ks) + " & & \\\\"),
        "\\bottomrule",
        "\\end{tabular}",
        "",
        "\\vspace{2pt}",
        "\\begin{minipage}{\\linewidth}\\footnotesize",
        "\\textsuperscript{a} 取自论文表 1，为 40 道题各运行 3 次的平均；论文没有按配置报告版图提交率（只给出全部 13 种配置合计为 "
        f"{gds_rate}\\%），也未公布逐题结果。"
        "本实验使用其中 14 道题，各运行 1 次，题目范围与次数均与论文不同。\\\\",
        f"\\textsuperscript{{b}} {n} 次运行中有 {n_blank} 次到结束时没有提交版图（记 0 分），"
        "其中 4 次是因为单次回复超过客户端的输出长度上限、会话被中止。"
        f"对这 {n_blank} 次的运行记录逐步分析的结果是：全程只做过器件级的试验，都没有生成过包含全部器件的版图。"
        "论文为自行部署模型，本实验通过 API 网关调用；与论文同配置结果的差距，原因尚未查清。\\\\",
        "\\textsuperscript{c} 每次运行的输入（含缓存命中）与输出 token 之和，单位百万。论文表注为“记录的输入与输出 token 累计”，"
        "未说明是否含缓存命中；不含缓存命中时本实验为 "
        f"{sum(r['cost']['input_tokens'] + r['cost']['output_tokens'] for r in raw.values()) / n / 1e6:.1f}。\\\\",
        "\\textsuperscript{d} 分别为论文中得分最高的通用 agent 和版图专用系统，列出以示该任务可达到的水平。"
        "\\\\ 说明：基准当前版本的后仿真只执行公开测试台中写明的工况"
        "（施密特触发器一题为题面 9 个工艺角与温度组合中的 1 个），“后仿真”和“完全通过”均按此口径。",
        "\\end{minipage}",
        "\\end{table}",
    ]
    return "\n".join(lines)


def table2(runs) -> str:
    n = 14
    lines = [
        "\\begin{table}[t]",
        "\\centering\\small",
        "\\caption{对 agent 的两级改进在 14 道留出测试题上的效果（上），以及自动优化两轮的过程（下）。"
        "模型与 agent 工具始终不变，改动的只是其外部的辅助流程。}",
        "\\label{tab:improve}",
        "\\vspace{3pt}",
        "\\setlength{\\tabcolsep}{5pt}",
        "\\begin{tabular}{@{}lcccccccc@{}}",
        "\\toprule",
        "\\multicolumn{9}{@{}l}{\\textbf{(a) 留出测试集}（14 题，每种条件各运行 1 次）} \\\\",
        "\\midrule",
        "条件 & 总分 & 较上一行 & 升/平/降 & 完全通过 & 有效版图 & DRC & LVS & 后仿真 \\\\",
        "\\midrule",
    ]
    prev = None
    for arm in ARMS:
        rs = runs[arm]
        if prev is None:
            delta, wtl = "--", "--"
        else:
            d = [rs[t]["Y_star"] - runs[prev][t]["Y_star"] for t in rs]
            delta = f"{mean(rs) - mean(runs[prev]):+.4f}"
            wtl = f"{sum(x > 1e-9 for x in d)}/{sum(abs(x) <= 1e-9 for x in d)}/{sum(x < -1e-9 for x in d)}"
        sup = {"H_base": "\\textsuperscript{a}", "H_star": "\\textsuperscript{b}"}.get(arm, "")
        lines.append(
            f"{ARM_LABEL[arm]}{sup} & {mean(rs):.4f} & {delta} & {wtl} & "
            f"{frac(sum(r['C_star'] for r in rs.values()), n)} & {frac(count(rs, 'gds'), n)} & "
            f"{frac(count(rs, 'drc'), n)} & {frac(count(rs, 'lvs'), n)} & {frac(count(rs, 'postsim'), n)} \\\\")
        prev = arm
    lines += ["\\bottomrule", "\\end{tabular}", "", "\\vspace{6pt}",
              "\\begin{tabular}{@{}clcccc@{}}", "\\toprule",
              "\\multicolumn{6}{@{}l}{\\textbf{(b) 自动优化过程}（开发集 12 题，每题 2 次，共 24 次运行）} \\\\",
              "\\midrule",
              "轮次 & 修改内容 & 尝试次数\\textsuperscript{c} & 平均分变化 & 是否采纳 & 模型调用 \\\\",
              "\\midrule"]
    for k in (1, 2):
        rep = round_report(k)
        ev = rep["evaluation"]
        tries = max(a["attempt"] for a in rep["attempts"])
        assert rep["attempts"][-1]["stage"] == "passed" and ev["n_trials"] == 24
        lines.append(f"{k} & {ROUND_TEXT[k]} & {tries} & {ev['sum_delta_Y'] / ev['n_trials']:+.4f} & "
                     f"{'是' if ev['promoted'] else '否'} & {ev['llm_calls']} \\\\")
    dev = json.loads((SI / "phase1c_sync.json").read_text())["harvest"]
    base, raw = runs["H_base"], runs["H_raw"]
    gain = {t: base[t]["Y_star"] - raw[t]["Y_star"] for t in base}
    top = max(gain, key=gain.get)
    share = gain[top] / sum(gain.values())
    n_rerun = sum(1 for r in runs["H_star"].values() if r.get("gate_triggered"))
    lines += [
        "\\bottomrule", "\\end{tabular}", "", "\\vspace{2pt}",
        "\\begin{minipage}{\\linewidth}\\footnotesize",
        "\\textsuperscript{a} 人工编写的辅助流程包括：规定在草稿目录中工作并可随时送检、交卷；第 140 分钟给出时间提醒；"
        "第 165 分钟停止主会话，由一个新会话做最多 15 分钟的修补。"
        f"该行增益的 {100 * share:.0f}\\% 来自一道题（唯一完全通过的那道）。\\\\",
        "\\textsuperscript{b} 两轮被采纳的修改都只涉及最后的修补环节，因此该行是在上一行各次运行第 165 分钟的存档上"
        "重新执行修补环节得到的，此前的过程与上一行相同。\\\\",
        "\\textsuperscript{c} 自动提出修改方案的 agent 得到一个可评测的方案所用的尝试次数。\\\\",
        f"说明：开发集上“人工辅助流程”的平均分为 {dev['mean_Y_star']:.4f}（{dev['n_trials']} 次运行）。"
        "各条件在测试集上只运行了 1 次，没有重复实验，无法给出随机波动的范围。",
        "\\end{minipage}",
        "\\end{table}",
    ]
    return "\n".join(lines), n_rerun


def table3(runs) -> str:
    cat = {t["task_id"]: t for t in json.loads((ROOT / "configs" / "alw" / "catalog.json").read_text())["tasks"]}

    def cell(r: dict) -> str:
        if not passed(r, "gds"):
            marks = "--"
        else:
            marks = "G" + "".join(m for m, key in (("D", "drc"), ("L", "lvs"), ("S", "postsim")) if passed(r, key))
        return f"{r['Y_star']:.2f} & {marks}"

    lines = [
        "\\begin{table}[t]",
        "\\centering\\small",
        "\\caption{14 道测试题在三种条件下的逐题结果。G：提交了有效版图；D：DRC 通过；L：LVS 通过；"
        "S：后仿真通过；“--”：没有提交版图。}",
        "\\label{tab:pertask}",
        "\\vspace{3pt}",
        "\\begin{tabular}{@{}lccccccc@{}}",
        "\\toprule",
        " & & \\multicolumn{2}{c}{原始 agent} & \\multicolumn{2}{c}{+ 人工辅助流程} & "
        "\\multicolumn{2}{c}{+ 两轮自动优化} \\\\",
        "\\cmidrule(lr){3-4}\\cmidrule(lr){5-6}\\cmidrule(l){7-8}",
        "电路 & 器件数 & 得分 & 通过项 & 得分 & 通过项 & 得分 & 通过项 \\\\",
        "\\midrule",
    ]
    for t in sorted(runs["H_raw"], key=lambda t: cat[t]["devices"]):
        title = cat[t]["title"]
        title = title.strip()
        title = title[:-2].strip() if title.endswith("版图") else title
        lines.append(f"{tex(title)} & {cat[t]['devices']} & " + " & ".join(cell(runs[a][t]) for a in ARMS) + " \\\\")
    lines += ["\\midrule",
              "平均 & & " + " & ".join(f"{mean(runs[a]):.4f} & " for a in ARMS) + " \\\\",
              "\\bottomrule", "\\end{tabular}", "\\end{table}"]
    return "\n".join(lines)


def main() -> None:
    runs = load_runs()
    paper = json.loads(PAPER.read_text())
    t2, n_rerun = table2(runs)
    body = "\n\n".join([
        "% Generated by docs/report/make_result_tables.py -- do not edit by hand.\n"
        "% Needs: booktabs (and a CJK-capable setup such as ctex).",
        table1(runs, paper), t2, table3(runs)]) + "\n"
    OUT.write_text(body)
    print(f"wrote {OUT} ({len(body)} chars); H_star runs with repair re-executed: {n_rerun}")


if __name__ == "__main__":
    main()
