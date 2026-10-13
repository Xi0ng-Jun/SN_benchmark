"""CLI tests for consuming the sealed E5-S3 readiness receipt before launch.

The legacy non-captured runner must not read the receipt at all.  The explicit
captured branch derives scope/source/index from the installed artifacts and
rejects any receipt that is missing, invalid, not ``ready`` or not an exact
identity match, with a stable category and no path/secret/score leakage.
"""
import importlib.util
import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[1]

CASE_ID = 'qasper:397a1e851aab41c455c2b284f5e4947500d797f0'
PARTITION_ID = 'qasper-group-synthetic'
REQUEST_REVISION = 'notebook-request-v3'
SOURCE_MANIFEST_SHA256 = 'a' * 64


def _script():
    path = ROOT / 'scripts' / 'run_notebook_benchmarks.py'
    spec = importlib.util.spec_from_file_location('tested_readiness_cli', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _notebook_runner():
    from rag_eval import notebook_runner
    return notebook_runner


def captured_fixture(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.identity import fingerprint

    bundle_dir = tmp_path / 'bundle'
    bundle_dir.mkdir()
    manifest = {'format': 'sn-synthetic-bundle-v1', 'files': {}}
    manifest_bytes = (json.dumps(manifest, indent=2) + '\n').encode()
    (bundle_dir / 'manifest.json').write_bytes(manifest_bytes)

    store = ArtifactStore(tmp_path / 'store')
    bundle_ref = store.install_bytes(
        'bundle', {'manifest.json': manifest_bytes},
        identity={'bundle_id': fingerprint(manifest), 'partition_capsules': {}})
    index_ref = store.install_bytes(
        'index', {'index.json': b'{"format":"sn-bundle-index-v1"}\n'},
        identity={'format': 'sn-bundle-index-v1'})
    pointer = store.root / 'bundle-indexes' / (fingerprint(manifest) + '.json')
    pointer.parent.mkdir(parents=True, exist_ok=True)
    pointer.write_text(json.dumps({'format': 'sn-bundle-index-v1', 'bundle': bundle_ref,
                                   'index': index_ref}))
    source_ref = store.install_bytes(
        'sn', {'product-source.tar': b'synthetic-source'},
        identity={'source_format': 'sn-source-capture-v1',
                  'source_manifest_sha256': SOURCE_MANIFEST_SHA256})
    ref_file = tmp_path / 'source-ref.json'
    ref_file.write_text(json.dumps(source_ref))
    expectation_file = tmp_path / 'expectation.json'
    expectation_file.write_text(json.dumps({'executable': sys.executable, 'prefix': sys.prefix}))
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('pydantic>=2\n')
    return SimpleNamespace(
        bundle_dir=bundle_dir, store_root=tmp_path / 'store', source_ref=source_ref,
        source_ref_file=ref_file, expectation_file=expectation_file,
        requirements=requirements, bundle_id=fingerprint(manifest), index_id=index_ref['id'])


def captured_argv(tmp_path, fixture, receipt=None):
    argv = ['run_notebook_benchmarks.py',
            '--artifact-root', str(fixture.store_root),
            '--artifact-index-id', fixture.index_id,
            '--bundle', str(fixture.bundle_dir),
            '--partition-id', PARTITION_ID,
            '--mode', 'chunk',
            '--run-dir', str(tmp_path / 'run'),
            '--project-root', str(tmp_path / 'product'),
            '--request-revision', REQUEST_REVISION,
            '--case-id', CASE_ID,
            '--sn-source-ref', str(fixture.source_ref_file),
            '--dependency-requirements', str(fixture.requirements),
            '--runtime-expectation', str(fixture.expectation_file)]
    if receipt is not None:
        argv.extend(['--readiness-receipt', str(receipt)])
    return argv


def ready_receipt(fixture, **scope_overrides):
    from test_s3_readiness import api, readiness_input, scope_fixture
    scope = scope_fixture(case_id=CASE_ID, partition_id=PARTITION_ID,
                          request_revision=REQUEST_REVISION, bundle_id=fixture.bundle_id,
                          index_id=fixture.index_id,
                          sn_source_manifest_sha256=SOURCE_MANIFEST_SHA256)
    scope.update(scope_overrides)
    return api().decide_readiness(readiness_input(scope=scope))


def write_receipt(tmp_path, value, name='readiness-receipt.json'):
    path = tmp_path / name
    if isinstance(value, bytes):
        path.write_bytes(value)
    else:
        path.write_text(json.dumps(value))
    return path


def test_exact_ready_receipt_reaches_captured_launch(tmp_path, monkeypatch, capsys):
    fixture = captured_fixture(tmp_path)
    receipt = write_receipt(tmp_path, ready_receipt(fixture))
    calls = []
    monkeypatch.setattr(_notebook_runner(), 'execute', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, 'argv', captured_argv(tmp_path, fixture, receipt))

    assert _script().main() == 0
    assert len(calls) == 1
    assert calls[0]['sn_source_ref'] == fixture.source_ref
    assert calls[0]['artifact_index_id'] == fixture.index_id
    assert calls[0]['case_ids'] == [CASE_ID]
    assert calls[0]['dependency_requirements'] == fixture.requirements.resolve()
    assert capsys.readouterr().err == ''


def test_captured_launch_without_receipt_keeps_old_behavior(tmp_path, monkeypatch, capsys):
    fixture = captured_fixture(tmp_path)
    calls = []
    monkeypatch.setattr(_notebook_runner(), 'execute', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, 'argv', captured_argv(tmp_path, fixture))

    assert _script().main() == 0
    assert len(calls) == 1
    assert calls[0]['sn_source_ref'] == fixture.source_ref
    assert capsys.readouterr().err == ''


def test_non_captured_legacy_path_never_reads_receipt(tmp_path, monkeypatch, capsys):
    receipt = tmp_path / 'never-read.json'
    calls = []
    monkeypatch.setattr(_notebook_runner(), 'execute', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, 'argv', [
        'run_notebook_benchmarks.py', '--bundle', str(tmp_path / 'bundle'),
        '--partition-id', 'part', '--mode', 'chunk', '--run-dir', str(tmp_path / 'run'),
        '--project-root', str(tmp_path / 'product'), '--readiness-receipt', str(receipt)])

    assert _script().main() == 0
    assert len(calls) == 1
    assert capsys.readouterr().err == ''
    assert not receipt.exists()


def _missing(tmp_path, fixture):
    return tmp_path / 'absent-readiness.json'


def _malformed(tmp_path, fixture):
    secret = 'sk-' + 'A' * 24
    payload = '{"format": "sn-e5-s3-readiness-receipt-v1", "secret": "' + secret + '"}'
    return write_receipt(tmp_path, payload.encode(), 'malformed.json')


def _diagnostic(tmp_path, fixture):
    return write_receipt(tmp_path, {'format': 'sn-captured-cli-diagnostic-score-v1',
                                    'diagnostic_complete': True, 'official': False,
                                    'score': 1.0}, 'diagnostic-score.json')


def _not_ready(tmp_path, fixture):
    from test_s3_readiness import api, readiness_input
    return write_receipt(tmp_path, api().decide_readiness(readiness_input(identity=None)),
                         'not-ready.json')


def _scope(tmp_path, fixture):
    return write_receipt(tmp_path, ready_receipt(fixture, case_id='qasper:other-case'),
                         'scope.json')


def _source(tmp_path, fixture):
    return write_receipt(tmp_path, ready_receipt(fixture, sn_source_manifest_sha256='9' * 64),
                         'source.json')


def _index(tmp_path, fixture):
    return write_receipt(tmp_path, ready_receipt(fixture, index_id='f' * 64), 'index.json')


@pytest.mark.parametrize('receipt_factory, category', [
    (_missing, 'readiness_receipt_missing'),
    (_malformed, 'readiness_receipt_invalid'),
    (_diagnostic, 'readiness_receipt_invalid'),
    (_not_ready, 'readiness_not_ready'),
    (_scope, 'readiness_scope_mismatch'),
    (_source, 'readiness_source_mismatch'),
    (_index, 'readiness_index_mismatch'),
])
def test_receipt_rejections_are_stable_and_redacted(tmp_path, monkeypatch, capsys,
                                                    receipt_factory, category):
    fixture = captured_fixture(tmp_path)
    receipt = receipt_factory(tmp_path, fixture)
    calls = []
    monkeypatch.setattr(_notebook_runner(), 'execute', lambda **kwargs: calls.append(kwargs))
    monkeypatch.setattr(sys, 'argv', captured_argv(tmp_path, fixture, receipt))

    assert _script().main() == 2
    assert calls == []
    stderr = capsys.readouterr().err
    assert category in stderr
    assert str(tmp_path) not in stderr
    assert 'sk-' not in stderr
