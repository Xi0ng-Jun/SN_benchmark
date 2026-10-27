"""Partition parallelism, question concurrency, deadlines, the model gate and retry merging (offline)."""
import json
import sys
import threading
import time
from pathlib import Path

import pytest

from rag_eval.notebook_batch import execute_batch
from rag_eval.run_report import load_run
from test_prepared_seed_retry import fake_sn  # noqa: F401


# ------------------------------------------------------------ runner level
@pytest.fixture
def slow_sn(fake_sn, monkeypatch, tmp_path):
    """Three questions in one partition; the fake model sleeps per question."""
    from rag_eval import system_runtime
    fake_sn.delay, fake_sn.hang, fake_sn.active, fake_sn.peak = {}, set(), 0, 0
    lock = threading.Lock()
    base = system_runtime.run_system_question

    def ask(repo, notebook, question, mode, mapping, **kwargs):
        with lock:
            fake_sn.active += 1
            fake_sn.peak = max(fake_sn.peak, fake_sn.active)
        try:
            if question['case_id'] in fake_sn.hang:
                time.sleep(30)
            time.sleep(fake_sn.delay.get(question['case_id'], 0.3))
            return base(repo, notebook, question, mode, mapping, **kwargs)
        finally:
            with lock:
                fake_sn.active -= 1
    monkeypatch.setattr(system_runtime, 'run_system_question', ask)
    return fake_sn


def partition_cases(tmp_path):
    from rag_eval.notebook_bundle import load_bundle
    bundle = load_bundle(tmp_path / 'bundle')
    partition = bundle['partitions'][0]
    return partition['partition_id'], partition['case_ids']


def run_partition(sn, tmp_path, name, **kwargs):
    from rag_eval.notebook_runner import execute
    pid, cases = partition_cases(tmp_path)
    path = tmp_path / name
    execute(root=Path(__file__).resolve().parents[1], project=tmp_path / 'product', bundle_dir=tmp_path / 'bundle',
            run=path, mode='chunk', partition_id=pid, case_ids=cases, **kwargs)
    return path, cases


def test_question_workers_overlap_independent_questions(slow_sn, tmp_path):
    _, cases = partition_cases(tmp_path)
    if len(cases) < 2:
        pytest.skip('fixture partition has a single question')
    start = time.monotonic()
    serial, _ = run_partition(slow_sn, tmp_path, 'serial')
    serial_time = time.monotonic() - start
    slow_sn.peak = 0
    start = time.monotonic()
    pooled, _ = run_partition(slow_sn, tmp_path, 'pooled', question_workers=4)
    pooled_time = time.monotonic() - start
    assert slow_sn.peak == min(4, len(cases))
    assert pooled_time < serial_time * 0.75
    left, right = load_run(serial), load_run(pooled)
    assert sorted(r['case_id'] for r in right['outputs']) == sorted(cases) and not right['warnings']
    assert {r['case_id']: r['status'] for r in left['outputs']} == {r['case_id']: r['status'] for r in right['outputs']}
    manifest = json.loads((pooled / 'manifest.json').read_text())
    assert manifest['execution_controls']['question_workers'] == 4
    assert json.loads((serial / 'manifest.json').read_text())['identity'] == manifest['identity']


def test_case_deadline_saves_error_with_phase_and_partition_finishes(slow_sn, tmp_path):
    _, cases = partition_cases(tmp_path)
    slow_sn.hang.add(cases[0])
    start = time.monotonic()
    run, _ = run_partition(slow_sn, tmp_path, 'deadline', question_workers=2, case_deadline_seconds=1)
    assert time.monotonic() - start < 15
    outputs = {r['case_id']: r for r in load_run(run)['outputs']}
    assert set(outputs) == set(cases)
    hung = outputs[cases[0]]
    assert hung['status'] == 'error'
    record = hung['product_record']
    assert record['error_type'] == 'case_timeout' and record['failed_phase'] == 'asking'
    assert all(outputs[c]['status'] == 'success' for c in cases[1:])
    assert json.loads((run / 'state.json').read_text())['phase'] == 'finished_with_errors'


