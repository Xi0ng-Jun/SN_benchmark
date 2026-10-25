"""P4: one verified source tree per execution, and run identities that keep only the manifest digest."""
import hashlib
import json
import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest

from test_sn_source_loading import _fixture_repo
from test_prepared_seed_retry import fake_sn  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def captured(tmp_path):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.sn_source_capture import capture_sn_source
    project = _fixture_repo(tmp_path)
    store = ArtifactStore(tmp_path / 'store')
    return project, store, capture_sn_source(project, store)


def test_shared_tree_is_verified_compiled_read_only_and_linked_per_run(captured, tmp_path):
    from rag_eval.runtime_environment import link_shared_sn_source, materialize_shared_sn_source
    project, store, capture = captured
    shared = materialize_shared_sn_source(store, capture.ref, tmp_path / 'execution/invocation/sn-source')
    module = shared / 'backend/app/core/text_whitespace.py'
    assert stat.S_IMODE(module.stat().st_mode) == 0o444 and stat.S_IMODE(shared.stat().st_mode) == 0o555
    assert list((shared / 'backend/app/core/__pycache__').glob('text_whitespace.*.pyc'))
    identity = json.loads((store.resolve(capture.ref, files=[]) / 'object.json').read_text())['identity']
    run = tmp_path / 'execution/partitions/0001'
    run.mkdir(parents=True)
    linked = link_shared_sn_source(shared, capture.ref, run / 'captured-source', identity)
    target = linked / 'backend/app/core/text_whitespace.py'
    assert os.stat(target).st_ino == os.stat(module).st_ino  # a link, not a copy
    assert not (linked / '.sn-shared-source.json').exists()
    # Imports come from the run's own path and reuse the compiled bytecode.
    script = ('import sys, json, pathlib; sys.dont_write_bytecode = False; sys.path.insert(0, sys.argv[1]); '
              'import app.core.text_whitespace as m; print(json.dumps([m.VALUE, m.__file__, m.__cached__]))')
    value, origin, cached = json.loads(subprocess.run([sys.executable, '-c', script, str(linked / 'backend')],
                                                      check=True, capture_output=True, text=True).stdout)
    assert value == 'captured-v1' and Path(origin) == target and Path(cached).is_relative_to(linked)
    assert os.stat(cached).st_ino == os.stat(next((shared / 'backend/app/core/__pycache__').glob(
        'text_whitespace.*.pyc'))).st_ino
    with pytest.raises(ValueError, match='different capture'):
        link_shared_sn_source(shared, dict(capture.ref, id='0' * 64), run / 'other', identity)
    changed = dict(identity, actual_manifest=dict(identity['actual_manifest'], **{'backend/extra.py': {
        'sha256': '0' * 64, 'size': 1}}))
    from rag_eval.identity import fingerprint
    changed['source_manifest_sha256'] = fingerprint(changed['actual_manifest'])
    with pytest.raises(ValueError):
        link_shared_sn_source(shared, capture.ref, run / 'third', changed)


def test_snapshot_with_shared_source_saves_only_the_manifest_digest(captured, tmp_path):
    from rag_eval.artifact_store import write_run_refs
    from rag_eval.runtime_environment import materialize_shared_sn_source, snapshot_sources
    from rag_eval.bundle_index import install_bundle
    from test_notebook_execution import bundle_fixture
    project, store, capture = captured
    (tmp_path / 'data').mkdir()
    bundle_fixture(tmp_path / 'data')
    refs = install_bundle(tmp_path / 'data/bundle', store.root)
    shared = materialize_shared_sn_source(store, capture.ref, tmp_path / 'execution/sn-source')
    run = tmp_path / 'execution/run'
    run.mkdir()
    write_run_refs(run, store.root, refs)
    code = snapshot_sources(ROOT, project, run, artifact_root=store.root, captured_source_ref=capture.ref,
                            shared_source=shared)
    assert code['actual_manifest'] == capture.actual_manifest  # in memory, for loader checks
    saved = json.loads((run / 'source-identity.json').read_text())
    assert 'actual_manifest' not in saved and saved['source_manifest_sha256'] == capture.source_manifest_sha256
    archive = store.resolve(capture.ref, files=['product-source.tar']) / 'product-source.tar'
    assert saved['product_archive_sha256'] == hashlib.sha256(archive.read_bytes()).hexdigest()
    from rag_eval.run_reader import RunReadContext
    RunReadContext().validate_sources(store, json.loads((run / 'artifact-refs.json').read_text()), saved)
    assert RunReadContext().source_manifest(run) == capture.actual_manifest


