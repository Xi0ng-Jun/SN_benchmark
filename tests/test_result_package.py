"""Offline packaging must preserve evidence without copying private runtime state."""
import hashlib
import json
from pathlib import Path
import shutil
import stat
import subprocess
import sys
import tarfile
from time import perf_counter
import tracemalloc

import pytest

from rag_eval.result_package import inventory, package_campaign, validate_package


def write(root, name, value):
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value) + '\n' if not isinstance(value, str) else value)
    return path


def campaign(root):
    write(root, 'scope.json', {'case_ids': ['one']})
    write(root, 'runs/one/planned.jsonl', {'case_id': 'one'})
    planned = hashlib.sha256((root / 'runs/one/planned.jsonl').read_bytes()).hexdigest()
    write(root, 'runs/one/manifest.json', {'run_id': 'one', 'planned_sha256': planned})
    write(root, 'runs/one/state.json', {'phase': 'finished'})
    write(root, 'runs/one/source-identity.json', {'versions': {}})
    write(root, 'runs/one/product-bundle.json', {'documents': []})
    write(root, 'runs/one/outputs.jsonl', {'case_id': 'one', 'answer': 'secret is a normal word'})
    write(root, 'runs/one/scores.jsonl', {'case_id': 'one', 'status': 'pending'})
    write(root, 'runs/one/agent/components.jsonl', {'case_id': 'one', 'input': 'question'})
    write(root, 'runs/one/agent/native-traces.jsonl', {'trace': []})
    write(root, 'runs/one/agent/native-scores.jsonl', {'status': 'error'})
    write(root, 'runs/one/runtime/database.db', 'private db')
    write(root, 'runs/one/runtime/storage/raw.txt', 'private storage')
    write(root, 'runs/one/runtime/model-services.toml', 'api_key = "private"')
    write(root, 'runs/one/model-events.jsonl', {'api_key': 'private'})
    write(root, 'runs/one/agent/judge-events.jsonl', {'api_key': 'private'})
    write(root, 'submissions/one/submission.json', {'predictions': []})
    write(root, 'official-scores/one/scores.json', {'metrics': {'f1': 0}})
    write(root, 'official-scores/one/prepared.json', {'data': []})
    write(root, 'official-scores/one/credentials.json', {'api_key': 'private'})
    write(root, 'reports/comparison.md', '# report\n')
    write(root, 'arbitrary.json', {'api_key': 'private'})
    return root


def test_inventory_counts_actual_bytes_and_categories_without_mutation(tmp_path):
    root = tmp_path / 'campaign'
    write(root, 'runs/one/input/cases.jsonl', 'input')
    write(root, 'runs/one/source/source.py', 'source')
    write(root, 'runs/one/runtime/database.db', 'runtime')
    write(root, 'runs/one/outputs.jsonl', 'outputs')
    write(root, 'runs/one/agent/components.jsonl', 'agent')
    write(root, 'official-scores/scores.json', 'official')
    write(root, 'reports/report.md', 'report')
    write(root, 'unknown', 'other')
    report = inventory(root)
    assert report['totals']['file_count'] == 8
    assert report['totals']['logical_bytes'] == 49
    assert {key: value['file_count'] for key, value in report['categories'].items()} == {
        'input': 1, 'source': 1, 'runtime': 1, 'outputs': 1, 'agent': 1,
        'official': 1, 'report': 1, 'other': 1,
    }
    assert report['categories']['runtime']['logical_bytes'] == 7
    assert report['elapsed_seconds'] >= 0
    assert report['totals']['allocated_bytes'] is None or report['totals']['allocated_bytes'] >= 0
    assert len([path for path in root.rglob('*') if path.is_file()]) == 8


