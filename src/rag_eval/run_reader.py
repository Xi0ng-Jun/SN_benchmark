"""Offline run reads and operation-local, byte-verified immutable input context."""
from __future__ import annotations

from collections import Counter
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
        if (objects['evaluator']['files'] != code.get('benchmark_source_hashes')
                or objects['sn']['files'] != {'product-source.tar': code.get('product_archive_sha256')}
                or objects['sn']['identity'].get('product_revision') != code.get('product_revision')):
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

def read_journal(path, warnings):
    if not path.exists():
        warnings.append(f"{path.name}: not created")
        return []
    content = path.read_text(encoding="utf-8")
    lines = content.splitlines()
    rows = []
    for index, line in enumerate(lines):
        try:
            row = json.loads(line)
            if not isinstance(row, dict):
                raise ValueError("Journal entries must be objects")
            rows.append(row)
        except json.JSONDecodeError:
            if index == len(lines) - 1 and not content.endswith("\n"):
                warnings.append(f"{path.name}: incomplete final line ignored; run is incomplete")
            else:
                raise ValueError(f"Corrupt journal: {path.name}, line {index + 1}") from None
    return rows


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
    events = read_journal(run / "model-events.jsonl", warnings) if include_model_events else []
    started = {e["call_id"] for e in events if e.get("event") == "started"}
    terminal = {e["call_id"] for e in events if e.get("event") in {"completed", "failed"}}
    return {"path": str(run), "manifest": manifest, "state": state, "groups": groups,
            "planned": planned, "outputs": outputs, "scores": scores, "warnings": warnings,
            "unfinished_model_calls": len(started - terminal) if include_model_events else None,
            "input_validation": "partition-capsule" if context.partition_only else "canonical-full"}
