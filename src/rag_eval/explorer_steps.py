"""Logical browsing stages derived only from frozen inputs and saved observations."""
from __future__ import annotations

from .artifacts import digest
from .explorer_artifacts import _file, _json, _relative
from .starter_report import read_journal


def read_step_artifacts(run, manifest, warnings):
    product = _json(run, "product-bundle.json") or {}
    mapping = _json(run, "product-artifacts/document-map.json")
    state = _json(run, "product-artifacts/state.json")
    partitions = read_journal(_file(run, "input/partitions.jsonl"), []) if _file(run, "input/partitions.jsonl").exists() else []
    # Snapshots are references, never arbitrary source-file contents. Hashes are
    # verified when the saved snapshot exists; absent old snapshots are disclosed.
    code = {}
    for name, checksum in manifest["identity"].get("code", {}).get("benchmark_source_hashes", {}).items():
        _relative(name)
        saved = _file(run, "source/" + name)
        if saved.exists():
            if digest(saved) != checksum:
                raise ValueError("Saved source code hash mismatch: " + name)
            code[name] = "source/" + name
    return dict(product=product, mapping=mapping, state=state, partitions=partitions,
                files={name for name in ("input/cases.jsonl", "input/manifest.json", "input/raw-data",
                    "input/partitions.jsonl", "product-bundle.json", "product-artifacts/document-map.json",
                    "product-artifacts/state.json", "outputs.jsonl", "scores.jsonl", "planned.jsonl",
                    "agent/components.jsonl", "agent/native-scores.jsonl", "agent/native-traces.jsonl",
                    "model-events.jsonl", "judge-events.jsonl") if _file(run, name).exists()}, code=code)


