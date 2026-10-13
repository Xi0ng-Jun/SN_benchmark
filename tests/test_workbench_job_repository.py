"""Shared Job seams use one transaction and preserve pre-existing records."""
import sqlite3

import pytest

from rag_eval.workbench import evaluation_plans
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_evaluation_plans import setup


def test_fixed_revision_reads_callers_uncommitted_transaction(tmp_path):
    repo, _, task, plans, _, command = setup(tmp_path)
    first = plans.save(**command)
    second = plans.save(**dict(command, plan_id=first['plan_id'],
                              base_revision=first['revision'], request_key='next', title='Next'))
    with repo.connect(write=True) as conn:
        assert plans.get_fixed_in_transaction(conn, task['id'], first['plan_id'], first['revision']) == first
        # A nested read connection cannot see this mutation. Scope must be
        # checked through the caller's actual transaction, before Job insertion.
        conn.execute('DELETE FROM history WHERE kind=? AND object_id=?', ('plan.draft.saved', first['plan_id']))
        with pytest.raises(ValueError):
            plans.get_fixed_in_transaction(conn, task['id'], first['plan_id'], first['revision'])
        conn.rollback()
    assert plans.get(task['id'], first['plan_id']) == second
    assert evaluation_plans.validate_plan_snapshot(first, task_id=task['id'], plan_id=first['plan_id']) == first


def test_fixed_read_requires_revision_and_task_scope(tmp_path):
    repo, _, task, plans, _, command = setup(tmp_path)
    saved = plans.save(**command)
    with repo.connect() as conn:
        for revision in (None, '', 'unknown'):
            with pytest.raises(ValueError):
                plans.get_fixed_in_transaction(conn, task['id'], saved['plan_id'], revision)
        with pytest.raises(ValueError):
            plans.get_fixed_in_transaction(conn, 'other-task', saved['plan_id'], saved['revision'])


def test_additive_jobs_schema_reopens_version2_without_changing_old_rows(tmp_path):
    repo, _, task, plans, _, command = setup(tmp_path)
    saved = plans.save(**command)
    with repo.connect() as conn:
        before = {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table)]
                  for table in ('workspaces', 'tasks', 'history', 'requests')}
        conn.execute('DROP TABLE IF EXISTS jobs')
        conn.execute('PRAGMA user_version=2')
    reopened = WorkbenchRepository(repo.root)
    with reopened.connect() as conn:
        assert conn.execute('PRAGMA user_version').fetchone()[0] == 4
        assert [row['name'] for row in conn.execute('PRAGMA table_info(jobs)')] == ['id', 'task_id', 'workspace_id', 'record', 'probe']
        assert conn.execute('SELECT count(*) FROM jobs').fetchone()[0] == 0
        for table, old in before.items():
            assert [tuple(row) for row in conn.execute('SELECT * FROM ' + table)] == old
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute('INSERT INTO jobs(id,task_id,workspace_id,record) VALUES(?,?,?,?)', ('job', 'unknown', saved['workspace_id'], '{}'))
