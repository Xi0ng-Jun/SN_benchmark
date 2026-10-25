"""Offline probe for P8: 100 documents x 0.1 s simulated per-source processing, serial vs 8 workers.

Run: PYTHONPATH=src:tests python tests/perf_p8_probe.py
"""
from contextlib import contextmanager
from contextvars import ContextVar
import json
from pathlib import Path
import sys
import tempfile
import time
from types import ModuleType, SimpleNamespace


def main(count=100, delay=0.1):
    suppressed = ContextVar('probe_suppressed', default=False)

    @contextmanager
    def suppress():
        token = suppressed.set(True)
        try:
            yield
        finally:
            suppressed.reset(token)

    for name, values in {'app.models.notebooks': dict(NotebookCreate=SimpleNamespace),
                         'app.services.repository': dict(UploadedSourceFile=SimpleNamespace),
                         'app.services.notebook_metadata': dict(suppress_notebook_metadata_refresh=suppress),
                         'app.services.batch_ingest': dict(backfill_chunk_embeddings=lambda repo, nb, missing_only:
                             repo.db.execute('INSERT INTO chunk_embeddings SELECT id FROM chunks'))}.items():
        module = ModuleType(name)
        module.__dict__.update(values)
        sys.modules[name] = module
    from rag_eval.benchmark_runtime import prepare_notebook
    from test_parallel_preparation import FakeSN, documents, without_timing
    results, cells = {}, {}
    with tempfile.TemporaryDirectory() as root:
        for workers in (1, 8):
            repo = FakeSN(suppressed, delay=delay)
            cell = Path(root) / f'workers-{workers}'
            cell.mkdir()
            started = time.monotonic()
            prepare_notebook(repo, cell, documents(count), 'alce',
                             preparation_policy='deferred-notebook-metadata-v1', workers=workers)
            results[f'workers_{workers}_seconds'] = round(time.monotonic() - started, 2)
            cells[workers] = (without_timing(cell), repo.db.execute('SELECT * FROM chunks ORDER BY id').fetchall())
        results['identical_except_timing'] = cells[1] == cells[8]
    print(json.dumps(dict(documents=count, delay_seconds=delay, **results), indent=2))


if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    main()