def test_partition_deadline_marks_unstarted_questions(slow_sn, tmp_path):
    _, cases = partition_cases(tmp_path)
    slow_sn.hang.add(cases[0])
    run, _ = run_partition(slow_sn, tmp_path, 'partition-deadline', partition_deadline_seconds=1)
    outputs = {r['case_id']: r['product_record'] for r in load_run(run)['outputs']}
    assert set(outputs) == set(cases)
    assert outputs[cases[0]]['error_type'] == 'partition_deadline' and outputs[cases[0]]['failed_phase'] == 'asking'
    for cid in cases[1:]:
        assert outputs[cid]['error_type'] == 'partition_deadline' and outputs[cid]['failed_phase'] == 'not_started'


# ------------------------------------------------------------- batch level
def synthetic_job(root, index, *, questions=3, latency=0.2, hang=False):
    run = root / f'{index:04d}-p{index}-chunk'
    code = (
        'import json, sys, time\n'
        'from pathlib import Path\n'
        f'run = Path({str(run)!r}); run.mkdir()\n'
        f'hang = {hang!r}\n'
        'args = sys.argv[1:]\n'
        'limit = float(args[args.index("--partition-deadline-seconds") + 1]) if "--partition-deadline-seconds" in args else None\n'
        'start = time.monotonic(); rows = []\n'
        f'for q in range({questions}):\n'
        '    if hang: time.sleep(3600)\n'
        f'    time.sleep({latency})\n'
        '    rows.append(json.dumps({"case_id": "c%d" % q, "status": "success"}))\n'
        '(run / "outputs.jsonl").write_text("\\n".join(rows) + "\\n")\n'
        '(run / "state.json").write_text(json.dumps({"phase": "finished"}))\n'
    )
    return dict(partition_id=f'p{index}', case_ids=[f'c{q}' for q in range(questions)], run_dir=str(run),
                mode='chunk', argv=[sys.executable, '-c', code])


def timed_batch(tmp_path, name, parallel, partitions=10):
    root = tmp_path / name
    jobs = [synthetic_job(root, i) for i in range(1, partitions + 1)]
    finished = []
    start = time.monotonic()
    report = execute_batch(jobs, output_root=root, parallel=parallel, append_run=lambda row: None,
                           after_run=lambda job, row: finished.append((time.monotonic() - start, job['partition_id'])))
    return time.monotonic() - start, report, finished


def test_ten_partitions_with_parallel_eight_take_under_a_quarter_of_serial(tmp_path):
    serial, report, _ = timed_batch(tmp_path, 'serial', 1)
    parallel, report8, finished = timed_batch(tmp_path, 'parallel', 8)
    assert report['exit_code'] == report8['exit_code'] == 0
    assert parallel < serial / 4, (serial, parallel)
    # after_run fires as each partition ends, long before the batch returns.
    assert len(finished) == 10 and finished[0][0] < parallel * 0.75


def test_hung_partition_is_bounded_and_others_finish(tmp_path, monkeypatch):
    from rag_eval import notebook_batch
    monkeypatch.setattr(notebook_batch, 'PARTITION_KILL_GRACE', 0.5)
    root = tmp_path / 'batch'
    jobs = [synthetic_job(root, 1, hang=True), synthetic_job(root, 2), synthetic_job(root, 3)]
    for job in jobs:
        job['partition_deadline_seconds'] = 1.5
    ended = {}
    start = time.monotonic()
    report = execute_batch(jobs, output_root=root, parallel=3, append_run=lambda row: None,
                           after_run=lambda job, row: ended.setdefault(job['partition_id'], row))
    assert time.monotonic() - start < 10
    assert ended['p1']['execution_status'] == 'killed' and ended['p1']['error_type'] == 'partition_deadline_kill'
    assert ended['p2']['exit_code'] == ended['p3']['exit_code'] == 0
    assert ended['p2']['partition_deadline_applied_seconds'] == 1.5
    assert report['exit_code'] == 1


def test_execution_deadline_leaves_late_partitions_not_started(tmp_path):
    root = tmp_path / 'late'
    jobs = [synthetic_job(root, i, latency=0.4) for i in range(1, 4)]
    report = execute_batch(jobs, output_root=root, parallel=1, append_run=lambda row: None,
                           deadline=time.monotonic() + 1.0)
    statuses = [row['execution_status'] for row in report['runs']]
    assert statuses.count('not_started') >= 1 and statuses[0] == 'exited'
    assert all(not Path(row['run_dir']).exists() for row in report['runs'] if row['execution_status'] == 'not_started')


