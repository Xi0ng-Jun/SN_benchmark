#!/usr/bin/env python3
"""Offline performance probes for the pre-LCD-R1 plan.

Every probe builds synthetic state under a fresh temporary directory; none
starts SN, calls a model or reads ``var/``.  Numbers are machine-dependent and
are recorded in docs/superpowers/plans/2026-10-24-performance-before-lcd-r1.md.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
import tempfile
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))


def history_lookup(executions=200, noise=20000, repeats=5):
    """P0: JobExecutionStore.records() over a large history, with and without indexes."""
    from rag_eval.workbench.job_execution import JobExecutionStore, FORMAT
    from rag_eval.workbench.repository import WorkbenchRepository

    with tempfile.TemporaryDirectory() as root:
        repo = WorkbenchRepository(Path(root) / 'state')
        workspace = repo.register_workspace('w', root)
        task = repo.create_task(workspace['id'], 't', '', request_key='t')
        with repo.connect(write=True) as conn:
            for index in range(noise):
                repo._event(conn, task['id'], 'note.saved', f'n{index}', {'i': index})
            for index in range(executions):
                record = dict(format=FORMAT, execution_id=f'e{index}', job_id=f'j{index}', task_id=task['id'],
                              state='finished', terminal=True, argv=['x'], run_dir='/nonexistent',
                              budget={'timeout_seconds': 1}, preparation_id='p' * 64, started_at='', finished_at='',
                              cancelled_at=None, exit_code=0, run_state=None, error_type=None)
                repo._event(conn, task['id'], 'job.execution_started', record['execution_id'], dict(record, state='running', terminal=False))
                repo._event(conn, task['id'], 'job.execution_finished', record['execution_id'], record)
        store = JobExecutionStore(repo)

        def measure():
            best = None
            for _ in range(repeats):
                start = time.perf_counter()
                assert len(store.records(workspace['id'])) == executions
                elapsed = time.perf_counter() - start
                best = elapsed if best is None else min(best, elapsed)
            return best

        indexed = measure()
        with repo.connect() as conn:
            conn.execute('DROP INDEX history_object_kind')
            conn.execute('DROP INDEX history_task_kind_object')
        unindexed = measure()
    return dict(probe='history_lookup', executions=executions, noise_rows=noise,
                records_seconds_without_indexes=round(unindexed, 4),
                records_seconds_with_indexes=round(indexed, 4))


def reference_scan(rows=20000, dim=1024, repeats=3):
    """P1: the seed/cache reference check over a database dominated by embedding blobs."""
    import sqlite3
    from rag_eval.preparation_cache import rewrite_storage_paths

    def legacy(database, forbidden):
        # The pre-P1 check: every column of every table cast to text.
        db = sqlite3.connect(database)
        try:
            for (table,) in db.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall():
                for column in [row[1] for row in db.execute(f'PRAGMA table_info("{table}")')]:
                    db.execute(f'SELECT 1 FROM "{table}" WHERE instr(CAST("{column}" AS TEXT), ?) LIMIT 1',
                               (forbidden,)).fetchone()
        finally:
            db.close()

    with tempfile.TemporaryDirectory() as root:
        database = Path(root) / 'database.db'
        db = sqlite3.connect(database)
        with db:
            db.execute('CREATE TABLE sources (id TEXT, file_path TEXT, title TEXT)')
            db.execute('CREATE TABLE chunks (id TEXT, source_id TEXT, text TEXT)')
            db.execute('CREATE TABLE chunk_embeddings (chunk_id TEXT, embedding BLOB)')
            db.executemany('INSERT INTO sources VALUES (?,?,?)',
                           [(f's{i}', f'/old/storage/{i}.md', f'title {i}') for i in range(50)])
            db.executemany('INSERT INTO chunks VALUES (?,?,?)',
                           [(f'c{i}', f's{i % 50}', 'chunk text ' * 50) for i in range(rows)])
            blob = bytes(range(256)) * (dim * 4 // 256)
            db.executemany('INSERT INTO chunk_embeddings VALUES (?,?)', [(f'c{i}', blob) for i in range(rows)])
        db.close()
        size = database.stat().st_size

        def best(call):
            times = []
            for _ in range(repeats):
                start = time.perf_counter(); call(); times.append(time.perf_counter() - start)
            return min(times)
        before = best(lambda: legacy(database, '/old/run'))
        after = best(lambda: rewrite_storage_paths(database, '/old/storage/', '/old/storage/', '/old/run'))
    return dict(probe='reference_scan', database_mb=round(size / 1e6, 1), embedding_rows=rows,
                legacy_all_columns_seconds=round(before, 3), text_columns_seconds=round(after, 3))


def parallel_batch(partitions=10, questions=3, latency=0.2):
    """P2: synthetic partition processes (fake model latency per question), serial vs parallel 8."""
    from rag_eval.notebook_batch import execute_batch

    def jobs(root):
        made = []
        for index in range(1, partitions + 1):
            run = root / f'{index:04d}-p{index}-chunk'
            code = ('import json, time\nfrom pathlib import Path\n'
                    f'run = Path({str(run)!r}); run.mkdir()\n'
                    f'for q in range({questions}): time.sleep({latency})\n'
                    '(run / "state.json").write_text(json.dumps({"phase": "finished"}))\n')
            made.append(dict(partition_id=f'p{index}', case_ids=[], run_dir=str(run), mode='chunk',
                             argv=[sys.executable, '-c', code]))
        return made

    timings = {}
    with tempfile.TemporaryDirectory() as root:
        for parallel in (1, 8):
            out = Path(root) / f'parallel-{parallel}'
            first = []
            start = time.perf_counter()
            execute_batch(jobs(out), output_root=out, parallel=parallel, append_run=lambda row: None,
                          after_run=lambda job, row: first.append(time.perf_counter() - start))
            timings[parallel] = (time.perf_counter() - start, first[0])
    return dict(probe='parallel_batch', partitions=partitions, questions=questions, latency_seconds=latency,
                serial_seconds=round(timings[1][0], 2), parallel8_seconds=round(timings[8][0], 2),
                ratio=round(timings[8][0] / timings[1][0], 3),
                first_partition_ready_serial=round(timings[1][1], 2),
                first_partition_ready_parallel8=round(timings[8][1], 2))


PROBES = {'history-lookup': history_lookup, 'reference-scan': reference_scan, 'parallel-batch': parallel_batch}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', choices=sorted(PROBES))
    args = parser.parse_args(argv)
    print(json.dumps(PROBES[args.probe](), indent=2))


if __name__ == '__main__':
    main()
