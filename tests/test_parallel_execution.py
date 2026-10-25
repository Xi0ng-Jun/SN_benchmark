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
def test_model_gate_caps_requests_across_threads(tmp_path):
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
    with install_model_gate(tmp_path / 'gate', 2) as gate:
        def call():
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                assert client.post('http://model.invalid/v1', json={}).json() == {'ok': True}
        threads = [threading.Thread(target=call) for _ in range(6)]
        [t.start() for t in threads]
        [t.join() for t in threads]
    assert peak[0] == 2 and gate.requests == 6
    assert httpx.Client.send.__name__ == 'send'  # restored


def test_model_gate_slots_are_shared_between_processes(tmp_path):
    import fcntl
    import os
    import subprocess
    from rag_eval.model_gate import ModelGate
    gate = ModelGate(tmp_path / 'gate', 1, poll=0.01)
    fd = gate.acquire()
    child = subprocess.Popen([sys.executable, '-c', (
        'import sys, time; sys.path.insert(0, %r)\n'
        'from rag_eval.model_gate import ModelGate\n'
        'g = ModelGate(%r, 1, poll=0.01); t = time.monotonic(); fd = g.acquire(); print(time.monotonic() - t)'
    ) % (str(Path(__file__).resolve().parents[1] / 'src'), str(tmp_path / 'gate'))], stdout=subprocess.PIPE, text=True)
    time.sleep(0.5)
    gate.release(fd)
    waited = float(child.communicate(timeout=10)[0])
    assert waited >= 0.4


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
