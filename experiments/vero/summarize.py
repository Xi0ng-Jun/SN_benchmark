"""Result tables of the VeRO experiment, as markdown on stdout.

    python experiments/vero/summarize.py [--state $VERO_STATE]

Sources: ``<state>/<task>/runs/<run>/{endpoint,state,identity}.json`` (method-v4.1) and
``<state>/gepa/<task>/<run>/{summary.json,gepa.log}`` (GEPA). Only finished runs enter the tables;
the others are listed below them. Every number is read from those files: nothing is estimated.

GEPA's sample executions are the samples of every evaluation in its log except the first and the last
(the initial and final test evaluations), i.e. its executions on train + validation, as for v4.1.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import statistics
from pathlib import Path

TASKS = (("SimpleQA", "simpleqa", "simpleqa"), ("Retail", "retail", "tau-bench"))   # label, v4.1 root, GEPA task
MEASURED = ("measured", "minibatch_fail", "smoke_fail")


def read(path: Path):
    return json.loads(path.read_text()) if path.exists() else None


def mean_std(values: list) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "–"
    if len(values) == 1:
        return f"{values[0]:.3f} (n=1)"
    return f"{statistics.mean(values):.3f} ± {statistics.stdev(values):.3f} (n={len(values)})"


def signed(values: list) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "–"
    if len(values) == 1:
        return f"{values[0]:+.3f}"
    return f"{statistics.mean(values):+.3f} ± {statistics.stdev(values):.3f}"


def v41_runs(root: Path) -> tuple[list[dict], list[str]]:
    done, open_ = [], []
    for run in sorted((root / "runs").glob("*")) if (root / "runs").exists() else []:
        st, ep = read(run / "state.json") or {}, read(run / "endpoint.json")
        if ep is None:
            open_.append(f"method-v4.1 {root.name} {run.name}: halt reason {st.get('halt_reason') or 'none (running or crashed)'}"
                         + (f"; {st['failure_detail'][:160]}" if st.get("failure_detail") else ""))
            continue
        s = ep["summary"]
        records = [r for rd in st.get("rounds", []) + ([st["current_round"]] if st.get("current_round") else [])
                   for r in (rd.get("records") or {}).values()]
        used = s.get("optimisation_executions") or {}
        done.append(dict(run=run.name, seed=s["seed_score"], final=s["final_score"], delta=s["delta"], se=s["se"],
                         rounds=s["rounds"], adopted=s["adopted"], halt=s["halt_reason"], hours=s["elapsed_h"],
                         measured=sum(r["stage"] in MEASURED for r in records), cards=len(records),
                         val=[n.get("delta_val") for n in st.get("lineage", [])[1:]],
                         used=sum(used.values()), budget=(read(run / "identity.json") or {}).get("max_executions")))
    return done, open_


def gepa_runs(root: Path) -> tuple[list[dict], list[str]]:
    done, open_ = [], []
    for run in sorted(root.glob("*")) if root.exists() else []:
        if not run.is_dir() or ".failed-" in run.name:
            continue
        summary = read(run / "summary.json")
        if summary is None:
            open_.append(f"GEPA {root.name} {run.name}: not finished")
            continue
        log = (run / "gepa.log").read_text(errors="replace") if (run / "gepa.log").exists() else ""
        initial = re.search(r"Initial test score: ([0-9.]+)", log)
        sizes = [int(m.group(1) or 0) for m in
                 re.finditer(r"Task completed with metrics: (?:\{'num_samples': (\d+)|None)", log)]
        seed = float(initial.group(1)) if initial else None
        final = summary["final_test_score"]
        done.append(dict(run=run.name, seed=seed, final=final, delta=None if seed is None else final - seed,
                         used=sum(sizes[1:-1]) if len(sizes) >= 2 else None, hours=summary.get("wall_h"),
                         budget=None, models=summary.get("models")))
    return done, open_


def used_cell(runs: list[dict], budget) -> str:
    used = [r["used"] for r in runs if r.get("used") is not None]
    if not used:
        return "–"
    return f"{statistics.mean(used):.0f}" + (f" / {budget}" if budget else "")


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--state", default=os.environ.get("VERO_STATE") or str(Path(__file__).resolve().parents[2] / ".vero-state"))
    state = Path(p.parse_args().state)
    data, unfinished, models = {}, [], {}
    for label, v41_name, gepa_name in TASKS:
        ours, o1 = v41_runs(state / v41_name)
        gepa, o2 = gepa_runs(state / "gepa" / gepa_name)
        setup = read(state / v41_name / "setup.json") or {}
        sizes = setup.get("splits") or {}
        budget = 8 * (sizes["train"] + sizes["validation"]) if sizes else None
        data[label] = dict(ours=ours, gepa=gepa, budget=budget, sizes=sizes)
        models[label] = setup.get("models")
        unfinished += o1 + o2

    print("## 表 1  测试集正确率（各次运行的均值 ± 标准差；Δ 为同一次运行内 最终 − 种子）\n")
    head = "| 方法 |" + "".join(f" {t} | Δ 对种子 | 执行次数 / 预算 |" for t in data)
    print(head + "\n|" + "---|" * (1 + 3 * len(data)))
    rows = (("种子（未优化）", lambda d: (mean_std([r["seed"] for r in d["ours"] + d["gepa"]]), "–", "–")),
            ("GEPA（只改提示词）", lambda d: (mean_std([r["final"] for r in d["gepa"]]),
                                        signed([r["delta"] for r in d["gepa"]]), used_cell(d["gepa"], d["budget"]))),
            ("method-v4.1", lambda d: (mean_std([r["final"] for r in d["ours"]]),
                                       signed([r["delta"] for r in d["ours"]]), used_cell(d["ours"], d["budget"]))))
    for name, cells in rows:
        print(f"| {name} |" + "".join(" {} | {} | {} |".format(*cells(d)) for d in data.values()))

    print("\n## 表 2  method-v4.1 每次运行\n")
    print("| 任务 | 运行 | 轮数 | 卡片 | 测量的候选 | 采纳 | 各次采纳的验证集 Δ | 测试集 种子 → 最终 | 测试集 Δ ± SE | 执行次数 | 用时 h | 停机原因 |")
    print("|" + "---|" * 12)
    for label, d in data.items():
        for r in d["ours"]:
            val = ", ".join(f"{v:+.3f}" for v in r["val"] if v is not None) or "–"
            se = f" ± {r['se']:.3f}" if r["se"] is not None else ""
            print(f"| {label} | {r['run']} | {r['rounds']} | {r['cards']} | {r['measured']} | {r['adopted']} | {val} | "
                  f"{r['seed']:.3f} → {r['final']:.3f} | {r['delta']:+.3f}{se} | {r['used']} | {r['hours']:.1f} | {r['halt']} |")

    print("\n## 表 3  GEPA 每次运行\n")
    print("| 任务 | 运行 | 测试集 种子 → 最终 | 测试集 Δ | 执行次数 | 用时 h |\n|---|---|---|---|---|---|")
    for label, d in data.items():
        for r in d["gepa"]:
            seed = "?" if r["seed"] is None else f"{r['seed']:.3f}"
            delta = "–" if r["delta"] is None else f"{r['delta']:+.3f}"
            hours = "–" if r["hours"] is None else f"{r['hours']:.1f}"
            print(f"| {label} | {r['run']} | {seed} → {r['final']:.3f} | {delta} | {r['used'] if r['used'] is not None else '–'} | {hours} |")

    print("\n## 设置\n")
    for label, d in data.items():
        print(f"- {label}：划分 {d['sizes'] or '?'}；预算 {d['budget'] or '?'} 次样本执行；模型 {models[label] or '未记录'}")
    if unfinished:
        print("\n## 未进入表格的运行\n")
        for line in unfinished:
            print(f"- {line}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
