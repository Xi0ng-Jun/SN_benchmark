"""Read-only result explorer. No SDK, product runtime or model construction."""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re

from .artifacts import digest, save_json
from .metric_catalog import describe_metric
from .starter_protocol import fingerprint
from .starter_report import load_run, read_journal

ASSETS = Path(__file__).with_name("dashboard")
_SECRET = re.compile(r"(?:api[_-]?key|authorization|password|secret|access[_-]?token|refresh[_-]?token|endpoint|base[_-]?url|service[_-]?url)$", re.I)


def _redact(value):
    """Do not export configured credentials/addresses; keep hashes and usage."""
    if isinstance(value, dict):
        return {key: "[redacted]" if _SECRET.search(key) else _redact(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def discover_runs(root: str | Path) -> list[Path]:
    """Stop descent at a run, so its input/snapshots never become other runs."""
    root = Path(root).resolve()
    if not root.is_dir():
        raise ValueError(f"Run root does not exist: {root}")
    found = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        path = Path(directory)
        dirs[:] = sorted(d for d in dirs if d not in {
            ".git", ".venv", "node_modules", "runtime", "input", "invocations", "sdk-source"})
        if "manifest.json" in files:
            try:
                manifest = json.loads((path / "manifest.json").read_text(encoding="utf-8"))
            except (ValueError, OSError):
                if "planned.jsonl" in files or "state.json" in files:
                    raise ValueError(f"Unreadable run manifest: {path}") from None
                continue
            if isinstance(manifest, dict) and manifest.get("format") == "public-starter-run-v1":
                found.append(path)
                dirs[:] = []
        elif "state.json" in files:
            state = json.loads((path / "state.json").read_text(encoding="utf-8"))
            if isinstance(state, dict) and state.get("phase") in {"initializing", "failed", "interrupted"}:
                found.append(path)
                dirs[:] = []
    return sorted(found)


def _local_file(run, relative):
    path = (run / relative).resolve()
    if not path.is_relative_to(run):
        raise ValueError("Dashboard artifact points outside run: " + relative)
    return path


def _source_cases(run, manifest, warnings):
    path = _local_file(run, "input/cases.jsonl")
    source = manifest["identity"].get("source", {})
    expected = source.get("artifacts", {}).get("cases.jsonl")
    if expected and (not path.exists() or digest(path) != expected):
        raise ValueError("Frozen source cases artifact changed: " + str(path))
    if not path.exists():
        warnings.append("input/cases.jsonl 未保存；详情中原始题目不可用。")
        return {}
    rows = read_journal(path, warnings)
    by_id = {row["case_id"]: row for row in rows}
    if len(by_id) != len(rows):
        raise ValueError("Duplicate source case ID")
    if not expected:
        warnings.append("原始 case 没有 cases.jsonl 哈希；仅展示记录，不声称已核实原始文件。")
    return by_id


def aggregate_runs(run_dirs: list[str | Path]) -> dict:
    paths = [Path(p).resolve() for p in run_dirs]
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Provide distinct run directories")
    runs, entries, observations, audit = [], [], {}, []
    seen_runs = set()
    for path in paths:
        # The existing loader rejects changed identities, duplicate and invalid
        # scores; an unfinished JSONL tail remains a disclosed missing record.
        loaded = load_run(path)
        manifest = loaded["manifest"]
        warnings = list(loaded["warnings"])
        key = fingerprint(str(path))
        cell = dict(key=key, path=str(path), run_id=path.name, suite=None, track=None, mode="native",
                    phase=loaded["state"].get("phase", "unknown"), planned_predictions=0,
                    planned_scores=0, saved_outputs=0, recorded_outputs=0, recorded_scores=0,
                    warnings=warnings, manifest=manifest)
        runs.append(cell)
        if manifest is None:
            audit.append({"run_key": key, "warnings": warnings})
            continue
        identity = manifest["identity"]
        run_identity = (manifest["run_id"], manifest["protocol_id"])
        if run_identity in seen_runs:
            raise ValueError("Duplicate saved run identity; copied runs must not be counted twice")
        seen_runs.add(run_identity)
        config = dict(identity)
        if identity.get("source", {}).get("selection_protocol"):
            # The validated partition-plan hash identifies the shared corpus
            # policy. Only members of that SAME plan may pool partitions.
            config["selection_context"] = {
                k: v for k, v in identity["selection_context"].items()
                if k not in {"partition_id", "case_ids"}}
            config.pop("product_bundle", None)
        # Non-selection runs keep the full corpus/review identity; different
        # distractors must never disappear into an apparently comparable mean.
        complete_identity = {"source", "models", "code", "runtime_settings", "product_services", "audits_sha256", "track"} <= config.keys()
        if manifest["track"] == "R" and not identity.get("product_bundle"):
            complete_identity = False
        family = fingerprint(config) if complete_identity else None
        if not complete_identity:
            warnings.append("配置身份不完整：条目可查看，但不可进行配对差值比较。")
        source_cases = _source_cases(path, manifest, warnings)
        outputs = {r["case_id"]: r for r in loaded["outputs"]}
        results = {r["result_id"]: r for r in loaded["scores"]}
        events = defaultdict(list)
        for event in read_journal(_local_file(path, "model-events.jsonl"), warnings):
            if event.get("case_id"):
                events[event["case_id"]].append(event)
        plans_by_case = defaultdict(list)
        for plan in loaded["planned"]:
            plans_by_case[plan["case_id"]].append(plan)
        for case_id, plans in plans_by_case.items():
            oid = fingerprint([key, case_id])
            output = outputs.get(case_id)
            source_case = source_cases.get(case_id)
            observations[oid] = {"case": source_case, "output": output,
                                 "events": events[case_id],
                                 "warnings": ([] if source_case else ["原始 case 未保存"])}
            for plan in plans:
                result = results.get(plan["result_id"])
                behavior = ((output or {}).get("behavior") or (output or {}).get("product_record", {}).get("behavior") or {})
                entries.append({
                    "id": fingerprint([key, plan["result_id"]]), "observation_id": oid,
                    "run_key": key, "run_id": manifest["run_id"], "suite": plan["suite"],
                    "task": plan.get("task", plan["suite"]), "track": manifest["track"],
                    "mode": manifest["mode"] or "native", "scorer": plan["scorer"],
                    "case_id": case_id, "status": result["status"] if result else "missing",
                    "output_status": output["status"] if output else "missing",
                    "behavior": behavior.get("kind", "not_observed"),
                    "partition": (manifest.get("selection_context") or {}).get("partition_id") or "unpartitioned",
                    "phase": cell["phase"], "config_family": family,
                    "pairing_id": manifest.get("pairing_id"),
                    "score": result["score"] if result else None,
                    "reason": result.get("reason") if result else "计划中存在，但尚未保存评分记录。",
                    "plan": plan, "result": result,
                })
        cell.update(run_id=manifest["run_id"], suite=manifest["suite"], track=manifest["track"],
                    mode=manifest["mode"] or "native", config_family=family,
                    planned_predictions=len(plans_by_case), planned_scores=len(loaded["planned"]),
                    saved_outputs=sum(o["output_available"] for o in outputs.values()),
                    recorded_outputs=len(outputs), recorded_scores=len(results))
        audit.append({"run_key": key, "warnings": warnings,
                      "missing_outputs": len(plans_by_case) - len(outputs),
                      "missing_scores": len(loaded["planned"]) - len(results)})
    status = Counter((o["output"] or {}).get("status", "missing") for o in observations.values())
    data = {
        "format": "sn-experiment-dashboard-v2", "generated_at": datetime.now(timezone.utc).isoformat(),
        "runs": runs, "entries": entries, "observations": observations,
        "catalog": {scorer: describe_metric(scorer) for scorer in sorted({e["scorer"] for e in entries})},
        "summary": {"run_count": len(runs), "planned_outputs": len(observations),
                    "planned_scores": len(entries), "saved_outputs": sum(r["saved_outputs"] for r in runs),
                    "recorded_outputs": sum(r["recorded_outputs"] for r in runs),
                    "saved_scores": sum(r["recorded_scores"] for r in runs),
                    "scored": sum(e["status"] == "scored" for e in entries),
                    "prediction_status": dict(status)},
        "audit": audit,
        "limitations": ["范围为本次提供的 run；没有 run 的分区不在分母。全题单覆盖请使用 report_public_selection.py。",
                        "条目 = run × case × scorer；问题数按 run × case 去重，跨运行是尝试次数。",
                        "均值按 suite / task / track / mode / scorer / 配置身份分组；缺配置身份时按 run 分开，不生成综合质量总分。",
                        "这是运行产物的离线快照；未终止 run 的结果可能继续增长。刷新需要生成新报告。",
                        "文本拒答仅为 refusal_candidate；引用对象存在不证明语义支持；未做人审不设门禁。",
                        "计算说明描述当前代码；具体实验以保存的源码/SDK 身份、details 与 judge 事件为准。未保存的过程不补造。"],
    }
    return _redact(data)


def write_dashboard(run_dirs: list[str | Path], output: str | Path) -> Path:
    output = Path(output).resolve()
    paths = [Path(p).resolve() for p in run_dirs]
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in paths):
        raise ValueError("Report directory must be separate from saved runs")
    if output.exists():
        raise FileExistsError("Use a new report directory: " + str(output))
    data = aggregate_runs(paths)
    # Replace static placeholders before inserting data, so an artifact cannot
    # inject asset placeholders. Escape '<' even inside application/json scripts.
    html = (ASSETS / "template.html").read_text(encoding="utf-8")
    for marker, name in (("__STYLE__", "style.css"), ("__CORE__", "core.js"), ("__APP__", "app.js")):
        html = html.replace(marker, (ASSETS / name).read_text(encoding="utf-8"))
    payload = json.dumps(data, ensure_ascii=False, allow_nan=False).replace("<", "\\u003c").replace("&", "\\u0026")
    html = html.replace("__DATA__", payload)
    output.mkdir(parents=True, exist_ok=False)
    save_json(output / "dashboard-data.json", data)
    save_json(output / "summary.json", data["summary"])
    save_json(output / "audit.json", {"runs": data["audit"], "limitations": data["limitations"]})
    target = output / "dashboard.html"
    with target.open("x", encoding="utf-8") as handle:
        os.chmod(target, 0o600)
        handle.write(html)
    return output
