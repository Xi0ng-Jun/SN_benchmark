"""Isolated, synthetic registration intents: no runner, service or real bundle."""
import copy
import json
import multiprocessing
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor, TimeoutError

import pytest

from rag_eval.identity import fingerprint
from rag_eval.workbench.repository import Conflict, WorkbenchRepository
from test_workbench_evaluation_plans import setup as plan_setup


def job_store(repo):
    from rag_eval.workbench.evaluation_jobs import EvaluationJobStore
    return EvaluationJobStore(repo)


def job_rejected():
    from rag_eval.workbench.evaluation_jobs import JobRejected
    assert issubclass(JobRejected, ValueError)
    return JobRejected


@pytest.fixture
def environment(tmp_path):
    repo, workspace, task, plans, _, save = plan_setup(tmp_path)
    plan = plans.save(**save)
    command = dict(task_id=task['id'], plan_id=plan['plan_id'], revision=plan['revision'],
                   expected_content_hash=plan['content_hash'], actor='alice', request_key=' prepare ')
    return repo, workspace, task, plans, save, plan, command


def rows(repo):
    with repo.connect() as conn:
        return {table: [tuple(row) for row in conn.execute('SELECT * FROM ' + table + ' ORDER BY rowid')]
                for table in ('jobs', 'history', 'requests')}


def events(repo, task_id):
    return [e for e in repo.task(task_id)['history'] if e['kind'].startswith('job.')]


def test_prepare_freezes_whole_fixed_plan_and_only_an_unexecutable_intent(environment):
    repo, workspace, task, _, _, plan, command = environment
    store = job_store(repo)
    result = store.prepare(**command)
    assert result['task_id'] == task['id'] and result['workspace_id'] == workspace['id']
    assert (result['plan_id'], result['plan_revision'], result['plan_content_hash']) == (
        plan['plan_id'], plan['revision'], plan['content_hash'])
    assert result['plan_snapshot'] == plan
    assert result['state'] == 'not_dispatched' and type(result['state_version']) is int
    assert result['state_version'] == 1
    assert result['executable'] is False and result['execution_spec'] is None
    assert result['bindings'] == dict(input='not_captured', source='not_captured',
                                      model_runtime='not_captured', scorer='not_captured')
    assert result['prepared_by'] == 'alice' and result['prepared_at']
    assert result['cancelled_by'] is None and result['cancelled_at'] is None
    assert not {'run_id', 'result_id', 'process', 'pid', 'exit_code'} & result.keys()
    assert store.get(task['id'], result['job_id']) == result
    assert store.list(task['id']) == [result]
    assert store.receipt(task['id'], actor='alice', request_key=' prepare ') == result
    audit = events(repo, task['id'])
    assert [(e['kind'], e['object_id'], e['content']) for e in audit] == [
        ('job.prepared', result['job_id'], result)]
    with repo.connect() as conn:
        saved = conn.execute('SELECT * FROM jobs').fetchone()
        assert (saved['id'], saved['task_id'], saved['workspace_id']) == (
            result['job_id'], task['id'], workspace['id'])
        assert json.loads(saved['record']) == result


def test_old_revision_survives_head_append_source_deletion_and_database_reopen(environment, tmp_path):
    repo, _, task, plans, save, first, command = environment
    second = plans.save(**dict(save, plan_id=first['plan_id'], base_revision=first['revision'],
                              request_key='append', title='New head', mode='reasoning'))
    # Only this test's freshly generated canonical bundle is removed.
    import shutil
    shutil.rmtree(tmp_path / 'data/bundle')
    store = job_store(WorkbenchRepository(tmp_path / 'state'))
    result = store.prepare(**command)
    assert result['plan_snapshot'] == first and result['plan_revision'] != second['revision']
    assert store.prepare(**command) == result
    reopened = job_store(WorkbenchRepository(tmp_path / 'state'))
    assert reopened.get(task['id'], result['job_id']) == result
    assert reopened.receipt(task['id'], actor='alice', request_key=command['request_key']) == result
    assert reopened.list(task['id']) == [result]


def test_same_key_different_content_conflicts_before_new_validation(environment, monkeypatch):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    original = store.prepare(**command)
    from rag_eval.workbench.evaluation_plans import PlanDraftStore
    def forbidden(*args, **kwargs):
        raise AssertionError('replay must not reread the plan')
    monkeypatch.setattr(PlanDraftStore, 'get_fixed_in_transaction', forbidden)
    before = rows(repo)
    assert store.prepare(**command) == original
    for change in (dict(plan_id='unknown'), dict(revision=None), dict(expected_content_hash=[])):
        with pytest.raises(Conflict):
            store.prepare(**dict(command, **change))
    assert rows(repo) == before
    assert store.receipt(task['id'], actor='alice', request_key='missing') is None


