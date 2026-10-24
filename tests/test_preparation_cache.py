"""Content-addressed reuse of prepared notebooks, with the offline fake SN."""
from copy import deepcopy
import json
import sqlite3
import stat
from pathlib import Path

import pytest

from rag_eval.benchmark_submission import sn_submission_identity
from rag_eval.preparation_cache import (PREPARATION_SOURCES, preparation_source_identity,
                                        rewrite_storage_paths)
from rag_eval.run_report import load_run
from test_prepared_seed_retry import fake_sn  # noqa: F401

MANIFEST = {
    'backend/app/services/chunking.py': {'sha256': '1' * 64, 'size': 1},
    'backend/app/services/ask_service.py': {'sha256': '2' * 64, 'size': 1},
    'backend/app/core/config.py': {'sha256': '3' * 64, 'size': 1},
}


@pytest.fixture
def sn(fake_sn, monkeypatch, tmp_path):
    from rag_eval import runtime_environment
    fake_sn.code = dict(revision='test', head_revision='a' * 40, actual_manifest=deepcopy(MANIFEST))
    fake_sn.settings = dict(chunk_target_chars=600, chunk_mmr_k=8)
    monkeypatch.setattr(runtime_environment, 'snapshot_sources', lambda *a, **k: deepcopy(fake_sn.code))
    base = runtime_environment.configure_environment

    def configure(project, run, **kwargs):
        settings, runtime = base(project, run, **kwargs)
        for name, value in fake_sn.settings.items():
            setattr(settings, name, value)
        return settings, runtime
    monkeypatch.setattr(runtime_environment, 'configure_environment', configure)
    fake_sn.cache = tmp_path / 'preparation-cache'
    original = fake_sn.run
    fake_sn.cached = lambda name, **kwargs: original(name, preparation_cache=fake_sn.cache, **kwargs)
    return fake_sn


def receipt(run):
    return json.loads((run / 'preparation-cache.json').read_text())


def manifest(run):
    return json.loads((run / 'manifest.json').read_text())


def test_second_attempt_imports_nothing_and_keeps_a_private_runtime(sn):
    first = sn.cached('attempt-1')
    assert sn.prepared == 1
    assert receipt(first)['status'] == 'miss' and receipt(first)['store']['status'] == 'stored'
    assert manifest(first)['preparation_cache']['status'] == 'miss' and 'prepared_from' not in manifest(first)

    second = sn.cached('attempt-2')
    assert sn.prepared == 1  # documents imported once
    hit = receipt(second)
    assert hit['status'] == 'hit' and hit['source_run'] == 'attempt-1' and hit['rewritten_source_paths'] == 1
    assert manifest(second)['prepared_from'] == {k: hit[k] for k in ('cache_key', 'source_run', 'entry_sha256')}
    db = sqlite3.connect(second / 'runtime/database.db')
    (path,), = db.execute('SELECT file_path FROM sources').fetchall()
    db.close()
    assert path == str(second / 'runtime/storage/notebooks/nb/source.md') and Path(path).is_file()
    loaded = load_run(second)
    assert loaded['outputs'][0]['status'] == 'success' and not loaded['warnings']
    # Disclosure only: identity, pairing and the export family are unchanged.
    left, right = manifest(first), manifest(second)
    assert left['identity'] == right['identity']
    assert left['pairing_id'] == right['pairing_id'] and left['protocol_id'] == right['protocol_id']
    assert sn_submission_identity(left['identity']) == sn_submission_identity(right['identity'])
    assert 'prepared_from' not in json.dumps(right['identity'])


def test_answer_path_change_hits_and_preparation_change_misses(sn):
    sn.cached('root')
    sn.code['actual_manifest']['backend/app/services/ask_service.py']['sha256'] = 'f' * 64
    answer = sn.cached('answer-candidate')
    assert receipt(answer)['status'] == 'hit' and sn.prepared == 1
    sn.code['actual_manifest']['backend/app/services/chunking.py']['sha256'] = 'e' * 64
    chunking = sn.cached('chunking-candidate')
    assert receipt(chunking)['status'] == 'miss' and sn.prepared == 2
    assert receipt(chunking)['store']['status'] == 'stored'
    assert len([p for p in sn.cache.iterdir() if not p.name.startswith('.')]) == 2
    # A new preparation file appearing on the declared path also misses.
    sn.code['actual_manifest']['backend/app/repositories/new_table.py'] = {'sha256': 'd' * 64, 'size': 1}
    assert receipt(sn.cached('new-file'))['status'] == 'miss'


def test_preparation_settings_miss_and_answer_settings_hit(sn):
    sn.cached('root')
    sn.settings['chunk_mmr_k'] = 12
    assert receipt(sn.cached('answer-setting'))['status'] == 'hit'
    sn.settings['chunk_target_chars'] = 800
    assert receipt(sn.cached('chunk-size'))['status'] == 'miss'
    assert sn.prepared == 2


