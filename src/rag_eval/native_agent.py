"""Sequential, local native DeepEval evaluation of one live SN invocation.

The product callback must persist its answer before returning. SN owns observation;
this module attaches component metrics while those real spans are active. Multi-query
samples use a separate public ``evaluate`` group because one aggregate retrieval
span cannot honestly represent several independent LLMTestCases.

Only public SDK entrypoints are used. Raw checkpoints deliberately select trace
fields rather than serializing metric/model/client objects. An empty applicable
metric set produces a durable N/A record: SDK 4.2.2 raises NoMetricsError before
its report exporter, so there is no SDK score report for that case.
"""
from __future__ import annotations

from collections import Counter
from enum import Enum
from importlib import import_module
import json
import os
from pathlib import Path
import threading
from time import perf_counter
from uuid import uuid4

from .system_runtime import is_cancellation
from .native_sdk import configure_local_sdk, sdk_timeout_identity


NATIVE_TRACE_VERSION = "sn-deepeval-native-v1"


class _ObservedProductError(Exception):
    """Expose only an exception type to the SDK, preserving the original outside."""


def require_native_tracing():
    """Validate the paired contract and configure runtime-only local SDK use."""
    configure_local_sdk()
    module = import_module("app.core.evaluation_tracing")
    if getattr(module, "NATIVE_TRACE_VERSION", None) != NATIVE_TRACE_VERSION:
        raise RuntimeError("SN native trace contract mismatch; apply the paired SN patch")
    if not callable(getattr(module, "evaluation_session", None)):
        raise RuntimeError("SN native evaluation_session is unavailable")
    return module


