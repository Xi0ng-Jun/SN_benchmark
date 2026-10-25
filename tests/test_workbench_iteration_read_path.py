"""Cost of reading one iteration tree: SQL statements and recomputation per GET (offline)."""
from contextlib import contextmanager
import os

import pytest

from test_workbench_comparisons import Views, child, ran, tree  # noqa: F401  (fixture)
from test_workbench_iterations import env  # noqa: F401  (fixture)


def grow(t, candidates):
    t['views'].run_calls = []
    t['views'].run = lambda run_id: (t['views'].run_calls.append(run_id), {
        'official': {'primary_metric': 'answer_f1', 'metrics': {'answer_f1': 0.5}}})[1]
    root = t['tree']['root_candidate_id']
    ran(t, root, {'a': .5, 'b': .5}, 'base-1')
    ran(t, root, {'a': .5, 'b': .5}, 'base-2')
    service = t['env']['service']
    for index in range(candidates - 1):
        cid = child(t, root, f'c{index}', {'a': .6, 'b': .4})
        ran(t, cid, {'a': .6, 'b': .4}, f'c{index}-second')
    return service


@contextmanager
def counting(repo):
    count = [0]
    original = repo.connect

    @contextmanager
    def connect(*args, **kwargs):
        with original(*args, **kwargs) as conn:
            conn.set_trace_callback(lambda statement: count.__setitem__(0, count[0] + 1))
            yield conn
    repo.connect = connect
    try:
        yield count
    finally:
        repo.connect = original


def reader(t):
    from rag_eval.workbench.iterations_api import IterationAPI
    api = object.__new__(IterationAPI)
    api.service, api.comparisons, api.views = t['env']['service'], t['comparisons'], t['views']
    api.repository, api.actor, api.workspace = t['env']['repo'], 'alice', t['env']['workspace']
    api._shown = {}
    return api


def statements_for_get(t, candidates):
    grow(t, candidates)
    api = reader(t)
    with counting(t['env']['repo']) as first:
        api.show(t['tree']['id'])
    calls = len(t['views'].flip_calls), len(t['views'].admission_calls)
    with counting(t['env']['repo']) as second:
        api.show(t['tree']['id'])
    from rag_eval import notebook_bundle, run_reader
    loads = []
    original_load, original_bundle = notebook_bundle.load_bundle, run_reader.RunReadContext.bundle
    notebook_bundle.load_bundle = lambda *a, **k: loads.append(a) or original_load(*a, **k)
    run_reader.RunReadContext.bundle = lambda self, *a, **k: loads.append(a) or original_bundle(self, *a, **k)
    try:
        with counting(t['env']['repo']) as third:
            body, version = api.show(t['tree']['id'])
    finally:
        notebook_bundle.load_bundle, run_reader.RunReadContext.bundle = original_load, original_bundle
    assert loads == []  # an unchanged tree is served without loading any bundle
    assert (len(t['views'].flip_calls), len(t['views'].admission_calls)) == calls  # nothing recomputed
    with t['env']['repo'].connect(write=True) as conn:  # any task event invalidates the version
        t['env']['repo']._event(conn, t['tree']['task_id'], 'note.saved', 'n', {})
    with counting(t['env']['repo']) as changed:
        api.show(t['tree']['id'])
    assert (len(t['views'].flip_calls), len(t['views'].admission_calls)) == calls  # records are read, not recomputed
    return first[0], second[0], third[0], changed[0], body


@pytest.mark.parametrize('candidates', [4, 10])
def test_statements_per_get_grow_linearly_and_repeat_gets_only_check_the_version(tree, candidates):
    first, second, third, changed, body = statements_for_get(tree, candidates)
    assert len(body['candidates']) == candidates
    child_rows = [c for c in body['candidates'] if c['id'] != tree['tree']['root_candidate_id']]
    assert all(c['comparisons']['parent']['status'] == 'admitted' for c in child_rows)
    if os.environ.get('SN_EVAL_PRINT_COUNTS'):
        print(f'\nCOUNTS candidates={candidates} first={first} second={second} third={third} changed={changed}')
    # Per candidate: a bounded number of statements (attempts, links, worktree,
    # execution reads, record lookup); never a rebuild of the whole tree.
    assert first <= 40 + 25 * candidates and changed <= 40 + 25 * candidates
    assert second == third <= 20  # the version check alone


