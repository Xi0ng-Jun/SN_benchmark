"""Runtime setup used only by the explicit execution command, never reports."""
from __future__ import annotations

from importlib.metadata import distributions
import json
import math
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

from .artifacts import digest, save_json
from .starter_protocol import fingerprint, require_text


def resolve_models(path, required_roles):
    """Resolve explicit environment references without importing a client."""
    config = json.loads(Path(path).read_text(encoding="utf-8"))
    if set(config) - {"tested", "judge"}:
        raise ValueError("Model configuration accepts only tested/judge roles")
    resolved, public = {}, {}
    for role in required_roles:
        entry = config.get(role)
        if not isinstance(entry, dict) or set(entry) != {"model_id", "base_url_env", "api_key_env", "parameters"}:
            raise ValueError(f"Explicit {role} model configuration is required")
        model_id = require_text(entry["model_id"], "model_id")
        for field in ("base_url_env", "api_key_env"):
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", entry[field]):
                raise ValueError("Model endpoint/credential must use an environment variable name")
        base_url = require_text(os.environ.get(entry["base_url_env"]), "configured model endpoint")
        api_key = require_text(os.environ.get(entry["api_key_env"]), "configured model credential")
        parameters = entry["parameters"]
        required = {"temperature", "top_p", "max_tokens", "max_retries", "timeout"}
        if not isinstance(parameters, dict) or not required <= parameters.keys() or set(parameters) - required - {"thinking_mode"}:
            raise ValueError("Explicit sampling, token limit, timeout and retries required")
        for key in required:
            value = parameters[key]
            if type(value) not in (int, float) or not math.isfinite(value):
                raise ValueError("Model parameters must be finite numeric values")
        if (parameters["temperature"] < 0 or not 0 < parameters["top_p"] <= 1
                or parameters["timeout"] <= 0 or parameters["max_tokens"] <= 0
                or parameters["max_retries"] < 0
                or any(type(parameters[k]) is not int for k in ("max_tokens", "max_retries", "timeout"))):
            raise ValueError("Invalid model parameter range")
        if parameters.get("thinking_mode") not in (None, "enabled", "disabled"):
            raise ValueError("Invalid thinking mode")
        identity = {"model_id": model_id, "endpoint_sha256": fingerprint(base_url),
                    "parameters": parameters, "adapter": "ExplicitBenchmarkModel"}
        public[role] = {**identity, "config_sha256": fingerprint(identity)}
        resolved[role] = {**public[role], "base_url": base_url, "api_key": api_key}
    return resolved, public


def snapshot_sources(root, project, run):
    revision = subprocess.check_output(["git", "-C", str(project), "rev-parse", "HEAD"], text=True).strip()
    changed = subprocess.check_output(["git", "-C", str(project), "diff", "HEAD", "--name-only"], text=True)
    if changed.strip():
        raise ValueError("Product tracked files differ from HEAD; use a reviewed clean product snapshot")
    hashes = {}
    for folder in ("src/rag_eval", "scripts"):
        for source in sorted((root / folder).rglob("*.py")):
            relative = source.relative_to(root)
            destination = run / "source" / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
            hashes[str(relative)] = digest(destination)
    shutil.copyfile(root / "pyproject.toml", run / "source/pyproject.toml")
    hashes["pyproject.toml"] = digest(run / "source/pyproject.toml")
    with (run / "product-source.tar").open("xb") as handle:
        subprocess.run(["git", "-C", str(project), "archive", revision], stdout=handle, check=True)
    identity = {"product_revision": revision, "product_archive_sha256": digest(run / "product-source.tar"),
                "benchmark_source_hashes": hashes,
                "versions": {d.metadata["Name"]: d.version for d in distributions()}}
    save_json(run / "source-identity.json", identity)
    return identity