def test_old_identities_with_manifest_and_audits_project_like_new_ones():
    from rag_eval.benchmark_submission import sn_submission_identity
    from rag_eval.identity import fingerprint
    manifest = {'backend/app/m.py': {'sha256': 'a' * 64, 'size': 1}}
    new = dict(source={'format': 'x'}, code=dict(source_format='sn-source-capture-v1', head_revision='b' * 40,
                                                 source_manifest_sha256=fingerprint(manifest)), track='R')
    old = dict(new, audits_sha256=fingerprint([]), code=dict(new['code'], actual_manifest=manifest))
    assert sn_submission_identity(old) == sn_submission_identity(new)
    assert 'actual_manifest' not in json.dumps(sn_submission_identity(old))
    with pytest.raises(ValueError, match='inconsistent'):
        sn_submission_identity(dict(old, code=dict(old['code'], actual_manifest={'other.py': manifest['backend/app/m.py']})))
    # A non-constant audit value would carry information and is kept.
    assert sn_submission_identity(dict(new, audits_sha256='c' * 64))['audits_sha256'] == 'c' * 64


def test_republishing_an_existing_object_checks_only_its_record(tmp_path, monkeypatch):
    from rag_eval import artifact_store
    from rag_eval.artifact_store import ArtifactStore
    store = ArtifactStore(tmp_path / 'store')
    source = tmp_path / 'a.txt'
    source.write_text('x' * 1000)
    ref = store.install_files('evaluator', {'a.txt': source}, identity={})
    seen = []
    original = ArtifactStore.resolve
    monkeypatch.setattr(ArtifactStore, 'resolve', lambda self, r, files=None: seen.append(files) or original(self, r, files=files))
    assert store.install_files('evaluator', {'a.txt': source}, identity={}) == ref
    assert seen == [[]]


def test_pre_change_run_directory_still_loads_and_merges_with_new_runs(fake_sn, tmp_path):
    """A run written before P4 (whole manifest and constant audits in its identity) stays readable."""
    import shutil
    from rag_eval.artifacts import digest, save_json, save_jsonl
    from rag_eval.benchmark_submission import sn_submission_identity
    from rag_eval.identity import fingerprint
    from rag_eval.run_report import load_run
    from rag_eval.run_support import read_rows
    new = fake_sn.run('new')
    old = tmp_path / 'old'
    shutil.copytree(new, old)
    manifest = json.loads((old / 'manifest.json').read_text())
    files = {'backend/app/m.py': {'sha256': 'a' * 64, 'size': 1}}
    identity = dict(manifest['identity'], audits_sha256=fingerprint([]),
                    code=dict(manifest['identity']['code'], actual_manifest=files,
                              source_manifest_sha256=fingerprint(files)))
    protocol = fingerprint({**identity, 'mode': manifest['mode']})
    from rag_eval.notebook_bundle import load_bundle, partition_bundle
    from rag_eval.notebook_runner import plan_rows, selected_cases
    bundle = load_bundle(tmp_path / 'bundle')
    context = identity['notebook_context']
    product = partition_bundle(bundle, context['partition_id'],
                               request_revision=context.get('request_revision', 'notebook-request-v1'))
    cases = selected_cases(bundle, product, context.get('case_ids'))
    planned = plan_rows(cases, manifest['run_id'], protocol, manifest['mode'])
    save_jsonl(old / 'planned.jsonl', planned)
    by_key = {(row['case_id'], row['scorer']): row for row in planned}
    save_jsonl(old / 'scores.jsonl', [dict(row, **by_key[(row['case_id'], row['scorer'])])
                                      for row in read_rows(old / 'scores.jsonl')])
    manifest.update(identity=identity, protocol_id=protocol, pairing_id=fingerprint(identity),
                    planned_sha256=digest(old / 'planned.jsonl'))
    save_json(old / 'manifest.json', manifest)
    loaded = load_run(old)
    assert loaded['outputs'][0]['status'] == 'success'
    current = json.loads((new / 'manifest.json').read_text())['identity']
    assert 'audits_sha256' not in current and 'actual_manifest' not in json.dumps(current)
    # The same answer conditions project identically, so old and new runs can be exported together.
    assert sn_submission_identity(identity) == sn_submission_identity(dict(
        current, code=dict(current['code'], source_manifest_sha256=fingerprint(files))))


def test_stage_metrics_saved_by_the_runner_are_reused_only_while_current(fake_sn):
    from rag_eval.run_report import write_report
    from rag_eval.stage_metrics import saved_stage_metrics
    run = fake_sn.run('metrics')
    assert saved_stage_metrics(run) is None  # nothing saved yet
    write_report([run], run.parent / 'metrics-report', save_stage_metrics=True)
    saved = saved_stage_metrics(run)
    assert saved is not None and saved['run_path'] == str(run.resolve())
    with (run / 'outputs.jsonl').open('a') as stream:
        stream.write('')
    (run / 'product-artifacts/attempts.jsonl').write_text('')
    assert saved_stage_metrics(run) is None  # an input changed: recompute
