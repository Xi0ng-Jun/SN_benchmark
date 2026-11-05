"""Run-scoped admission exercised over durable synthetic execution services."""
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
import threading
import time

import pytest

from rag_eval.optimizer.sn_adapter import SNAdapter
from rag_eval.optimizer.adapter import EvaluationUnavailable
from rag_eval.workbench.loop_measurements import RoundMeasurements
from test_workbench_iterations import env, iteration


def runtime(env, tree, limit=1, seconds=5):
    measurements = RoundMeasurements(SimpleNamespace(service=env['service'], repository=env['repo'],
        views=SimpleNamespace(execution_runs=lambda _: []), _scope=lambda **_: None))
    sn = SNAdapter(dict(run_id='run', actor='alice', task_id=tree['task_id']),
                   round_operations=SimpleNamespace(measurements=measurements), infra_retry=0)
    assert hasattr(sn, 'configure_runtime'), 'actual execution admission must be configurable'
    end = time.monotonic() + seconds
    sn.configure_runtime(remaining=lambda: end - time.monotonic(), max_concurrent_executions=limit)
    return sn, measurements


def test_limit_counts_running_execution_after_dispatch_return_and_shared_fanout(env, monkeypatch):
    tree = iteration(env, k_base=3, case_ids=['qasper:q', 'qasper:q2', 'qasper:q3'])
    root = tree['root_candidate_id']
    sn, measurements = runtime(env, tree, limit=2)
    executions = env['executions']
    started, peak, lock = [], [0], threading.Lock()
    def start(record):
        with lock:
            started.append(record['execution_id'])
            live = sum(not executions.get(tree['task_id'], eid)['terminal'] for eid in started)
            peak[0] = max(peak[0], live)
        def finish():
            time.sleep(.04)
            executions._finish(record['execution_id'], state='finished', exit_code=0, error_type=None,
                               run_state=dict(phase='finished'))
        threading.Thread(target=finish).start()
    monkeypatch.setattr(executions, '_start', start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        calls = [pool.submit(measurements.split, root, split, key='v3/run/' + split,
                             **sn._v2_run_kwargs()) for split in ('search', 'validation')]
        for call in calls:
            call.result(timeout=4)
    assert len(started) == 6
    assert peak[0] <= 2
    assert peak[0] == 2


def test_deadline_cancels_owned_live_execution_and_preserves_replay_identity(env, monkeypatch):
    tree = iteration(env, k_base=3, case_ids=['qasper:q', 'qasper:q2', 'qasper:q3'])
    root = tree['root_candidate_id']
    sn, measurements = runtime(env, tree)
    deadline = [time.monotonic() + 5]
    sn._runtime_remaining = lambda: deadline[0] - time.monotonic()
    monkeypatch.setattr(env['executions'], '_start',
                        lambda _: deadline.__setitem__(0, time.monotonic() + .08))
    with pytest.raises(EvaluationUnavailable):
        measurements.split(root, 'search', key='v3/run/search', **sn._v2_run_kwargs())
    attempts = env['service'].candidate(root)['attempts']
    launched = [a for a in attempts if a['execution_id']]
    assert len(launched) == 1
    sn.cancel_pending()
    saved_id = launched[0]['execution_id']
    assert env['executions'].get(tree['task_id'], saved_id)['state'] == 'cancelled'
    # Runtime cancellation never changes the immutable measurement request.
    sn2, resumed = runtime(env, tree)
    with env['repo'].connect() as conn:
        state = __import__('json').loads(conn.execute('SELECT payload_json FROM round_measurements').fetchone()[0])
    assert state['original_key'] == 'v3/run/search'
    assert len(state['generations'][0]) == 3
    assert saved_id in [a['execution_id'] for a in env['service'].candidate(root)['attempts']]


def test_recovered_active_receipts_consume_capacity_before_new_dispatch(env):
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q2', 'qasper:q3'])
    root = tree['root_candidate_id']
    sn, measurements = runtime(env, tree, limit=2)
    measurements.split(root, 'search', key='v3/run/search', **sn._v2_run_kwargs())
    # Simulate a new coordinator process: its admission must discover the original
    # still-running receipt before any fresh scope can launch.
    resumed, measurements2 = runtime(env, tree, limit=1, seconds=.6)
    with pytest.raises(EvaluationUnavailable):
        measurements2.split(root, 'validation', key='v3/run/validation',
                            **resumed._v2_run_kwargs())
    assert sum(bool(a['execution_id']) for a in env['service'].candidate(root)['attempts']) == 2
    resumed.cancel_pending()


def test_cancellation_during_launch_stops_execution_published_after_cancel_scan(env, monkeypatch):
    tree = iteration(env, k_base=1)
    sn, measurements = runtime(env, tree)
    entered, release = threading.Event(), threading.Event()
    def starting(_):
        entered.set()
        assert release.wait(2)
    monkeypatch.setattr(env['executions'], '_start', starting)
    with ThreadPoolExecutor(max_workers=1) as pool:
        job = pool.submit(measurements.split, tree['root_candidate_id'], 'search',
                          key='v3/run/launch-race', **sn._v2_run_kwargs())
        assert entered.wait(2)
        sn.cancel_pending()
        release.set()
        try:
            job.result(timeout=2)
        except EvaluationUnavailable:
            pass
    attempts = env['service'].candidate(tree['root_candidate_id'])['attempts']
    assert len(attempts) == 1
    assert env['executions'].get(tree['task_id'], attempts[0]['execution_id'])['state'] == 'cancelled'
    assert any(r.get('execution_id') == attempts[0]['execution_id'] for r in sn.cancellation_report)


def test_recovery_counts_execution_receipt_before_candidate_attachment(env):
    tree = iteration(env, case_ids=['qasper:q', 'qasper:q2', 'qasper:q3'])
    root = tree['root_candidate_id']
    sn, measurements = runtime(env, tree, limit=2)
    measurements.split(root, 'search', key='v3/run/search', **sn._v2_run_kwargs())
    # A crash can occur after executions.run persisted its receipt but before
    # IterationService attaches that receipt to the candidate attempt.
    with env['repo'].connect(write=True) as conn:
        conn.execute('UPDATE candidate_attempts SET execution_id=NULL WHERE candidate_id=?', (root,))
    resumed, measurements2 = runtime(env, tree, limit=1, seconds=.6)
    with pytest.raises(EvaluationUnavailable):
        measurements2.split(root, 'validation', key='v3/run/validation', **resumed._v2_run_kwargs())
    assert len(env['executions'].records(env['workspace']['id'])) == 2
    resumed.cancel_pending()


def test_cancellation_stops_a_real_owned_subprocess_without_replacing_its_receipt(tmp_path):
    from rag_eval.workbench.job_execution import JobExecutionStore
    from test_workbench_job_execution import fixture, spec
    repo, task, job = fixture(tmp_path)
    executions = JobExecutionStore(repo)
    sn = SNAdapter(dict(run_id='real-process', actor='alice'))
    sn.configure_runtime(remaining=lambda: 5, max_concurrent_executions=1)
    with sn._execution_admission.dispatch() as track:
        execution = executions.run(task['id'], job['job_id'], 1,
            execution_spec=spec(tmp_path, 'import time; time.sleep(30)'), actor='alice', request_key='owned')
        track(executions, task['id'], execution)
    process = executions._processes[execution['execution_id']]
    assert process.poll() is None
    sn.cancel_pending()
    assert process.wait(timeout=2) is not None
    record = executions.get(task['id'], execution['execution_id'])
    assert record['state'] == 'cancelled'
    assert record['execution_id'] == execution['execution_id']
    assert record['preparation_id'] == execution['preparation_id']


def test_stop_polling_leaves_running_execution_resumable_under_same_identity(env):
    tree = iteration(env)
    root = tree['root_candidate_id']
    sn, measurements = runtime(env, tree, limit=2)
    measurements.split(root, 'search', key='v3/run/search', **sn._v2_run_kwargs())
    original = [a['execution_id'] for a in env['service'].candidate(root)['attempts']]
    assert hasattr(sn, 'stop_polling'), 'process interruption only stops local workers'
    sn.stop_polling()
    with pytest.raises(EvaluationUnavailable):
        sn.check_runtime()
    assert all(env['executions'].get(tree['task_id'], eid)['state'] == 'running' for eid in original)
    resumed, measurements2 = runtime(env, tree, limit=2)
    measurements2.split(root, 'search', key='v3/run/search', **resumed._v2_run_kwargs())
    assert [a['execution_id'] for a in env['service'].candidate(root)['attempts']] == original
    resumed.cancel_pending()


def test_deadline_interrupts_durable_measurement_lock_wait_without_launch(env):
    import fcntl
    from pathlib import Path
    tree = iteration(env)
    sn, measurements = runtime(env, tree, seconds=.1)
    directory = Path(env['service'].trees.store.root) / 'round-measurement-locks'
    directory.mkdir(parents=True, exist_ok=True)
    outcome = []
    def worker():
        try:
            measurements.split(tree['root_candidate_id'], 'search', key='v3/run/locked', **sn._v2_run_kwargs())
            outcome.append('launched')
        except EvaluationUnavailable:
            outcome.append('cancelled')
    with (directory / (tree['id'] + '.lock')).open('a') as held:
        fcntl.flock(held, fcntl.LOCK_EX)
        thread = threading.Thread(target=worker)
        thread.start()
        thread.join(.4)
        finished_in_time = not thread.is_alive()
        fcntl.flock(held, fcntl.LOCK_UN)
    thread.join(2)
    assert finished_in_time
    assert outcome == ['cancelled']
    assert not env['executions'].records(env['workspace']['id'])


def test_admission_bounds_real_processes_across_two_measurement_fanouts(env, monkeypatch):
    import sys
    from rag_eval.workbench.job_execution import JobExecutionStore
    tree = iteration(env, k_base=3, case_ids=['qasper:q', 'qasper:q2', 'qasper:q3'])
    sn, measurements = runtime(env, tree, limit=2, seconds=10)
    service, executions = env['service'], env['executions']
    build_spec = service.build_spec
    def synthetic_spec(*args, **kwargs):
        spec = build_spec(*args, **kwargs)
        spec['argv'] = [sys.executable, '-c',
            'import time; time.sleep(.15)']
        return spec
    monkeypatch.setattr(service, 'build_spec', synthetic_spec)
    processes, peak, lock = [], [0], threading.Lock()
    def start(record):
        JobExecutionStore._start(executions, record)
        with lock:
            process = executions._processes.get(record['execution_id'])
            if process is not None:
                processes.append(process)
            peak[0] = max(peak[0], sum(p.poll() is None for p in processes))
    monkeypatch.setattr(executions, '_start', start)
    with ThreadPoolExecutor(max_workers=2) as pool:
        jobs = [pool.submit(measurements.split, tree['root_candidate_id'], split,
                            key='v3/run/' + split, **sn._v2_run_kwargs())
                for split in ('search', 'validation')]
        for job in jobs:
            job.result(timeout=8)
    for process in processes:
        process.wait(timeout=2)
    assert len(processes) == 6
    assert peak[0] == 2


def test_frozen_implementation_replay_recovers_committed_diff_without_agent(env):
    from rag_eval.optimizer.adapter import Card
    from test_workbench_loop_measurements import child
    tree = iteration(env)
    cid = child(env, tree, 'frozen-implementation')
    node = env['service'].candidate(cid)
    agents = []
    sn = SNAdapter(dict(run_id='run', actor='alice', task_id=tree['task_id'], iteration_id=tree['id']),
                   round_operations=SimpleNamespace(service=env['service'],
                       agent=lambda *a, **k: agents.append(k) or dict(status='idle')))
    workspace = dict(candidate_id=cid, parent_candidate_id=tree['root_candidate_id'],
                     hypothesis_id='H', path=node['worktree']['path'])
    diff = sn.implement(Card('failure', 'change', ref='H'), workspace)
    assert '+VALUE = 3' in diff.content
    assert '-VALUE = 1' in diff.content
    assert diff.metadata['candidate_id'] == cid
    assert agents == []


def test_frozen_repair_replay_recovers_diff_without_reapplying_or_waking_agent(env, tmp_path):
    from rag_eval.optimizer.adapter import Card, EvalResult, SmokeResult
    from test_workbench_loop_measurements import child
    tree = iteration(env)
    root = tree['root_candidate_id']
    failed, repaired = child(env, tree, 'failed'), child(env, tree, 'repaired')
    calls = []
    def candidate(cid):
        return dict(env['service'].candidate(cid), proposal_id='H')
    ops = SimpleNamespace(service=SimpleNamespace(candidate=candidate),
        derive=lambda *a, **k: repaired,
        rebase=lambda *a, **k: calls.append('rebase') or dict(status='applied'),
        agent=lambda *a, **k: calls.append('agent') or dict(status='idle'))
    sn = SNAdapter(dict(run_id='run', actor='alice', task_id=tree['task_id'], iteration_id=tree['id']),
                   round_operations=ops, evidence_dir=tmp_path / 'evidence')
    sn._repair_feedback = lambda *a: ('original repair feedback', {})
    empty = EvalResult({}, {}, {}, {})
    diff = sn.repair(Card('failure', 'change', ref='H'), sn.version_for(root), sn.version_for(failed),
        smoke=SmokeResult('failed', 'H', True, True, True, 'fail'),
        candidate_smoke=empty, parent_smoke=empty, attempt=2)
    assert '+VALUE = 3' in diff.content
    assert diff.metadata['repair_of'] == failed
    assert diff.metadata['candidate_id'] == repaired
    assert calls == []


def test_recovery_skips_exhausted_footprints_without_dispatch_metadata(env):
    from test_workbench_loop_measurements import child
    tree = iteration(env)
    cid = child(env, tree, 'exhausted')
    sn, measurements = runtime(env, tree)
    result = measurements.fresh(cid, key='v3/run/exhausted', **sn._v2_run_kwargs())
    assert result['status'] == 'exhausted'
    resumed, _ = runtime(env, tree)
    resumed.check_runtime()