def configure_environment(project, run, *, product_track, document_limit=40):
    """Process-global setup: caller must use one fresh CLI process per cell."""
    if type(document_limit) is not int or document_limit < 1:
        raise ValueError("Document capacity must be a positive integer")
    private = run / "runtime"
    private.mkdir(mode=0o700)
    overrides = {
        "DATABASE_URL": "sqlite:///" + str(private / "database.db"),
        "SHADOW_DATABASE_URL": "",
        "SILICON_NOTEBOOK_STORAGE_DIR": str(private / "storage"),
        "LLM_CACHE_PATH": str(private / "llm-cache.db"),
        "EVENT_LOG_DIR": str(private / "logs"), "LLM_LOG_PATH": str(private / "logs/llm.jsonl"),
        "LLM_CACHE_ENABLED": "false", "LLM_LOG_ENABLED": "true", "USER_UPLOAD_DOCUMENT_LIMIT": str(document_limit),
        "AGENT_PROFILE_ENABLED": "false", "USER_SEARCH_PROFILE_ENABLED": "false",
        "RETRIEVAL_EXPERIENCE_ENABLED": "false", "RETRIEVAL_EXPERIENCE_INJECT_ENABLED": "false",
        "REASONING_CONSULT_MEMORY_ENABLED": "false", "GENERATED_QUESTION_INDEX_MODE": "off",
        "CHUNK_KG_OVERLAY_ENABLED": "false", "KG_AUTO_EXTRACT": "false",
        "DEEPEVAL_DISABLE_DOTENV": "1", "DEEPEVAL_TELEMETRY_OPT_OUT": "YES",
    }
    service_hash = None
    if product_track:
        # The registry may hot-reload its file. Pin it inside this private runtime.
        service = private / "model-services.toml"
        source = project / ".local/model-services.toml"
        descriptor = os.open(service, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(source.read_bytes())
        service_hash = digest(service)
        overrides["MODEL_SERVICES_CONFIG"] = str(service)
    os.environ.update(overrides)
    sys.path.insert(0, str(project / "backend"))
    os.chdir(run)
    from app.core.config import Settings
    # Product settings are read-only input; N uses no production dotenv defaults.
    settings = Settings(_env_file=project / ".env" if product_track else None)
    for field in ("storage_dir", "llm_cache_path", "event_log_dir", "llm_log_path"):
        if not Path(getattr(settings, field)).resolve().is_relative_to(private):
            raise ValueError("Runtime path isolation failed: " + field)
    for field in ("llm_cache_enabled", "agent_profile_enabled", "user_search_profile_enabled",
                  "retrieval_experience_enabled", "retrieval_experience_inject_enabled",
                  "reasoning_consult_memory_enabled"):
        if getattr(settings, field) is not False:
            raise ValueError("Runtime isolation flag not applied: " + field)
    if settings.shadow_database_url is not None:
        raise ValueError("Shadow database must be disabled in benchmark runtime")
    if product_track and Path(settings.model_services_config).resolve() != private / "model-services.toml":
        raise ValueError("Product service configuration was not pinned")
    values = settings.model_dump(mode="json")
    # Normalize only this run's absolute path for comparing chunk/reasoning cells.
    normalized = json.loads(json.dumps(values, sort_keys=True).replace(str(run), "<RUN>"))
    identity = {"settings_sha256": fingerprint(values), "comparable_settings_sha256": fingerprint(normalized),
                "service_config_sha256": service_hash, "overrides": overrides}
    save_json(run / "runtime-identity.json", identity)
    return settings, identity


def make_adapter(spec, role, settings, sink):
    from app.core.llm import OpenAICompatibleClient
    from .starter_model import ExplicitBenchmarkModel
    params = spec["parameters"]
    # Explicit init overrides prevent inherited environment defaults changing budgets.
    model_settings = settings.model_copy(update={
        "openai_compat_timeout_seconds": params["timeout"],
        "openai_compat_max_retries": params["max_retries"],
        "openai_compat_max_tokens": params["max_tokens"], "llm_cache_enabled": False,
    })
    client = OpenAICompatibleClient(model_settings, base_url=spec["base_url"],
                                    api_key=spec["api_key"], model=spec["model_id"],
                                    max_retries=params["max_retries"])
    return ExplicitBenchmarkModel(client, model_id=spec["model_id"], role=role,
                                  parameters=params, config_sha256=spec["config_sha256"], sink=sink)
