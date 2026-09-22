"""Validated, read-only v3 index and lazy case payloads; no SDK or runtime imports."""
from __future__ import annotations

from collections import Counter, defaultdict
import json
import math
import os
from pathlib import Path, PurePosixPath
import re

from .artifacts import digest
from .experiment_aggregation import aggregate_runs, _redact
from .starter_protocol import fingerprint
from .starter_report import read_journal

NATIVE_VERSION = "sn-deepeval-native-v1"
_NATIVE_FILES = {"components": "components.jsonl", "scores": "native-scores.jsonl",
                 "diagnostics": "native-diagnostics.jsonl", "traces": "native-traces.jsonl",
                 "errors": "native-errors.jsonl", "outputs": "native-outputs.jsonl"}
_HASH = re.compile(r"[0-9a-f]{64}\Z")


def _relative(name):
    if not isinstance(name, str) or not name or "\\" in name:
        raise ValueError("Invalid artifact/source relative path")
    path = PurePosixPath(name)
    if path.is_absolute() or ".." in path.parts or ":" in name:
        raise ValueError("Artifact/source points outside run: " + name)
    return name


def _file(run, name):
    path = run / _relative(name)
    if not path.resolve().is_relative_to(run):
        raise ValueError("Artifact points outside run: " + name)
    return path


def _json(run, name):
    path = _file(run, name)
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def _preflight(run):
    # Existing loaders follow input and source manifests. Validate their path
    # boundary before invoking them, including symlinked ancestor directories.
    for directory, dirs, files in os.walk(run, followlinks=False):
        for name in dirs + files:
            path = Path(directory) / name
            if path.is_symlink() and not path.resolve().is_relative_to(run):
                raise ValueError("Artifact points outside run: " + str(path.relative_to(run)))
    for name in ("manifest.json", "input/manifest.json", "base-manifest.json"):
        value = _json(run, name)
        if value is None:
            continue
        def visit(item):
            if isinstance(item, dict):
                for key, child in item.items():
                    if key in {"files", "artifacts", "origin_artifacts", "benchmark_source_hashes"} and isinstance(child, dict):
                        for filename in child:
                            _relative(filename)
                    visit(child)
            elif isinstance(item, list):
                for child in item:
                    visit(child)
        visit(value)


def _preview(value, length=180):
    text = str(value or "")
    return text if len(text) <= length else text[:length] + "…"


def _hash(value, label):
    if not isinstance(value, str) or not _HASH.fullmatch(value):
        raise ValueError("Invalid " + label + " hash")
    return value


def _lineage(run, manifest):
    identity = manifest["identity"]
    batch = identity.get("scoring_batch")
    if manifest.get("scoring_batch") != batch:
        raise ValueError("Scoring batch differs from frozen identity")
    if batch is None:
        return None
    if not isinstance(batch, dict) or batch.get("version") != "notebook-rescoring-v1":
        raise ValueError("Unknown scoring batch provenance")
    origin = _hash(batch.get("origin_manifest_sha256"), "origin manifest")
    files = batch.get("origin_artifacts")
    if not isinstance(files, dict) or files.get("manifest.json") != origin:
        raise ValueError("Scoring origin manifest hash differs from origin artifacts")
    for name, checksum in files.items():
        _relative(name)
        _hash(checksum, "origin artifact")
    # Only the explicitly supplied run or its copied base is read, never an
    # origin directory named in untrusted provenance metadata.
    base_path = _file(run, "base-manifest.json")
    if not base_path.exists() or digest(base_path) != origin:
        raise ValueError("Scoring origin manifest hash does not match saved base manifest")
    base = _json(run, "base-manifest.json")
    if base.get("run_id") != batch.get("origin_run_id"):
        raise ValueError("Scoring origin run identity mismatch")
    if base.get("identity", {}).get("source") != identity.get("source"):
        raise ValueError("Scoring origin source mismatch")
    answer_hash = files.get("outputs.jsonl")
    copied = _file(run, "outputs.jsonl")
    if answer_hash is not None:
        if not copied.exists() or digest(copied) != answer_hash:
            raise ValueError("Scoring origin answers hash mismatch")
    elif copied.exists() and copied.stat().st_size:
        raise ValueError("Scoring copied answers have no origin hash")
    for original, saved in (("planned.jsonl", "base-planned.jsonl"), ("scores.jsonl", "base-scores.jsonl")):
        target = _file(run, saved)
        if original in files and (not target.exists() or digest(target) != files[original]):
            raise ValueError("Scoring base artifact hash mismatch: " + saved)
    return batch


