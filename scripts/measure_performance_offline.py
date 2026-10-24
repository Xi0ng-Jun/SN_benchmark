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


PROBES = {'history-lookup': history_lookup}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('probe', choices=sorted(PROBES))
    args = parser.parse_args(argv)
    print(json.dumps(PROBES[args.probe](), indent=2))


if __name__ == '__main__':
    main()
