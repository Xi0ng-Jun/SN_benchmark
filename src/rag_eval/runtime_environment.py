"""Runtime setup used only by the explicit execution command, never reports."""
from __future__ import annotations

from importlib.metadata import distributions
from dataclasses import dataclass
import hashlib
import importlib
import json
import math
import os
from pathlib import Path
from pathlib import PurePosixPath
import re
import shutil
import subprocess
import sys
import tarfile
import tempfile

from .artifacts import digest, save_json
from .identity import fingerprint, require_text


@dataclass(frozen=True)
class MaterializedSNSource:
    """A validated, private filesystem view of one captured SN source object."""

    root: Path
    backend_root: Path
    artifact_ref: dict
    source_manifest_sha256: str
    actual_manifest: dict[str, dict[str, int | str]]


def materialize_captured_sn_source(store, ref, destination) -> MaterializedSNSource:
    """Safely materialize an E1 ``sn`` object for a fresh import process.

    Only source-capture objects with an exact byte manifest are accepted. The
    archive is validated before its directory is atomically made visible; tar
    links, special files, aliases and duplicate members are rejected. This
    binds the loader to captured bytes while leaving dependency/config/model
    resolution outside this helper's guarantee.
    """
    from .artifact_store import ArtifactStore

    if not isinstance(store, ArtifactStore):
        raise ValueError("A shared ArtifactStore is required")
    if not isinstance(ref, dict) or ref.get("kind") != "sn":
        raise ValueError("Captured source reference must be an sn artifact")
    destination = Path(destination).absolute()
    # Check before mkdir: a linked ancestor must not redirect even parent creation.
    # This rejects observed links; it is not a lock against concurrent renames.
    if any(path.is_symlink() for path in (destination, *destination.parents)):
        raise ValueError('Materialized source destination contains a symlink ancestor')
    if destination.exists() or destination.is_symlink():
        raise ValueError("Materialized source destination already exists")
    parent = destination.parent
    parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    if parent.is_symlink():
        raise ValueError("Materialized source parent cannot be a symlink")

    object_path = store.resolve(ref, files=["product-source.tar"])
    object_json = json.loads((object_path / "object.json").read_text(encoding="utf-8"))
    identity = object_json["identity"]
    from .source_identity import captured_source_manifest
    expected = captured_source_manifest(identity)

    temporary = Path(tempfile.mkdtemp(prefix=".sn-source-", dir=parent))
    seen: set[str] = set()
    observed: dict[str, dict[str, int | str]] = {}
    try:
        archive_path = object_path / "product-source.tar"
        with tarfile.open(archive_path, mode="r:") as archive:
            for member in archive.getmembers():
                raw_name = member.name
                pure = PurePosixPath(raw_name)
                if (not raw_name or pure.is_absolute() or raw_name != pure.as_posix()
                        or any(part in {"", ".", ".."} for part in pure.parts)):
                    raise ValueError("Archive contains an unsafe path member")
                name = pure.as_posix()
                if name in seen:
                    raise ValueError("Archive contains a duplicate member")
                seen.add(name)
                target = temporary / Path(*pure.parts)
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True, mode=0o700)
                    continue
                if not member.isreg():
                    raise ValueError("Archive contains a link or special member")
                source = archive.extractfile(member)
                if source is None:
                    raise ValueError("Archive member has no readable bytes")
                data = source.read()
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                if target.parent.is_symlink() or target.is_symlink():
                    raise ValueError("Archive extraction encountered a symlink")
                target.write_bytes(data)
                os.chmod(target, 0o600)
                observed[name] = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
        if observed != expected:
            raise ValueError("Archive bytes do not match captured manifest")
        backend_root = temporary / "backend"
        if not backend_root.is_dir() or backend_root.is_symlink():
            raise ValueError("Captured source has no backend root")
        temporary.rename(destination)
        return MaterializedSNSource(
            root=destination,
            backend_root=destination / "backend",
            artifact_ref=dict(ref),
            source_manifest_sha256=identity["source_manifest_sha256"],
            actual_manifest=expected,
        )
    except (OSError, tarfile.TarError, UnicodeError) as exc:
        raise ValueError("Unable to materialize captured source archive") from exc
    finally:
        if temporary.exists():
            shutil.rmtree(temporary)


