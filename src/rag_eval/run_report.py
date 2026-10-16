"""Offline reports from saved artifacts. No SDK, model, or product imports."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
from pathlib import Path

from .artifacts import digest, save_json
from .identity import fingerprint
from .run_results import GROUP_FIELDS, summarize
from .stage_metrics import FORMAT as STAGE_FORMAT, compute_stage_metrics, summarize_cases

from .notebook_data import SUITES as NOTEBOOK_SUITES, VERSION as NOTEBOOK_VERSION

ALL_SUITES = NOTEBOOK_SUITES


def _reasoning_protocol(manifest):
    value = (manifest.get("identity", {}).get("reasoning_protocol")
             if isinstance(manifest, dict) else None)
    return value.get("name") if isinstance(value, dict) else "reasoning-v1"


def _mode_label(manifest):
    if manifest.get("mode") == "reasoning" and _reasoning_protocol(manifest) != "reasoning-v1":
        return _reasoning_protocol(manifest)
    return manifest.get("mode")


from .run_reader import RunReadContext, load_run, read_journal


def paired_modes(runs):
    candidates = defaultdict(list)
    for run in runs:
        manifest = run["manifest"]
        if (manifest and manifest["track"] == "R" and manifest.get("pairing_id")
                and manifest.get("execution_status") != "not_applicable"
                and manifest["mode"] in {"chunk", "reasoning"}
                and _reasoning_protocol(manifest) == "reasoning-v1"):
            candidates[run.get('comparison_pairing_id', manifest['pairing_id'])].append(run)
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


def write_report(run_dirs, output, *, partition_only=False):
    """Create a new report directory; saved experimental artifacts remain unchanged."""
    paths = [Path(p).resolve() for p in run_dirs]
    if len(paths) != len(set(paths)) or not paths:
        raise ValueError("Provide distinct Notebook run directories")
    context = RunReadContext(partition_only=partition_only)
    runs = [load_run(p, context=context) for p in paths]
    from .oracle_track import require_formal_run
    for loaded in runs:
        if loaded['manifest'] is not None:
            require_formal_run(loaded['manifest'])
    stage_groups = defaultdict(list)
    stage_unavailable = []
    for run in runs:
        manifest = run['manifest']
        try:
            if not manifest or not manifest.get('identity', {}).get('notebook_context'):
                raise ValueError('No frozen Notebook scope')
            metrics = compute_stage_metrics(run['path'], context=context)
        except (OSError, ValueError, KeyError) as exc:
            # Saved outputs and existing reports remain readable when diagnostics fail.
            metrics = dict(metric_role='diagnostic', status='unavailable', reason=type(exc).__name__)
            stage_unavailable.append(dict(run_path=run['path'], **metrics))
        else:
            stage_groups[(metrics['suite'], metrics['mode'])].extend(metrics['cases'])
        run['stage_metrics'] = metrics
    pairs, pairing_warnings = paired_modes(runs)
    output = Path(output).resolve()
    output.mkdir(parents=True, exist_ok=False)
    (output / "cases").mkdir()
    lines = ["# Notebook 评测报告", "", "五套任务分别报告；诊断分与官方分不混合，不作为发布门禁。",
             "", "最终上下文覆盖不代表检索排名，引用对象存在不代表逐项断言获得支持。", "", "## 覆盖范围", "",
             "| 套件 | SN chunk | SN reasoning | BM25 对照 |", "|---|---|---|---|"]
    for suite in ALL_SUITES:
        statuses = []
        for track, mode in (("R", "chunk"), ("R", "reasoning"), ("R", "bm25")):
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
            if manifest.get("scoring_batch"):
                batch = manifest["scoring_batch"]
                lines.extend(["评分批次：独立重评分；来源运行 " + _cell(batch["origin_run_id"])
                              + "；选择 " + _cell(batch["selection"])
                              + "；指标 " + _cell(batch.get("scorers", [])),
                              "回答来自来源运行的只读副本；本批次没有重新执行 SN Ask 或生成模型。"])
            errors = sum(o["status"] == "error" for o in run["outputs"])
            skipped = sum(o["status"] == "not_applicable" for o in run["outputs"])
            missing = manifest["planned_predictions"] - len(run["outputs"])
            lines.extend(["", f"计划预测 {manifest['planned_predictions']} 题；预测错误 {errors}；预测不适用 {skipped}；缺失预测 {missing}。"])
            if manifest.get("product_protocol") in {NOTEBOOK_VERSION, "sn-notebook-baseline-v1"}:
                notebook_context = manifest["identity"]["notebook_context"]
                lines.extend(["", "Notebook 资料评测：本次为分区 " + notebook_context["partition_id"]
                              + "；全题单 " + str(notebook_context["selected_cases"]) + " 题 / " + str(notebook_context["partition_count"]) + " 个资料分区。",
                              "F1/ROUGE 等连续主分仅报告有效评分均值与覆盖率，不解释为答对率。ALCE 模型指标需要独立官方评分步骤；当前未评分保留 null。"])
            lines.append(f"路径：{manifest['track']}；模式：{_mode_label(manifest) or '不适用'}；显式模型适配器中未结束的调用记录：{run['unfinished_model_calls']}（不含产品内部全部请求）。")
            preparation_policy = manifest.get('identity', {}).get('preparation_policy')
            if preparation_policy:
                lines.append('资料准备策略：' + _cell(preparation_policy) + '。')
            audit = run.get('scheduled_generation_audit')
            if audit is not None:
                completeness = audit['completeness']
                lines.extend([
                    "scheduled-generation 日志审计：观测 " + audit['observation_state']
                    + f"；提交记录 {audit['attempt_count']}；终态记录 {audit['terminal_count']}"
                    + f"；已配对 {audit['paired_count']}；未配对 {len(audit['unfinished'])}"
                    + "；配对状态 " + completeness['pair_state']
                    + "；尾行截断 " + ('是' if completeness['truncated_tail'] else '否') + "。",
                    "仅核已记录journal结构，不证明整次执行历史完整，也不代表HTTP、评分或Job完成。",
                ])
                lines.extend('scheduled-generation 说明：' + _cell(w) for w in audit['warnings'])
            lines.extend(["", "| 指标 | 计划评分 | 已存输出 | 有效评分 | 均分（仅有效项） | 缺失评分 | 评分错误 | 不适用 | 未解析 | 未评分 |",
                          "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"])
            for group in run["groups"]:
                counts = group["status_counts"]
                values = [f"{group.get('task', group['suite'])} / {group['scorer']}", group["planned"], group["saved_outputs"], group["scored"],
                          _number(group["mean_over_scored"]), group["missing"], counts["error"],
                          counts["not_applicable"], counts["unparsed"], counts["unscored"]]
                lines.append("| " + " | ".join(map(_cell, values)) + " |")
            for group in run["groups"]:
                lines.extend(["", "任务 " + _cell(group.get("task", group["suite"]))
                              + "：不适用 " + str(group["non_applicable"])
                              + "；trace 完整度 " + _cell(group["trace_completeness"])
                              + "；Agent 指标尚未接入（trace 字段本身不是 Agent 评分）。"])
            metrics = run['stage_metrics']
            lines.extend(['', '### 阶段指标（diagnostic）', ''])
            if 'summary' not in metrics:
                lines.append('不可计算：' + metrics['reason'] + '；未补零。')
            else:
                stage = metrics['summary']
                lines.extend(['| 指标 | 值 | 观测分子 / 分母 |', '|---|---:|---:|'])
                for name in ('gold_evidence_recall', 'context_precision', 'citation_precision', 'refusal_rate'):
                    value = stage[name]
                    lines.append('| ' + ' | '.join(map(_cell, [name, _number(value['value']),
                        f"{value['numerator']} / {value['denominator']} ({value['status']})"])) + ' |')
                lines.extend(['', '调用与耗时：' + _cell(json.dumps(stage['model_usage'], ensure_ascii=False)),
                    'scheduled journal 按保存的 Ask 时间窗口归因；provider 与 scheduled 分开报告，历史完整性未知。'])
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
    lines.extend(["", "## chunk/reasoning 配对", "", "只对相同题目、资料库、模型配置和源码身份的产品运行并列展示。"])
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
    summary["input_validation"] = "partition-capsule; full canonical audit required before official export" if partition_only else "canonical-full"
    summary['stage_metrics'] = dict(format=STAGE_FORMAT, metric_role='diagnostic',
        groups=[dict(suite=suite, mode=mode, **summarize_cases(cases))
                for (suite, mode), cases in stage_groups.items()], unavailable_runs=stage_unavailable)
    save_json(output / "summary.json", summary)
    (output / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    return summary