# --------------------------------------------------------------- model gate
@pytest.fixture
def gate_dir(tmp_path):
    """Kernel semaphores outlive the test; remove every one recorded here."""
    from rag_eval.model_gate import remove_gates
    directory = tmp_path / 'gate'
    yield directory
    remove_gates(directory)


def test_model_gate_caps_requests_across_threads(gate_dir):
    import httpx
    from rag_eval.model_gate import install_model_gate
    active, peak, lock = [0], [0], threading.Lock()

    def handler(request):
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.1)
        with lock:
            active[0] -= 1
        return httpx.Response(200, json={'ok': True})
    with install_model_gate(gate_dir, 2) as gate:
        def call():
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                assert client.post('http://model.invalid/v1', json={}).json() == {'ok': True}
        threads = [threading.Thread(target=call) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert peak[0] == 2 and gate.requests == 6 and gate.waited_max > 0
    assert gate.status() == dict(slots=2, in_flight=0, waiting=0)
    assert httpx.Client.send.__name__ == 'send'  # restored


def _child(gate_dir, slots, body):
    import subprocess
    return subprocess.Popen([sys.executable, '-c', (
        'import os, sys, time; sys.path.insert(0, %r)\n'
        'from rag_eval.model_gate import ModelGate\n'
        'g = ModelGate(%r, %d)\n' + body) % (str(Path(__file__).resolve().parents[1] / 'src'), str(gate_dir), slots)],
        stdout=subprocess.PIPE, text=True)


def test_model_gate_slots_are_shared_between_processes(gate_dir):
    from rag_eval.model_gate import ModelGate
    gate = ModelGate(gate_dir, 1)
    token = gate.acquire()
    child = _child(gate_dir, 1, 't = time.monotonic(); g.acquire(); print(time.monotonic() - t)')
    time.sleep(0.5)
    assert gate.status() == dict(slots=1, in_flight=1, waiting=1)  # the child is queued, not polling
    gate.release(token)
    assert float(child.communicate(timeout=10)[0]) >= 0.4


def test_a_killed_process_gives_its_slots_back(gate_dir):
    import signal
    from rag_eval.model_gate import ModelGate, gate_status
    child = _child(gate_dir, 2, 'g.acquire(); g.acquire(); print("held", flush=True); time.sleep(60)')
    assert child.stdout.readline().strip() == 'held'
    gate = ModelGate(gate_dir, 2)
    assert gate.status()['in_flight'] == 2
    with pytest.raises(TimeoutError):
        gate.acquire(timeout=0.2)
    child.send_signal(signal.SIGKILL)
    child.wait(timeout=10)
    gate.release(gate.acquire(timeout=5))
    assert [dict(s, record=None) for s in gate_status(gate_dir)] == [
        dict(slots=2, in_flight=0, waiting=0, record=None, state='live')]


def test_waiters_are_served_in_arrival_order(gate_dir):
    from rag_eval.model_gate import ModelGate
    gate = ModelGate(gate_dir, 1)
    token, order = gate.acquire(), []

    def wait(index):
        gate.release(order.append(index) or gate.acquire())
    threads = []
    for index in range(6):
        threads.append(threading.Thread(target=wait, args=(index,)))
        threads[-1].start()
        while gate.status()['waiting'] < index + 1:  # queued before the next one arrives
            time.sleep(0.01)
    gate.release(token)
    [t.join(timeout=10) for t in threads]
    assert order == list(range(6))


def test_a_removed_gate_is_recreated_for_waiters_and_new_processes(gate_dir):
    from rag_eval.model_gate import ModelGate, remove_gates
    gate = ModelGate(gate_dir, 1)
    token, result = gate.acquire(), []
    waiter = threading.Thread(target=lambda: result.append(gate.acquire(timeout=5)))
    waiter.start()
    while gate.status()['waiting'] < 1:
        time.sleep(0.01)
    remove_gates(gate_dir)
    waiter.join(timeout=10)
    gate.release(token)  # the old semaphore is gone; giving back is a no-op
    assert result and ModelGate(gate_dir, 1).status() == dict(slots=1, in_flight=1, waiting=0)
    gate.release(result[0])
    # Different slot counts are separate gates.
    other = ModelGate(gate_dir, 3)
    assert other.status() == dict(slots=3, in_flight=0, waiting=0)
    assert sorted(p.name for p in gate_dir.glob('semaphore-*.json')) == ['semaphore-1.json', 'semaphore-3.json']


# ------------------------------------------------------------ retry merging
def test_flips_take_retry_values_only_for_declared_cases(tmp_path, monkeypatch):
    from rag_eval import flip_report
    from rag_eval.workbench.run_views import RunViews
    root = tmp_path / 'exec'
    first, retry = root / 'partitions/0001', root / 'retry-1/0001/r'
    for path in (first, retry):
        path.mkdir(parents=True)
    for name in ('0001', '0001-r1'):
        scores = root / 'official' / name / 'scores'
        scores.mkdir(parents=True)
        (scores / 'scores.json').write_text(json.dumps({'suite': 'qasper', 'metrics': {'answer_f1': 0.5}}))
    (root / 'batch-plan.json').write_text(json.dumps([dict(
        partition_id='p', case_ids=['a', 'b'], run_dir=str(first), suite='qasper', mode='chunk', official_name='0001',
        retries=[dict(round=1, run_dir=str(retry), case_ids=['b'], official_name='0001-r1', complete=True,
                      selected_case_ids=['b'])])]))
    values = {str(first): {'a': 1.0, 'b': None}, str(retry): {'b': 0.75}}

    def fake(before, after, *, before_scores=None, after_scores=None, bundle=None, **kwargs):
        left, right = values[str(before)], values[str(after)]
        cases = [dict(case_id=c, metrics={'answer_f1': dict(before=left.get(c), after=right.get(c),
                                                             difference=None, direction='unavailable')})
                 for c in sorted(set(left) | set(right))]
        return dict(admission=dict(status='admitted', reason=None), before={}, cases=cases)
    monkeypatch.setattr(flip_report, 'compare_runs_flips', fake)
    record = dict(execution_id='exec', task_id='task', run_dir=str(root), state='finished', terminal=True)
    views = RunViews([], execution_records=lambda: [record])
    [partition] = views.execution_runs('exec')  # the retry is not a partition of its own
    assert partition.retries == (('exec-1-r1', ('b',)),)
    flips = views.flips('exec-1', 'exec-1', bundle=object())
    rows = {r['case_id']: r for r in flips['rows']}
    assert rows['a']['left'] == 1.0 and rows['b']['left'] == rows['b']['right'] == 0.75
    assert flips['admission'] == 'admitted' and views.admission('exec-1')['status'] == 'admitted'
    values[str(retry)] = {}

    def refused(before, after, **kwargs):
        report = fake(before, after, **kwargs)
        if str(before) == str(retry):
            report['admission'] = dict(status='not_admitted', reason='synthetic refusal')
        return report
    monkeypatch.setattr(flip_report, 'compare_runs_flips', refused)
    assert views.flips('exec-1', 'exec-1', bundle=object())['admission'] == 'not_admitted'
    assert views.admission('exec-1')['status'] == 'not_admitted'


def test_old_batch_plans_without_retries_are_still_discovered(tmp_path):
    from rag_eval.workbench.run_views import RunViews
    root = tmp_path / 'old'
    run = root / 'partitions/0001'
    run.mkdir(parents=True)
    (root / 'batch-plan.json').write_text(json.dumps([dict(partition_id='p', case_ids=['a'], run_dir=str(run),
                                                           suite='qasper', mode='chunk', official_name='0001')]))
    (root / 'retry-rounds.json').write_text(json.dumps([dict(round=1, partitions=1, still_failed=0)]))
    record = dict(execution_id='old', task_id='task', run_dir=str(root), state='finished', terminal=True)
    [entry] = RunViews([], execution_records=lambda: [record]).execution_runs('old')
    assert entry.retries == () and entry.case_ids == ('a',)