def module_source_identity(module_name, materialized: MaterializedSNSource, *, expected_manifest=None):
    """Return the loaded module's relative origin and exact captured bytes."""
    if not isinstance(module_name, str) or not module_name:
        raise ValueError("Module name is required")
    if not isinstance(materialized, MaterializedSNSource):
        raise ValueError("A materialized source is required")
    importlib.invalidate_caches()
    module = sys.modules.get(module_name) or importlib.import_module(module_name)
    origin = getattr(module, "__file__", None)
    if not origin:
        raise ValueError("Loaded module has no filesystem origin")
    origin = Path(origin).resolve()
    root = materialized.root.resolve()
    if not origin.is_relative_to(root):
        raise ValueError("Loaded module origin escaped materialized source")
    relative = origin.relative_to(root).as_posix()
    data = origin.read_bytes()
    actual = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    manifest = materialized.actual_manifest if expected_manifest is None else expected_manifest
    expected = manifest.get(relative)
    if expected != actual:
        raise ValueError("Loaded module bytes differ from captured manifest")
    return {"module": module_name, "origin": relative, **actual}


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


def snapshot_sources(root, project, run, *, artifact_root=None, captured_source_ref=None):
    if captured_source_ref is not None:
        if artifact_root is None:
            raise ValueError("Captured source references require a shared ArtifactStore")
        return _shared_snapshot_captured_sources(
            Path(root), Path(project), Path(run), artifact_root, captured_source_ref,
        )
    if artifact_root is not None:
        return _shared_snapshot_sources(Path(root), Path(project), Path(run), artifact_root)
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


def _require_dotenv_binding(config, intended):
    """Check the default consumed by both Settings and SN's registry reader."""
    if not isinstance(config, dict):
        raise ValueError('Captured Settings has no supported dotenv configuration')
    configured = config.get('env_file')
    paths = configured if isinstance(configured, (list, tuple)) else [configured]
    if intended is None:
        valid = all(path is None or path == '' for path in paths)
    else:
        valid = (len(paths) == 1 and isinstance(paths[0], (str, Path))
                 and bool(str(paths[0])) and Path(paths[0]).resolve() == intended.resolve())
    if not valid:
        raise ValueError('Captured Settings dotenv default does not match private snapshot')


