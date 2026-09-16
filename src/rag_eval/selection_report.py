"""Offline whole-selection coverage, including unexecuted corpus partitions."""
from collections import Counter, defaultdict
from pathlib import Path

from .artifacts import save_json
from .selection_execution import execution_context, expected_scorers
from .starter_protocol import fingerprint
from .system_product import SYSTEM_SUITES


def summarize_selection(selection, plan, runs, modes=("chunk", "reasoning")):
    """Aggregate verified runs; this function never discovers or starts a run."""
    modes = tuple(modes)
    if not modes or len(set(modes)) != len(modes) or not set(modes) <= {"chunk", "reasoning"}:
        raise ValueError("Choose distinct chunk/reasoning report modes")
    source, cases = selection["native_source"], selection["cases"]
    suite = source["suite"]
    by_id = {c["case_id"]: c for c in cases}
    decisions = {d["case_id"]: d for d in plan["decisions"]}
    if len(decisions) != len(plan["decisions"]) or set(decisions) != set(by_id):
        raise ValueError("Partition decisions must cover the full selected case set")
    partitions = {p["partition_id"]: p for p in plan["partitions"]}
    if len(partitions) != len(plan["partitions"]):
        raise ValueError("Duplicate planned partition")
    executable = [cid for p in partitions.values() for cid in p["case_ids"]]
    if len(executable) != len(set(executable)) or set(executable) != {
            cid for cid, d in decisions.items() if d["status"] == "applicable"}:
        raise ValueError("Partitions must contain every executable membership exactly once")
    for pid, p in partitions.items():
        if any(decisions[cid].get("partition_id") != pid for cid in p["case_ids"]):
            raise ValueError("Partition decisions and assignment disagree")
    from .ifeval_protocol import DIRECT_POLICY
    policies = {r["manifest"]["identity"].get("ifeval_scoring") for r in runs
                if r["manifest"] is not None} if suite == "ifeval" else set()
    if len(policies) > 1:
        raise ValueError("Cannot aggregate different IFEval scoring policies/configurations")
    policy = next(iter(policies)) if policies else DIRECT_POLICY
    scorers = expected_scorers(suite, "R", ifeval_scoring=policy)
    observed, unbound, family = {}, [], None
    for run in runs:
        manifest = run["manifest"]
        if manifest is None:
            unbound.append({"path": run["path"], "state": run["state"],
                            "reason": "No run manifest; cannot attribute outputs to a partition"})
            continue
        if (manifest["suite"] != suite or manifest["track"] != "R"
                or manifest["mode"] not in modes or manifest["identity"].get("source") != source):
            raise ValueError("Run is outside this selection, track or requested report modes")
        context = manifest.get("selection_context") or {}
        pid, mode = context.get("partition_id"), manifest["mode"]
        if pid not in partitions or context != execution_context(source, len(cases), plan, partitions[pid]):
            raise ValueError("Run selection context differs from the complete partition plan")
        if manifest["identity"].get("selection_context") != context:
            raise ValueError("Run partition context is not part of its protocol identity")
        key = (pid, mode)
        if key in observed:
            raise ValueError("Duplicate partition/mode run; select an explicit experiment instead of averaging retries")
        configuration = fingerprint({k: v for k, v in manifest["identity"].items()
                                     if k not in {"product_bundle", "selection_context"}})
        if family is None:
            family = configuration
        elif family != configuration:
            raise ValueError("Cannot aggregate different model, source, audit or runtime configurations")
        expected = Counter((cid, scorer) for cid in partitions[pid]["case_ids"] for scorer in scorers)
        if Counter((p["case_id"], p["scorer"]) for p in run["planned"]) != expected:
            raise ValueError("Run plan does not cover its complete frozen partition")
        observed[key] = run
    cells, predictions, scores = [], {}, {}
    all_complete = bool(partitions) and not unbound
    for mode in modes:
        for pid, partition in partitions.items():
            run = observed.get((pid, mode))
            complete = False
            state = "not_run"
            if run is not None:
                state = run["state"]["phase"]
                for row in run["outputs"]:
                    key = (mode, row["case_id"])
                    if row["case_id"] not in partition["case_ids"] or key in predictions:
                        raise ValueError("Duplicate or out-of-partition saved prediction")
                    predictions[key] = row
                for row in run["scores"]:
                    key = (mode, row["case_id"], row["scorer"])
                    if row["case_id"] not in partition["case_ids"] or row["scorer"] not in scorers or key in scores:
                        raise ValueError("Duplicate or out-of-partition score")
                    scores[key] = row
                complete = (state in {"finished", "finished_with_errors"} and not run["warnings"]
                            and len(run["outputs"]) == len(partition["case_ids"])
                            and len(run["scores"]) == len(partition["case_ids"]) * len(scorers))
            all_complete = all_complete and complete
            cells.append({"partition_id": pid, "mode": mode, "planned_predictions": len(partition["case_ids"]),
                          "state": state, "ledgers_complete": complete,
                          "run_path": run["path"] if run else None,
                          "warnings": run["warnings"] if run else ["Partition has not been run"]})
    grouped = defaultdict(list)
    for case in cases:
        grouped[case["task"]].append(case)
    groups = []
    for mode in modes:
        for task, members in grouped.items():
            ids = [c["case_id"] for c in members if decisions[c["case_id"]]["status"] == "applicable"]
            output_rows = [predictions[(mode, cid)] for cid in ids if (mode, cid) in predictions]
            available = sum(row["output_available"] for row in output_rows)
            extraction = Counter((r.get("answer_extraction") or {}).get("status", "missing")
                                 for r in output_rows if r["output_available"])
            for scorer in scorers:
                results = [scores[(mode, cid, scorer)] for cid in ids if (mode, cid, scorer) in scores]
                values = [row["score"] for row in results if row["status"] == "scored"]
                primary = scorer == scorers[0]
                binary = primary and (suite in SYSTEM_SUITES or suite == "boolq")
                if binary and any(value not in (0, 1) for value in values):
                    raise ValueError("Binary product scorer returned a nonbinary score")
                known_correct = sum(value == 1 for value in values) if binary else None
                groups.append({
                    "suite": suite, "task": task, "mode": mode, "scorer": scorer,
                    "metric_role": "primary" if primary else "diagnostic",
                    "selected_memberships": len(members),
                    "distinct_selected_questions": len({c["sample_id"] for c in members}),
                    "applicability": dict(Counter(decisions[c["case_id"]]["status"] for c in members)),
                    "planned": len(ids), "recorded_predictions": len(output_rows), "saved_outputs": available,
                    "missing_predictions": len(ids) - len(output_rows),
                    "prediction_status_counts": dict(Counter(r["status"] for r in output_rows)),
                    "recorded_scores": len(results), "scored": len(values),
                    "missing_scores": len(ids) - len(results),
                    "score_status_counts": dict(Counter(r["status"] for r in results)),
                    "mean_over_scored": sum(values) / len(values) if values else None,
                    "known_correct": known_correct,
                    "correct_over_planned": known_correct / len(ids) if binary and ids else None,
                    "output_coverage_over_selected": available / len(members),
                    "score_coverage_over_selected": len(values) / len(members),
                    "answer_extraction_counts": dict(extraction) if primary and suite in SYSTEM_SUITES else None,
                    "answer_parse_coverage_over_saved_outputs": extraction["parsed"] / available
                    if primary and suite in SYSTEM_SUITES and suite != "ifeval" and available else None,
                })
    return {
        "format": "public-selection-report-v1", "suite": suite,
        "selection_bundle_sha256": source["selection_bundle_sha256"],
        "partition_plan_sha256": fingerprint(plan), "experiment_configuration": family,
        "selection_summary": source.get("selection_summary", selection["manifest"].get("selection", {})),
        "selected_memberships": len(cases), "distinct_selected_questions": len({c["sample_id"] for c in cases}),
        "product_applicability": dict(Counter(d["status"] for d in decisions.values())),
        "executable_memberships": len(executable),
        "execution_plan_coverage": len(executable) / len(cases) if cases else None,
        "partitions": cells, "groups": groups, "unbound_runs": unbound,
        "all_partition_ledgers_complete": all_complete,
        "all_selected_primary_scores_available": bool(cases) and all(
            g["scored"] == g["selected_memberships"] for g in groups if g["metric_role"] == "primary"),
        # Local record reconstruction and an operator coverage declaration do
        # not independently verify that every upstream record was exported.
        "full_upstream_evaluation_complete": False,
        "upstream_completeness": "not_independently_verified",
        "release_gate": False, "agent_metrics_status": "not_implemented",
    }