def test_distinct_keys_and_actors_create_independent_jobs_preserving_spelling(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    first = store.prepare(**command)
    second = store.prepare(**dict(command, request_key='prepare'))
    third = store.prepare(**dict(command, actor=' bob '))
    assert len({first['job_id'], second['job_id'], third['job_id']}) == 3
    assert third['prepared_by'] == ' bob '
    assert store.list(task['id']) == [first, second, third]
    assert store.receipt(task['id'], actor='bob', request_key=' prepare ') is None
    assert store.receipt(task['id'], actor=' alice ', request_key=' prepare ') is None
    assert store.receipt(task['id'], actor='alice', request_key='prepare') == second


def test_task_actor_scope_is_unambiguous_even_when_both_contain_colons(environment):
    repo, workspace, _, plans, save, _, _ = environment
    with repo.connect(write=True) as conn:
        for task_id in ('task', 'task:actor'):
            conn.execute('INSERT INTO tasks VALUES(?,?,?,?,?)',
                         (task_id, workspace['id'], 'Scope fixture', '', 'fixture-time'))
    store = job_store(repo)
    prepared = []
    for task_id, actor in (('task', 'actor:x'), ('task:actor', 'x')):
        # PlanDraftStore's existing concatenated scope is outside our ownership;
        # unique plan keys isolate this experiment to the Job scope under test.
        plan = plans.save(**dict(save, task_id=task_id, actor=actor, request_key=task_id))
        prepared.append(store.prepare(task_id, plan['plan_id'], plan['revision'],
                                      plan['content_hash'], actor=actor, request_key='same'))
    assert prepared[0]['job_id'] != prepared[1]['job_id']
    assert store.receipt('task', actor='actor:x', request_key='same') == prepared[0]
    assert store.receipt('task:actor', actor='x', request_key='same') == prepared[1]


def test_cancel_cas_receipts_and_replays_never_restore_current_state(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    original = store.prepare(**command)
    cancel = dict(task_id=task['id'], job_id=original['job_id'], expected_state_version=1,
                  actor='bob', request_key=command['request_key'])
    cancelled = store.cancel(**cancel)
    assert cancelled == dict(original, state='cancelled', state_version=2,
                             cancelled_by='bob', cancelled_at=cancelled['cancelled_at'])
    assert cancelled['cancelled_at']
    before = rows(repo)
    assert store.cancel(**cancel) == cancelled
    assert store.prepare(**command) == original
    assert store.receipt(task['id'], actor='alice', request_key=command['request_key']) == original
    assert store.receipt(task['id'], actor='bob', request_key=command['request_key']) is None
    assert store.receipt(task['id'], actor='bob', request_key=command['request_key'], operation='cancel') == cancelled
    assert store.get(task['id'], original['job_id']) == cancelled
    assert store.list(task['id']) == [cancelled]
    assert rows(repo) == before
    for change in (dict(expected_state_version=2), dict(job_id='unknown')):
        with pytest.raises(Conflict):
            store.cancel(**dict(cancel, **change))
    with pytest.raises(Conflict):
        store.cancel(**dict(cancel, request_key='stale'))
    assert rows(repo) == before
    assert store.cancel(**dict(cancel, expected_state_version=2, request_key='ack', actor='alice')) == cancelled
    assert len(events(repo, task['id'])) == 2
    assert store.receipt(task['id'], actor='alice', request_key='ack', operation='cancel') == cancelled
    # The same actor/key also has its independent prepare receipt.
    independent = store.prepare(**dict(command, request_key='ack'))
    assert independent['job_id'] != original['job_id']
    assert store.receipt(task['id'], actor='alice', request_key='ack') == independent
    assert store.receipt(task['id'], actor='alice', request_key='ack', operation='cancel') == cancelled


def test_cancelled_receipt_is_old_command_fact_when_current_record_is_invalid(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    cancel = dict(task_id=task['id'], job_id=job['job_id'], expected_state_version=1,
                  actor='alice', request_key='cancel')
    cancelled = store.cancel(**cancel)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET record=?', ('{}',))
    before = rows(repo)
    assert store.cancel(**cancel) == cancelled
    assert store.receipt(task['id'], actor='alice', request_key='cancel', operation='cancel') == cancelled
    with pytest.raises(job_rejected()):
        store.get(task['id'], job['job_id'])
    assert rows(repo) == before


@pytest.mark.parametrize('change', [dict(plan_id=1), dict(plan_id=''), dict(revision=False),
    dict(revision=None), dict(revision='unknown'), dict(expected_content_hash=True),
    dict(expected_content_hash='a' * 63), dict(expected_content_hash='A' * 64),
    dict(expected_content_hash='0' * 64), dict(actor=False), dict(actor=' '),
    dict(request_key=12), dict(request_key=' '), dict(task_id=False), dict(task_id='unknown')])
def test_invalid_prepare_is_strict_and_leaves_zero_residue(environment, change):
    repo, _, _, _, _, _, command = environment
    before = rows(repo)
    error_type = Conflict if change == dict(expected_content_hash='0' * 64) else job_rejected()
    with pytest.raises(error_type):
        job_store(repo).prepare(**dict(command, **change))
    assert rows(repo) == before


@pytest.mark.parametrize('version', [True, False, 1.0, '1', None, [], {}, 0, -1, 2])
def test_cancel_requires_real_int_and_matching_current_version(environment, version):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    before = rows(repo)
    with pytest.raises(Conflict if type(version) is int and version == 2 else job_rejected()):
        store.cancel(task['id'], job['job_id'], version, actor='alice', request_key='cancel')
    assert rows(repo) == before


@pytest.mark.parametrize('operation', [None, True, 1, [], {}, 'unknown', ' prepare ', 'PREPARE'])
def test_receipt_operation_is_a_strict_closed_choice(environment, operation):
    repo, _, task, _, _, _, _ = environment
    before = rows(repo)
    with pytest.raises(job_rejected()):
        job_store(repo).receipt(task['id'], actor='alice', request_key='key', operation=operation)
    assert rows(repo) == before


def test_task_reads_are_shared_but_cross_task_and_workspace_changes_are_rejected(environment, tmp_path):
    repo, workspace, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    other_ws = repo.register_workspace('Other', tmp_path / 'other')
    other = repo.create_task(other_ws['id'], 'Other', '', request_key='other')
    before = rows(repo)
    for action in (lambda: store.prepare(**dict(command, task_id=other['id'])),
                   lambda: store.get(other['id'], job['job_id']),
                   lambda: store.cancel(other['id'], job['job_id'], 1, actor='alice', request_key='cancel')):
        with pytest.raises(job_rejected()):
            action()
    assert store.list(other['id']) == []
    assert store.receipt(other['id'], actor='alice', request_key=command['request_key']) is None
    assert rows(repo) == before
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE tasks SET workspace_id=? WHERE id=?', (other_ws['id'], task['id']))
    before = rows(repo)
    for action in (lambda: store.prepare(**dict(command, request_key='new')),
                   lambda: store.prepare(**command), lambda: store.get(task['id'], job['job_id']),
                   lambda: store.list(task['id']),
                   lambda: store.receipt(task['id'], actor='alice', request_key=command['request_key']),
                   lambda: store.cancel(task['id'], job['job_id'], 1, actor='bob', request_key='cancel')):
        with pytest.raises(job_rejected()):
            action()
    assert rows(repo) == before


@pytest.mark.parametrize('operation', ['prepare', 'cancel'])
@pytest.mark.parametrize('fault', ['event', 'receipt'])
def test_job_audit_receipt_roll_back_together_on_real_sqlite_failure(environment, operation, fault):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command) if operation == 'cancel' else None
    table = 'history' if fault == 'event' else 'requests'
    # Fail at SQLite insertion, after the current Job has already been written.
    with repo.connect(write=True) as conn:
        conn.execute('CREATE TRIGGER fail_write BEFORE INSERT ON ' + table +
                     " BEGIN SELECT RAISE(ABORT, 'injected write failure'); END")
    before = rows(repo)
    with pytest.raises(sqlite3.IntegrityError, match='injected write failure'):
        if operation == 'prepare':
            store.prepare(**command)
        else:
            store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel')
    assert rows(repo) == before
    with repo.connect(write=True) as conn:
        conn.execute('DROP TRIGGER fail_write')
    result = (store.prepare(**command) if operation == 'prepare' else
              store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel'))
    assert result['state'] == ('not_dispatched' if operation == 'prepare' else 'cancelled')


@pytest.mark.parametrize('change', [dict(format='future'), dict(state='success'),
    dict(state=[]), dict(state_version=True), dict(state_version=1.0), dict(state_version=0),
    dict(executable=0), dict(executable=True), dict(execution_spec={}),
    dict(bindings=dict(input='captured')), dict(plan_content_hash='0' * 64),
    dict(plan_revision='wrong'), dict(plan_id='wrong'), dict(task_id='wrong'),
    dict(workspace_id='wrong'), dict(plan_snapshot={}), dict(extra='unknown'),
    dict(cancelled_by='alice'), dict(prepared_by=False), dict(job_id=[]),
    dict(prepared_at=1), dict(state_version=3), dict(plan_content_hash=True)])
@pytest.mark.parametrize('where', ['jobs', 'requests'])
def test_unknown_or_corrupted_job_schema_is_never_interpreted_or_rewritten(environment, change, where):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    altered = dict(copy.deepcopy(job), **change)
    with repo.connect(write=True) as conn:
        if where == 'jobs':
            conn.execute('UPDATE jobs SET record=?', (json.dumps(altered),))
        else:
            conn.execute('UPDATE requests SET receipt=? WHERE receipt LIKE ?',
                         (json.dumps(altered), '%"job_id"%'))
    before = rows(repo)
    reads = ([lambda: store.get(task['id'], job['job_id']), lambda: store.list(task['id']),
              lambda: store.cancel(task['id'], job['job_id'], 1, actor='bob', request_key='cancel')]
             if where == 'jobs' else
             [lambda: store.prepare(**command), lambda: store.receipt(
                 task['id'], actor='alice', request_key=command['request_key'])])
    for read in reads:
        with pytest.raises(job_rejected()):
            read()
    assert rows(repo) == before


def test_current_job_record_must_match_its_sql_identity(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET record=?', (json.dumps(dict(job, job_id='wrong')),))
    before = rows(repo)
    for action in (lambda: store.get(task['id'], job['job_id']), lambda: store.list(task['id']),
                   lambda: store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel')):
        with pytest.raises(job_rejected()):
            action()
    assert rows(repo) == before
    # Recovery does not derive the original response from a damaged current row.
    assert store.receipt(task['id'], actor='alice', request_key=command['request_key']) == job


@pytest.mark.parametrize('mutation', ['state', 'actor', 'plan'])
def test_prepare_receipt_must_remain_the_original_prepare_command(environment, mutation):
    repo, _, task, plans, save, plan, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    altered = copy.deepcopy(job)
    if mutation == 'state':
        altered.update(state='cancelled', state_version=2, cancelled_by='alice', cancelled_at='fixture')
    elif mutation == 'actor':
        altered['prepared_by'] = 'different'
    else:
        second = plans.save(**dict(save, plan_id=plan['plan_id'], base_revision=plan['revision'],
                                  request_key='head', title='Other'))
        altered.update(plan_snapshot=second, plan_revision=second['revision'],
                       plan_content_hash=second['content_hash'])
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE requests SET receipt=? WHERE receipt LIKE ?',
                     (json.dumps(altered), '%"job_id"%'))
    before = rows(repo)
    with pytest.raises(job_rejected()):
        store.prepare(**command)
    if mutation != 'plan':  # Receipt lookup has scope, but not the original command payload.
        with pytest.raises(job_rejected()):
            store.receipt(task['id'], actor='alice', request_key=command['request_key'])
    assert rows(repo) == before


def test_cancel_receipt_cannot_contain_a_prepare_fact(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    cancel = dict(task_id=task['id'], job_id=job['job_id'], expected_state_version=1,
                  actor='alice', request_key='cancel')
    store.cancel(**cancel)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE requests SET receipt=? WHERE scope LIKE ?',
                     (json.dumps(job), 'job.cancel:%'))
    before = rows(repo)
    with pytest.raises(job_rejected()):
        store.cancel(**cancel)
    with pytest.raises(job_rejected()):
        store.receipt(task['id'], actor='alice', request_key='cancel', operation='cancel')
    assert rows(repo) == before


@pytest.mark.parametrize('mutation', ['format', 'hash', 'snapshot', 'workspace', 'task'])
def test_prepare_rejects_bad_fixed_snapshot_with_zero_residue(environment, mutation):
    repo, _, _, _, _, plan, command = environment
    altered = copy.deepcopy(plan)
    if mutation == 'format':
        altered['content']['format'] = 'future'
        altered['content_hash'] = fingerprint(altered['content'])
    elif mutation == 'hash':
        altered['content_hash'] = '0' * 64
    elif mutation == 'snapshot':
        del altered['content']['selection']
        altered['content_hash'] = fingerprint(altered['content'])
    elif mutation == 'workspace':
        altered['workspace_id'] = 'different'
        altered['content']['source_intent']['workspace_id'] = 'different'
        altered['content_hash'] = fingerprint(altered['content'])
    else:
        altered['task_id'] = 'different'
    with repo.connect(write=True) as conn:
        conn.execute("UPDATE history SET content=? WHERE kind='plan.draft.saved'", (json.dumps(altered),))
    before = rows(repo)
    with pytest.raises(job_rejected()):
        job_store(repo).prepare(**dict(command, expected_content_hash=altered['content_hash']))
    assert rows(repo) == before


@pytest.mark.parametrize('encoded', ['null', '[]', 'true', '1', '"record"', '{', '{}'])
def test_record_json_must_be_a_complete_object(environment, encoded):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE jobs SET record=?', (encoded,))
    before = rows(repo)
    for action in (lambda: store.get(task['id'], job['job_id']), lambda: store.list(task['id']),
                   lambda: store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel')):
        with pytest.raises(job_rejected()):
            action()
    assert rows(repo) == before


def test_jobs_and_receipts_are_independent_of_audit_history(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    cancelled = store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel')
    with repo.connect(write=True) as conn:
        conn.execute("UPDATE history SET content='{}' WHERE kind LIKE 'job.%'")
        conn.execute("DELETE FROM history WHERE kind='plan.draft.saved'")
    assert store.get(task['id'], job['job_id']) == cancelled
    assert store.list(task['id']) == [cancelled]
    assert store.prepare(**command) == job
    assert store.receipt(task['id'], actor='alice', request_key='cancel', operation='cancel') == cancelled
    before = rows(repo)
    with pytest.raises(job_rejected()):
        store.prepare(**dict(command, request_key='new'))
    assert rows(repo) == before


def test_unknown_database_schema_is_rejected_by_every_operation_without_writes(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    with repo.connect(write=True) as conn:
        conn.execute('PRAGMA user_version=99')
    before = rows(repo)
    for action in (lambda: store.prepare(**dict(command, request_key='new')), lambda: store.prepare(**command),
                   lambda: store.get(task['id'], job['job_id']), lambda: store.list(task['id']),
                   lambda: store.receipt(task['id'], actor='alice', request_key=command['request_key']),
                   lambda: store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel')):
        with pytest.raises(job_rejected()):
            action()
    assert rows(repo) == before


@pytest.mark.parametrize('change', [dict(task_id=False), dict(task_id=[]), dict(job_id=1),
    dict(job_id=None), dict(actor=[]), dict(actor=' '), dict(request_key=False), dict(request_key=' ')])
def test_cancel_command_types_cannot_be_coerced(environment, change):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    cancel = dict(task_id=task['id'], job_id=job['job_id'], expected_state_version=1,
                  actor='alice', request_key='cancel')
    before = rows(repo)
    with pytest.raises(job_rejected()):
        store.cancel(**dict(cancel, **change))
    assert rows(repo) == before


@pytest.mark.parametrize('field', ['job_id', 'workspace_id', 'plan_snapshot', 'bindings',
                                    'state', 'state_version', 'execution_spec', 'run_id'])
def test_prepare_cannot_override_server_owned_fields(environment, field):
    repo, _, _, _, _, _, command = environment
    before = rows(repo)
    with pytest.raises(TypeError):
        job_store(repo).prepare(**dict(command, **{field: 'forged'}))
    assert rows(repo) == before


def test_mutating_return_values_cannot_change_any_authoritative_record(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    expected = copy.deepcopy(job)
    returned = [job, store.get(task['id'], job['job_id']), store.list(task['id'])[0],
                store.receipt(task['id'], actor='alice', request_key=command['request_key'])]
    for value in returned:
        value['plan_snapshot']['content']['selection']['case_ids'].append('forged')
        value['bindings']['input'] = 'captured'
        value['state'] = 'success'
    assert store.get(task['id'], expected['job_id']) == expected
    assert store.prepare(**command) == expected
    assert events(repo, task['id'])[0]['content'] == expected


def test_same_key_across_threads_creates_one_job_and_one_receipt(environment):
    repo, _, task, _, _, _, command = environment
    barrier = threading.Barrier(4)
    def prepare(_):
        barrier.wait(timeout=10)
        return job_store(repo).prepare(**command)
    with ThreadPoolExecutor(max_workers=4) as pool:
        values = list(pool.map(prepare, range(4)))
    assert all(v == values[0] for v in values)
    assert len(rows(repo)['jobs']) == 1 and len(events(repo, task['id'])) == 1
    assert len(rows(repo)['requests']) == 3  # Task, plan, one Job command.


def _process_prepare(state, command, barrier, queue):
    try:
        store = job_store(WorkbenchRepository(state))
        barrier.wait(timeout=15)
        queue.put(('ok', store.prepare(**command)))
    except BaseException as exc:
        queue.put(('error', type(exc).__name__ + ': ' + str(exc)))


def test_same_key_across_real_spawned_processes_creates_one_job(environment):
    repo, _, task, _, _, _, command = environment
    ctx = multiprocessing.get_context('spawn')
    barrier, queue = ctx.Barrier(3), ctx.Queue()
    children = [ctx.Process(target=_process_prepare, args=(str(repo.root), command, barrier, queue))
                for _ in range(2)]
    try:
        for child in children:
            child.start()
        barrier.wait(timeout=20)
        outcomes = [queue.get(timeout=20) for _ in children]
        for child in children:
            child.join(timeout=20)
            assert child.exitcode == 0
        assert [kind for kind, _ in outcomes] == ['ok', 'ok']
        assert outcomes[0][1] == outcomes[1][1]
        assert len(rows(repo)['jobs']) == 1 and len(events(repo, task['id'])) == 1
        assert len(rows(repo)['requests']) == 3
    finally:
        for child in children:
            if child.is_alive():
                child.terminate()
                child.join(timeout=5)
        queue.close()


def test_concurrent_head_append_does_not_change_explicit_revision(environment):
    repo, _, _, plans, save, old, command = environment
    barrier = threading.Barrier(2)
    def prepare():
        barrier.wait(timeout=10)
        return job_store(repo).prepare(**command)
    def append():
        barrier.wait(timeout=10)
        return plans.save(**dict(save, plan_id=old['plan_id'], base_revision=old['revision'],
                                 request_key='head', title='Head'))
    with ThreadPoolExecutor(max_workers=2) as pool:
        job_future, head_future = pool.submit(prepare), pool.submit(append)
        job, head = job_future.result(timeout=15), head_future.result(timeout=15)
    assert job['plan_snapshot'] == old and job['plan_revision'] != head['revision']


def test_two_cancel_commands_race_on_one_global_state_version(environment):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    barrier = threading.Barrier(2)
    def cancel(actor):
        barrier.wait(timeout=10)
        try:
            return store.cancel(task['id'], job['job_id'], 1, actor=actor, request_key='cancel')
        except Conflict:
            return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(cancel, ['alice', 'bob']))
    winner = next(v for v in results if v is not None)
    assert sum(v is not None for v in results) == 1
    assert store.get(task['id'], job['job_id']) == winner
    assert len(events(repo, task['id'])) == 2
    assert len(rows(repo)['requests']) == 4


@pytest.fixture
def lock_environment(tmp_path):
    # Fresh canonical and state for lock experiments, separate from other tests.
    repo, _, task, plans, _, save = plan_setup(tmp_path / 'lock-only')
    plan = plans.save(**save)
    return repo, dict(task_id=task['id'], plan_id=plan['plan_id'], revision=plan['revision'],
                     expected_content_hash=plan['content_hash'], actor='alice', request_key='lock')


def test_registration_waits_for_independent_writer_then_publishes_atomically(lock_environment):
    repo, command = lock_environment
    entered = threading.Event()
    def prepare():
        entered.set()
        return job_store(repo).prepare(**command)
    lock = sqlite3.connect(repo.database)
    lock.execute('BEGIN IMMEDIATE')
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(prepare)
            assert entered.wait(timeout=5)
            try:
                with pytest.raises(TimeoutError):
                    future.result(timeout=0.1)
                assert rows(repo)['jobs'] == []
            finally:
                lock.rollback()
            result = future.result(timeout=15)
        assert job_store(repo).get(command['task_id'], result['job_id']) == result
        assert len(events(repo, command['task_id'])) == 1
    finally:
        lock.close()


def test_lock_timeout_leaves_no_partial_job_or_receipt(lock_environment, monkeypatch):
    repo, command = lock_environment
    original_connect = sqlite3.connect
    def short_timeout(*args, **kwargs):
        return original_connect(*args, **dict(kwargs, timeout=0.05))
    before = rows(repo)
    lock = original_connect(repo.database)
    lock.execute('BEGIN IMMEDIATE')
    try:
        monkeypatch.setattr(sqlite3, 'connect', short_timeout)
        with pytest.raises(sqlite3.OperationalError, match='locked'):
            job_store(repo).prepare(**command)
        assert rows(repo) == before
    finally:
        lock.rollback()
        lock.close()
    assert job_store(repo).prepare(**command)['state'] == 'not_dispatched'


@pytest.mark.parametrize('operation', ['prepare', 'cancel'])
@pytest.mark.parametrize('seam', ['_event', '_receipt', 'commit', 'sql'])
@pytest.mark.parametrize('error_type', [ValueError, FileNotFoundError, sqlite3.OperationalError])
def test_internal_write_exceptions_propagate_exactly_and_roll_back(environment, monkeypatch,
                                                                  operation, seam, error_type):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command) if operation == 'cancel' else None
    before = rows(repo)
    error = error_type('synthetic internal failure at ' + seam)
    def fail(*args, **kwargs):
        raise error
    if seam in ('_event', '_receipt'):
        monkeypatch.setattr(repo, seam, fail)
    else:
        original_connect = sqlite3.connect
        class FaultConnection(sqlite3.Connection):
            def execute(self, query, *args, **kwargs):
                if seam == 'sql' and query.startswith(('INSERT INTO jobs', 'UPDATE jobs')):
                    raise error
                return super().execute(query, *args, **kwargs)

            def commit(self):
                if seam == 'commit' and self.in_transaction:
                    raise error
                return super().commit()

        def failing_connect(*args, **kwargs):
            return original_connect(*args, **dict(kwargs, factory=FaultConnection))
        monkeypatch.setattr(sqlite3, 'connect', failing_connect)
    with pytest.raises(error_type) as caught:
        if operation == 'prepare':
            store.prepare(**command)
        else:
            store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel')
    assert caught.value is error
    assert rows(repo) == before


@pytest.mark.parametrize('operation', ['prepare', 'get', 'list', 'receipt', 'cancel'])
@pytest.mark.parametrize('error_type', [ValueError, FileNotFoundError, TypeError])
def test_plan_validator_program_exceptions_are_never_job_rejections(environment, monkeypatch,
                                                                  operation, error_type):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    from rag_eval.workbench import evaluation_jobs
    error = error_type('synthetic validator implementation failure')
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(evaluation_jobs, 'validate_plan_snapshot', fail)
    before = rows(repo)
    actions = dict(prepare=lambda: store.prepare(**dict(command, request_key='new')),
                   get=lambda: store.get(task['id'], job['job_id']), list=lambda: store.list(task['id']),
                   receipt=lambda: store.receipt(task['id'], actor='alice', request_key=command['request_key']),
                   cancel=lambda: store.cancel(task['id'], job['job_id'], 1, actor='alice', request_key='cancel'))
    with pytest.raises(error_type) as caught:
        actions[operation]()
    assert caught.value is error
    assert rows(repo) == before


@pytest.mark.parametrize('seam', ['require', 'fixed', 'prior', 'decode'])
@pytest.mark.parametrize('error_type', [ValueError, FileNotFoundError, sqlite3.OperationalError, TypeError])
def test_read_seams_preserve_unclassified_program_exceptions(environment, monkeypatch, seam, error_type):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    before = rows(repo)
    error = error_type('synthetic internal read failure')
    def fail(*args, **kwargs):
        raise error
    if seam == 'require':
        monkeypatch.setattr(repo, '_require', fail)
        action = lambda: store.list(task['id'])
    elif seam == 'fixed':
        from rag_eval.workbench.evaluation_plans import PlanDraftStore
        monkeypatch.setattr(PlanDraftStore, 'get_fixed_in_transaction', fail)
        action = lambda: store.prepare(**dict(command, request_key='new'))
    elif seam == 'prior':
        monkeypatch.setattr(repo, '_prior', fail)
        action = lambda: store.prepare(**command)
    else:
        from rag_eval.workbench import evaluation_jobs
        monkeypatch.setattr(evaluation_jobs.json, 'loads', fail)
        action = lambda: store.get(task['id'], job['job_id'])
    with pytest.raises(error_type) as caught:
        action()
    assert caught.value is error
    assert rows(repo) == before


@pytest.mark.parametrize('case', ['input', 'unknown_task', 'unknown_job', 'unknown_plan',
    'unknown_revision', 'workspace', 'record', 'plan_schema', 'plan_json', 'receipt_json',
    'job_json', 'db_schema', 'receipt_operation', 'receipt_semantics'])
def test_known_domain_rejections_have_the_explicit_job_type_and_no_writes(environment, case):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    action = lambda: store.prepare(**dict(command, request_key='new'))
    if case == 'input':
        action = lambda: store.prepare(**dict(command, request_key='new', revision=False))
    elif case == 'unknown_task':
        action = lambda: store.list('unknown')
    elif case == 'unknown_job':
        action = lambda: store.get(task['id'], 'unknown')
    elif case == 'unknown_plan':
        action = lambda: store.prepare(**dict(command, request_key='new', plan_id='unknown'))
    elif case == 'unknown_revision':
        action = lambda: store.prepare(**dict(command, request_key='new', revision='unknown'))
    elif case == 'receipt_operation':
        action = lambda: store.receipt(task['id'], actor='alice', request_key='key', operation=True)
    else:
        with repo.connect(write=True) as conn:
            if case == 'workspace':
                # Keep FK-valid identity while making snapshot ownership disagree.
                altered = dict(job, workspace_id='different')
                conn.execute('UPDATE jobs SET record=?', (json.dumps(altered),))
            elif case == 'record':
                conn.execute("UPDATE jobs SET record='{}'")
            elif case in ('plan_schema', 'plan_json'):
                conn.execute("UPDATE history SET content=? WHERE kind='plan.draft.saved'",
                             ('{}' if case == 'plan_schema' else '{',))
            elif case in ('receipt_json', 'receipt_semantics'):
                altered = dict(job, prepared_by='different')
                conn.execute('UPDATE requests SET receipt=? WHERE scope LIKE ?',
                             ('{' if case == 'receipt_json' else json.dumps(altered), 'job.prepare:%'))
            elif case == 'job_json':
                conn.execute("UPDATE jobs SET record='{' ")
            elif case == 'db_schema':
                conn.execute('PRAGMA user_version=99')
        if case in ('workspace', 'record', 'job_json'):
            action = lambda: store.get(task['id'], job['job_id'])
        elif case in ('receipt_json', 'receipt_semantics'):
            action = lambda: store.prepare(**command)
    before = rows(repo)
    with pytest.raises(job_rejected()):
        action()
    assert rows(repo) == before


def test_conflict_is_still_its_own_known_type(environment):
    repo, _, _, _, _, _, command = environment
    store = job_store(repo)
    store.prepare(**command)
    before = rows(repo)
    with pytest.raises(Conflict) as caught:
        store.prepare(**dict(command, revision='different'))
    assert not isinstance(caught.value, job_rejected())
    assert rows(repo) == before


@pytest.mark.parametrize('seam', ['_event', '_receipt'])
@pytest.mark.parametrize('error_name', ['UnknownIdentity', 'PlanSnapshotRejected', 'FixedPlanRejected'])
def test_known_error_classes_are_not_reclassified_outside_their_read_boundary(environment, monkeypatch,
                                                                            seam, error_name):
    repo, _, _, _, _, _, command = environment
    from rag_eval.workbench import evaluation_plans, repository
    namespace = repository if error_name == 'UnknownIdentity' else evaluation_plans
    error = getattr(namespace, error_name)('synthetic write-time implementation error')
    before = rows(repo)
    def fail(*args, **kwargs):
        raise error
    monkeypatch.setattr(repo, seam, fail)
    with pytest.raises(type(error)) as caught:
        job_store(repo).prepare(**command)
    assert caught.value is error
    assert rows(repo) == before


@pytest.mark.parametrize('encoded', ['null', '[]', 'true', '1', '"record"', '{}', '{'])
@pytest.mark.parametrize('operation', ['prepare', 'cancel'])
def test_nonrecord_receipt_is_typed_on_read_and_replay_without_writes(environment, encoded, operation):
    repo, _, task, _, _, _, command = environment
    store = job_store(repo)
    job = store.prepare(**command)
    if operation == 'cancel':
        replay_command = dict(task_id=task['id'], job_id=job['job_id'], expected_state_version=1,
                              actor='alice', request_key='cancel')
        store.cancel(**replay_command)
        action = lambda: store.cancel(**replay_command)
        key = 'cancel'
    else:
        action = lambda: store.prepare(**command)
        key = command['request_key']
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE requests SET receipt=? WHERE scope LIKE ?', (encoded, 'job.' + operation + ':%'))
    before = rows(repo)
    with pytest.raises(job_rejected()):
        store.receipt(task['id'], actor='alice', request_key=key, operation=operation)
    with pytest.raises(job_rejected()):
        action()
    assert rows(repo) == before