def test_version_changes_when_the_tree_changes(tree):
    grow(tree, 2)
    api = reader(tree)
    _, before = api.show(tree['tree']['id'])
    root = tree['tree']['root_candidate_id']
    ran(tree, root, {'a': .1, 'b': .1}, 'base-3')
    body, after = api.show(tree['tree']['id'])
    assert after != before
    assert len(next(c for c in body['candidates'] if c['id'] == root)['attempts']) == 3


def test_http_get_returns_an_etag_and_304_for_an_unchanged_tree(tree, monkeypatch):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    grow(tree, 2)
    api = reader(tree)
    monkeypatch.setattr(api, '_scope', lambda **kwargs: None)
    app = FastAPI()
    app.include_router(api.router())
    client = TestClient(app)
    first = client.get(f"/api/iterations/{tree['tree']['id']}")
    assert first.status_code == 200 and first.headers['etag'] and first.headers['cache-control'] == 'no-cache'
    again = client.get(f"/api/iterations/{tree['tree']['id']}", headers={'If-None-Match': first.headers['etag']})
    assert again.status_code in (200, 304)
    settled = client.get(f"/api/iterations/{tree['tree']['id']}").headers['etag']
    unchanged = client.get(f"/api/iterations/{tree['tree']['id']}", headers={'If-None-Match': settled})
    assert unchanged.status_code == 304 and not unchanged.content


def test_request_scope_discovers_executions_once_and_checks_each_execution_once(tmp_path):
    import json
    import threading
    from rag_eval.workbench.comparisons import Admission
    from rag_eval.workbench.run_views import RunViews
    root = tmp_path / 'exec'
    run = root / 'partitions/0001'
    run.mkdir(parents=True)
    (root / 'batch-plan.json').write_text(json.dumps([dict(partition_id='p', case_ids=['a'], run_dir=str(run),
                                                           suite='qasper', mode='chunk', official_name='0001')]))
    calls = []
    record = dict(execution_id='exec', task_id='task', run_dir=str(root), state='finished', terminal=True)
    views = RunViews([], execution_records=lambda: calls.append(1) or [record])
    checked = []
    views._assert_unsealed = lambda run_id, case_id=None: checked.append(run_id)
    views.admission = lambda run_id, bundle=None: dict(status='admitted', reason=None)
    admission = Admission(views)
    measurement = dict(execution_id='exec', execution_state='finished', run_dir=str(root))
    with views.request_scope():
        for _ in range(3):
            views.execution_runs('exec')
            assert admission(measurement)['status'] == 'admitted'
    assert len(calls) == 1 and checked == ['exec-1']
    views.execution_runs('exec')  # outside a request: unchanged behaviour
    admission(measurement)
    assert len(calls) == 3 and checked == ['exec-1', 'exec-1']
    # Scopes are per thread.
    seen = []
    with views.request_scope():
        thread = threading.Thread(target=lambda: seen.append(views.once_per_request('k')))
        thread.start(); thread.join()
        assert views.once_per_request('k') and not views.once_per_request('k')
    assert seen == [True]


def test_ledger_rows_are_reparsed_only_when_the_file_changes(tmp_path, monkeypatch):
    from rag_eval import run_ledger
    from rag_eval.workbench.run_views import RunViews
    ledger = tmp_path / 'runs.jsonl'
    ledger.write_text('')
    parsed = []
    monkeypatch.setattr(run_ledger, 'query_runs', lambda path: parsed.append(path) or [dict(run_dir='x')])
    views = RunViews([], ledger=ledger)
    assert views._ledger_rows() == views._ledger_rows() == [dict(run_dir='x')]
    assert len(parsed) == 1
    views._ledger_rows()[0]['run_dir'] = 'mutated'  # callers get copies
    ledger.write_text('{}\n')
    assert views._ledger_rows() == [dict(run_dir='x')] and len(parsed) == 2
