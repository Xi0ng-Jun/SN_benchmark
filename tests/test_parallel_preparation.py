"""P8: sequential uploads, bounded concurrent per-source processing, same content."""
from contextlib import contextmanager
from contextvars import ContextVar
import hashlib
import json
import sqlite3
import sys
import threading
import time
from types import ModuleType, SimpleNamespace

import pytest

from rag_eval.benchmark_runtime import prepare_notebook
from test_prepared_seed_retry import fake_sn  # noqa: F401
from test_preparation_cache import sn  # noqa: F401

DEFERRED = 'deferred-notebook-metadata-v1'
STANDARD = 'per-source-metadata-v1'
TIMING = ('ingest_seconds', 'completion_index')


class FakeSN:
    """Upload registers a source; processing sleeps like model work, then writes chunks."""

    def __init__(self, suppressed, *, delay=0.0, failing=(), raising=()):
        self.suppressed, self.delay, self.failing, self.raising = suppressed, delay, set(failing), set(raising)
        self.lock = threading.Lock()
        self.db = sqlite3.connect(':memory:', check_same_thread=False)
        self.db.executescript('CREATE TABLE chunks(id TEXT, source_id TEXT, notebook_id TEXT);'
                              'CREATE TABLE chunk_embeddings(chunk_id TEXT);')
        self.titles, self.refreshes, self.refresh_while_processing = {}, 0, 0
        self.active = self.peak = 0

    def create_notebook(self, request):
        return SimpleNamespace(id='notebook')

    def effective_document_limit(self, user):
        return 1000

    def upload_sources(self, notebook, files, *, scheduler, capacity_limit):
        file, = files
        source = 'src-' + hashlib.sha256(file.content).hexdigest()[:12]
        self.titles[source] = file.title
        if scheduler is None:
            self.process_source(source)
        else:
            scheduler(source)
        return [SimpleNamespace(id=source, status='queued')]

    def process_source(self, source):
        with self.lock:
            self.active += 1
            self.peak = max(self.peak, self.active)
        try:
            time.sleep(self.delay)
            if self.titles[source] in self.raising:
                raise RuntimeError('synthetic pipeline crash')
            if self.titles[source] in self.failing:
                return SimpleNamespace(id=source, status='failed', parse_status='failed')
            with self.lock:
                self.db.execute('INSERT INTO chunks VALUES (?,?,?)', (source + '-c', source, 'notebook'))
            self._augment_notebook_meta('notebook', during=True)
            return SimpleNamespace(id=source, status='extracted', parse_status='extracted')
        finally:
            with self.lock:
                self.active -= 1

    def _augment_notebook_meta(self, notebook, during=False):
        if not self.suppressed.get():
            with self.lock:
                self.refreshes += 1
                self.refresh_while_processing += during

    def _connect(self):
        return self.db

    def _notebook_has_kg(self, notebook):
        return False

    def close_local(self):
        pass

    def build_scale_index(self, notebook):
        pass


@pytest.fixture
def sn_modules(monkeypatch):
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

    def backfill(repo, notebook, *, missing_only):
        repo.db.execute('INSERT INTO chunk_embeddings SELECT id FROM chunks')

    module('app.models.notebooks', NotebookCreate=SimpleNamespace)
    module('app.services.repository', UploadedSourceFile=SimpleNamespace)
    module('app.services.notebook_metadata', suppress_notebook_metadata_refresh=suppress)
    module('app.services.batch_ingest', backfill_chunk_embeddings=backfill)
    return suppressed


def documents(count):
    return [dict(id=f'doc-{i:03d}', title=f'T{i:03d}', text=f'Passage {i:03d} about herons.') for i in range(count)]


def prepare(tmp_path, suppressed, name, workers, *, policy=DEFERRED, count=40, **fake):
    repo = FakeSN(suppressed, **fake)
    cell = tmp_path / name
    cell.mkdir()
    started = time.monotonic()
    prepare_notebook(repo, cell, documents(count), 'alce', preparation_policy=policy, workers=workers)
    return repo, cell, time.monotonic() - started