def test_results_package_allowlist_and_private_permissions(tmp_path):
    root = campaign(tmp_path / 'campaign')
    before = {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}
    report = package_campaign(root, tmp_path / 'results', mode='results')
    output = tmp_path / 'results'
    assert (output / 'submissions/one/submission.json').is_file()
    assert (output / 'official-scores/one/prepared.json').is_file()
    assert (output / 'runs/one/source-identity.json').is_file()
    assert (output / 'runs/one/state.json').is_file()
    assert (output / 'reports/comparison.md').is_file()
    assert not (output / 'runs/one/runtime').exists()
    assert not (output / 'runs/one/outputs.jsonl').exists()
    assert not (output / 'runs/one/agent').exists()
    assert not (output / 'official-scores/one/credentials.json').exists()
    assert not (output / 'arbitrary.json').exists()
    assert report['package_bytes'] == (tmp_path / 'results.tar.gz').stat().st_size
    assert report['elapsed_seconds'] >= 0
    assert (tmp_path / 'results.tar.gz.receipt.json').is_file()
    assert validate_package(output)['valid'] is True
    for path in [output, *output.rglob('*'), tmp_path / 'results.tar.gz', tmp_path / 'results.tar.gz.receipt.json']:
        assert stat.S_IMODE(path.stat().st_mode) == (0o700 if path.is_dir() else 0o600)
    assert before == {path.relative_to(root): path.read_bytes() for path in root.rglob('*') if path.is_file()}


def test_archive_only_has_inventory_and_selected_evidence(tmp_path):
    root = campaign(tmp_path / 'campaign')
    output = tmp_path / 'review.tar.gz'
    package_campaign(root, output, mode='review')
    with tarfile.open(output) as archive:
        names = archive.getnames()
        assert 'inventory.json' in names
        assert 'runs/one/outputs.jsonl' in names
        assert 'runs/one/planned.jsonl' in names
        assert 'runs/one/product-bundle.json' in names
        assert 'runs/one/agent/components.jsonl' in names
        assert 'runs/one/agent/native-traces.jsonl' in names
        assert 'runs/one/agent/native-scores.jsonl' in names
        assert not any('runtime' in name or 'judge-events' in name or 'model-events' in name for name in names)
        assert all(item.mode == (0o700 if item.isdir() else 0o600) for item in archive.getmembers())
        assert not any(item.issym() or item.islnk() for item in archive.getmembers())


@pytest.mark.parametrize('kind', ['inside', 'existing', 'symlink', 'bad-hash', 'duplicate'])
def test_invalid_paths_identities_and_duplicate_attempts_fail_closed(tmp_path, kind):
    root = campaign(tmp_path / 'campaign')
    output = tmp_path / 'output.tar.gz'
    if kind == 'inside':
        output = root / 'result.tar.gz'
    elif kind == 'existing':
        output.write_text('do not overwrite')
    elif kind == 'symlink':
        (root / 'runs/one/runtime/link').symlink_to(tmp_path)
    elif kind == 'bad-hash':
        write(root, 'runs/one/planned.jsonl', {'case_id': 'tampered'})
    elif kind == 'duplicate':
        write(root, 'runs/two/manifest.json', {'run_id': 'one'})
    with pytest.raises((ValueError, FileExistsError)):
        package_campaign(root, output, mode='review')
    assert (root / 'runs/one/runtime/database.db').read_text() == 'private db'


def test_credential_keys_are_rejected_but_free_text_is_preserved(tmp_path):
    root = campaign(tmp_path / 'campaign')
    write(root, 'submissions/one/submission.json', {'answer': 'normal text', 'api_key': 'private'})
    with pytest.raises(ValueError, match='credential'):
        package_campaign(root, tmp_path / 'invalid', mode='results')
    write(root, 'submissions/one/submission.json', {'answer': 'password and secret are normal prose'})
    package_campaign(root, tmp_path / 'valid', mode='results')
    assert json.loads((tmp_path / 'valid/submissions/one/submission.json').read_text())['answer'] == 'password and secret are normal prose'


def test_validation_detects_tampering_and_missing_files(tmp_path):
    root = campaign(tmp_path / 'campaign')
    package_campaign(root, tmp_path / 'review', mode='review')
    write(tmp_path / 'review', 'runs/one/outputs.jsonl', {'answer': 'changed'})
    with pytest.raises(ValueError, match='hash'):
        validate_package(tmp_path / 'review')


