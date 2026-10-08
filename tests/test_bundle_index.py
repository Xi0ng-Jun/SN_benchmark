import json
from pathlib import Path

import pytest

from rag_eval.bundle_index import install_bundle, load_partition
from rag_eval.notebook_bundle import prepare, load_bundle, partition_bundle
from rag_eval.run_reader import RunReadContext
from test_notebook_execution import bundle_fixture


def test_capsule_is_same_complete_public_request_and_gold_stays_evaluator_side(tmp_path):
    part = bundle_fixture(tmp_path)
    # Fixture uses historical adaptation; build the official v3 source separately.
    bundle = prepare('qasper', tmp_path/'raw.json', tmp_path/'source.json', tmp_path/'v3',
                     adaptation_revision='notebook-data-v3')
    refs = install_bundle(tmp_path/'v3', tmp_path/'artifacts')
    capsule = load_partition(tmp_path/'artifacts', refs, part, 'notebook-request-v3')
    assert capsule['product'] == partition_bundle(bundle, part, request_revision='notebook-request-v3')
    assert capsule['cases'] == bundle['cases']
    assert 'gold' not in capsule['product']['questions'][0]
    assert 'expected_answer' not in capsule['product']['questions'][0]
    assert capsule['manifest'] == bundle['manifest']
    assert refs == install_bundle(tmp_path/'v3', tmp_path/'artifacts')


def test_canonical_bundle_rebuilt_once_per_operation(tmp_path, monkeypatch):
    part = bundle_fixture(tmp_path)
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    calls = []
    from rag_eval import notebook_bundle
    original = notebook_bundle.load_bundle
    def count(path):
        calls.append(Path(path)); return original(path)
    monkeypatch.setattr(notebook_bundle, 'load_bundle', count)
    reader = RunReadContext()
    from rag_eval.artifact_store import ArtifactStore
    directory = ArtifactStore(tmp_path/'artifacts').resolve(refs['bundle'])
    a = reader.bundle(directory); b = reader.bundle(directory)
    assert a is b
    assert len(calls) == 1


def test_missing_partition_or_tampered_capsule_rejected(tmp_path):
    part = bundle_fixture(tmp_path)
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    with pytest.raises(ValueError, match='partition'):
        load_partition(tmp_path/'artifacts', refs, 'unknown', 'notebook-request-v3')
    from rag_eval.artifact_store import ArtifactStore
    directory = ArtifactStore(tmp_path/'artifacts').resolve(refs['index'])
    files = [p for p in directory.rglob('*.json') if p.name not in {'object.json','index.json'}]
    files[0].write_text('{}')
    with pytest.raises(ValueError, match='hash|changed'):
        load_partition(tmp_path/'artifacts', refs, part, 'notebook-request-v3')


def test_shared_execution_report_dashboard_and_rescore_without_input_copies(tmp_path, monkeypatch):
    from test_notebook_execution import test_execution_preserves_clarification_and_dashboard_pairing
    from rag_eval import notebook_runner
    execute = notebook_runner.execute
    calls = []
    def shared_execute(**kwargs):
        refs = install_bundle(kwargs['bundle_dir'], tmp_path/'artifacts')
        kwargs['artifact_index_id'] = refs['index']['id']
        # The original test substitutes the product runtime and source snapshot;
        # no model or SN code is executed here.
        from rag_eval import runtime_environment
        snapshot = runtime_environment.snapshot_sources
        monkeypatch.setattr(runtime_environment, 'snapshot_sources', lambda *a, **k: snapshot(*a))
        kwargs['artifact_root'] = tmp_path/'artifacts'
        calls.append(kwargs['run'])
        return execute(**kwargs)
    import test_notebook_execution as fixture
    monkeypatch.setattr(fixture, 'execute', shared_execute)
    test_execution_preserves_clarification_and_dashboard_pairing(tmp_path, monkeypatch)
    from rag_eval.notebook_rescoring import rescore_run
    for path in calls:
        assert not (path/'input').exists()
        assert (path/'artifact-refs.json').is_file()
    # The reused fixture ends by deliberately corrupting chunk's saved product.
    # Restore the original tested capsule before exercising derived scoring.
    from rag_eval.artifact_store import read_run_refs, store_for_run
    refs = read_run_refs(calls[0])
    source_manifest = json.loads((calls[0]/'manifest.json').read_text())
    part = source_manifest['identity']['notebook_context']['partition_id']
    product = load_partition(store_for_run(calls[0]).root, refs, part, 'notebook-request-v1')['product']
    (calls[0]/'product-bundle.json').write_text(json.dumps(product))
    derived = rescore_run(calls[0], tmp_path/'derived', all_scores=True)
    assert not (derived/'input').exists()
    from rag_eval.run_report import load_run
    assert load_run(derived)['manifest']['planned_predictions'] == 2


