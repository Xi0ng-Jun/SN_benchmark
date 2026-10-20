import hashlib
import json
import sqlite3
import sys
from contextlib import nullcontext
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

from rag_eval.notebook_runner import execute
from rag_eval.run_report import load_run
from test_notebook_execution import bundle_fixture

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def fake_sn(tmp_path, monkeypatch):
    from rag_eval import runtime_environment, benchmark_runtime, system_runtime, usage_capture
    part = bundle_fixture(tmp_path)
    state = SimpleNamespace(answers={}, prepared=0)
    monkeypatch.setattr(runtime_environment, 'snapshot_sources', lambda *a: {'revision': 'test'})

    def configure(project, run, **kwargs):
        (run / 'runtime').mkdir(mode=0o700)
        return SimpleNamespace(db_path=run / 'runtime/database.db'), dict(
            comparable_settings_sha256='settings', service_config_sha256='services')
    monkeypatch.setattr(runtime_environment, 'configure_environment', configure)
    module = ModuleType('app.services.sqlite_repository')
    module.SQLiteRepository = lambda s: SimpleNamespace(db_path=s.db_path, close=lambda: None)
    monkeypatch.setitem(sys.modules, module.__name__, module)
    llm = ModuleType('app.core.llm_logging')
    llm.LLMInteractionLogger = type('Logger', (), {})
    monkeypatch.setitem(sys.modules, llm.__name__, llm)
    monkeypatch.setattr(usage_capture, 'capture_usage', lambda *a: nullcontext({}))

    def prepare_notebook(repo, cell, docs, suite, *, preparation_policy='per-source-metadata-v1'):
        state.prepared += 1
        run = cell.parent
        stored = run / 'runtime/storage/notebooks/nb/source.md'
        stored.parent.mkdir(parents=True)
        stored.write_text(docs[0]['text'])
        db = sqlite3.connect(run / 'runtime/database.db')
        with db:
            db.execute('CREATE TABLE sources (id TEXT, file_path TEXT)')
            db.execute('CREATE TABLE conversations (id TEXT, title TEXT)')
            db.execute('INSERT INTO sources VALUES (?, ?)', ('source', str(stored)))
        db.close()
        mapping = {docs[0]['id']: {'source_id': 'source', 'chunk_ids': ['chunk'],
                                   'text_sha256': hashlib.sha256(docs[0]['text'].encode()).hexdigest()}}
        (cell / 'document-map.json').write_text(json.dumps(mapping))
        (cell / 'state.json').write_text(json.dumps(dict(notebook_id='nb', prepared=True,
                                                         preparation_policy=preparation_policy)))
        return 'nb', mapping
    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', prepare_notebook)

    def ask(repo, notebook, question, mode, mapping, **diagnostics):
        assert notebook == 'nb'
        status = state.answers.get(question['sample_id'], 'success')
        return dict(question, status=status, answer='Red birds' if status == 'success' else '',
                    response={'anchors': [], 'citations': []}, context_supported=True,
                    retrieval_context=['Red birds fly.'], source_ids=['source'], retrieved_document_ids=list(mapping),
                    deterministic=dict(citation_count=0, citation_valid_count=0),
                    behavior={'kind': 'answer_returned' if status == 'success' else status})
    monkeypatch.setattr(system_runtime, 'run_system_question', ask)

    def run(name, mode='chunk', **kwargs):
        path = tmp_path / name
        execute(root=ROOT, project=tmp_path / 'product', bundle_dir=tmp_path / 'bundle', run=path,
                mode=mode, partition_id=part, case_ids=['qasper:0'], **kwargs)
        return path
    state.run = run
    return state


def test_retry_asks_again_on_copied_prepared_notebook(fake_sn):
    fake_sn.answers['0'] = 'error'
    seed = fake_sn.run('seed')
    assert load_run(seed)['outputs'][0]['status'] == 'error'
    fake_sn.answers.clear()

    retry = fake_sn.run('retry', seed_prepared_run=seed)

    assert fake_sn.prepared == 1  # documents imported once, by the seed only
    loaded = load_run(retry)
    assert loaded['outputs'][0]['status'] == 'success'
    assert not loaded['warnings']
    receipt = json.loads((retry / 'prepared-seed.json').read_text())
    assert receipt['seed_run_id'] == 'seed' and receipt['rewritten_source_paths'] == 1
    assert receipt['storage_files'] == 1
    db = sqlite3.connect(retry / 'runtime/database.db')
    (path,), = db.execute('SELECT file_path FROM sources').fetchall()
    db.close()
    assert path == str(retry / 'runtime/storage/notebooks/nb/source.md') and Path(path).is_file()
    manifest = json.loads((retry / 'manifest.json').read_text())
    assert manifest['prepared_seed']['seed_run_id'] == 'seed'
    seed_manifest = json.loads((seed / 'manifest.json').read_text())
    # Same answer conditions: the retry pairs with, and can be exported alongside, the original batch.
    assert manifest['pairing_id'] == seed_manifest['pairing_id']
    assert json.loads((retry / 'preparation-usage.json').read_text()) == {}


def test_seed_must_match_mode_and_policy(fake_sn):
    seed = fake_sn.run('seed')
    with pytest.raises(ValueError, match='different mode'):
        fake_sn.run('other-mode', mode='reasoning', seed_prepared_run=seed)
    with pytest.raises(ValueError, match='preparation policy'):
        fake_sn.run('other-policy', seed_prepared_run=seed, preparation_policy='deferred-notebook-metadata-v1')


def test_seed_refuses_hidden_references_and_changed_corpus(fake_sn):
    seed = fake_sn.run('seed')
    db = sqlite3.connect(seed / 'runtime/database.db')
    with db:
        db.execute('INSERT INTO conversations VALUES (?, ?)', ('c', 'see ' + str(seed) + '/logs'))
    db.close()
    with pytest.raises(ValueError, match='references the seed run'):
        fake_sn.run('hidden', seed_prepared_run=seed)

    clean = fake_sn.run('seed2')
    mapping_path = clean / 'product-artifacts/document-map.json'
    mapping = json.loads(mapping_path.read_text())
    next(iter(mapping.values()))['text_sha256'] = '0' * 64
    mapping_path.write_text(json.dumps(mapping))
    with pytest.raises(ValueError, match='text differs'):
        fake_sn.run('changed', seed_prepared_run=clean)


def test_source_change_needs_explicit_permission_and_is_recorded(fake_sn, monkeypatch):
    from rag_eval import runtime_environment
    seed = fake_sn.run('seed')
    monkeypatch.setattr(runtime_environment, 'snapshot_sources',
                        lambda *a, **k: {'revision': 'test', 'head_revision': 'b' * 40})
    with pytest.raises(ValueError, match='differs in bundle, SN source'):
        fake_sn.run('candidate', seed_prepared_run=seed)
    run = fake_sn.run('candidate-allowed', seed_prepared_run=seed, seed_allow_source_change=True)
    assert fake_sn.prepared == 1
    record = json.loads((run / 'manifest.json').read_text())['prepared_seed']
    assert record['source_change'] is True and record['seed_source']['head_revision'] is None
    assert load_run(run)['outputs'][0]['status'] == 'success'
