"""Verified input/source facts for a fixed Job; never an executable spec.

The caller fetches a validated current Job, performs this expensive read outside
its write transaction, and rechecks identity/version before persisting. Offline
validation uses the original Job snapshot saved alongside the preparation, so a
later cancellation does not erase an earlier receipt. A digest detects damage;
it is not authentication of a caller-supplied preparation.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path, PurePosixPath
import re

from ..artifact_store import ArtifactStore
from ..bundle_index import BundleLookup, FORMAT as INDEX_FORMAT, load_partition
from ..identity import fingerprint
from ..notebook_bundle import load_bundle
from ..source_identity import captured_source_manifest
from .evaluation_jobs import _saved as validate_job_snapshot
from .plan_inputs import ADAPTER, ADAPTATION, _ids


FORMAT = 'sn-workbench-job-preparation-v1'
_UNCAPTURED = ('runtime', 'dependencies', 'model', 'scorer', 'evaluator', 'config')
_MISSING = (*_UNCAPTURED, 'source_loading', 'owner_binding')
_BINDING = ('job_id', 'task_id', 'workspace_id', 'plan_id', 'plan_revision', 'plan_content_hash')
_FIELDS = {'format', 'preparation_id', *_BINDING, 'job_state_version', 'input', 'source',
           'execution_intent', *_UNCAPTURED, 'readiness', 'executable'}


class JobPreparationRejected(ValueError):
    """Invalid preparation, incompatible fixed intention, or invalid artifact."""


def _require(condition):
    if not condition:
        raise JobPreparationRejected('Invalid Job input/source preparation')


def _shape(value, fields):
    _require(type(value) is dict and set(value) == set(fields))


def _hash(value):
    _require(type(value) is str and re.fullmatch('[0-9a-f]{64}', value) is not None)


def _ref(value, role):
    _shape(value, ('kind', 'id', 'manifest_sha256'))
    _require(value['kind'] == role)
    _hash(value['id'])
    _hash(value['manifest_sha256'])


def _job(value):
    _require(type(value) is dict and {'task_id', 'workspace_id'} <= value.keys())
    # Existing authoritative closed Job schema; no duplicate plan/Job schema.
    job = validate_job_snapshot(value, task_id=value['task_id'], workspace_id=value['workspace_id'])
    _require(job['state'] == 'not_dispatched')
    return job


def _source_shape(source):
    _shape(source, ('status', 'artifact_ref', 'source_format', 'source_manifest_sha256',
                    'provenance', 'selected_roots'))
    _require(source['status'] == 'captured_unbound'
             and source['source_format'] == 'sn-source-capture-v1')
    _ref(source['artifact_ref'], 'sn')
    _hash(source['source_manifest_sha256'])
    _shape(source['provenance'], ('head_revision', 'head_tree'))
    for value in source['provenance'].values():
        _require(type(value) is str and re.fullmatch('(?:[0-9a-f]{40}|[0-9a-f]{64})', value) is not None)
    roots = source['selected_roots']
    _require(type(roots) is list and bool(roots))
    for root in roots:
        _require(type(root) is str and bool(root) and '\\' not in root and ':' not in root)
        path = PurePosixPath(root)
        _require(not path.is_absolute() and path.as_posix() == root
                 and all(part not in ('', '.', '..') for part in root.split('/')))
    _require(roots == sorted(set(roots)))


def validate_job_preparation(record, job) -> dict:
    """Validate a closed receipt payload offline against its original Job.

    No source, registry, database or artifact reads. The stored payload is
    accepted only with the exact fixed plan and expected Job state version.
    """
    job = _job(job)
    _shape(record, _FIELDS)
    _require(record['format'] == FORMAT and record['executable'] is False)
    _hash(record['preparation_id'])
    _require(type(record['job_state_version']) is int
             and record['job_state_version'] == job['state_version'])
    for field in _BINDING:
        _require(record[field] == job[field])
    content = job['plan_snapshot']['content']
    _require(record['execution_intent'] == content['execution_intent'])
    for field in _UNCAPTURED:
        _shape(record[field], ('status',))
        _require(record[field]['status'] == 'not_captured')
    _shape(record['readiness'], ('missing',))
    _require(record['readiness']['missing'] == list(_MISSING))
    inp = record['input']
    _shape(inp, ('status', 'identity', 'bundle_ref', 'index_ref', 'request_revision',
                 'selection', 'partitions'))
    _require(inp['status'] == 'verified' and inp['identity'] == content['input']
             and inp['selection'] == content['selection']
             and inp['request_revision'] == content['execution_intent']['request_revision'])
    _ref(inp['bundle_ref'], 'bundle')
    _ref(inp['index_ref'], 'index')
    _require(type(inp['partitions']) is list)
    for part in inp['partitions']:
        _shape(part, ('partition_id', 'capsule_sha256', 'partition_sha256'))
        _hash(part['capsule_sha256'])
        _hash(part['partition_sha256'])
    _require([p['partition_id'] for p in inp['partitions']] == content['selection']['partition_ids'])
    _source_shape(record['source'])
    _require(record['preparation_id'] == fingerprint({k: v for k, v in record.items()
                                                    if k != 'preparation_id'}))
    return deepcopy(record)


def _canonical(bundle, content):
    manifest = bundle['manifest']
    identity = dict(input_id=content['input']['input_id'], adapter=ADAPTER,
                    bundle_id=fingerprint(manifest), suite=manifest['suite'],
                    source_revision='sha256:' + fingerprint(manifest['source']['revision']),
                    adaptation_revision=manifest['adaptation_revision'])
    _require(identity == content['input'] and identity['adaptation_revision'] == ADAPTATION)
    case_ids = _ids([case['case_id'] for case in bundle['cases']], 'canonical cases')
    _ids([part['partition_id'] for part in bundle['partitions']], 'canonical partitions')
    owned = set()
    for part in bundle['partitions']:
        cases = set(_ids(part['case_ids'], 'partition cases'))
        _require(cases <= set(case_ids) and not owned.intersection(cases))
        owned.update(cases)
    _require(owned == set(case_ids))
    selected = set(content['selection']['case_ids'])
    _require(selected <= owned)
    selection = dict(kind='explicit', case_ids=[cid for cid in case_ids if cid in selected],
                     partition_ids=[p['partition_id'] for p in bundle['partitions']
                                    if selected.intersection(p['case_ids'])])
    if 'roles' in content['selection']:
        from .splits import add_roles
        add_roles(selection, content['selection']['roles'], bundle['partitions'])
    else:
        selection['selection_hash'] = fingerprint(selection)
    _require(selection == content['selection'])
    return identity, selection


def _read_input_artifact(reader, *args):
    """Normalize non-object JSON at the two canonical reader boundaries only."""
    try:
        return reader(*args)
    except AttributeError as exc:
        # These readers call .get on decoded objects. JSON primitives/arrays
        # have no .get; other AttributeErrors still indicate programming faults.
        if exc.name != 'get' or type(exc.obj) not in (list, str, int, float, bool, type(None)):
            raise
        raise JobPreparationRejected('Invalid or unavailable preparation artifact') from None


def build_job_preparation(job, store, *, bundle_ref, index_ref, sn_ref) -> dict:
    """Verify installed canonical inputs and captured SN bytes, without loading SN.

    References must already exist. No pointer lookup, new artifact, Job write,
    runtime/model configuration or live plan-head read is performed. The source
    archive hash and manifest are checked; members are verified at execution.
    """
    job = deepcopy(_job(job))
    _require(isinstance(store, ArtifactStore))
    bundle_ref, index_ref, sn_ref = deepcopy((bundle_ref, index_ref, sn_ref))
    for ref, kind in ((bundle_ref, 'bundle'), (index_ref, 'index'), (sn_ref, 'sn')):
        _ref(ref, kind)
    content = job['plan_snapshot']['content']
    # Only this external artifact-reading boundary classifies malformed bytes;
    # offline schema checks and unrelated programming errors remain separate.
    try:
        directory = store.resolve(bundle_ref)
        bundle = _read_input_artifact(load_bundle, directory)
        identity, selection = _canonical(bundle, content)
        bundle_object = json.loads((directory / 'object.json').read_text(encoding='utf-8'))
        _require(bundle_object['identity'].get('bundle_id') == identity['bundle_id'])
        index_dir = store.resolve(index_ref)
        index_object = json.loads((index_dir / 'object.json').read_text(encoding='utf-8'))
        _require(index_object['identity'].get('format') == INDEX_FORMAT
                 and index_object['identity'].get('bundle') == bundle_ref)
        refs = dict(bundle=bundle_ref, index=index_ref)
        request_revision = content['execution_intent']['request_revision']
        lookup = BundleLookup(bundle)
        partitions = []
        for partition_id in selection['partition_ids']:
            capsule = _read_input_artifact(load_partition, store.root, refs, partition_id, request_revision)
            _require(capsule == lookup.partition(partition_id, request_revision))
            partitions.append(dict(partition_id=partition_id, capsule_sha256=fingerprint(capsule),
                                   partition_sha256=fingerprint(capsule['partition'])))
        source_dir = store.resolve(sn_ref)
        source_identity = json.loads((source_dir / 'object.json').read_text(encoding='utf-8'))['identity']
        manifest = captured_source_manifest(source_identity)
        source = dict(status='captured_unbound', artifact_ref=sn_ref,
            source_format=source_identity['source_format'],
            source_manifest_sha256=source_identity['source_manifest_sha256'],
            provenance={key: source_identity[key] for key in ('head_revision', 'head_tree')},
            selected_roots=source_identity['selected_roots'])
        _source_shape(source)
        _require(all(any(name == root or name.startswith(root + '/')
                         for root in source['selected_roots']) for name in manifest))
        # The archive hash was verified above; its members are checked against
        # this manifest when an execution materializes it, so no extraction here.
    except (ValueError, OSError, KeyError, TypeError, IndexError):
        raise JobPreparationRejected('Invalid or unavailable preparation artifact') from None
    record = dict(format=FORMAT, **{key: job[key] for key in _BINDING},
        job_state_version=job['state_version'],
        input=dict(status='verified', identity=identity, selection=selection,
                   bundle_ref=bundle_ref, index_ref=index_ref, request_revision=request_revision,
                   partitions=partitions), source=source, execution_intent=content['execution_intent'],
        **{field: dict(status='not_captured') for field in _UNCAPTURED},
        readiness=dict(missing=list(_MISSING)), executable=False)
    record['preparation_id'] = fingerprint(record)
    return validate_job_preparation(record, job)