def test_partition_only_reader_does_not_rebuild_entire_canonical_data(tmp_path, monkeypatch):
    part = bundle_fixture(tmp_path)
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    run = tmp_path/'run'; run.mkdir()
    from rag_eval.artifact_store import write_run_refs
    write_run_refs(run, tmp_path/'artifacts', refs)
    (run/'manifest.json').write_text(json.dumps({'identity': {'notebook_context': {'partition_id': part}}}))
    from rag_eval import notebook_bundle
    monkeypatch.setattr(notebook_bundle, 'load_bundle', lambda _: pytest.fail('canonical rebuild on bounded read'))
    reader = RunReadContext(partition_only=True)
    bundle = reader.bundle_for_run(run)
    assert bundle['partitions'][0]['partition_id'] == part
    assert reader.stats['canonical_rebuilds'] == 0
    assert reader.partition(bundle, part, 'notebook-request-v1') == load_partition(tmp_path/'artifacts', refs, part, 'notebook-request-v1')['product']


def test_partition_reader_context_handles_multiple_hotpot_partitions(tmp_path):
    from test_hotpotqa import hotpot_row, hotpot_source
    from rag_eval.notebook_bundle import prepare
    from rag_eval.artifact_store import write_run_refs
    rows = [hotpot_row(), dict(hotpot_row(), _id='second')]
    raw = tmp_path/'raw.json'; raw.write_text(json.dumps(rows))
    source = tmp_path/'source.json'; source.write_text(json.dumps(hotpot_source()))
    bundle = prepare('hotpotqa', raw, source, tmp_path/'bundle', max_documents=3,
                     adaptation_revision='notebook-data-v3')
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    reader = RunReadContext(partition_only=True)
    for ordinal, part in enumerate(bundle['partitions']):
        run = tmp_path/f'run-{ordinal}'; run.mkdir()
        write_run_refs(run, tmp_path/'artifacts', refs)
        (run/'manifest.json').write_text(json.dumps({'identity': {'notebook_context': {
            'partition_id': part['partition_id'], 'request_revision': 'notebook-request-v3'}}}))
        subset = reader.bundle_for_run(run)
        product = reader.partition(subset, part['partition_id'], 'notebook-request-v3')
        assert [c['case_id'] for c in reader.selected_cases(subset, product)] == part['case_ids']
    assert len(bundle['partitions']) == 2
    assert reader.stats['canonical_rebuilds'] == 0