def test_entries_are_read_only_and_tampering_is_rejected(sn):
    sn.cached('root')
    entry, = [p for p in sn.cache.iterdir() if not p.name.startswith('.')]
    for path in [entry, *entry.rglob('*')]:
        mode = stat.S_IMODE(path.stat().st_mode)
        assert mode == (0o555 if path.is_dir() else 0o444), path
    target = entry / 'storage/notebooks/nb/source.md'
    target.chmod(0o644)
    target.write_text('tampered')
    run = sn.cached('after-tamper')
    assert receipt(run)['status'] == 'rejected' and 'differ' in receipt(run)['reason']
    assert sn.prepared == 2  # prepared again from the documents
    assert manifest(run)['preparation_cache']['status'] == 'rejected' and 'prepared_from' not in manifest(run)
    assert receipt(run)['store']['status'] == 'already_cached'
    assert load_run(run)['outputs'][0]['status'] == 'success'


def test_cache_and_explicit_seed_are_exclusive_and_cache_stays_outside_the_run(sn, tmp_path):
    seed = sn.run('seed')
    with pytest.raises(ValueError, match='not both'):
        sn.cached('both', seed_prepared_run=seed)
    with pytest.raises(ValueError, match='separate'):
        sn.run('inside', preparation_cache=tmp_path / 'inside' / 'cache')


def test_runs_without_a_cache_keep_their_old_shape(sn):
    run = sn.run('legacy')
    assert not (run / 'preparation-cache.json').exists()
    assert 'preparation_cache' not in manifest(run) and 'prepared_from' not in manifest(run)
    assert load_run(run)['outputs'][0]['status'] == 'success'


def test_shared_module_is_keyed_by_the_definitions_preparation_uses(tmp_path):
    assert 'backend/app/services/retrieval.py::_payload_text' in PREPARATION_SOURCES
    source = tmp_path / 'backend/app/services/retrieval.py'
    source.parent.mkdir(parents=True)

    def identity(text):
        source.write_text(text)
        import hashlib
        manifest = {'backend/app/services/retrieval.py': {'sha256': hashlib.sha256(text.encode()).hexdigest(),
                                                          'size': len(text)}}
        return preparation_source_identity(dict(actual_manifest=manifest), tmp_path)

    base = identity('_PAYLOAD_SKIP_KEYS = {"id"}\ndef _payload_text(p):\n    return "x"\ndef rank(q):\n    return 1\n')
    answer_only = identity('_PAYLOAD_SKIP_KEYS = {"id"}\ndef _payload_text(p):\n    return "x"\ndef rank(q):\n    return 2\n')
    embedding_text = identity('_PAYLOAD_SKIP_KEYS = {"id"}\ndef _payload_text(p):\n    return "y"\ndef rank(q):\n    return 1\n')
    assert base == answer_only and base != embedding_text
    # Without the captured bytes the whole file is used, which is only stricter.
    whole = preparation_source_identity(dict(actual_manifest={'backend/app/services/retrieval.py': {
        'sha256': '0' * 64, 'size': 1}}), None)
    assert whole == {'files': {'backend/app/services/retrieval.py': '0' * 64}}
    assert 'whole_source' in preparation_source_identity({'revision': 'x'})


def test_reference_check_reads_text_values_only(tmp_path):
    database = tmp_path / 'db.sqlite'
    db = sqlite3.connect(database)
    with db:
        db.execute('CREATE TABLE sources (id TEXT, file_path TEXT)')
        db.execute('CREATE TABLE vectors (id INTEGER, embedding BLOB, note)')
        db.execute('INSERT INTO sources VALUES (?, ?)', ('s', '/old/storage/a.md'))
        db.execute('INSERT INTO vectors VALUES (?, ?, ?)', (1, b'/old/run/' * 4, 1.5))
    db.close()
    assert rewrite_storage_paths(database, '/old/storage/', '/new/storage/', '/old/run') == 1
    db = sqlite3.connect(database)
    with db:
        db.execute('INSERT INTO vectors VALUES (?, ?, ?)', (2, b'', 'see /old/run/logs'))
    db.close()
    with pytest.raises(ValueError, match='references the source run'):
        rewrite_storage_paths(database, '/old/storage/', '/new/storage/', '/old/run')


def test_concurrent_attempts_wait_for_one_preparation(sn, monkeypatch):
    import threading
    import time
    from rag_eval import benchmark_runtime
    original = benchmark_runtime.prepare_notebook
    entered = threading.Event()

    def slow(*args, **kwargs):
        entered.set()
        time.sleep(0.4)
        return original(*args, **kwargs)
    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', slow)
    errors = []

    def attempt(name):
        try:
            sn.cached(name)
        except BaseException as exc:  # surfaced below
            errors.append(exc)
    first = threading.Thread(target=attempt, args=('attempt-1',))
    first.start()
    assert entered.wait(5)
    second = threading.Thread(target=attempt, args=('attempt-2',))
    second.start()
    first.join(10); second.join(10)
    assert not errors
    assert sn.prepared == 1
    waited = receipt(sn.cache.parent / 'attempt-2')
    assert waited['status'] == 'hit' and waited['lock_waited_seconds'] > 0.1