def test_cli_requires_explicit_campaign_output_and_mode(tmp_path):
    root = campaign(tmp_path / 'campaign')
    script = Path(__file__).resolve().parents[1] / 'scripts/package_benchmark_results.py'
    completed = subprocess.run([sys.executable, str(script), '--campaign', str(root),
                                '--output', str(tmp_path / 'cli.tar.gz'), '--mode', 'results'],
                               text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    assert json.loads(completed.stdout)['package_bytes'] > 0
    missing = subprocess.run([sys.executable, str(script), '--campaign', str(root)],
                             text=True, capture_output=True)
    assert missing.returncode == 2


def shared_campaign(root):
    from rag_eval.artifact_store import ArtifactStore, write_run_refs
    campaign(root)
    store = ArtifactStore(root / 'artifacts')
    refs = {
        'bundle': store.install_bytes('bundle', {'data.json': b'{}'}, identity={'suite': 'fixture'}),
        'index': store.install_bytes('index', {'index.json': b'{}'}, identity={}),
        'sources': {
            'evaluator': store.install_bytes('evaluator', {'source.py': b'print(1)'}, identity={}),
            'sn': store.install_bytes('sn', {'product-source.tar': b'offline source bytes'}, identity={}),
        },
    }
    write_run_refs(root / 'runs/one', root / 'artifacts', refs)
    write(root, 'runs/two/manifest.json', {'run_id': 'two'})
    write_run_refs(root / 'runs/two', root / 'artifacts', refs)
    return refs


def test_shared_objects_pack_once_and_resolve_after_relocation(tmp_path):
    from rag_eval.artifact_store import ArtifactStore, read_run_refs, store_for_run
    root = tmp_path / 'campaign'
    refs = shared_campaign(root)
    unused = ArtifactStore(root / 'artifacts').install_bytes('sn', {'unused': b'excluded'}, identity={})
    package_campaign(root, tmp_path / 'packed', mode='review')
    relocated = tmp_path / 'moved/deep/packed'
    relocated.parent.mkdir(parents=True)
    (tmp_path / 'packed').rename(relocated)
    assert len(list((relocated / 'artifacts/objects').glob('*/*/object.json'))) == 4
    assert not (relocated / 'artifacts/objects/sn' / unused['id']).exists()
    for name in ('one', 'two'):
        run = relocated / 'runs' / name
        packed_refs = read_run_refs(run)
        assert packed_refs['root'] == '../../artifacts'
        assert packed_refs['bundle'] == refs['bundle']
        assert (store_for_run(run).resolve(packed_refs['bundle']) / 'data.json').read_bytes() == b'{}'
    assert validate_package(relocated)['valid']


@pytest.mark.parametrize('mode', ['results', 'review'])
def test_shared_declared_artifact_tampering_rejected_in_both_modes(tmp_path, mode):
    from rag_eval.artifact_store import ArtifactStore
    root = tmp_path / 'campaign'
    refs = shared_campaign(root)
    directory = ArtifactStore(root / 'artifacts').resolve(refs['bundle'])
    (directory / 'data.json').write_bytes(b'{"tampered":true}')
    with pytest.raises(ValueError, match='hash'):
        package_campaign(root, tmp_path / 'invalid', mode=mode)


def test_review_preserves_official_attachment_dependencies(tmp_path):
    root = campaign(tmp_path / 'campaign')
    for name in ('input.json', 'invocation.json', 'execution.json', 'scores.json'):
        write(root, 'runs/one/official-scoring/' + name, {'offline': True})
    write(root, 'runs/one/official-scoring/official-source/eval.py', 'def score(): pass\n')
    package_campaign(root, tmp_path / 'review', mode='review')
    assert (tmp_path / 'review/runs/one/official-scoring/invocation.json').is_file()
    assert (tmp_path / 'review/runs/one/official-scoring/official-source/eval.py').is_file()


def test_declared_legacy_source_missing_is_not_silently_accepted(tmp_path):
    root = campaign(tmp_path / 'campaign')
    write(root, 'runs/one/source-identity.json', {'benchmark_source_hashes': {'source.py': 'a' * 64}})
    with pytest.raises(ValueError, match='source'):
        package_campaign(root, tmp_path / 'invalid', mode='results')


def test_legacy_review_copies_only_declared_input_and_source_files(tmp_path):
    from test_notebook_execution import bundle_fixture
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    bundle_fixture(fixture)
    root = campaign(tmp_path / 'campaign')
    shutil.copytree(fixture / 'bundle', root / 'runs/one/input')
    source = write(root, 'runs/one/source/source.py', 'print("offline")\n')
    write(root, 'runs/one/source-identity.json', {
        'benchmark_source_hashes': {'source.py': hashlib.sha256(source.read_bytes()).hexdigest()},
    })
    write(root, 'runs/one/source/credentials.json', {'api_key': 'private'})
    write(root, 'runs/one/input/credentials.json', {'api_key': 'private'})
    package_campaign(root, tmp_path / 'review', mode='review')
    assert (tmp_path / 'review/runs/one/input/raw-data').is_file()
    assert (tmp_path / 'review/runs/one/source/source.py').is_file()
    assert not (tmp_path / 'review/runs/one/input/credentials.json').exists()
    assert not (tmp_path / 'review/runs/one/source/credentials.json').exists()


@pytest.mark.parametrize('shared', [False, True])
def test_packaged_synthetic_run_can_read_and_rescore_offline(tmp_path, monkeypatch, shared):
    from test_notebook_baseline import run_fixture
    from rag_eval.artifact_store import reference_identity, write_run_refs
    from rag_eval.bundle_index import install_bundle
    from rag_eval.notebook_rescoring import rescore_run
    from rag_eval.run_report import load_run
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    generated = run_fixture(fixture, monkeypatch)
    root = tmp_path / 'campaign'
    run = root / 'runs' / generated.name
    shutil.copytree(generated, run)
    if shared:
        refs = install_bundle(run / 'input', root / 'artifacts')
        write_run_refs(run, root / 'artifacts', refs)
        manifest = json.loads((run / 'manifest.json').read_text())
        write(run, 'manifest.json', {**manifest, **reference_identity(run)})
    before = load_run(run)
    package_campaign(root, tmp_path / 'review', mode='review')
    relocated = tmp_path / 'relocated' / 'review'
    relocated.parent.mkdir()
    (tmp_path / 'review').rename(relocated)
    packed_run = relocated / 'runs' / generated.name
    after = load_run(packed_run)
    assert after['outputs'] == before['outputs']
    assert after['scores'] == before['scores']
    from rag_eval import notebook_scoring
    monkeypatch.setattr(notebook_scoring, 'score_case', lambda *args: {
        'status': 'scored', 'score': 0.75, 'details': {'offline': True},
    })
    rescored = rescore_run(packed_run, tmp_path / 'rescored', all_scores=True)
    assert all(row['score'] == 0.75 for row in load_run(rescored)['scores'])


def test_results_shared_identity_is_preserved_without_shared_payload(tmp_path):
    root = tmp_path / 'campaign'
    refs = shared_campaign(root)
    package_campaign(root, tmp_path / 'results', mode='results')
    identity = json.loads((tmp_path / 'results/runs/one/artifact-identities.json').read_text())
    assert identity['bundle'] == refs['bundle']
    assert 'root' not in identity
    assert not (tmp_path / 'results/artifacts').exists()


def test_inventory_walk_is_bounded_and_rejects_links(tmp_path, monkeypatch):
    import rag_eval.result_package as packages
    root = campaign(tmp_path / 'campaign')
    monkeypatch.setattr(packages, 'MAX_FILES', 1)
    with pytest.raises(ValueError, match='limit'):
        inventory(root)
    monkeypatch.setattr(packages, 'MAX_FILES', 1_000_000)
    (root / 'link').symlink_to(tmp_path)
    with pytest.raises(ValueError, match='Symlink'):
        inventory(root)


def test_manifest_attachment_declared_hash_must_match_before_packaging(tmp_path):
    root = campaign(tmp_path / 'campaign')
    manifest = json.loads((root / 'runs/one/manifest.json').read_text())
    write(root, 'runs/one/manifest.json', {**manifest, 'scoring_attachment': {
        'files': {'outputs.jsonl': 'a' * 64},
    }})
    with pytest.raises(ValueError, match='hash'):
        package_campaign(root, tmp_path / 'invalid', mode='review')


def test_shared_source_identity_uses_shared_source_object(tmp_path):
    root = tmp_path / 'campaign'
    shared_campaign(root)
    write(root, 'runs/one/source-identity.json', {
        'benchmark_source_hashes': {'source.py': hashlib.sha256(b'print(1)').hexdigest()},
        'product_archive_sha256': hashlib.sha256(b'offline source bytes').hexdigest(),
    })
    package_campaign(root, tmp_path / 'review', mode='review')
    assert validate_package(tmp_path / 'review')['valid']
    assert not (tmp_path / 'review/runs/one/source').exists()


def test_reference_relocation_records_attachment_hash_rewrite(tmp_path):
    root = tmp_path / 'campaign'
    shared_campaign(root)
    (root / 'artifacts').rename(root / 'shared-store')
    for name in ('one', 'two'):
        refs = json.loads((root / 'runs' / name / 'artifact-refs.json').read_text())
        write(root, f'runs/{name}/artifact-refs.json', {**refs, 'root': '../../shared-store'})
    run = root / 'runs/one'
    manifest = json.loads((run / 'manifest.json').read_text())
    old_hash = hashlib.sha256((run / 'artifact-refs.json').read_bytes()).hexdigest()
    write(run, 'manifest.json', {**manifest, 'scoring_attachment': {
        'files': {'artifact-refs.json': old_hash},
    }})
    package_campaign(root, tmp_path / 'review', mode='review')
    packed_run = tmp_path / 'review/runs/one'
    provenance = json.loads((packed_run / 'package-provenance.json').read_text())
    assert provenance['reference_rewrites']['artifact-refs.json']['original_sha256'] == old_hash
    assert json.loads((packed_run / 'package-origin-manifest.json').read_text()) == json.loads((run / 'manifest.json').read_text())
    assert json.loads((run / 'artifact-refs.json').read_text())['root'] == '../../shared-store'
    assert validate_package(tmp_path / 'review')['valid']


def test_archive_records_private_directory_permissions(tmp_path):
    root = campaign(tmp_path / 'campaign')
    output = tmp_path / 'results.tar.gz'
    package_campaign(root, output, mode='results')
    with tarfile.open(output) as archive:
        directories = [item for item in archive.getmembers() if item.isdir()]
        assert directories
        assert all(item.mode == 0o700 for item in directories)


def test_inventory_directory_entries_are_bounded_even_without_files(tmp_path, monkeypatch):
    import rag_eval.result_package as packages
    root = tmp_path / 'directories'
    (root / 'one').mkdir(parents=True)
    (root / 'two').mkdir()
    (root / 'three').mkdir()
    monkeypatch.setattr(packages, 'MAX_ENTRIES', 2, raising=False)
    with pytest.raises(ValueError, match='entry limit'):
        inventory(root)


def test_packaging_verifies_each_shared_object_once_per_physical_store(tmp_path, monkeypatch):
    from collections import Counter
    from rag_eval.artifact_store import ArtifactStore
    root = tmp_path / 'campaign'
    shared_campaign(root)
    source_hash = hashlib.sha256(b'print(1)').hexdigest()
    for name in ('one', 'two'):
        write(root, f'runs/{name}/source-identity.json', {'benchmark_source_hashes': {'source.py': source_hash}})
    calls = Counter()
    original = ArtifactStore.resolve

    def counted(store, reference, **kwargs):
        calls[(str(store.root), reference['kind'], reference['id'])] += 1
        return original(store, reference, **kwargs)

    monkeypatch.setattr(ArtifactStore, 'resolve', counted)
    package_campaign(root, tmp_path / 'review', mode='review')
    assert len(calls) == 8
    assert set(calls.values()) == {1}


def test_package_source_inventory_scans_once_without_opening_runtime(tmp_path, monkeypatch):
    import rag_eval.result_package as packages
    root = campaign(tmp_path / 'campaign')
    runtime = root / 'runs/one/runtime/database.db'
    with runtime.open('wb') as handle:
        handle.truncate(1024 * 1024 * 1024)
    walks = []
    original_walk = packages._walk
    original_open = Path.open

    def counted_walk(directory):
        walks.append(directory)
        return original_walk(directory)

    def guarded_open(path, *args, **kwargs):
        assert 'runtime' not in path.relative_to(root).parts if path.is_relative_to(root) else True
        return original_open(path, *args, **kwargs)

    monkeypatch.setattr(packages, '_walk', counted_walk)
    monkeypatch.setattr(Path, 'open', guarded_open)
    receipt = package_campaign(root, tmp_path / 'results', mode='results')
    assert walks.count(root) == 1
    assert receipt['inventory']['categories']['runtime']['logical_bytes'] > 1024 * 1024 * 1024


@pytest.mark.parametrize('shared', [False, True])
def test_canonical_bundle_reconstruction_is_not_repeated_within_package(tmp_path, monkeypatch, shared):
    from collections import Counter
    from test_notebook_baseline import run_fixture
    from rag_eval import notebook_bundle
    from rag_eval.artifact_store import reference_identity, write_run_refs
    from rag_eval.bundle_index import install_bundle
    fixture = tmp_path / 'fixture'
    fixture.mkdir()
    generated = run_fixture(fixture, monkeypatch)
    root = tmp_path / 'campaign'
    run = root / 'runs' / generated.name
    shutil.copytree(generated, run)
    if shared:
        refs = install_bundle(run / 'input', root / 'artifacts')
        write_run_refs(run, root / 'artifacts', refs)
        manifest = json.loads((run / 'manifest.json').read_text())
        write(run, 'manifest.json', {**manifest, **reference_identity(run)})
    calls = Counter()
    original = notebook_bundle.load_bundle

    def counted(directory):
        calls[str(directory)] += 1
        return original(directory)

    monkeypatch.setattr(notebook_bundle, 'load_bundle', counted)
    package_campaign(root, tmp_path / 'review', mode='review')
    assert sum(calls.values()) == (1 if shared else 2)
    assert set(calls.values()) == {1}


def test_saved_shared_source_identity_cannot_omit_referenced_source_files(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    root = tmp_path / 'campaign'
    shared_campaign(root)
    refs = json.loads((root / 'runs/one/artifact-refs.json').read_text())
    refs['sources']['evaluator'] = ArtifactStore(root / 'artifacts').install_bytes(
        'evaluator', {'source.py': b'print(1)', 'another.py': b'print(2)'}, identity={})
    write(root, 'runs/one/artifact-refs.json', refs)
    write(root, 'runs/one/source-identity.json', {
        'benchmark_source_hashes': {'source.py': hashlib.sha256(b'print(1)').hexdigest()},
    })
    with pytest.raises(ValueError, match='source identity'):
        package_campaign(root, tmp_path / 'invalid', mode='review')


def test_product_source_revision_must_match_shared_snapshot_identity(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    root = tmp_path / 'campaign'
    shared_campaign(root)
    refs = json.loads((root / 'runs/one/artifact-refs.json').read_text())
    refs['sources']['sn'] = ArtifactStore(root / 'artifacts').install_bytes(
        'sn', {'product-source.tar': b'offline source bytes'}, identity={'product_revision': 'actual'})
    write(root, 'runs/one/artifact-refs.json', refs)
    write(root, 'runs/one/source-identity.json', {
        'product_revision': 'different',
        'product_archive_sha256': hashlib.sha256(b'offline source bytes').hexdigest(),
    })
    with pytest.raises(ValueError, match='source revision'):
        package_campaign(root, tmp_path / 'invalid', mode='results')


@pytest.mark.parametrize('count', [1000, 5000, 10000])
def test_synthetic_shared_package_scale_and_readback(tmp_path, count, record_property):
    from rag_eval.artifact_store import ArtifactStore, write_run_refs
    from rag_eval.bundle_index import install_bundle
    from rag_eval.notebook_bundle import prepare, partition_bundle
    from rag_eval.run_reader import RunReadContext
    root = tmp_path / 'campaign'
    setup_started = perf_counter()
    raw = {'paper': {'title': 'Synthetic', 'abstract': 'Birds fly.',
                     'full_text': [{'section_name': 'Results', 'paragraphs': ['Red birds fly.']}],
                     'qas': [{'question_id': str(index), 'question': 'What flies?',
                              'answers': [{'answer': {'unanswerable': False, 'extractive_spans': ['birds'],
                                                      'free_form_answer': '', 'evidence': ['Red birds fly.'],
                                                      'yes_no': None}}]} for index in range(count)]}}
    write(tmp_path, 'raw.json', raw)
    write(tmp_path, 'source.json', {'dataset': 'qasper', 'split': 'test', 'revision': 'synthetic',
                                   'source_url': 'https://example.org/synthetic', 'license': 'fixture'})
    bundle = prepare('qasper', tmp_path / 'raw.json', tmp_path / 'source.json', tmp_path / 'bundle',
                     adaptation_revision='notebook-data-v3')
    refs = install_bundle(tmp_path / 'bundle', root / 'artifacts')
    store = ArtifactStore(root / 'artifacts')
    refs['sources'] = {'evaluator': store.install_bytes('evaluator', {'source.py': b'print(1)'}, identity={}),
                       'sn': store.install_bytes('sn', {'product-source.tar': b'offline product'}, identity={})}
    product = partition_bundle(bundle, bundle['partitions'][0]['partition_id'], request_revision='notebook-request-v3')
    for attempt in range(4):
        run = root / 'runs' / f'attempt-{attempt}'
        planned = ''.join(json.dumps({'case_id': case['case_id']}) + '\n' for case in bundle['cases'])
        write(run, 'planned.jsonl', planned)
        write(run, 'manifest.json', {'run_id': run.name, 'planned_sha256': hashlib.sha256(planned.encode()).hexdigest()})
        write(run, 'product-bundle.json', product)
        write(run, 'outputs.jsonl', ''.join(json.dumps({'case_id': case['case_id'], 'answer': f'Observed {index}'}) + '\n'
                                           for index, case in enumerate(bundle['cases'])))
        write(run, 'scores.jsonl', ''.join(json.dumps({'case_id': case['case_id'], 'status': 'pending', 'score': None}) + '\n'
                                          for case in bundle['cases']))
        write_run_refs(run, root / 'artifacts', refs)
    setup_seconds = perf_counter() - setup_started
    tracemalloc.start()
    package_started = perf_counter()
    receipt = package_campaign(root, tmp_path / 'review', mode='review')
    package_seconds = perf_counter() - package_started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    readback_started = perf_counter()
    assert validate_package(tmp_path / 'review')['valid']
    reader = RunReadContext()
    for run in sorted((tmp_path / 'review/runs').iterdir()):
        assert len(reader.bundle_for_run(run)['cases']) == count
    readback_seconds = perf_counter() - readback_started
    measurements = {'case_count': count, 'runs': 4, 'setup_seconds': setup_seconds,
                    'package_seconds': package_seconds, 'readback_seconds': readback_seconds,
                    'peak_python_bytes': peak, 'packed_logical_bytes': receipt['packed']['totals']['logical_bytes'],
                    'archive_bytes': receipt['package_bytes'], 'shared_objects_copied': receipt['shared_objects_copied'],
                    'package_object_verifications': receipt['validation']['object_verifications'],
                    'readback_canonical_rebuilds': reader.stats['canonical_rebuilds']}
    for key, value in measurements.items():
        record_property(key, value)
    print(json.dumps(measurements, sort_keys=True))
    assert receipt['shared_objects_copied'] == 4
    assert receipt['validation']['object_verifications'] == 8
    assert reader.stats['canonical_rebuilds'] == 1
    assert reader.stats['object_verifications'] == 4


def test_package_cannot_publish_inside_unrelated_immutable_store(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    root = tmp_path/'campaign'; shared_campaign(root)
    store = ArtifactStore(tmp_path/'external-artifacts')
    ref = store.install_bytes('index', {'index.json': b'{}'}, identity={})
    directory = store.resolve(ref)
    with pytest.raises(ValueError, match='artifact store'):
        package_campaign(root, directory/'package', mode='results')
    store.resolve(ref)
    assert not (directory/'package').exists()