def _json_value(value):
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, dict):
        return {k: _json_value(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(v) for v in value]
    return value


def _snapshot(node):
    fields = ("uuid", "status", "trace_uuid", "parent_uuid", "start_time", "end_time",
              "name", "metadata", "input", "output", "error", "retrieval_context", "context",
              "expected_output", "model", "input_token_count", "output_token_count",
              "top_k", "chunk_size", "available_tools", "agent_handoffs")
    result = {key: _json_value(getattr(node, key)) for key in fields if hasattr(node, key)}
    result["sdk_type"] = type(node).__name__
    for key in ("root_spans", "children"):
        if hasattr(node, key):
            result[key] = [_snapshot(child) for child in getattr(node, key)]
    return result


def _text(value):
    return value if isinstance(value, str) and value.strip() else None


def _contexts(candidates):
    if not isinstance(candidates, list):
        return []
    return [row["text"] for row in candidates if isinstance(row, dict) and _text(row.get("text"))]


class NativeAgentEvaluation:
    def __init__(self, output_dir, *, judge, judge_identity, enable_whole_trace_metrics=False, metrics=None):
        from deepeval.models import DeepEvalBaseLLM
        from .native_metrics import select_metrics, METRIC_NAMES
        if not isinstance(judge, DeepEvalBaseLLM) or not callable(getattr(judge, "for_case", None)):
            raise ValueError("Supply an explicit DeepEval judge with for_case(case_id, request_id)")
        if not isinstance(judge_identity, dict) or not judge_identity:
            raise ValueError("Public resolved judge identity is required")
        self.output_dir = Path(output_dir)
        self.output_dir.mkdir(parents=True, exist_ok=True)
        self.judge = judge
        self.judge_identity = json.loads(json.dumps(judge_identity, allow_nan=False))
        self.enable_whole_trace_metrics = enable_whole_trace_metrics
        self.metric_ids = select_metrics(metrics, trajectory=enable_whole_trace_metrics)
        self.metric_names = {METRIC_NAMES[key] for key in self.metric_ids}
        self.explicit_selection = metrics is not None
        self.has_errors = False
        self._counts = Counter()
        self._cases = []
        self._lock = threading.RLock()
        self._running = False
        self._save("native-manifest.json", {
            "schema_version": NATIVE_TRACE_VERSION, "sdk_version": "4.2.2",
            "judge": self.judge_identity, "whole_trace_metrics": enable_whole_trace_metrics,
            "selected_metrics": self.metric_ids,
            "sdk_timeout": sdk_timeout_identity(),
            "execution": "one invocation per synchronous SDK iterator; answers saved before scoring",
            "multi_query_grouping": "separate public evaluate cases linked to the genuine aggregate span",
        })

    def _append(self, filename, row):
        data = json.dumps(row, ensure_ascii=False, allow_nan=False) + "\n"
        with self._lock, (self.output_dir / filename).open("a", encoding="utf-8") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())

    def _save(self, filename, row):
        path = self.output_dir / filename
        temporary = path.with_suffix(path.suffix + ".tmp")
        with temporary.open("w", encoding="utf-8") as stream:
            json.dump(row, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)

    def _score(self, common, metric, *, status, score=None, reason=None, **extra):
        self._append("native-scores.jsonl", {**common, "metric": metric, "status": status,
                     "score": score, "reason": reason, **extra})
        self._counts[status] += 1
        if status == "error":
            self.has_errors = True

    def summary(self):
        return {"schema_version": NATIVE_TRACE_VERSION, "judge": self.judge_identity,
                "has_errors": self.has_errors, "score_status_counts": dict(self._counts),
                "cases": list(self._cases)}

    def finish(self):
        result = self.summary()
        self._save("native-summary.json", result)
        return result

    def run_case(self, *, case_id, mode, question, invoke_and_persist, session_factory, metadata=None):
        from deepeval import evaluate
        from deepeval.dataset import EvaluationDataset, Golden
        from deepeval.errors import NoMetricsError
        from deepeval.evaluate.configs import AsyncConfig, CacheConfig, DisplayConfig, ErrorConfig
        from deepeval.metrics import (AnswerRelevancyMetric, ContextualRelevancyMetric, FaithfulnessMetric,
                                      PlanAdherenceMetric, PlanQualityMetric, StepEfficiencyMetric,
                                      TaskCompletionMetric)
        from deepeval.test_case import LLMTestCase
        from deepeval.tracing import observe, trace, update_current_span, update_current_trace
        from .native_metrics import CheckpointMetric

        if not _text(case_id) or not _text(mode) or not _text(question):
            raise ValueError("Nonempty case_id, mode and question are required")
        with self._lock:
            if self._running:
                raise RuntimeError("Native evaluation is sequential; a case is already running")
            self._running = True
        request_id = uuid4().hex
        common = {"schema_version": NATIVE_TRACE_VERSION, "case_id": case_id, "mode": mode,
                  "request_id": request_id, "judge": self.judge_identity}
        state = {"case_id": case_id, "mode": mode, "request_id": request_id,
                 "status": "running", "product_seconds": None, "judge_seconds": None}
        metrics_pending = []
        separate_samples = []
        seen = Counter()
        span_names, stages, span_statuses, evidence = Counter(), Counter(), Counter(), Counter()
        explicit_plan = False
        native_trace = None
        session = None
        result = None
        case_error = None
        product_error = None
        iterator = None

        def tracked_metric(metric_type, link):
            def completed(metric, record):
                self._score(common, metric.__name__, **record)
            metric = CheckpointMetric(metric_type(model=self.judge, async_mode=False), link=link,
                on_start=lambda record: self._append('native-metric-events.jsonl',
                                                     {**common, **record, 'event': 'started'}),
                on_result=completed)
            metrics_pending.append((metric, link))
            return metric

        def configs(group):
            return {"async_config": AsyncConfig(run_async=False, max_concurrent=1),
                    "cache_config": CacheConfig(write_cache=False, use_cache=False),
                    "error_config": ErrorConfig(ignore_errors=True, skip_on_missing_params=False),
                    "display_config": DisplayConfig(show_indicator=False, print_results=False,
                         inspect_after_run=False, results_folder=str(self.output_dir / "sdk" / request_id / group))}

        def add_sample(event, *, input, output, contexts, metric_types, grouping="native_span", query_index=None):
            sample_id = uuid4().hex
            link = {"sample_id": sample_id, "span_id": event.get("span_id"),
                    "span_name": event["name"], "grouping": grouping, "query_index": query_index}
            failed = event.get("metadata", {}).get("status") in {"error", "cancelled"}
            retrieval_only = metric_types == [ContextualRelevancyMetric]
            available = _text(input) and (retrieval_only or _text(output)) and not failed
            sample = {**common, **link, "record_type": "sample", "input": input,
                      "actual_output": output, "retrieval_context": contexts,
                      "component_status": "error" if failed else "completed"}
            self._append("components.jsonl", sample)
            eligible = []
            for metric_type in metric_types:
                name = {ContextualRelevancyMetric: "Contextual Relevancy", FaithfulnessMetric: "Faithfulness",
                        AnswerRelevancyMetric: "Answer Relevancy"}[metric_type]
                if name not in self.metric_names:
                    continue
                needs_context = metric_type != AnswerRelevancyMetric
                if not available or (needs_context and not contexts):
                    reason = "component failed or cancelled" if failed else "missing question, answer or nonempty context"
                    self._score({**common, **link}, name, status="not_applicable", reason=reason)
                    continue
                metric = tracked_metric(metric_type, link)
                eligible.append(metric)
            if not available:
                return
            test_case = LLMTestCase(input=input, actual_output=output, retrieval_context=contexts or None,
                                    name=sample_id)
            if grouping == "native_span":
                # The SDK test_case setter replaces native input/output. Keep the
                # real candidate JSON as retrieval output so whole-trace metrics
                # retain source IDs/scores as well as retrieval_context text.
                update_current_span(test_case=test_case, metrics=eligible)
            elif eligible:
                separate_samples.append((test_case, eligible, sample_id))

        def on_span(event):
            nonlocal explicit_plan
            # SN catches callback exceptions and preserves them in session.errors.
            # The raw projected event is saved before deriving applicability.
            with self._lock:
                self._append("components.jsonl", {**common, "record_type": "span", **event})
                name = event["name"]
                span_names[name] += 1
                stages[event.get("stage") or "unspecified"] += 1
                span_statuses[(event.get("metadata") or {}).get("status", "unspecified")] += 1
                if name in {"sn.retrieve.chunks", "sn.retrieve.chunks_multi"} and isinstance(event.get("output"), list):
                    for row in event["output"]:
                        if isinstance(row, dict) and _text(row.get("text")):
                            evidence[("chunk", row["chunk_id"]) if row.get("chunk_id") else ("text", row["text"])] += 1
                event_input = event.get("input") or {}
                event_output = event.get("output")
                details = event.get("metadata") or {}
                if name == "sn.reasoning.initial_plan" and isinstance(event_output, dict):
                    explicit_plan = bool(event_output.get("subqueries") or event_output.get("pending_queries"))
                if name == "sn.retrieve.chunks":
                    seen["retrieval"] += 1
                    add_sample(event, input=event_input.get("query"), output=json.dumps(event_output, ensure_ascii=False),
                               contexts=_contexts(event_output), metric_types=[ContextualRelevancyMetric])
                elif name == "sn.retrieve.chunks_multi":
                    seen["retrieval"] += 1
                    mapping = details.get("per_query")
                    if not isinstance(mapping, list) or not mapping:
                        if 'Contextual Relevancy' in self.metric_names:
                            self._score(common, "Contextual Relevancy", status="not_applicable",
                                        reason="individual query/result mapping unavailable", span_name=name)
                    else:
                        for index, item in enumerate(mapping):
                            candidates = item.get("candidates")
                            add_sample(event, input=item.get("query"), output=json.dumps(candidates, ensure_ascii=False),
                                       contexts=_contexts(candidates), metric_types=[ContextualRelevancyMetric],
                                       grouping="multi_query_evaluate", query_index=index)
                elif event.get("stage") == "synthesis" and name.startswith("sn.synthesis."):
                    seen["synthesis"] += 1
                    output = event_output.get("answer") if isinstance(event_output, dict) else None
                    context = _text(details.get("context_block"))
                    add_sample(event, input=details.get("question") or event_input.get("question"), output=output,
                               contexts=[context] if context else [], metric_types=[FaithfulnessMetric, AnswerRelevancyMetric])

        def snapshot(phase):
            if native_trace is not None:
                self._append("native-traces.jsonl", {**common, "phase": phase, "trace": _snapshot(native_trace)})

        def whole_trace():
            selected = []
            valid_answer = isinstance(result, dict) and _text(result.get("answer"))
            for metric_type, name in [(TaskCompletionMetric, "Task Completion"), (StepEfficiencyMetric, "Step Efficiency"),
                                       (PlanQualityMetric, "Plan Quality"), (PlanAdherenceMetric, "Plan Adherence")]:
                if self.explicit_selection and name not in self.metric_names:
                    continue
                reason = None
                if not self.enable_whole_trace_metrics:
                    reason = "whole-trace evaluation disabled"
                elif session is not None and session.errors:
                    reason = "observation incomplete; full trajectory cannot be judged"
                elif not valid_answer or result.get("status") in {"error", "failed", "cancelled", "clarification"}:
                    reason = "successful final answer unavailable"
                elif metric_type in {PlanQualityMetric, PlanAdherenceMetric} and not (mode == "reasoning" and explicit_plan):
                    reason = "explicit reasoning plan unavailable"
                if reason:
                    self._score(common, name, status="not_applicable", reason=reason, grouping="whole_trace")
                else:
                    metric = tracked_metric(metric_type, {"grouping": "whole_trace"})
                    selected.append(metric)
            if selected:
                update_current_trace(metrics=selected)

        try:
            dataset = EvaluationDataset(goldens=[Golden(input=question, name=case_id,
                              additional_metadata={**(metadata or {}), "case_id": case_id, "mode": mode})])
            iterator = dataset.evals_iterator(identifier=request_id, **configs("trace"))
            next(iterator)
            with trace(name=f"{case_id}:{mode}", input=question, metadata={**(metadata or {}), **common}) as native_trace:
                @observe(name="benchmark.notebook_case", type="agent")
                def invoke():
                    nonlocal session, product_error
                    try:
                        with session_factory(on_span=on_span) as session:
                            answer = invoke_and_persist()
                        if not isinstance(answer, dict):
                            raise TypeError("Product callback must persist and return its full result dict")
                    except BaseException as exc:
                        product_error = exc
                        raise _ObservedProductError(type(exc).__name__) from None
                    update_current_span(input=question, output=answer.get("answer") or json.dumps(answer, ensure_ascii=False))
                    return answer
                started = perf_counter()
                try:
                    result = invoke()
                except BaseException as exc:
                    if product_error is None:
                        product_error = exc
                    raise
                finally:
                    state["product_seconds"] = perf_counter() - started
                update_current_trace(output=result.get("answer") or json.dumps(result, ensure_ascii=False))
                self._append("native-outputs.jsonl", {**common, "result": result})
                if session.errors:
                    self.has_errors = True
                    self._append("native-errors.jsonl", {**common, "phase": "observation", "errors": session.errors})
                for category, names in (("retrieval", ["Contextual Relevancy"]),
                                         ("synthesis", ["Faithfulness", "Answer Relevancy"])):
                    if not seen[category]:
                        for name in names:
                            if name in self.metric_names:
                                self._score(common, name, status="not_applicable", reason=f"no observed {category} component")
                whole_trace()
            snapshot("before_scoring")
            started = perf_counter()
            try:
                with self.judge.for_case(case_id, request_id):
                    try:
                        next(iterator)
                        raise RuntimeError("Single-case SDK iterator unexpectedly yielded another golden")
                    except StopIteration:
                        state["sdk_report_status"] = "exported"
                    except NoMetricsError:
                        state["sdk_report_status"] = "not_applicable"
                        state["sdk_report_reason"] = "SDK has no eligible native metrics; raw trace preserved"
                    for test_case, metrics, sample_id in separate_samples:
                        try:
                            evaluate(test_cases=[test_case], metrics=metrics, identifier=sample_id,
                                     **configs("multi-query-" + sample_id))
                        finally:
                            for metric in metrics:
                                metric.checkpoint()
            finally:
                state["judge_seconds"] = perf_counter() - started
            state["status"] = "completed"
        except BaseException as exc:
            case_error = product_error if product_error is not None else exc
            self.has_errors = True
            state["status"] = "cancelled" if is_cancellation(case_error) else "error"
            try:
                self._append("native-errors.jsonl", {**common, "phase": "product" if product_error else "evaluation",
                                                   "error_type": type(case_error).__name__})
            except Exception as artifact_error:
                state.setdefault("cleanup_errors", []).append({"phase": "error_export", "error_type": type(artifact_error).__name__})
        finally:
            def cleanup(action, phase):
                nonlocal case_error
                try:
                    action()
                except Exception as exc:
                    self.has_errors = True
                    state.setdefault("cleanup_errors", []).append({"phase": phase, "error_type": type(exc).__name__})
                    if case_error is None:
                        case_error = exc

            try:
                if iterator is not None:
                    cleanup(iterator.close, "iterator_close")
                cleanup(lambda: snapshot(state["status"]), "final_snapshot")
                for metric, link in metrics_pending:
                    cleanup(metric.checkpoint, "score_export")

                def count_spans(spans):
                    return sum(1 + count_spans(span.children) for span in spans)
                diagnostics = {**common, "status": state["status"], "callback_count": sum(span_names.values()),
                               "span_name_counts": dict(span_names), "stage_counts": dict(stages),
                               "span_status_counts": dict(span_statuses),
                               "sdk_span_count": count_spans(native_trace.root_spans) if native_trace else 0,
                               "observation_errors": session.errors if session else [],
                               "evidence_unique_count": len(evidence),
                               "evidence_repeat_count": sum(count - 1 for count in evidence.values()),
                               "evidence_repeat_definition": "repeated chunk ID (text when ID missing) across chunk retrieval callback outputs",
                               "product_seconds": state["product_seconds"], "judge_seconds": state["judge_seconds"]}
                cleanup(lambda: self._append("native-diagnostics.jsonl", diagnostics), "diagnostics_export")
                self._cases.append(state)
                cleanup(self.finish, "summary_export")
            finally:
                self._running = False
        if case_error is not None and (product_error is not None or result is None or is_cancellation(case_error) or not isinstance(case_error, Exception)):
            raise case_error
        return result
