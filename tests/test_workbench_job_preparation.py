"""Preparation verifies real canonical bytes, without becoming execution authority."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sqlite3
import sys

import pytest

from rag_eval.artifact_store import ArtifactStore
from rag_eval.bundle_index import install_bundle, load_partition
from rag_eval.identity import fingerprint
from rag_eval.notebook_bundle import partition_bundle
from rag_eval.sn_source_capture import capture_sn_source
from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
from test_sn_source_capture import _repo
from test_workbench_evaluation_plans import setup


@pytest.fixture
def environment(tmp_path):
    repo, workspace, task, plans, bundle, command = setup(tmp_path)
    plan = plans.save(**command)
    jobs = EvaluationJobStore(repo)
    job = jobs.prepare(task['id'], plan['plan_id'], plan['revision'], plan['content_hash'],
                       actor='alice', request_key='job')
    store = ArtifactStore(tmp_path / 'artifacts')
    refs = install_bundle(tmp_path / 'data/bundle', store.root)
    project = _repo(tmp_path)
    (project / 'backend/app/core/token.py').write_text("TOKEN = 'dirty'\n")
    (project / 'backend/app/new.py').write_text("raise RuntimeError('must never import')\n")
    source = capture_sn_source(project, store)
    return dict(repo=repo, plans=plans, command=command, jobs=jobs, job=job,
                store=store, refs=refs, source=source, project=project, bundle=bundle)


def build(env, **changes):
    from rag_eval.workbench.job_preparation import build_job_preparation
    args = dict(bundle_ref=env['refs']['bundle'], index_ref=env['refs']['index'],
                sn_ref=env['source'].artifact_ref)
    args.update(changes)
    return build_job_preparation(env['job'], env['store'], **args)


def validate(record, job):
    from rag_eval.workbench.job_preparation import validate_job_preparation
    return validate_job_preparation(record, job)


def rehash(record):
    record['preparation_id'] = fingerprint({k: v for k, v in record.items()
                                            if k != 'preparation_id'})
    return record


def test_verified_bytes_keep_full_partition_and_remain_unexecutable(environment, tmp_path):
    env = environment
    before = set(sys.modules)
    record = build(env)
    assert not {name for name in set(sys.modules) - before if name == 'app' or name.startswith('app.')}
    assert record['executable'] is False
    assert record['job_state_version'] == 1
    assert record['input']['selection']['case_ids'] == ['qasper:q']
    partition = env['bundle']['partitions'][0]
    assert partition['case_ids'] == ['qasper:q', 'qasper:q2']
    capsule = load_partition(env['store'].root, env['refs'], partition['partition_id'],
                             'notebook-request-v3')
    assert len(capsule['product']['questions']) == 2
    assert len(capsule['documents']) == 1
    assert record['input']['partitions'] == [dict(partition_id=partition['partition_id'],
        capsule_sha256=fingerprint(capsule), partition_sha256=fingerprint(partition))]
    assert record['source']['status'] == 'captured_unbound'
    assert record['source']['source_manifest_sha256'] == env['source'].source_manifest_sha256
    assert record['source']['selected_roots'] == ['backend/app']
    assert record['source']['provenance']['head_revision'] == env['source'].head_revision
    assert record['readiness']['missing'] == ['runtime', 'dependencies', 'model', 'scorer',
        'evaluator', 'config', 'source_loading', 'owner_binding']
    for key in ('runtime', 'dependencies', 'model', 'scorer', 'evaluator', 'config'):
        assert record[key] == {'status': 'not_captured'}
    assert validate(json.loads(json.dumps(record)), env['job']) == record
    encoded = json.dumps(record)
    for private in (str(tmp_path), 'Fish swim.', 'GOLD_SECRET', 'must never import', 'alice'):
        assert private not in encoded


def test_repeat_fixed_job_ignores_new_head_mutable_sources_and_returns_independent_values(environment):
    env = environment
    first = build(env)
    job = env['job']
    env['plans'].save(**dict(env['command'], plan_id=job['plan_id'],
        base_revision=job['plan_revision'], request_key='new-head', mode='reasoning',
        case_ids=['qasper:q3']))
    (env['project'] / 'backend/app/core/token.py').write_text('changed after capture')
    second = build(env)
    assert first == second
    assert first['execution_intent']['mode'] == 'chunk'
    assert first['plan_revision'] == job['plan_revision']
    second['input']['selection']['case_ids'].append('forged')
    assert build(env) == first


def test_cancel_rejected_but_original_snapshot_remains_valid_offline(environment, monkeypatch):
    env = environment
    record = build(env)
    original = env['job']
    env['job'] = env['jobs'].cancel(original['task_id'], original['job_id'], 1,
                                   actor='alice', request_key='cancel')
    with pytest.raises(ValueError):
        build(env)
    def no_io(*args, **kwargs):
        raise AssertionError('offline validation performed I/O')
    monkeypatch.setattr(Path, 'open', no_io)
    monkeypatch.setattr(ArtifactStore, 'resolve', no_io)
    assert validate(record, original) == record
    with pytest.raises(ValueError):
        validate(record, env['job'])


@pytest.mark.parametrize('role', ['bundle', 'index', 'sn'])
def test_mutated_artifact_bytes_are_rejected(environment, role):
    env = environment
    ref = env['source'].artifact_ref if role == 'sn' else env['refs'][role]
    directory = env['store'].resolve(ref)
    name = {'bundle': 'raw-data', 'index': 'index.json', 'sn': 'product-source.tar'}[role]
    (directory / name).write_bytes(b'changed')
    with pytest.raises(ValueError):
        build(env)


@pytest.mark.parametrize('role', ['bundle_ref', 'index_ref', 'sn_ref'])
def test_reference_roles_are_checked(environment, role):
    wrong = environment['refs']['index' if role == 'bundle_ref' else 'bundle']
    with pytest.raises(ValueError):
        build(environment, **{role: wrong})


def republish(store, ref, *, identity=None, contents=None):
    directory = store.resolve(ref)
    obj = json.loads((directory / 'object.json').read_text())
    files = {name: (directory / name).read_bytes() for name in obj['files']}
    files.update(contents or {})
    return store.install_bytes(ref['kind'], files,
                               identity=obj['identity'] if identity is None else identity)


@pytest.mark.parametrize('role,name', [('bundle', 'manifest.json'), ('index', 'index.json')])
@pytest.mark.parametrize('encoded', [b'[]', b'null'])
def test_malformed_reader_json_is_a_sanitized_preparation_rejection(environment, role, name, encoded):
    from rag_eval.workbench.job_preparation import JobPreparationRejected
    env = environment
    forged = republish(env['store'], env['refs'][role], contents={name: encoded})
    env['store'].resolve(forged)  # Hash-valid external bytes, not a broken reference.
    with pytest.raises(JobPreparationRejected) as caught:
        build(env, **{role + '_ref': forged})
    assert str(caught.value) == 'Invalid or unavailable preparation artifact'


@pytest.mark.parametrize('seam,error_type', [
    ('load_bundle', AttributeError), ('load_partition', AttributeError),
    ('load_bundle', sqlite3.OperationalError), ('load_partition', sqlite3.OperationalError),
    ('_canonical', AttributeError),
])
def test_reader_normalization_preserves_unrelated_exceptions(environment, monkeypatch, seam, error_type):
    from rag_eval.workbench import job_preparation
    failure = error_type('unrelated injected failure')

    def fail(*args, **kwargs):
        raise failure

    monkeypatch.setattr(job_preparation, seam, fail)
    with pytest.raises(error_type) as caught:
        build(environment)
    assert caught.value is failure


@pytest.mark.parametrize('change', ['index_identity', 'index_bundle', 'source_format', 'source_manifest'])
def test_valid_object_hashes_do_not_replace_semantic_binding(environment, change):
    env = environment
    store = env['store']
    if change.startswith('index'):
        ref = env['refs']['index']
        directory = store.resolve(ref)
        if change == 'index_identity':
            identity = json.loads((directory / 'object.json').read_text())['identity']
            identity['bundle'] = dict(env['refs']['bundle'], id='0' * 64)
            forged = republish(store, ref, identity=identity)
        else:
            index = json.loads((directory / 'index.json').read_text())
            index['bundle'] = dict(env['refs']['bundle'], id='0' * 64)
            forged = republish(store, ref, contents={'index.json': json.dumps(index).encode()})
        args = dict(index_ref=forged)
    else:
        ref = env['source'].artifact_ref
        identity = json.loads((store.resolve(ref) / 'object.json').read_text())['identity']
        if change == 'source_format':
            identity['source_format'] = 'legacy'
        else:
            entry = next(iter(identity['actual_manifest'].values()))
            entry['sha256'] = '0' * 64
            identity['source_manifest_sha256'] = fingerprint(identity['actual_manifest'])
        args = dict(sn_ref=republish(store, ref, identity=identity))
    if change == 'source_manifest':
        # Archive members are checked against the manifest when an execution
        # materializes the source, not by a throwaway extraction at preparation.
        from rag_eval.runtime_environment import materialize_shared_sn_source
        build(env, **args)
        with pytest.raises(ValueError, match='do not match captured manifest'):
            materialize_shared_sn_source(store, args['sn_ref'], env['store'].root.parent / 'execution' / 'sn-source')
        return
    with pytest.raises(ValueError):
        build(env, **args)


def test_jointly_rehashed_capsule_cannot_shrink_the_complete_canonical_partition(environment):
    env = environment
    store, refs = env['store'], env['refs']
    partition_id = env['bundle']['partitions'][0]['partition_id']
    revision = 'notebook-request-v3'
    capsule = load_partition(store.root, refs, partition_id, revision)
    capsule['cases'] = [case for case in capsule['cases'] if case['case_id'] == 'qasper:q']
    capsule['partition']['case_ids'] = ['qasper:q']
    capsule['decisions'] = [d for d in capsule['decisions'] if d['case_id'] == 'qasper:q']
    capsule['product'] = partition_bundle(dict(capsule, partitions=[capsule['partition']]),
                                         partition_id, request_revision=revision)
    encoded = json.dumps(capsule).encode()
    index = json.loads((store.resolve(refs['index']) / 'index.json').read_text())
    name = index['partitions'][partition_id][revision]
    identity = json.loads((store.resolve(refs['bundle']) / 'object.json').read_text())['identity']
    identity['partition_capsules'][name] = hashlib.sha256(encoded).hexdigest()
    bundle_ref = republish(store, refs['bundle'], identity=identity)
    index['bundle'] = bundle_ref
    index_ref = republish(store, refs['index'], identity=dict(format=index['format'], bundle=bundle_ref),
        contents={name: encoded, 'index.json': json.dumps(index).encode()})
    # Existing bounded reader accepts the internally consistent capsule. The
    # preparation boundary must compare it with the complete source rebuild.
    reduced = load_partition(store.root, dict(bundle=bundle_ref, index=index_ref), partition_id, revision)
    assert len(reduced['cases']) == 1
    with pytest.raises(ValueError):
        build(env, bundle_ref=bundle_ref, index_ref=index_ref)


def test_rehashed_derived_canonical_bytes_still_require_full_raw_rebuild(environment):
    env = environment
    store, ref = env['store'], env['refs']['bundle']
    directory = store.resolve(ref)
    manifest = json.loads((directory / 'manifest.json').read_text())
    documents = [json.loads(line) for line in (directory / 'documents.jsonl').read_text().splitlines()]
    documents[0]['text'] = 'silently reduced material'
    encoded = ('\n'.join(json.dumps(doc) for doc in documents) + '\n').encode()
    manifest['files']['documents.jsonl'] = hashlib.sha256(encoded).hexdigest()
    bundle_id = fingerprint(manifest)
    identity = json.loads((directory / 'object.json').read_text())['identity']
    identity['bundle_id'] = bundle_id
    forged = republish(store, ref, identity=identity, contents={
        'documents.jsonl': encoded, 'manifest.json': json.dumps(manifest).encode()})
    # Both reference and fixed plan now agree with the altered manifest.
    content = env['job']['plan_snapshot']['content']
    content['input']['bundle_id'] = bundle_id
    env['job']['plan_content_hash'] = env['job']['plan_snapshot']['content_hash'] = fingerprint(content)
    with pytest.raises(ValueError):
        build(env, bundle_ref=forged)


@pytest.mark.parametrize('change', ['bundle', 'suite', 'source_revision', 'adaptation', 'partition', 'case'])
def test_wrong_fixed_identity_or_selection_is_rejected_even_with_valid_plan_digest(environment, change):
    env = environment
    job = deepcopy(env['job'])
    content = job['plan_snapshot']['content']
    if change == 'bundle':
        content['input']['bundle_id'] = '0' * 64
    elif change == 'suite':
        content['input']['suite'] = 'qmsum'
    elif change == 'source_revision':
        content['input']['source_revision'] = 'sha256:' + '0' * 64
    elif change == 'adaptation':
        content['input']['adaptation_revision'] = 'notebook-data-v2'
    else:
        content['selection']['partition_ids' if change == 'partition' else 'case_ids'] = ['unknown']
        content['selection']['selection_hash'] = fingerprint({k: v for k, v in
            content['selection'].items() if k != 'selection_hash'})
    job['plan_content_hash'] = job['plan_snapshot']['content_hash'] = fingerprint(content)
    env['job'] = job
    with pytest.raises(ValueError):
        build(env)


@pytest.mark.parametrize('field', ['job_id', 'task_id', 'workspace_id', 'plan_id',
                                   'plan_revision', 'plan_content_hash', 'job_state_version'])
def test_offline_validator_rejects_rebound_identity_even_with_rehashed_record(environment, field):
    record = build(environment)
    record[field] = 2 if field == 'job_state_version' else 'wrong'
    with pytest.raises(ValueError):
        validate(rehash(record), environment['job'])


@pytest.mark.parametrize('change', ['extra', 'nested_extra', 'missing', 'executable', 'ready',
    'runtime', 'loaded', 'digest', 'partition', 'ref', 'selection', 'intent', 'bool_version', 'roots'])
def test_closed_offline_schema_rejects_false_readiness_and_corruption(environment, change):
    record = build(environment)
    if change == 'extra':
        record['actor'] = 'forged'
    elif change == 'nested_extra':
        record['source']['config'] = 'private'
    elif change == 'missing':
        del record['evaluator']
    elif change == 'executable':
        record['executable'] = True
    elif change == 'ready':
        record['readiness']['missing'] = []
    elif change == 'runtime':
        record['runtime']['status'] = 'captured'
    elif change == 'loaded':
        record['source']['status'] = 'loaded'
    elif change == 'digest':
        record['preparation_id'] = '0' * 64
    elif change == 'partition':
        record['input']['partitions'] = []
    elif change == 'ref':
        record['input']['index_ref']['kind'] = 'bundle'
    elif change == 'selection':
        record['input']['selection']['case_ids'] = ['qasper:q2']
    elif change == 'intent':
        record['execution_intent']['mode'] = 'reasoning'
    elif change == 'bool_version':
        record['job_state_version'] = True
    elif change == 'roots':
        record['source']['selected_roots'] = ['/private/source']
    if change != 'digest':
        rehash(record)
    with pytest.raises(ValueError):
        validate(record, environment['job'])