def _walk_spans(trace):
    def walk(rows):
        if not isinstance(rows, list):
            raise ValueError("Native trace spans must be a list")
        for span in rows:
            if not isinstance(span, dict):
                raise ValueError("Native span must be an object")
            yield span
            yield from walk(span.get("children", []))
    yield from walk(trace.get("root_spans", []))


def _native(run, manifest, case_ids, warnings):
    config = manifest["identity"].get("agent_evaluation")
    native_manifest = _json(run, "agent/native-manifest.json")
    exists = any(_file(run, "agent/" + name).exists() for name in _NATIVE_FILES.values())
    if native_manifest is None:
        if exists:
            raise ValueError("Native artifacts have no native manifest provenance")
        if config and config.get("protocol") == NATIVE_VERSION:
            warnings.append("原生评测已配置，但 native-manifest/trace 尚未保存。")
        return {}, None
    if native_manifest.get("schema_version") != NATIVE_VERSION:
        raise ValueError("Unsupported native manifest provenance; legacy Agent scores are not migrated")
    if not isinstance(config, dict) or config.get("protocol") != NATIVE_VERSION:
        raise ValueError("Native artifacts lack frozen native evaluation identity")
    if native_manifest.get("judge") != config.get("judge") or native_manifest.get("sdk_version") != config.get("sdk_version"):
        raise ValueError("Native manifest judge/SDK differs from run identity")
    by_case = {cid: {"manifest": native_manifest, **{key: [] for key in _NATIVE_FILES}, "judge_events": []}
               for cid in case_ids}
    request_cases = {}
    def validate(row, filename):
        if row.get("schema_version") != NATIVE_VERSION or row.get("case_id") not in case_ids:
            raise ValueError("Unknown native provenance/case in " + filename)
        request = row.get("request_id")
        if not isinstance(request, str) or not request:
            raise ValueError("Native request_id missing in " + filename)
        if row.get("mode") != manifest["mode"] or row.get("judge") != native_manifest["judge"]:
            raise ValueError("Native request mode/judge mismatch in " + filename)
        if request in request_cases and request_cases[request] != row["case_id"]:
            raise ValueError("Native request_id belongs to multiple cases")
        request_cases[request] = row["case_id"]
    for key, filename in _NATIVE_FILES.items():
        path = _file(run, "agent/" + filename)
        for row in read_journal(path, warnings):
            validate(row, filename)
            by_case[row["case_id"]][key].append(row)
    for cid, native in by_case.items():
        samples, spans, score_ids, snapshots = {}, {}, set(), defaultdict(list)
        for row in native["components"]:
            request = row["request_id"]
            if row.get("record_type") == "span":
                span_id = row.get("span_id")
                if not isinstance(span_id, str) or not span_id or (request, span_id) in spans:
                    raise ValueError("Missing or duplicate native component span identity")
                spans[request, span_id] = row
            elif row.get("record_type") == "sample":
                sample_id = row.get("sample_id")
                if not isinstance(sample_id, str) or not sample_id or (request, sample_id) in samples:
                    raise ValueError("Missing or duplicate native sample identity")
                samples[request, sample_id] = row
            else:
                raise ValueError("Unknown native component record type")
        for (request, _), sample in samples.items():
            span = spans.get((request, sample.get("span_id")))
            if span is None or sample.get("span_name") != span.get("name"):
                raise ValueError("Native sample has unknown or mismatched span identity")
        for row in native["scores"]:
            status, score = row.get("status"), row.get("score")
            if status not in {"scored", "error", "not_applicable"}:
                raise ValueError("Invalid native score status")
            if status == "scored":
                if type(score) not in {int, float} or not math.isfinite(score) or not 0 <= score <= 1:
                    raise ValueError("Invalid native score value")
            elif score is not None:
                raise ValueError("Non-scored native metric must not contain a number")
            if not isinstance(row.get("metric"), str) or not row["metric"]:
                raise ValueError("Missing native metric name")
            request = row["request_id"]
            if row.get("sample_id") is not None:
                sample = samples.get((request, row["sample_id"]))
                if sample is None or any(row.get(field) != sample.get(field) for field in
                        ("span_id", "span_name", "grouping", "query_index")):
                    raise ValueError("Native score has unknown/mismatched sample association")
            elif row.get("span_id") is not None and (request, row["span_id"]) not in spans:
                raise ValueError("Native score has unknown span association")
            elif status == "scored" and row.get("grouping") != "whole_trace":
                raise ValueError("Native component score has no sample association")
            identity = (request, row.get("sample_id"), row.get("span_id"), row["metric"], row.get("grouping"))
            if identity in score_ids:
                raise ValueError("Duplicate native score identity")
            score_ids.add(identity)
        scored_samples = {(r["request_id"], r.get("sample_id")) for r in native["scores"]}
        if any(key not in scored_samples for key in samples):
            warnings.append(f"{cid}: 已保存组件样本中有未保存评分的样本；原生指标尚未完整。")
        for row in native["traces"]:
            trace = row.get("trace")
            if not isinstance(trace, dict) or row.get("phase") not in {"before_scoring", "completed", "error", "cancelled"}:
                raise ValueError("Invalid native trace snapshot")
            metadata = trace.get("metadata") or {}
            if metadata.get("request_id", row["request_id"]) != row["request_id"]:
                raise ValueError("Native trace request identity mismatch")
            ids = [span.get("uuid") or span.get("span_id") for span in _walk_spans(trace)]
            if any(not isinstance(sid, str) or not sid for sid in ids) or len(ids) != len(set(ids)):
                raise ValueError("Missing or duplicate native trace span identity")
            snapshots[row["request_id"]].append(row)
        for request, rows in snapshots.items():
            if len({r["phase"] for r in rows}) != len(rows):
                raise ValueError("Duplicate native snapshot phase for one request")
            terminal = [r for r in rows if r["phase"] != "before_scoring"]
            if len(terminal) > 1:
                raise ValueError("Conflicting final native snapshots")
            selected = terminal[0] if terminal else rows[-1]
            final_ids = {span.get("uuid") or span.get("span_id") for span in _walk_spans(selected["trace"])}
            if any(sid not in final_ids for req, sid in spans if req == request):
                raise ValueError("Native component absent from selected trace")
            for row in rows:
                row["selected_for_display"] = row is selected
        if native["components"] and not native["traces"]:
            warnings.append(f"{cid}: 原生组件已保存，但完整 trace 未采集/未保存。")
    events_path = _file(run, "judge-events.jsonl")
    if events_path.exists():
        for event in read_journal(events_path, warnings):
            cid, request = event.get("case_id"), event.get("request_id")
            if cid not in by_case or request_cases.get(request) != cid:
                warnings.append("Judge event 缺少匹配的 native case/request 身份，未归入单题。")
                continue
            by_case[cid]["judge_events"].append(event)
    return by_case, native_manifest