def write_selection_report(bundle_dir, partition_plan_path, run_dirs, output, modes=("chunk", "reasoning")):
    from .selection_bundle import load_selection_bundle
    from .selection_partitions import load_partition_plan
    from .starter_report import load_run, paired_modes, _cell, _number
    bundle_dir, output = Path(bundle_dir).resolve(), Path(output).resolve()
    paths = [Path(path).resolve() for path in run_dirs]
    if len(paths) != len(set(paths)):
        raise ValueError("Provide distinct run directories")
    if output.is_relative_to(bundle_dir) or any(output.is_relative_to(path) for path in paths):
        raise ValueError("Report output must be separate from immutable inputs and runs")
    selection = load_selection_bundle(bundle_dir)
    plan = load_partition_plan(partition_plan_path, selection["cases"], selection["native_source"])
    runs = [load_run(path) for path in paths]
    report = summarize_selection(selection, plan, runs, modes)
    pairs, warnings = paired_modes([run for run in runs if run["manifest"] is not None])
    report.update(paired_modes=pairs, pairing_warnings=warnings)
    output.mkdir(parents=True, exist_ok=False)
    lines = ["# 完整公开题单覆盖报告", "", "套件：" + _cell(report["suite"]),
             "完整题单任务记录：" + str(report["selected_memberships"])
             + "；独立问题：" + str(report["distinct_selected_questions"]),
             "产品适用性：" + _cell(report["product_applicability"]),
             "可执行题单覆盖率：" + _number(report["execution_plan_coverage"]),
             "所有计划分区的台账完整：" + str(report["all_partition_ledgers_complete"]),
             "所有已选题目主分可用：" + str(report["all_selected_primary_scores_available"]),
             "台账完整可包含错误、澄清或 N/A；不等于答案正确或上游全量评测完成。",
             "语料范围为固定分区内全库；原始选题与来源覆盖详情见 summary.json。",
             "", "| 分区 | 模式 | 计划题数 | 状态 | 台账完整 |", "|---|---|---:|---|---|"]
    for cell in report["partitions"]:
        lines.append("| " + " | ".join(map(_cell, [cell["partition_id"], cell["mode"],
                     cell["planned_predictions"], cell["state"], cell["ledgers_complete"]])) + " |")
    lines.extend(["", "| 任务 | 模式 | 指标 | 已选 | 计划 | 已存答案 | 有效分 | 缺失评分 | 有效项均分 | 已知答对/计划 |",
                  "|---|---|---|---:|---:|---:|---:|---:|---:|---:|"])
    for g in report["groups"]:
        lines.append("| " + " | ".join(map(_cell, [g["task"], g["mode"], g["scorer"],
                     g["selected_memberships"], g["planned"], g["saved_outputs"], g["scored"],
                     g["missing_scores"], _number(g["mean_over_scored"]), _number(g["correct_over_planned"])])) + " |")
    lines.extend(["", "不跨套件或模型/产品路径合并分数。未核验上游完整性，Agent/DAG 未接入，无发布门禁。",
                  "chunk/reasoning 按相同分区、模型配置和源码身份配对，逐题配对见 summary.json。"])
    lines.extend("- " + _cell(w) for w in warnings)
    for item in report["unbound_runs"]:
        lines.append("- 未绑定分区的运行：" + _cell(item["path"]) + "；" + _cell(item["reason"]))
    save_json(output / "summary.json", report)
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return report
