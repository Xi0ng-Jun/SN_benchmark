"""Offline run reads and operation-local, byte-verified immutable input context."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path

from .artifacts import digest
from .identity import fingerprint
from .run_results import GROUP_FIELDS, summarize
from .notebook_data import SUITES as NOTEBOOK_SUITES, VERSION as NOTEBOOK_VERSION
from .artifact_store import read_run_refs, store_for_run, reference_identity


def input_directory(run):
    """Canonical input for a legacy or shared run; never guesses by file name."""
    run = Path(run).resolve()
    refs = read_run_refs(run)
    if refs is None:
        return run / 'input'
    return store_for_run(run, refs).resolve(refs['bundle'])


class RunReadContext:
    """One-operation caches, keyed by physical path AND exact expected identity.

    New invocations re-read and re-hash bytes. Different legacy physical copies
    are not trusted merely because they declare equal manifest values.
    """
    def __init__(self, *, partition_only=False):
        self.partition_only = partition_only
        self._canonical_by_manifest = {}
        self._bundles = {}
        self._objects = {}
        self._lookups = {}
        self._products = {}
        self.stats = {'canonical_rebuilds': 0, 'object_verifications': 0}

    @property
    def artifact_roots(self):
        return {key[0] for key in self._objects}

    def object(self, store, ref, *, files=None):
        key = (str(store.root), fingerprint(ref), None if files is None else tuple(sorted(files)))
        if key not in self._objects:
            self._objects[key] = store.resolve(ref, files=files)
            self.stats['object_verifications'] += 1
        return self._objects[key]

    def validate_sources(self, store, refs, code):
        sources = refs.get('sources', {})
        if not sources:
            return
        if set(sources) != {'evaluator', 'sn'}:
            raise ValueError('Shared code requires both source artifact roles')
        objects = {role: json.loads((self.object(store, ref) / 'object.json').read_text())
                   for role, ref in sources.items()}
        sn_identity = objects['sn']['identity']
        source_files_match = objects['sn']['files'] == {
            'product-source.tar': code.get('product_archive_sha256')
        }
        if sn_identity.get('source_format') == 'sn-source-capture-v1':
            from .source_identity import captured_source_manifest
            actual_manifest = captured_source_manifest(sn_identity)
            recorded_manifest = captured_source_manifest(code)
            source_binding_match = (
                code.get('source_format') == sn_identity.get('source_format')
                and code.get('source_manifest_sha256') == sn_identity.get('source_manifest_sha256')
                and recorded_manifest == actual_manifest
            )
        else:
            source_binding_match = sn_identity.get('product_revision') == code.get('product_revision')
        if (objects['evaluator']['files'] != code.get('benchmark_source_hashes')
                or not source_files_match or not source_binding_match):
            raise ValueError('Shared source artifacts differ from recorded code identity')

    def bundle(self, directory):
        from .notebook_bundle import load_bundle
        directory = Path(directory).resolve()
        if directory not in self._bundles:
            self._bundles[directory] = load_bundle(directory)
            self.stats['canonical_rebuilds'] += 1
            self._canonical_by_manifest[fingerprint(self._bundles[directory]['manifest'])] = self._bundles[directory]
        return self._bundles[directory]

    def input_directory(self, run):
        run = Path(run).resolve()
        refs = read_run_refs(run)
        if refs is None:
            return run / 'input'
        store = store_for_run(run, refs)
        directory = self.object(store, refs['bundle'], files=['manifest.json'] if self.partition_only else None)
        index_dir = self.object(store, refs['index'], files=['index.json'] if self.partition_only else None)
        index = json.loads((index_dir / 'index.json').read_text())
        if index.get('format') != 'sn-bundle-index-v1' or index.get('bundle') != refs['bundle']:
            raise ValueError('Run partition index refers to a different bundle')
        for ref in refs.get('sources', {}).values():
            self.object(store, ref)
        return directory

    def bundle_for_run(self, run):
        run = Path(run).resolve()
        directory = self.input_directory(run)
        refs = read_run_refs(run)
        if refs is not None and self.partition_only:
            from .bundle_index import load_partition
            manifest = json.loads((run / 'manifest.json').read_text())
            context = manifest['identity']['notebook_context']
            revision = context.get('request_revision', 'notebook-request-v1')
            capsule = load_partition(store_for_run(run, refs).root, refs, context['partition_id'], revision)
            bundle = dict(capsule, partitions=[capsule['partition']])
            if not capsule['qasper_evidence_catalogues']:
                bundle.pop('qasper_evidence_catalogues')
            self._products[(fingerprint(bundle['manifest']), context['partition_id'], revision)] = capsule['product']
            return bundle
        if refs is not None:
            # Independently hash-verified physical shared files match the full
            # source manifest already rebuilt at this operation's entrypoint.
            # This optimization NEVER applies to legacy physical copies.
            manifest = json.loads((directory / 'manifest.json').read_text())
            same = self._canonical_by_manifest.get(fingerprint(manifest))
            if same is not None:
                self._bundles[directory] = same
        return self.bundle(directory)

    @staticmethod
    def _lookup_key(bundle):
        # A capsule is only one partition; its source manifest names the full
        # corpus and cannot alone identify the subset in this operation.
        return (fingerprint(bundle['manifest']),
                bundle['partition']['partition_id'] if bundle.get('format') == 'sn-partition-capsule-v1' else None)

    def lookup(self, bundle):
        from .bundle_index import BundleLookup
        key = self._lookup_key(bundle)
        if key not in self._lookups:
            self._lookups[key] = BundleLookup(bundle)
        return self._lookups[key]

    def case_index(self, bundle):
        return self.lookup(bundle).cases

    def selected_cases(self, bundle, product, case_ids=None):
        from .notebook_runner import selected_cases
        lookup = self.lookup(bundle)
        subset = dict(bundle, cases=[lookup.cases[q['case_id']] for q in product['questions']])
        return selected_cases(subset, product, case_ids)

    def partition(self, bundle, partition_id, request_revision):
        from .bundle_index import BundleLookup
        key = self._lookup_key(bundle)
        if key not in self._lookups:
            self._lookups[key] = BundleLookup(bundle)
        product_key = (fingerprint(bundle['manifest']), partition_id, request_revision)
        if product_key not in self._products:
            self._products[product_key] = self._lookups[key].partition(partition_id, request_revision)['product']
        return self._products[product_key]

def _read_journal(path, warnings):
    """Parse once and retain explicit file facts for model-event auditing."""
    if not path.exists():
        warnings.append(f"{path.name}: not created")
        return {'rows': [], 'journal_present': False, 'truncated_tail': False}
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    rows = []
    truncated_tail = False
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("Journal entries must be objects")
            rows.append(row)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not content.endswith("\n"):
                truncated_tail = True
                warnings.append(f"{path.name}: incomplete final line ignored; run is incomplete")
            else:
                raise ValueError(f"Corrupt journal: {path.name}, line {index + 1}") from None
    return {'rows': rows, 'journal_present': True, 'truncated_tail': truncated_tail}


def read_journal(path, warnings):
    """Keep the existing row-only API and corruption/partial-tail behavior."""
    return _read_journal(path, warnings)['rows']


def read_model_events(path, warnings):
    """Return saved rows and file facts without guessing from warning text."""
    return _read_journal(path, warnings)


def validate_runtime_dependencies(run, identity):
    """Check saved startup observations offline, without inspecting this host."""
    binding = identity.get('runtime_dependencies')
    if binding is None:
        return  # Historical runs did not declare this startup observation.
    if (type(binding) is not dict
            or set(binding) != {'format', 'observation_sha256', 'requirements_sha256',
                               'environment_sha256', 'run_coordinate'}
            or binding['format'] != 'sn-startup-binding-v1'):
        raise ValueError('Invalid startup dependency reference')
    runtime = json.loads((Path(run) / 'runtime-identity.json').read_text(encoding='utf-8'))
    if type(runtime) is not dict or 'startup_dependencies' not in runtime:
        raise ValueError('Missing startup dependency observation')
    from .runtime_dependency_identity import (
        DEFAULT_STARTUP_MODULES, parse_dependency_requirements, validate_startup_observation,
    )
    observation = validate_startup_observation(runtime['startup_dependencies'])
    declarations = (Path(run) / 'dependency-requirements.txt').read_bytes()
    if (fingerprint(observation) != binding['observation_sha256']
            or observation['requirements']['sha256'] != binding['requirements_sha256']
            or hashlib.sha256(declarations).hexdigest() != binding['requirements_sha256']
            or observation['requirements']['size'] != len(declarations)):
        raise ValueError('Saved startup dependency identity changed')
    # Syntax parsing is host-neutral; marker truth remains validated against the
    # saved marker_environment by the generic validator, never this reader's host.
    entries = parse_dependency_requirements(declarations)['entries']
    checked = [{key: value for key, value in row.items()
                if key not in {'active', 'installed_version'}}
               for row in observation['direct_requirements']]
    if checked != entries:
        raise ValueError('Saved declaration does not match startup checks')
    if {row['module'] for row in observation['modules']} != set(DEFAULT_STARTUP_MODULES):
        raise ValueError('Saved startup module set does not match runner contract')
    from .runtime_dependency_comparison import startup_comparison_projection
    environment = startup_comparison_projection(observation, run_coordinate=binding['run_coordinate'])
    if fingerprint(environment) != binding['environment_sha256']:
        raise ValueError('Saved startup environment comparison identity changed')
    return {'format': environment['format'], 'sha256': binding['environment_sha256']}


def validate_runtime_model_services(run, identity):
    """Validate saved model startup facts without product/host configuration."""
    binding = identity.get('runtime_model_services')
    if binding is None:
        return  # Runs preceding this observation retain their existing meaning.
    if (type(binding) is not dict
            or set(binding) != {'format', 'observation_sha256', 'source_modules_sha256'}
            or binding['format'] != 'sn-loaded-model-services-binding-v1'):
        raise ValueError('Invalid loaded model services binding')
    runtime = json.loads((Path(run) / 'runtime-identity.json').read_text(encoding='utf-8'))
    saved = runtime.get('loaded_model_services')
    if type(saved) is not dict or set(saved) != {'observation', 'source_modules'}:
        raise ValueError('Missing loaded model services observation')
    from .runtime_model_identity import validate_loaded_model_identity
    observation = validate_loaded_model_identity(saved['observation'])
    if (fingerprint(observation) != binding['observation_sha256']
            or observation['declared_config_sha256'] != identity.get('product_services')
            or fingerprint(saved['source_modules']) != binding['source_modules_sha256']):
        raise ValueError('Saved loaded model services identity changed')
    manifest = identity.get('code', {}).get('actual_manifest', {})
    modules = saved['source_modules']
    if type(modules) is not list:
        raise ValueError('Invalid model consumer source observations')
    names = []
    for item in modules:
        if type(item) is not dict or set(item) != {'module', 'origin', 'sha256', 'size'}:
            raise ValueError('Invalid model consumer source observation')
        name, origin = item['module'], item['origin']
        if not isinstance(name, str) or not (name == 'app' or name.startswith('app.')):
            raise ValueError('Invalid model consumer source module')
        base = 'backend/' + name.replace('.', '/')
        if (origin not in {base + '.py', base + '/__init__.py'}
                or manifest.get(origin) != {'sha256': item['sha256'], 'size': item['size']}):
            raise ValueError('Model consumer source differs from capture')
        names.append(name)
    if (names != sorted(set(names)) or not {'app.core.event_logging',
            'app.services.model_registry', 'app.services.model_provider'} <= set(names)):
        raise ValueError('Missing model consumer source observations')


def load_run(run, *, context=None, include_model_events=True):
    run = Path(run).resolve()
    context = context or RunReadContext()
    warnings = []
    state_path = run / "state.json"
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {"phase": "unknown"}
    manifest_path = run / "manifest.json"
    if not manifest_path.exists():
        if not state_path.exists():
            raise ValueError("Not a Notebook run directory: " + str(run))
        return {"path": str(run), "manifest": None, "state": state, "groups": [], "planned": [],
                "outputs": [], "scores": [], "warnings": ["Initialization did not produce a complete run manifest"]}
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    from .oracle_track import RUN_FORMAT, read_oracle_run, require_formal_run
    if manifest.get('format') == RUN_FORMAT:
        return {**read_oracle_run(run), 'path': str(run), 'groups': [], 'scores': [],
                'warnings': ['diagnostic/oracle: excluded from formal Notebook reports and official scoring']}
    require_formal_run(manifest)
    artifacts = reference_identity(run)
    if manifest.get("artifact_identity") != artifacts.get("artifact_identity"):
        raise ValueError("Run artifact reference identity changed")
    if manifest.get("format") != "public-starter-run-v1":
        raise ValueError("Unsupported run format")
    if manifest.get("suite") not in NOTEBOOK_SUITES:
        raise ValueError("Unsupported benchmark: " + str(manifest.get("suite")))
    if manifest.get("track") != "R":
        raise ValueError("Notebook reports require track R")
    if manifest.get("product_protocol") not in {NOTEBOOK_VERSION, "sn-notebook-baseline-v1"}:
        raise ValueError("Unsupported Notebook product protocol")
    identity_product = manifest.get("identity", {}).get("product_bundle") or {}
    if fingerprint({**manifest["identity"], "mode": manifest["mode"]}) != manifest["protocol_id"]:
        raise ValueError("Protocol fingerprint does not match the recorded configuration")
    if manifest["track"] == "R" and fingerprint(manifest["identity"]) != manifest["pairing_id"]:
        raise ValueError("Product pairing identity does not match its configuration")
    startup_comparison = validate_runtime_dependencies(run, manifest['identity'])
    validate_runtime_model_services(run, manifest['identity'])
    if 'feature_set' in manifest['identity']:
        from .feature_identity import build_feature_identity, validate_feature_identity
        saved = validate_feature_identity(manifest['identity']['feature_set'])
        runtime = json.loads((run / 'runtime-identity.json').read_text(encoding='utf-8'))
        if saved != build_feature_identity(runtime):
            raise ValueError('Manifest features differ from saved runtime observations')
    comparison = {}
    if startup_comparison is not None:
        comparison_identity = {**manifest['identity'], 'runtime_dependencies': startup_comparison}
        # Exact manifest identity/pairing remain provenance; comparison uses
        # vetted startup facts with only known audit coordinates normalized.
        comparison = {'comparison_identity': comparison_identity,
                      'comparison_pairing_id': fingerprint(comparison_identity)}
    if digest(run / "planned.jsonl") != manifest["planned_sha256"]:
        raise ValueError("Planned result ledger changed")
    planned = read_journal(run / "planned.jsonl", warnings)
    outputs = read_journal(run / "outputs.jsonl", warnings)
    scores = read_journal(run / "scores.jsonl", warnings)
    if (identity_product.get("protocol_version") == NOTEBOOK_VERSION) != (manifest.get("product_protocol") == NOTEBOOK_VERSION):
        raise ValueError("Notebook protocol declaration differs from run identity")
    if manifest.get("product_protocol") == NOTEBOOK_VERSION:
        from .notebook_runner import validate_saved_run
        validate_saved_run(run, manifest, planned, outputs, reader=context)
    baseline = "sn-notebook-baseline-v1"
    if (identity_product.get("protocol_version") == baseline) != (manifest.get("product_protocol") == baseline):
        raise ValueError("Baseline protocol declaration differs from run identity")
    if manifest["mode"] == "bm25" and manifest.get("product_protocol") != baseline:
        raise ValueError("BM25 requires the baseline protocol")
    if manifest.get("product_protocol") == baseline:
        from .notebook_baseline import validate_saved_baseline_run
        validate_saved_baseline_run(run, manifest, planned, outputs, reader=context)
    refs = read_run_refs(run)
    if refs is not None:
        context.validate_sources(store_for_run(run, refs), refs, manifest['identity'].get('code', {}))
    cases = {p["case_id"] for p in planned}
    observed = {}
    for output in outputs:
        case_id = output["case_id"]
        if case_id not in cases or case_id in observed:
            raise ValueError("Unexpected or duplicate saved prediction")
        if output.get("status") not in {"success", "error", "not_applicable", "clarification", "no_answer"} or type(output.get("output_available")) is not bool:
            raise ValueError("Invalid prediction status or availability")
        observed[case_id] = output
    if len(cases) != manifest["planned_predictions"] or len(planned) != manifest["planned_scores"]:
        raise ValueError("Manifest counts differ from the plan")
    for p in planned:
        if any(p[field] != manifest[field] for field in ("run_id", "protocol_id", "suite", "track", "mode")):
            raise ValueError("Planned identity differs from manifest")
    for score in scores:
        if score.get("output_available") and not observed.get(score["case_id"], {}).get("output_available"):
            raise ValueError("Score claims an output that was not saved")
    groups = summarize(planned, scores)
    for group in groups:
        members = [p for p in planned if all((p.get("task", p["suite"]) if k == "task" else p[k]) == group[k] for k in GROUP_FIELDS)]
        ids = {p["case_id"] for p in members}
        group.update(saved_outputs=sum(observed.get(i, {}).get("output_available", False) for i in ids),
                     prediction_errors=sum(observed.get(i, {}).get("status") == "error" for i in ids),
                     missing_predictions=sum(i not in observed for i in ids),
                     prediction_not_applicable=sum(observed.get(i, {}).get("status") == "not_applicable" for i in ids))
        group["prediction_status_counts"] = dict(Counter(observed[i]["status"] for i in ids if i in observed))
        group["behavior_observations"] = dict(Counter(
            (observed[i].get("behavior") or {}).get("kind", "not_observed") for i in ids if i in observed))
        group["material_roles"] = sorted({p["material_role"] for p in members if "material_role" in p})
        # This denominator includes answers already saved when scoring was interrupted.
        group["saved_output_coverage"] = group["saved_outputs"] / len(ids) if ids else None
    if len(outputs) != len(cases) or len(scores) != len(planned):
        warnings.append("Prediction or score ledger incomplete; missing items remain in planned denominators")
    if state["phase"] not in {"finished", "finished_with_errors", "failed", "interrupted", "not_applicable"}:
        warnings.append("No terminal state recorded; the process may still be running or may have stopped")
    events, generation_audit = [], None
    if include_model_events:
        from .scheduled_generation_audit import audit_scheduled_generation
        journal = read_model_events(run / 'model-events.jsonl', warnings)
        events = journal['rows']
        generation_audit = audit_scheduled_generation(
            events, journal_present=journal['journal_present'], truncated_tail=journal['truncated_tail'])
        # Empty journals remain visible in the new projection. Preserve legacy
        # run warnings while surfacing genuinely incomplete scheduled records.
        if generation_audit['completeness']['pair_state'] == 'incomplete':
            warnings.extend(generation_audit['warnings'])
    started = {e["call_id"] for e in events if e.get("event") == "started"}
    terminal = {e["call_id"] for e in events if e.get("event") in {"completed", "failed"}}
    return {**comparison, "path": str(run), "manifest": manifest, "state": state, "groups": groups,
            "planned": planned, "outputs": outputs, "scores": scores, "warnings": warnings,
            "unfinished_model_calls": len(started - terminal) if include_model_events else None,
            "scheduled_generation_audit": generation_audit,
            "input_validation": "partition-capsule" if context.partition_only else "canonical-full"}