def without_timing(cell):
    mapping = json.loads((cell / 'document-map.json').read_text())
    state = json.loads((cell / 'state.json').read_text())
    state['final_metadata_refresh'].pop('elapsed_seconds', None)
    return ({key: {k: v for k, v in item.items() if k not in TIMING} for key, item in mapping.items()}, state)


def test_eight_workers_are_faster_and_produce_the_same_notebook(tmp_path, sn_modules):
    serial, serial_cell, serial_time = prepare(tmp_path, sn_modules, 'serial', 1, delay=0.05)
    pooled, pooled_cell, pooled_time = prepare(tmp_path, sn_modules, 'pooled', 8, delay=0.05)
    assert serial_time >= 2.0 and pooled_time < serial_time / 3, (serial_time, pooled_time)
    assert serial.peak == 1 and pooled.peak == 8
    assert without_timing(serial_cell) == without_timing(pooled_cell)
    query = 'SELECT * FROM chunks ORDER BY id'
    assert serial.db.execute(query).fetchall() == pooled.db.execute(query).fetchall()
    mapping = json.loads((pooled_cell / 'document-map.json').read_text())
    assert list(mapping) == [d['id'] for d in documents(40)]  # upload order
    assert sorted(item['completion_index'] for item in mapping.values()) == list(range(1, 41))
    assert all(item['ingest_seconds'] >= 0.05 for item in mapping.values())
    # The deferred-metadata suppression (a ContextVar) reached every worker.
    assert pooled.refresh_while_processing == 0 and pooled.refreshes == serial.refreshes == 1


def test_per_source_policy_still_refreshes_inside_workers(tmp_path, sn_modules):
    pooled, _, _ = prepare(tmp_path, sn_modules, 'pooled', 4, policy=STANDARD, count=12)
    assert pooled.refresh_while_processing == 12


@pytest.mark.parametrize('fault', ['failing', 'raising'])
def test_a_failed_document_fails_preparation_and_keeps_finished_records(tmp_path, sn_modules, fault):
    with pytest.raises(ValueError, match='1 document\\(s\\) failed preparation: doc-005'):
        prepare(tmp_path, sn_modules, 'broken', 4, count=20, **{fault: ['T005']})
    cell = tmp_path / 'broken'
    mapping = json.loads((cell / 'document-map.json').read_text())
    state = json.loads((cell / 'state.json').read_text())
    assert 'doc-005' not in mapping and len(mapping) >= 5 and not state.get('prepared')
    assert state['preparation_failures'] == [dict(document_id='doc-005', source_id=state['preparation_failures'][0]['source_id'],
                                                  error='source processing failed' if fault == 'failing' else 'RuntimeError')]
    assert list(mapping) == [d['id'] for d in documents(20) if d['id'] in mapping]


def test_worker_count_is_validated(tmp_path, sn_modules):
    with pytest.raises(ValueError, match='Preparation workers'):
        prepare(tmp_path, sn_modules, 'invalid', 0)


def test_worker_count_is_disclosed_but_changes_neither_identity_nor_cache_key(sn, monkeypatch):
    from rag_eval import benchmark_runtime
    fake = benchmark_runtime.prepare_notebook
    seen = []
    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook',
                        lambda *a, workers=1, **k: (seen.append(workers), fake(*a, **k))[1])
    serial = sn.run('serial', preparation_cache=sn.cache.parent / 'cache-serial')
    pooled = sn.run('pooled', preparation_cache=sn.cache.parent / 'cache-pooled', preparation_workers=8)
    assert seen == [1, 8]
    left, right = (json.loads((run / 'manifest.json').read_text()) for run in (serial, pooled))
    assert 'execution_controls' not in left
    assert right['execution_controls']['preparation_workers'] == 8
    assert left['identity'] == right['identity'] and left['pairing_id'] == right['pairing_id']
    assert left['preparation_cache']['cache_key'] == right['preparation_cache']['cache_key']