def test_selected_object_verification_does_not_cache_unverified_files(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    store = ArtifactStore(tmp_path/'artifacts')
    ref = store.install_bytes('index', {'a': b'a', 'b': b'b'}, identity={})
    reader = RunReadContext()
    directory = reader.object(store, ref, files=['a'])
    (directory/'b').write_bytes(b'changed')
    with pytest.raises(ValueError, match='hash|changed'):
        reader.object(store, ref)


def test_capsule_public_request_is_rebuilt_at_consumer_boundary(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    part = bundle_fixture(tmp_path)
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    store = ArtifactStore(tmp_path/'artifacts')
    directory = store.resolve(refs['index'])
    index = json.loads((directory/'index.json').read_text())
    name = index['partitions'][part]['notebook-request-v3']
    files = {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob('*')
             if p.is_file() and p.name != 'object.json'}
    capsule = json.loads(files[name]); capsule['product']['questions'][0]['gold'] = 'injected'
    files[name] = json.dumps(capsule).encode()
    refs['index'] = store.install_bytes('index', files, identity={})
    with pytest.raises(ValueError, match='request|product|binding'):
        load_partition(store.root, refs, part, 'notebook-request-v3')


@pytest.mark.parametrize('baseline', [False, True])
def test_runtime_cannot_overlap_artifact_store(tmp_path, baseline):
    from rag_eval import notebook_runner, notebook_baseline_runner
    kwargs = dict(root=tmp_path/'code', project=tmp_path/'product', bundle_dir=tmp_path/'bundle',
                  run=tmp_path/'artifacts/run', artifact_root=tmp_path/'artifacts', partition_id='p')
    if baseline:
        kwargs['model_config'] = tmp_path/'model.json'
        execute = notebook_baseline_runner.execute
    else:
        kwargs['mode'] = 'chunk'
        execute = notebook_runner.execute
    with pytest.raises(ValueError, match='artifact store'):
        execute(**kwargs)


def test_replacement_index_cannot_change_canonical_partition_under_same_bundle(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.notebook_bundle import partition_bundle
    part = bundle_fixture(tmp_path)
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    store = ArtifactStore(tmp_path/'artifacts'); directory = store.resolve(refs['index'])
    index = json.loads((directory/'index.json').read_text())
    name = index['partitions'][part]['notebook-request-v3']
    files = {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob('*')
             if p.is_file() and p.name != 'object.json'}
    capsule = json.loads(files[name]); capsule['cases'][0]['question'] = 'GOLD injected into question'
    subset = dict(capsule, partitions=[capsule['partition']]); subset.pop('qasper_evidence_catalogues', None)
    capsule['product'] = partition_bundle(subset, part, request_revision='notebook-request-v3')
    files[name] = json.dumps(capsule).encode()
    refs['index'] = store.install_bytes('index', files, identity={})
    with pytest.raises(ValueError, match='canonical|binding'):
        load_partition(store.root, refs, part, 'notebook-request-v3')


def test_shared_baseline_cli_preserves_offline_reader_and_derived_scoring(tmp_path, monkeypatch):
    from test_notebook_baseline import run_fixture
    from rag_eval import notebook_baseline_runner, runtime_environment
    original = notebook_baseline_runner.execute
    def shared(**kwargs):
        refs = install_bundle(kwargs['bundle_dir'], tmp_path/'artifacts')
        kwargs['artifact_index_id'] = refs['index']['id']
        snapshot = runtime_environment.snapshot_sources
        monkeypatch.setattr(runtime_environment, 'snapshot_sources', lambda *a, **k: snapshot(*a))
        kwargs['artifact_root'] = tmp_path/'artifacts'
        return original(**kwargs)
    monkeypatch.setattr(notebook_baseline_runner, 'execute', shared)
    run = run_fixture(tmp_path, monkeypatch)
    from rag_eval.run_report import load_run
    from rag_eval.notebook_rescoring import rescore_run
    assert not (run/'input').exists()
    assert len(load_run(run)['outputs']) == 2
    derived = rescore_run(run, tmp_path/'rescored', all_scores=True)
    assert not (derived/'input').exists()
    assert len(load_run(derived)['outputs']) == 2


def test_shared_alce_attachment_preserves_reference_and_scores(tmp_path):
    import shutil
    from test_notebook_alce_results import saved_run, score_artifact
    from rag_eval.artifact_store import write_run_refs, reference_identity, ArtifactStore
    from rag_eval.notebook_alce_results import attach_scores
    from rag_eval.run_report import load_run
    run = saved_run(tmp_path)
    refs = install_bundle(run/'input', tmp_path/'artifacts')
    write_run_refs(run, tmp_path/'artifacts', refs)
    manifest = json.loads((run/'manifest.json').read_text()); manifest.update(reference_identity(run))
    (run/'manifest.json').write_text(json.dumps(manifest)); shutil.rmtree(run/'input')
    official = score_artifact(tmp_path, run)
    derived = attach_scores(run, official, tmp_path/'derived')
    assert not (tmp_path/'derived/input').exists()
    assert [r['score'] for r in load_run(tmp_path/'derived')['scores'] if r['status']=='scored'] == [.75,.5]
    forbidden = ArtifactStore(tmp_path/'artifacts').resolve(refs['index'])/'bad-derived'
    with pytest.raises(ValueError, match='artifact store'):
        attach_scores(run, official, forbidden)
    assert not forbidden.exists()


def test_pinned_installer_identity_rejects_simultaneous_bundle_index_replacement(tmp_path):
    import hashlib
    from rag_eval.artifact_store import ArtifactStore, atomic_pointer
    from rag_eval.bundle_index import find_installed_bundle
    from rag_eval.notebook_bundle import partition_bundle
    from rag_eval.identity import fingerprint
    part = bundle_fixture(tmp_path)
    refs = install_bundle(tmp_path/'bundle', tmp_path/'artifacts')
    expected = refs['index']['id']
    store = ArtifactStore(tmp_path/'artifacts'); directory = store.resolve(refs['index'])
    files = {p.relative_to(directory).as_posix(): p.read_bytes() for p in directory.rglob('*')
             if p.is_file() and p.name != 'object.json'}
    index = json.loads(files['index.json']); name = index['partitions'][part]['notebook-request-v3']
    capsule = json.loads(files[name]); capsule['cases'][0]['question'] = 'FORGED REQUEST'
    subset = dict(capsule, partitions=[capsule['partition']]); subset.pop('qasper_evidence_catalogues', None)
    capsule['product'] = partition_bundle(subset, part, request_revision='notebook-request-v3')
    files[name] = json.dumps(capsule).encode()
    original = store.resolve(refs['bundle']); obj = json.loads((original/'object.json').read_text())
    identity = dict(obj['identity']); identity['partition_capsules'][name] = hashlib.sha256(files[name]).hexdigest()
    refs['bundle'] = store.install_files('bundle', {n: original/n for n in obj['files']}, identity=identity)
    index['bundle'] = refs['bundle']; files['index.json'] = json.dumps(index).encode()
    refs['index'] = store.install_bytes('index', files, identity={})
    atomic_pointer(store.root/'bundle-indexes'/f"{fingerprint(index['source_manifest'])}.json", refs)
    with pytest.raises(ValueError, match='pinned'):
        find_installed_bundle(tmp_path/'bundle', store.root, expected_index_id=expected)


def test_install_cannot_mutate_original_bundle_directory(tmp_path):
    bundle_fixture(tmp_path)
    before = {p.relative_to(tmp_path/'bundle'): p.read_bytes() for p in (tmp_path/'bundle').rglob('*') if p.is_file()}
    with pytest.raises(ValueError, match='separate|overlap'):
        install_bundle(tmp_path/'bundle', tmp_path/'bundle/artifacts')
    after = {p.relative_to(tmp_path/'bundle'): p.read_bytes() for p in (tmp_path/'bundle').rglob('*') if p.is_file()}
    assert after == before