def _native_entry(row, run, oid, base):
    metric = row["metric"]
    scope = "trajectory" if row.get("grouping") == "whole_trace" or metric in {
        "Task Completion", "Step Efficiency", "Plan Quality", "Plan Adherence"} else (
        "retrieval" if metric == "Contextual Relevancy" else "synthesis")
    scorer = "native." + re.sub(r"[^a-z0-9]+", "_", metric.lower()).strip("_")
    fields = {k: base.get(k) for k in ("run_key", "run_id", "suite", "case_id", "task", "track", "mode",
              "output_status", "behavior", "partition", "phase", "config_family", "pairing_id")}
    return {**fields, "id": fingerprint([run["key"], row]), "observation_id": oid, "scorer": scorer,
            "scope": scope, "metric_name": metric, "status": row["status"], "score": row.get("score"),
            "reason": row.get("reason"), "judge": row["judge"], "request_id": row["request_id"],
            **{k: row[k] for k in ("sample_id", "span_id", "span_name", "grouping", "query_index") if k in row},
            "result": row, "plan": None}


def _write_detail(output, detail):
    name = "details/" + detail["id"] + ".js"
    payload = json.dumps(_redact(detail), ensure_ascii=False, allow_nan=False)
    encoded = json.dumps(payload, ensure_ascii=True).replace("<", "\\u003c").replace("&", "\\u0026")
    with (output / name).open("x", encoding="utf-8") as handle:
        os.chmod(output / name, 0o600)
        handle.write("window.SNExplorer.registerDetail(" + json.dumps(detail["id"]) + ", JSON.parse(" + encoded + "));\n")
    return name