def build_steps(detail, artifacts):
    case, output = detail.get("case"), detail.get("output")
    run = detail["run"]
    manifest = run["manifest"]
    identity = manifest["identity"]
    product = artifacts["product"]
    record = (output or {}).get("product_record") or {}
    native = detail["native"]
    cid = detail["case_id"]
    question = next((q for q in product.get("questions", []) if q.get("case_id") == cid), None)
    # The importer only receives documents; never pass product question gold or
    # annotation fields into the import/retrieval/synthesis representation.
    documents = product.get("documents")
    partitions = [p for p in artifacts["partitions"] if cid in p.get("case_ids", [])]
    components = [c for c in native["components"] if c.get("record_type") == "span"]
    retrieves = [c for c in components if c.get("name", "").startswith("sn.retrieve.")]
    syntheses = [c for c in components if c.get("stage") == "synthesis"]
    baseline = run["mode"] == "bm25"
    steps = []
    def add(id, title, status, description, input, output, files, code, fields):
        steps.append(dict(id=id, title=title, status=status, description=description, input=input, output=output,
            artifacts=[f for f in files if f in artifacts["files"]],
            code=[artifacts["code"].get(f, f) for f in code], fields=fields,
            code_note="source/ 路径表示已核验的保存快照；其他路径仅为当前代码入口，不声称历史实现完全相同。"))
    add("source", "原始题目与来源", "recorded" if case else "missing",
        "冻结原题、参考答案与来源身份；gold 仅供评测，不表示进入 SN 上下文。", identity.get("source"), case,
        ["input/raw-data", "input/manifest.json", "input/cases.jsonl"], ["src/rag_eval/notebook_data.py"],
        {"raw_row": "保存的原始数据行", "references": "参考答案，仅用于评测", "question": "原始问题"})
    adapted = {k: question[k] for k in ("case_id", "original_question", "question", "request_revision", "material_document_ids") if k in question} if question else None
    add("normalize", "适配与提问", "recorded" if adapted else "missing",
        "保存的规范化 case 与请求模板；这是静态协议证明，不是一次运行观测。", (case or {}).get("question"), adapted,
        ["input/cases.jsonl", "product-bundle.json"], ["src/rag_eval/notebook_data.py", "src/rag_eval/notebook_bundle.py"],
        {"question": "包装后的请求问题，不包含参考答案", "request_revision": "提问模板版本，缺失时按原运行协议解释"})
    add("partition", "资料分区", "recorded" if partitions or product.get("manifest") else "missing",
        "按冻结分区选完整会议/论文/候选资料；没有保存分区时不推测范围。", identity.get("notebook_context"),
        {"partitions": partitions, "product_manifest": product.get("manifest"), "documents": documents} if partitions or documents else None,
        ["input/partitions.jsonl", "product-bundle.json"], ["src/rag_eval/notebook_bundle.py"],
        {"case_ids": "同一不可拆分资料分区的题目", "documents": "该分区实际冻结的完整资料"})
    imported = artifacts["mapping"] is not None or artifacts["state"] is not None
    add("import", "资料准备 / 导入", "recorded" if baseline and documents else "observed" if imported else "missing",
        "BM25 读取冻结会议发言，不执行 SN 导入。" if baseline else "仅保存的导入映射/状态作为执行观测；冻结 documents 本身不证明已导入。",
        documents, {"document_map": artifacts["mapping"], "state": artifacts["state"]} if imported else None,
        ["product-bundle.json", "product-artifacts/document-map.json", "product-artifacts/state.json"],
        ["src/rag_eval/notebook_baseline.py" if baseline else "src/rag_eval/benchmark_runtime.py"],
        {"document_map": "公开资料 ID 到 SN source/chunk ID 的已保存映射", "state": "准备状态，不是质量评分"})
    retrieval_keys = ("retrieval_context", "retrieval_turn_ids", "retrieval_scores", "retrieved_document_ids", "context_supported", "context_block")
    retrieval_saved = {k: record[k] for k in retrieval_keys if k in record}
    retrieval_saved.update({k: output[k] for k in ("retrieval_turn_ids", "retrieval_scores", "retrieval") if output and k in output})
    retrieval_saved.update({k: record[k] for k in ("retrieval", "retrieval_scores") if k in record})
    add("retrieve", "检索", "observed" if retrieves or retrieval_saved else "missing",
        "优先显示真实检索 span；旧上下文快照只证明保存了最终上下文，不能还原每次检索排名。空列表保留为空检索观测。",
        [r.get("input") for r in retrieves] if retrieves else record.get("question"),
        {"spans": retrieves, "saved_context": retrieval_saved} if retrieves or retrieval_saved else None,
        ["agent/components.jsonl", "agent/native-traces.jsonl", "outputs.jsonl"],
        ["src/rag_eval/notebook_baseline.py" if baseline else "src/rag_eval/system_runtime.py"],
        {"spans": "原生真实检索动作；request_id/span_id 唯一定位", "retrieval_context": "已保存的上下文，不自动等同于完整检索过程"})
    generation_events = [e for e in detail["events"] if e.get("role") in {"tested", "generation"}]
    prompt = record.get("prompt") or (output or {}).get("prompt")
    saved_generation = {k: record[k] for k in ("prompt", "prediction") if k in record}
    if prompt is not None:
        saved_generation["prompt"] = prompt
        saved_generation["answer"] = record.get("answer", (output or {}).get("prediction"))
    # A saved final answer belongs to the answer stage; without a prompt/event
    # it does not prove that a synthesis call was observed.
    add("synthesize", "回答生成", "observed" if syntheses or generation_events or saved_generation else "missing",
        "reasoning 可能多次分节生成；只展示已采集的 synthesis span 或显式生成调用。最终答案本身不证明内部调用已保存。",
        [s.get("input") for s in syntheses] if syntheses else None,
        {"spans": syntheses, "model_events": generation_events, "saved_invocation": saved_generation} if syntheses or generation_events or saved_generation else None,
        ["agent/components.jsonl", "model-events.jsonl", "outputs.jsonl"], ["src/rag_eval/system_capture.py", "src/rag_eval/native_agent.py"],
        {"context_block": "该次 synthesis 实际保存的上下文", "actual_output": "该组件的回答，不一定是最终整题答案"})
    add("answer", "保存答卷", "observed" if output else "missing",
        "普通输出是整题答卷；成功、澄清、错误和未保存分别保留。重评分复用已保存答卷。",
        record.get("question"), output, ["outputs.jsonl"], ["src/rag_eval/notebook_runner.py"],
        {"output_available": "非空输出可用性，不等同于回答成功", "status": "执行/回答状态", "prediction": "保存的最终正文"})
    saved_scores = any(e["status"] != "missing" for e in detail["entries"])
    add("score", "评分", "observed" if saved_scores else "recorded" if detail["entries"] else "missing",
        "客观指标和原生组件/轨迹指标分别计数；missing、N/A、error 不转换为零。组件按实际 sample/span/request 关联。",
        {"references": (case or {}).get("references"), "native_components": [c for c in native["components"] if c.get("record_type") == "sample"]},
        {"entries": detail["entries"], "judge_events": native["judge_events"]},
        ["planned.jsonl", "scores.jsonl", "agent/native-scores.jsonl", "judge-events.jsonl"],
        ["src/rag_eval/notebook_scoring.py", "src/rag_eval/native_agent.py"],
        {"score": "只有 scored 项有数值；0 是有效零分", "sample_id": "组件样本唯一身份", "span_id": "真实执行 span 身份", "request_id": "本次调用身份"})
    return steps
