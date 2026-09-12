"""Offline reports from saved artifacts. No SDK, model, or product imports."""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path

from .artifacts import digest, save_json
from .starter_protocol import SUITES, fingerprint
from .public_expansion_protocol import EXPANSION_SUITES
from .starter_results import GROUP_FIELDS, summarize

ALL_SUITES = {**SUITES, **EXPANSION_SUITES}


def read_journal(path, warnings):
    if not path.exists():
        warnings.append(f"{path.name}: not created")
        return []
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    rows = []
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("Journal entries must be objects")
            rows.append(row)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not content.endswith("\n"):
                warnings.append(f"{path.name}: incomplete final line ignored; run is incomplete")
            else:
                raise ValueError(f"Corrupt journal: {path.name}, line {index + 1}") from None
    return rows


def load_run(run):
    warnings = []
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"phase": "unknown"}
    manifest_path = run / "manifest.json"
    if not manifest_path.exists():
        if not state_path.exists():
            raise ValueError("Not a starter run directory: " + str(run))
        return {"path": str(run), "manifest": None, "state": state, "groups": [], "planned": [],
                "outputs": [], "scores": [], "warnings": ["Initialization did not produce a complete run manifest"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("format") != "public-starter-run-v1":
        raise ValueError("Unsupported run format")
    if fingerprint({**manifest["identity"], "mode": manifest["mode"]}) != manifest["protocol_id"]:
        raise ValueError("Protocol fingerprint does not match the recorded configuration")
    if manifest["track"] == "R" and fingerprint(manifest["identity"]) != manifest["pairing_id"]:
        raise ValueError("Product pairing identity does not match its configuration")
    if digest(run / "planned.jsonl") != manifest["planned_sha256"]:
        raise ValueError("Planned result ledger changed")
    planned = read_journal(run / "planned.jsonl", warnings)
    outputs = read_journal(run / "outputs.jsonl", warnings)
    scores = read_journal(run / "scores.jsonl", warnings)
    for row in planned:
        row.setdefault("task", row.get("suite"))
        row.setdefault("applicability", {"status": "applicable", "reason": None})
    cases = {p["case_id"] for p in planned}
    observed = {}
    for output in outputs:
        case_id = output["case_id"]
        if case_id not in cases or case_id in observed:
            raise ValueError("Unexpected or duplicate saved prediction")
        if output.get("status") not in {"success", "error", "not_applicable"} or type(output.get("output_available")) is not bool:
            raise ValueError("Invalid prediction status or availability")
        observed[case_id] = output
    if len(cases) != manifest["planned_predictions"] or len(planned) != manifest["planned_scores"]:
        raise ValueError("Manifest counts differ from the plan")
    for p in planned:
        if any(p[field] != manifest[field] for field in ("run_id", "protocol_id", "suite", "track", "mode")):
            raise ValueError("Planned identity differs from manifest")
    for score in scores:
        score.setdefault("applicability", next((p.get("applicability", {"status": "applicable", "reason": None})
                                                 for p in planned if p["result_id"] == score.get("result_id")),
                             {"status": "applicable", "reason": None}))
        score.setdefault("task", next((p.get("task", p["suite"]) for p in planned if p["result_id"] == score.get("result_id")), score.get("suite")))
        score.setdefault("trace", {"trace_id": None, "completeness": "none", "spans": []})
        if score.get("output_available") and not observed.get(score["case_id"], {}).get("output_available"):
            raise ValueError("Score claims an output that was not saved")
    groups = summarize(planned, scores)
    for group in groups:
        members = [p for p in planned if all(p[k] == group[k] for k in GROUP_FIELDS)]
        ids = {p["case_id"] for p in members}
        group.update(saved_outputs=sum(observed.get(i, {}).get("output_available", False) for i in ids),
                     prediction_errors=sum(observed.get(i, {}).get("status") == "error" for i in ids),
                     missing_predictions=sum(i not in observed for i in ids),
                     prediction_not_applicable=sum(observed.get(i, {}).get("status") == "not_applicable" for i in ids))
        # This denominator includes answers already saved when scoring was interrupted.
        group["saved_output_coverage"] = group["saved_outputs"] / len(ids) if ids else None
        if group["scorer"] == "product.boolq.explicit_conclusion.v1":
            group["label_parse_coverage_over_saved_outputs"] = group["scored"] / group["saved_outputs"] if group["saved_outputs"] else None
    if len(outputs) != len(cases) or len(scores) != len(planned):
        warnings.append("Prediction or score ledger incomplete; missing items remain in planned denominators")
    if state["phase"] not in {"finished", "finished_with_errors", "failed", "interrupted"}:
        warnings.append("No terminal state recorded; the process may still be running or may have stopped")
    events = read_journal(run / "model-events.jsonl", warnings)
    started = {e["call_id"] for e in events if e.get("event") == "started"}
    terminal = {e["call_id"] for e in events if e.get("event") in {"completed", "failed"}}
    return {"path": str(run), "manifest": manifest, "state": state, "groups": groups,
            "planned": planned, "outputs": outputs, "scores": scores, "warnings": warnings,
            "unfinished_model_calls": len(started - terminal)}


def paired_modes(runs):
    candidates = defaultdict(list)
    for run in runs:
        manifest = run["manifest"]
        if manifest and manifest["track"] == "R" and manifest.get("pairing_id"):
            candidates[manifest["pairing_id"]].append(run)
    pairs, warnings = [], []
    for identity, members in candidates.items():
        if len(members) != 2 or {m["manifest"]["mode"] for m in members} != {"chunk", "reasoning"}:
            warnings.append("No unique chunk/reasoning pair for identity " + identity[:12])
            continue
        modes = {m["manifest"]["mode"]: m for m in members}
        keys = [{(p["case_id"], p["scorer"]) for p in m["planned"]} for m in members]
        if keys[0] != keys[1]:
            warnings.append("Paired identity has mismatched question/scorer plans")
            continue
        index = {mode: {(r["case_id"], r["scorer"]): r for r in run["scores"]} for mode, run in modes.items()}
        for key in sorted(keys[0]):
            pair = {"pairing_id": identity, "suite": members[0]["manifest"]["suite"],
                    "case_id": key[0], "scorer": key[1]}
            for mode in ("chunk", "reasoning"):
                score = index[mode].get(key)
                pair[mode] = {"status": score["status"] if score else "missing",
                              "score": score["score"] if score else None,
                              "run_path": modes[mode]["path"]}
            pairs.append(pair)
    return pairs, warnings


def _cell(value):
    return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _number(value):
    return "—" if value is None else f"{value:.3f}"


def write_report(run_dirs, output):
    """Create a new report directory; saved experimental artifacts remain unchanged."""
    paths = [Path(p).resolve() for p in run_dirs]
    if len(paths) != len(set(paths)) or not paths:
        raise ValueError("Provide distinct starter run directories")
    runs = [load_run(p) for p in paths]
    pairs, pairing_warnings = paired_modes(runs)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "cases").mkdir()
    lines = ["# 公开评测起步报告", "", "模型参照 N 与产品路径 R 分开报告；分数未经人工校准，不作为发布门禁。",
             "", "最终上下文覆盖不代表检索排名，引用对象存在不代表逐项断言获得支持。", "", "## 覆盖范围", "",
             "| 公开套件 | 模型参照 N | 产品 chunk | 产品 reasoning |", "|---|---|---|---|"]
    for suite in ALL_SUITES:
        statuses = []
        for track, mode in (("N", None), ("R", "chunk"), ("R", "reasoning")):
            matches = [r for r in runs if r["manifest"] and r["manifest"]["suite"] == suite
                       and r["manifest"]["track"] == track and r["manifest"]["mode"] == mode]
            statuses.append("；".join(f"{r['manifest']['run_id']}: {r['state']['phase']} / {sum(o['output_available'] for o in r['outputs'])} 份输出"
                                      for r in matches) if matches else "未覆盖")
        lines.append("| " + " | ".join(map(_cell, [suite, *statuses])) + " |")
    for run in runs:
        manifest = run["manifest"]
        lines.extend(["", "## " + _cell(Path(run["path"]).name), "", "运行目录：" + _cell(run["path"]),
                      "记录状态：" + _cell(run["state"]["phase"])])
        if not manifest:
            lines.append("初始化未完成，尚无可核对的执行计划。")
        else:
            errors = sum(o["status"] == "error" for o in run["outputs"])
            skipped = sum(o["status"] == "not_applicable" for o in run["outputs"])
            missing = manifest["planned_predictions"] - len(run["outputs"])
            lines.extend(["", f"计划预测 {manifest['planned_predictions']} 题；预测错误 {errors}；预测不适用 {skipped}；缺失预测 {missing}。"])
            if manifest.get("adaptation_counts") is not None:
                lines.extend(["", "产品适用性筛选（筛选后才进入执行计划）：" + _cell(manifest["adaptation_counts"])])
            lines.extend([f"路径：{manifest['track']}；模式：{manifest['mode'] or '不适用'}；显式模型适配器中未结束的调用记录：{run['unfinished_model_calls']}（不含产品内部全部请求）。",
                          "", "| 指标 | 计划评分 | 已存输出 | 有效评分 | 均分（仅有效项） | 缺失评分 | 评分错误 | 不适用 | 未解析 | 未评分 |",
                          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
            for group in run["groups"]:
                counts = group["status_counts"]
                values = [f"{group.get('task', group['suite'])} / {group['scorer']}", group["planned"], group["saved_outputs"], group["scored"],
                          _number(group["mean_over_scored"]), group["missing"], counts["error"],
                          counts["not_applicable"], counts["unparsed"], counts["unscored"]]
                lines.append("| " + " | ".join(map(_cell, values)) + " |")
                lines.append("任务适用性：" + _cell(group.get("non_applicable", counts["not_applicable"]))
                             + "；trace 完整度：" + _cell(group.get("trace_completeness", {}))
                             + ("；Agent 指标已抑制（没有 complete trace）。" if group.get("agent_metrics_suppressed") else ""))
            for group in run["groups"]:
                if "label_parse_coverage_over_saved_outputs" in group:
                    lines.extend(["", "BoolQ 标签解析覆盖率（已完成解析 / 已存输出）："
                                  + _number(group["label_parse_coverage_over_saved_outputs"])
                                  + "；尚未评分的回答也在分母内，未完成运行不能解释为最终解析成功率。"])
            lines.extend(["", "逐题记录（含完整回答、上下文、引用对象、评分状态与原因）：", ""])
            outputs_by_case = {r["case_id"]: r for r in run["outputs"]}
            for case_id in dict.fromkeys(p["case_id"] for p in run["planned"]):
                filename = fingerprint([run["path"], case_id]) + ".json"
                save_json(output / "cases" / filename, {"case_id": case_id, "run_path": run["path"],
                          "output": outputs_by_case.get(case_id),
                          "planned": [p for p in run["planned"] if p["case_id"] == case_id],
                          "scores": [s for s in run["scores"] if s["case_id"] == case_id]})
                label = case_id.replace("[", "\\[").replace("]", "\\]")
                lines.append(f"- [{label}](cases/{filename})")
        if run["warnings"]:
            lines.extend(["", "完整性说明：", ""] + ["- " + _cell(w) for w in run["warnings"]])
    lines.extend(["", "## chunk/reasoning 配对", "", "只对相同题目、资料库、模型配置和源码身份的产品运行并列展示；不计算 N/R 差值。"])
    if pairs:
        lines.extend(["", "| 题目 | 指标 | chunk 状态/分数 | reasoning 状态/分数 |", "|---|---|---|---|"])
        for pair in pairs:
            values = [pair["case_id"], pair["scorer"], *[f"{pair[m]['status']} / {_number(pair[m]['score'])}" for m in ("chunk", "reasoning")]]
            lines.append("| " + " | ".join(map(_cell, values)) + " |")
    else:
        lines.append("当前没有可唯一配对的产品运行。")
    if pairing_warnings:
        lines.extend([""] + ["- " + _cell(w) for w in pairing_warnings])
    summary = {"format": "public-starter-report-v1", "release_gate": False,
               "runs": [{k: v for k, v in r.items() if k not in {"planned", "outputs", "scores"}} for r in runs],
               "paired_modes": pairs, "pairing_warnings": pairing_warnings}
    save_json(output / "summary.json", summary)
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