def write_explorer_data(run_dirs, output):
    """Write lazy details into an already-created isolated report directory."""
    from .explorer_steps import build_steps, read_step_artifacts
    paths = [Path(p).resolve() for p in run_dirs]
    output = Path(output).resolve()
    if not paths or len(paths) != len(set(paths)):
        raise ValueError("Provide distinct run directories")
    if any(output.is_relative_to(p) or p.is_relative_to(output) for p in paths):
        raise ValueError("Report directory must be separate from saved runs")
    if not output.is_dir():
        raise ValueError("Report directory must already exist")
    (output / "details").mkdir(exist_ok=False)
    data = {"format": "sn-experiment-dashboard-v3", "runs": [], "entries": [], "observations": {},
            "catalog": {}, "audit": [], "graph": {"nodes": [], "edges": []}}
    seen, by_hash, batches = set(), {}, {}
    for path in paths:
        _preflight(path)
        raw_manifest = _json(path, "manifest.json")
        batch = _lineage(path, raw_manifest) if raw_manifest else None
        single = aggregate_runs([path])
        data["generated_at"] = single["generated_at"]
        data["limitations"] = single["limitations"]
        run = single["runs"][0]
        manifest = run["manifest"]
        signature = (manifest["run_id"], manifest["protocol_id"]) if manifest else (str(path), None)
        if signature in seen:
            raise ValueError("Duplicate saved run identity; copied runs must not be counted twice")
        seen.add(signature)
        checksum = digest(_file(path, "manifest.json")) if manifest else None
        source = manifest["identity"].get("source") if manifest else None
        source_id = "dataset:" + fingerprint(source) if source else "dataset:unknown:" + run["key"]
        run.update(kind="rescoring" if batch else "generation", source_id=source_id, manifest_sha256=checksum,
                   config_summary={k: manifest["identity"].get(k) for k in
                       ("models", "runtime_settings", "product_services", "notebook_context", "agent_evaluation")} if manifest else {},
                   answers_id="answers:" + (batch["origin_manifest_sha256"] if batch else checksum or run["key"]))
        if checksum:
            by_hash[checksum] = {"run": run, "path": path, "answer_hash": digest(_file(path, "outputs.jsonl"))
                                 if _file(path, "outputs.jsonl").exists() else None}
        if batch:
            batches[run["key"]] = batch
        data["runs"].append(run)
        data["catalog"].update(single["catalog"])
        for audit in single["audit"]:
            audit["warnings"] = run["warnings"]
        data["audit"].extend(single["audit"])
        if not manifest:
            continue
        grouped = defaultdict(list)
        for entry in single["entries"]:
            entry.update(scope="answer", metric_name=entry["scorer"])
            grouped[entry["observation_id"]].append(entry)
        case_ids = {rows[0]["case_id"] for rows in grouped.values()}
        native_by_case, native_manifest = _native(path, raw_manifest, case_ids, run["warnings"])
        artifacts = read_step_artifacts(path, raw_manifest, run["warnings"])
        run["native_protocol"] = native_manifest.get("schema_version") if native_manifest else None
        for oid, rows in grouped.items():
            observation = single["observations"][oid]
            cid = rows[0]["case_id"]
            native = native_by_case.get(cid, {"manifest": None, **{key: [] for key in _NATIVE_FILES}, "judge_events": []})
            source_case = observation.get("case") or {}
            question_id = "question:" + fingerprint({"source_id": source_id, "case": source_case}) if source_case else None
            product = artifacts.get("product") or {}
            corpus_id = None
            if manifest.get("product_protocol") in {"sn-notebook-benchmarks-v1", "sn-notebook-baseline-v1"} and product.get("documents") is not None and product.get("manifest"):
                corpus_id = "corpus:" + fingerprint(product.get("documents"))
            native_entries = [_native_entry(row, run, oid, rows[0]) for row in native["scores"]]
            for item in rows + native_entries:
                item.update(source_id=source_id, question_id=question_id, corpus_id=corpus_id,
                            product_protocol=manifest.get("product_protocol"),
                            evaluation_protocol=(native_manifest.get("schema_version") + ":" + fingerprint(native_manifest.get("sdk_version"))) if item["scope"] != "answer" and native_manifest else None)
                if item.get("scope") == "answer" and single["catalog"].get(item["scorer"], {}).get("method", "").startswith("LLM"):
                    judge = (manifest.get("identity") or {}).get("models", {}).get("judge")
                    if judge is not None:
                        item["judge"] = judge
            all_entries = rows + native_entries
            detail = {**observation, "id": oid, "run_key": run["key"], "case_id": cid,
                      "question_id": question_id, "corpus_id": corpus_id,
                      "entries": all_entries, "run": run, "native": native}
            detail["steps"] = build_steps(detail, artifacts)
            filename = _write_detail(output, detail)
            question = (observation.get("case") or {}).get("question") or (
                (observation.get("output") or {}).get("product_record") or {}).get("question")
            data["observations"][oid] = {"id": oid, "run_key": run["key"], "case_id": cid,
                "task": rows[0]["task"], "suite": run["suite"], "mode": run["mode"],
                "question": _preview(question), "output_status": rows[0]["output_status"], "detail_file": filename,
                "kind": run["kind"], "source_id": source_id, "answers_id": run["answers_id"],
                "question_id": question_id, "corpus_id": corpus_id,
                "product_protocol": manifest.get("product_protocol"),
                "evaluation_protocol": detail["entries"][0].get("evaluation_protocol")}
            for entry in all_entries:
                compact = {k: v for k, v in entry.items() if k not in {"plan", "result"}}
                compact["reason"] = _preview(compact.get("reason"))
                data["entries"].append(compact)
            for entry in native_entries:
                data["catalog"].setdefault(entry["scorer"], {
                    "name": entry["metric_name"], "method": "DeepEval 原生指标", "formula": "DeepEval 4.2.2 原生指标；仅有效评分计入均值",
                    "limitations": "组件按 request/sample/span 身份关联；多次调用只比较分布。", "scope": entry["scope"]})
        run["native_recorded_scores"] = sum(len(n["scores"]) for n in native_by_case.values())
        run["native_requests"] = len({row["request_id"] for n in native_by_case.values() for row in n["diagnostics"]})
        # Full case/native payloads are released on the next iteration.
    _build_graph(data, by_hash, batches)
    generations = [r for r in data["runs"] if r["kind"] == "generation"]
    status = Counter(o["output_status"] for o in data["observations"].values() if o["kind"] == "generation")
    data["summary"] = {"run_count": len(data["runs"]), "generation_run_count": len(generations),
        "rescoring_run_count": len(data["runs"]) - len(generations),
        "planned_outputs": sum(r["planned_predictions"] for r in generations),
        "saved_outputs": sum(r["saved_outputs"] for r in generations),
        "recorded_outputs": sum(r["recorded_outputs"] for r in generations),
        "successful_answers": status["success"], "prediction_status": dict(status),
        "planned_scores": len(data["entries"]), "saved_scores": sum(e["status"] != "missing" for e in data["entries"]),
        "scored": sum(e["status"] == "scored" for e in data["entries"]),
        "unique_questions": len({(o["source_id"], o["case_id"]) for o in data["observations"].values()}),
        "answer_sets": len({r["answers_id"] for r in data["runs"]}),
        "observation_count": len(data["observations"])}
    data["limitations"][1] = "生成数只统计 generation run；重评分副本不增加生成数。unique_questions 按冻结 source × case 去重；评分条目包含客观及原生组件项。"
    return _redact(data)