def configure_environment(project, run, *, product_track, document_limit=40, model_config=None,
                          source_root=None, source_identity=None, dependency_requirements=None,
                          expected_interpreter=None):
    """Process-global setup: caller must use one fresh CLI process per cell."""
    if type(document_limit) is not int or document_limit < 1:
        raise ValueError("Document capacity must be a positive integer")
    if ((dependency_requirements is None) != (expected_interpreter is None)
            or (dependency_requirements is not None and source_root is None)):
        raise ValueError('Startup dependency checks require captured source and explicit interpreter/requirements')
    declaration_bytes = None
    declarations = None
    if dependency_requirements is not None:
        from .runtime_dependency_identity import parse_dependency_requirements
        # This explicit declaration input is captured independently of E1's app bytes.
        declaration_bytes = Path(dependency_requirements).read_bytes()
        declarations = parse_dependency_requirements(declaration_bytes)
    private = run / "runtime"
    private.mkdir(mode=0o700)
    if declaration_bytes is not None:
        descriptor = os.open(run / 'dependency-requirements.txt', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, 'wb') as target:
            target.write(declaration_bytes)
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
        source = Path(model_config) if model_config is not None else project / ".local/model-services.toml"
        descriptor = os.open(service, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as target:
            target.write(source.read_bytes())
        service_hash = digest(service)
        overrides["MODEL_SERVICES_CONFIG"] = str(service)
    dotenv_file = project / ".env" if product_track else None
    dotenv_snapshot = None
    if source_root is not None:
        # SN's registry reads Settings.model_config again, independently of the
        # instance's _env_file argument. Set the import-time default and that
        # explicit argument to the same private bytes before loading Settings.
        dotenv_file = None
        dotenv_snapshot = {"status": "absent" if product_track else "disabled", "sha256": None}
        if product_track:
            try:
                dotenv_bytes = (project / ".env").read_bytes()
            except FileNotFoundError:
                pass
            else:
                dotenv_file = private / "settings.env"
                descriptor = os.open(dotenv_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                with os.fdopen(descriptor, "wb") as target:
                    target.write(dotenv_bytes)
                dotenv_snapshot = {"status": "captured", "sha256": hashlib.sha256(dotenv_bytes).hexdigest()}
        # An explicit empty override also disables default dotenv discovery.
        overrides["SILICON_NOTEBOOK_ENV_FILE"] = str(dotenv_file) if dotenv_file is not None else ""
    os.environ.update(overrides)
    if source_root is None:
        import_root = project / "backend"
    else:
        import_root = Path(source_root).resolve() / "backend"
        if not import_root.is_dir() or import_root.is_symlink():
            raise ValueError("Materialized source backend is unavailable")
        # An explicit source binding is a fresh-process contract. Remove old
        # app modules so a previous in-process execution cannot mask it.
        for name in list(sys.modules):
            if name == "app" or name.startswith("app."):
                del sys.modules[name]
    sys.path.insert(0, str(import_root))
    os.chdir(run)
    from app.core.config import Settings
    if source_root is not None:
        _require_dotenv_binding(getattr(Settings, 'model_config', None), dotenv_file)
    # Product settings are read-only input; reference/judge-only runs skip product dotenv defaults.
    settings = Settings(_env_file=dotenv_file)
    if source_root is not None:
        # Construction may change class defaults while keeping an instance copy.
        # Both subsequent default construction and this instance must stay bound.
        _require_dotenv_binding(getattr(Settings, 'model_config', None), dotenv_file)
        _require_dotenv_binding(getattr(settings, 'model_config', None), dotenv_file)
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
    if source_root is not None:
        module = sys.modules.get("app.core.config")
        source_identity = _loaded_source_identity(module, Path(source_root), source_identity)
    # Normalize only this run's absolute path for comparing chunk/reasoning cells.
    normalized = json.loads(json.dumps(values, sort_keys=True).replace(str(run), "<RUN>"))
    identity = {"settings_sha256": fingerprint(values), "comparable_settings_sha256": fingerprint(normalized),
                "service_config_sha256": service_hash, "overrides": overrides}
    if source_root is not None:
        identity["source_module"] = source_identity
        identity["dotenv_snapshot"] = dotenv_snapshot
    if declarations is not None:
        from .runtime_dependency_identity import observe_startup_dependencies
        # The current runner process owns this observation, after its Settings load
        # and before the caller constructs any repository or starts ingestion.
        identity['startup_dependencies'] = observe_startup_dependencies(
            declarations, expected_interpreter=expected_interpreter)
    save_json(run / "runtime-identity.json", identity)
    return settings, identity


def _loaded_source_identity(module, source_root: Path, source_identity):
    if module is None or not getattr(module, "__file__", None):
        raise ValueError("Captured source module was not loaded")
    origin = Path(module.__file__).resolve()
    root = source_root.resolve()
    if not origin.is_relative_to(root):
        raise ValueError("Loaded module did not come from captured source")
    relative = origin.relative_to(root).as_posix()
    data = origin.read_bytes()
    actual = {"sha256": hashlib.sha256(data).hexdigest(), "size": len(data)}
    expected_manifest = (source_identity or {}).get("actual_manifest", {})
    expected = expected_manifest.get(relative)
    if expected != actual:
        raise ValueError("Loaded source module differs from captured manifest")
    return {"module": "app.core.config", "origin": relative, **actual}


def initialize_captured_model_services(settings, run, *, source_identity):
    """Load the normal product provider before repository/ingestion side effects.

    Settings and TOML already refer to this run's private snapshots. The native
    loader owns credential resolution and strict complete-catalog validation;
    this observer never reads credentials or probes an upstream service.
    """
    from app.core.event_logging import EventLogger
    from app.services.model_provider import RuntimeModelProvider
    from app.services.model_registry import WORKLOADS
    from .runtime_model_identity import loaded_model_identity

    run = Path(run).resolve()
    service_file = Path(settings.model_services_config).resolve()
    if service_file != run / 'runtime/model-services.toml':
        raise ValueError('Model provider requires the private service snapshot')
    source_root = run / 'captured-source'
    modules = []
    for name, module in sorted(sys.modules.items()):
        if (name == 'app' or name.startswith('app.')) and getattr(module, '__file__', None):
            item = _loaded_source_identity(module, source_root, source_identity)
            modules.append({**item, 'module': name})
    declared_digest = digest(service_file)
    provider = RuntimeModelProvider(settings, EventLogger(settings, channel='events', per_user=True))
    try:
        if provider._owns_registry is not True:
            raise ValueError('Captured model provider must own its normal registry')
        if digest(service_file) != declared_digest:
            raise ValueError('Private service declaration changed during provider startup')
        observation = loaded_model_identity(settings, provider, workloads=WORKLOADS,
                                            declared_config_sha256=declared_digest)
        return provider, {'observation': observation, 'source_modules': modules}
    except BaseException:
        provider.close()
        raise


def make_adapter(spec, role, settings, sink):
    from app.core.llm import OpenAICompatibleClient
    from .model_adapter import ExplicitBenchmarkModel
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


def _shared_snapshot_sources(root, project, run, artifact_root):
    """Store exact evaluator bytes and one deterministic git archive per clean SN tree."""
    from .artifact_store import ArtifactStore, atomic_pointer, read_run_refs, write_run_refs
    import tempfile
    revision = subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD'], text=True).strip()
    changed = subprocess.check_output(['git', '-C', str(project), 'diff', 'HEAD', '--name-only'], text=True)
    if changed.strip():
        raise ValueError('Product tracked files differ from HEAD; use a reviewed clean product snapshot')
    tree = subprocess.check_output(['git', '-C', str(project), 'rev-parse', 'HEAD^{tree}'], text=True).strip()
    store = ArtifactStore(artifact_root)
    paths = {str(p.relative_to(root)): p for folder in ('src/rag_eval', 'scripts')
             for p in sorted((root / folder).rglob('*.py'))}
    paths['pyproject.toml'] = root / 'pyproject.toml'
    hashes = {name: digest(path) for name, path in paths.items()}
    evaluator = store.install_files('evaluator', paths, identity={'benchmark_source_hashes': hashes})
    product_identity = {'product_revision': revision, 'tree': tree}
    pointer = store.root / 'source-indexes' / (fingerprint(product_identity) + '.json')
    if pointer.is_file() and not pointer.is_symlink():
        product = json.loads(pointer.read_text())
        source = store.resolve(product)
        obj = json.loads((source / 'object.json').read_text())
        if obj['identity'] != product_identity or set(obj['files']) != {'product-source.tar'}:
            raise ValueError('Product source snapshot identity changed')
    else:
        with tempfile.TemporaryDirectory(prefix='sn-source-') as staging:
            archive = Path(staging) / 'product-source.tar'
            with archive.open('xb') as target:
                subprocess.run(['git', '-C', str(project), 'archive', revision], stdout=target, check=True)
            product = store.install_files('sn', {'product-source.tar': archive}, identity=product_identity)
        atomic_pointer(pointer, product)
        source = store.resolve(product)
    identity = {'product_revision': revision, 'product_archive_sha256': digest(source / 'product-source.tar'),
                'benchmark_source_hashes': hashes,
                'versions': {d.metadata['Name']: d.version for d in distributions()}}
    refs = read_run_refs(run)
    if refs is None:
        raise ValueError('Shared source snapshots require installed bundle references')
    refs['sources'] = {'evaluator': evaluator, 'sn': product}
    write_run_refs(run, store.root, refs)
    save_json(run / 'source-identity.json', identity)
    return identity


def _shared_snapshot_captured_sources(root, project, run, artifact_root, captured_source_ref):
    """Install evaluator bytes and bind one reviewed captured SN object.

    The caller owns the run reference file; this helper only replaces its
    source roles after validating the supplied capture. Product worktree dirt
    is intentionally permitted because E1's actual manifest is authoritative.
    """
    from .artifact_store import ArtifactStore, read_run_refs, write_run_refs

    store = ArtifactStore(artifact_root)
    materialized = materialize_captured_sn_source(store, captured_source_ref, run / "captured-source")
    paths = {str(p.relative_to(root)): p for folder in ('src/rag_eval', 'scripts')
             for p in sorted((root / folder).rglob('*.py'))}
    paths['pyproject.toml'] = root / 'pyproject.toml'
    hashes = {name: digest(path) for name, path in paths.items()}
    evaluator = store.install_files('evaluator', paths, identity={'benchmark_source_hashes': hashes})
    refs = read_run_refs(run)
    if refs is None:
        raise ValueError('Shared source snapshots require installed bundle references')
    if captured_source_ref.get('kind') != 'sn':
        raise ValueError('Captured source reference must be an sn artifact')
    refs['sources'] = {'evaluator': evaluator, 'sn': dict(captured_source_ref)}
    write_run_refs(run, store.root, refs)
    source_object = json.loads((store.resolve(captured_source_ref) / 'object.json').read_text(encoding='utf-8'))
    source_identity = source_object['identity']
    archive = store.resolve(captured_source_ref, files=['product-source.tar']) / 'product-source.tar'
    identity = {
        'product_archive_sha256': digest(archive),
        'benchmark_source_hashes': hashes,
        'versions': {d.metadata['Name']: d.version for d in distributions()},
        'source_format': source_identity['source_format'],
        'selected_roots': source_identity.get('selected_roots', []),
        'head_revision': source_identity.get('head_revision'),
        'head_tree': source_identity.get('head_tree'),
        'source_manifest_sha256': source_identity['source_manifest_sha256'],
        'actual_manifest': source_identity['actual_manifest'],
        'modified_paths': source_identity.get('modified_paths', []),
        'added_paths': source_identity.get('added_paths', []),
        'deleted_paths': source_identity.get('deleted_paths', []),
    }
    save_json(run / 'source-identity.json', identity)
    return identity
