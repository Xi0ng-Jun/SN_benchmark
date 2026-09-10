"""Build the immutable identity of the public benchmark judge protocol."""
import hashlib
import importlib.metadata
import json

from .artifacts import digest


JUDGE_SETTINGS = (
    "OPENAI_COMPAT_TIMEOUT_SECONDS",
    "OPENAI_COMPAT_MAX_RETRIES",
    "OPENAI_COMPAT_MAX_TOKENS",
    "ANSWER_MAX_TOKENS",
    "MODEL_JSON_REPAIR_MODE",
    "LLM_CACHE_ENABLED",
)
BENCHMARK_FILES = (
    "src/rag_eval/quality_metrics.py",
    "src/rag_eval/benchmark_judge.py",
    "src/rag_eval/cases.py",
)
PRODUCT_FILES = (
    "backend/app/core/config.py",
    "backend/app/core/llm.py",
    "backend/app/core/model_json.py",
    "backend/app/services/model_provider.py",
    "backend/app/services/model_registry.py",
)


def _hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def build_scoring_identity(root, project, model_config, *, settings, versions=None):
    service_name = model_config["bindings"]["ask_answer"]
    if versions is None:
        versions = {name: importlib.metadata.version(name)
                    for name in ("deepeval", "pydantic", "openai", "httpx",
                                 "json-repair")}
    benchmark_sources = {name: digest(root / name) for name in BENCHMARK_FILES}
    product_sources = {name: digest(project / name) for name in PRODUCT_FILES}
    judge_settings = {name: settings.get(name) for name in JUDGE_SETTINGS}
    protocol = {
        "benchmark_sources": benchmark_sources,
        "product_sources": product_sources,
        "versions": versions,
        "service": model_config["services"][service_name],
        "thinking": model_config.get("thinking", {}).get("ask_answer"),
        "settings_sha256": _hash(judge_settings),
    }
    return {
        "judge_protocol_sha256": _hash(protocol),
        "judge_source_hashes": benchmark_sources,
        "judge_product_source_hashes": product_sources,
        "judge_versions": versions,
        "judge_settings_sha256": protocol["settings_sha256"],
    }