def _build_graph(data, by_hash, batches):
    nodes, edges = {}, []
    def node(id, kind, label, run=None, **extra):
        nodes.setdefault(id, {"id": id, "kind": kind, "label": label, "status": "recorded",
                              "source_id": run["source_id"] if run else None,
                              **({"run_key": run["key"]} if run else {}), **extra})
    def edge(source, target, kind, provenance):
        edges.append(dict(source=source, target=target, kind=kind, provenance=provenance))
    for run in data["runs"]:
        key = "run:" + run["key"]
        node(run["source_id"], "dataset", run.get("suite") or "未知来源", source_id=run["source_id"])
        node(key, "run", run["run_id"], run, status=run["phase"], operation=run["kind"])
        edge(run["source_id"], key, "source", "冻结 identity.source 的完整指纹；不按数据集名称合并。")
        answers = run["answers_id"]
        batch = batches.get(run["key"])
        if batch:
            origin = by_hash.get(batch["origin_manifest_sha256"])
            if origin:
                if origin["run"]["run_id"] != batch["origin_run_id"] or origin["answer_hash"] != batch["origin_artifacts"].get("outputs.jsonl"):
                    raise ValueError("Provided scoring origin answers/identity hash mismatch")
                origin_run = origin["run"]
                node(answers, "answers", "答卷 · " + origin_run["run_id"], origin_run, counts={
                    "planned": origin_run["planned_predictions"], "recorded": origin_run["recorded_outputs"],
                    "available": origin_run["saved_outputs"]})
            else:
                external = "external:" + batch["origin_manifest_sha256"]
                node(external, "external", "外部来源 · " + batch["origin_run_id"], source_id=run["source_id"], status="not_provided")
                node(answers, "answers", "来源答卷 · " + batch["origin_run_id"], source_id=run["source_id"], status="copied_verified")
                edge(external, answers, "saved_answers", "来源 run 未提供；核对 base-manifest 及复制答卷哈希，不读取外部目录。")
            edge(answers, key, "rescore", "origin_manifest_sha256 与 origin_artifacts.outputs.jsonl 均经哈希核实。")
        else:
            node(answers, "answers", "答卷 · " + run["run_id"], run, counts={
                "planned": run["planned_predictions"], "recorded": run["recorded_outputs"], "available": run["saved_outputs"]})
            edge(key, answers, "generate", "当前 run 的只读 outputs.jsonl；每题至多一个普通输出。")
        scores = "scores:" + run["key"]
        entries = [e for e in data["entries"] if e["run_key"] == run["key"]]
        node(scores, "scores", "评分 · " + run["run_id"], run, counts={"planned": len(entries),
             "recorded": sum(e["status"] != "missing" for e in entries), "scored": sum(e["status"] == "scored" for e in entries)})
        edge(answers, scores, "evaluate", "当前评分条目；原生组件保留独立 request/sample/span 身份。")
        edge(key, scores, "scoring_batch", "当前 run 的评分批次身份。")
    data["graph"] = {"nodes": list(nodes.values()), "edges": edges}
