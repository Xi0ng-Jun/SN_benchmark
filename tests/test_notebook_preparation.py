"""Preparation must keep all documents and unwind deferred refresh on failure."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest

from rag_eval.benchmark_runtime import prepare_notebook


DEFERRED = 'deferred-notebook-metadata-v1'
STANDARD = 'per-source-metadata-v1'


@pytest.fixture
def preparation_boundary(tmp_path, monkeypatch):
    suppressed = ContextVar('test_metadata_suppressed', default=False)

    @contextmanager
    def suppress():
        token = suppressed.set(True)
        try:
            yield
        finally:
            suppressed.reset(token)

    def module(name, **values):
        result = ModuleType(name)
        result.__dict__.update(values)
        monkeypatch.setitem(sys.modules, name, result)

    module('app.models.notebooks', NotebookCreate=SimpleNamespace)
    module('app.services.repository', UploadedSourceFile=SimpleNamespace)
    module('app.services.notebook_metadata', suppress_notebook_metadata_refresh=suppress)

    class Repository:
        def __init__(self):
            self.uploads, self.refreshes, self.events = [], [], []
            self.failure = None
            self.db = sqlite3.connect(':memory:')
            self.db.executescript('''
                CREATE TABLE chunks(id TEXT, source_id TEXT, notebook_id TEXT);
                CREATE TABLE chunk_embeddings(chunk_id TEXT);
            ''')

        def create_notebook(self, request):
            self.events.append('create')
            return SimpleNamespace(id='notebook')

        def effective_document_limit(self, user):
            return 100

        def upload_sources(self, notebook, files, *, scheduler, capacity_limit):
            assert scheduler is None and capacity_limit == 100
            if self.failure and len(self.uploads) == 1:
                raise self.failure
            source = 'source-' + str(len(self.uploads))
            self.uploads.extend(files)
            self.db.execute('INSERT INTO chunks VALUES (?,?,?)', (source + '-chunk', source, notebook))
            self._augment_notebook_meta(notebook)
            return [SimpleNamespace(id=source)]

        def _augment_notebook_meta(self, notebook):
            if not suppressed.get():
                self.refreshes.append([file.content for file in self.uploads])
                self.events.append('refresh')

        def _connect(self):
            return self.db

        def _notebook_has_kg(self, notebook):
            return False

        def close_local(self):
            pass

        def build_scale_index(self, notebook):
            self.events.append('index')

    def backfill(repo, notebook, *, missing_only):
        assert missing_only is True
        repo.db.execute('INSERT INTO chunk_embeddings SELECT id FROM chunks')
        repo.events.append('backfill')

    module('app.services.batch_ingest', backfill_chunk_embeddings=backfill)
    repo = Repository()
    documents = [dict(id='doc-' + str(i), title='Title ' + str(i), text='Original\n\n  body ' + str(i))
                 for i in range(3)]
    cell = tmp_path / 'cell'
    cell.mkdir()
    yield SimpleNamespace(repo=repo, documents=documents, cell=cell, suppressed=suppressed)
    repo.db.close()


@pytest.mark.parametrize('policy,refresh_sizes', [(None, [1, 2, 3]), (DEFERRED, [3])])
def test_preparation_retains_bytes_mapping_and_full_embedding_coverage(preparation_boundary, policy, refresh_sizes):
    b = preparation_boundary
    kwargs = {} if policy is None else {'preparation_policy': policy}
    notebook, mapping = prepare_notebook(b.repo, b.cell, b.documents, 'alce', **kwargs)
    assert notebook == 'notebook'
    assert [file.content for file in b.repo.uploads] == [d['text'].encode() for d in b.documents]
    assert [file.title for file in b.repo.uploads] == [d['title'] for d in b.documents]
    assert [len(rows) for rows in b.repo.refreshes] == refresh_sizes
    assert list(mapping) == [d['id'] for d in b.documents]
    for index, doc in enumerate(b.documents):
        assert mapping[doc['id']]['chunk_ids'] == [f'source-{index}-chunk']
        assert mapping[doc['id']]['text_sha256'] == hashlib.sha256(doc['text'].encode()).hexdigest()
    state = json.loads((b.cell / 'state.json').read_text())
    assert state['prepared'] is True
    assert state['documents'] == state['chunks'] == state['embedded_chunks'] == 3
    assert state['preparation_policy'] == (policy or STANDARD)
    assert b.suppressed.get() is False
    if policy == DEFERRED:
        assert b.repo.events[-3:] == ['backfill', 'index', 'refresh']
        assert state['final_metadata_refresh']['call_returned'] is True
        assert state['final_metadata_refresh']['source_count'] == 3
        # Reading an already prepared cell must not trigger another model call.
        prepare_notebook(b.repo, b.cell, b.documents, 'alce', preparation_policy=policy)
        assert len(b.repo.refreshes) == 1


@pytest.mark.parametrize('error', [RuntimeError('ingest failed'), KeyboardInterrupt()])
def test_import_failure_restores_scope_without_refreshing_partial_corpus(preparation_boundary, error):
    b = preparation_boundary
    b.repo.failure = error
    with pytest.raises(type(error)):
        prepare_notebook(b.repo, b.cell, b.documents, 'alce', preparation_policy=DEFERRED)
    assert b.suppressed.get() is False
    assert b.repo.refreshes == []
    assert not json.loads((b.cell / 'state.json').read_text()).get('prepared')
    assert list(json.loads((b.cell / 'document-map.json').read_text())) == ['doc-0']
    assert b.repo.events == ['create']


def test_invalid_policy_rejected_before_notebook_creation(preparation_boundary):
    b = preparation_boundary
    with pytest.raises(ValueError, match='preparation policy'):
        prepare_notebook(b.repo, b.cell, b.documents, 'alce', preparation_policy='typo')
    assert b.repo.events == []
    assert list(b.cell.iterdir()) == []


def test_missing_sn_refresh_hook_rejected_before_ingest(preparation_boundary):
    b = preparation_boundary
    b.repo._augment_notebook_meta = None
    with pytest.raises(ValueError, match='metadata'):
        prepare_notebook(b.repo, b.cell, b.documents, 'alce', preparation_policy=DEFERRED)
    assert b.repo.events == []
    assert list(b.cell.iterdir()) == []


@pytest.mark.parametrize('saved_policy', [None, STANDARD])
def test_deferred_policy_cannot_relabel_existing_per_source_state(preparation_boundary, saved_policy):
    b = preparation_boundary
    state = {'notebook_id': 'notebook', 'prepared': True}
    if saved_policy:
        state['preparation_policy'] = saved_policy
    state_path = b.cell / 'state.json'
    state_path.write_text(json.dumps(state))
    before = state_path.read_bytes()
    with pytest.raises(ValueError, match='preparation policy'):
        prepare_notebook(b.repo, b.cell, b.documents, 'alce', preparation_policy=DEFERRED)
    assert state_path.read_bytes() == before
    assert b.repo.events == []


def test_failed_final_refresh_is_recorded_without_claiming_prepared(preparation_boundary):
    b = preparation_boundary
    original = b.repo._augment_notebook_meta

    def failing_refresh(notebook):
        if not b.suppressed.get():
            raise RuntimeError('refresh boundary failed')
        original(notebook)

    b.repo._augment_notebook_meta = failing_refresh
    with pytest.raises(RuntimeError, match='refresh boundary failed'):
        prepare_notebook(b.repo, b.cell, b.documents, 'alce', preparation_policy=DEFERRED)
    state = json.loads((b.cell / 'state.json').read_text())
    assert not state.get('prepared')
    assert state['final_metadata_refresh']['invoked'] is True
    assert state['final_metadata_refresh']['call_returned'] is False
    assert state['final_metadata_refresh']['source_count'] == 3
    assert state['final_metadata_refresh']['elapsed_seconds'] >= 0
    assert b.suppressed.get() is False


def test_incomplete_embeddings_prevent_final_refresh(preparation_boundary, monkeypatch):
    b = preparation_boundary
    monkeypatch.setattr(sys.modules['app.services.batch_ingest'], 'backfill_chunk_embeddings', lambda *a, **kw: None)
    with pytest.raises(ValueError, match='Embedding coverage incomplete'):
        prepare_notebook(b.repo, b.cell, b.documents, 'alce', preparation_policy=DEFERRED)
    state = json.loads((b.cell / 'state.json').read_text())
    assert not state.get('prepared')
    assert 'final_metadata_refresh' not in state
    assert b.repo.refreshes == []
    assert b.suppressed.get() is False
