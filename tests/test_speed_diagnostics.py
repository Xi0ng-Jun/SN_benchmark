"""Partition timing diagnostics: phases, gate waits, request latencies and their execution summary."""
import json
from pathlib import Path
import sys
import threading
import time

import pytest

from rag_eval.speed_diagnostics import (FILENAME, SpeedObserver, distribution, endpoint, load, observing, phase,
                                        summarize)


def test_endpoint_keeps_only_name_like_path_tail():
    assert endpoint('/compatible-mode/v1/chat/completions') == 'chat/completions'
    assert endpoint('/v1/embeddings?key=secret') == 'v1/embeddings'
    assert endpoint('/files/123e4567') == 'files/*'
    assert endpoint('') == '/'


def test_distribution_uses_observed_values():
    stats = distribution([5.0, 1.0, 3.0, 2.0, 4.0])
    assert stats == dict(count=5, sum=15.0, p50=3.0, p90=5.0, p99=5.0, max=5.0)
    assert distribution([]) == dict(count=0)


def test_requests_are_timed_per_phase_with_gate_waits(tmp_path, request):
    import httpx
    from rag_eval.model_gate import install_model_gate, remove_gates

    def handler(request):
        time.sleep(0.05)
        if request.url.path.endswith('fail'):
            return httpx.Response(429, json={})
        return httpx.Response(200, json={'ok': True})
    observer = SpeedObserver()
    request.addfinalizer(lambda: remove_gates(tmp_path / 'gate'))
    with install_model_gate(tmp_path / 'gate', 1, observer=observer), observing(observer):
        phase('preparation')

        def call(path):
            with httpx.Client(transport=httpx.MockTransport(handler)) as client:
                client.post('http://model.invalid' + path, json={})
        threads = [threading.Thread(target=call, args=('/v1/embeddings',)) for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]
        phase('answer')
        call('/v1/chat/fail')
        with httpx.Client(transport=httpx.MockTransport(handler)) as client:
            with client.stream('POST', 'http://model.invalid/v1/chat/completions', json={}) as response:
                response.read()
    phase('ignored')  # no observer installed any more
    saved = observer.snapshot()
    assert [p['name'] for p in saved['phases']] == ['setup', 'preparation', 'answer']
    assert saved['gate_slots'] == 1
    rows = [dict(zip(saved['requests']['columns'], row)) for row in saved['requests']['rows']]
    assert [(r['phase'], r['endpoint'], r['outcome']) for r in rows][-2:] == [
        ('answer', 'chat/fail', 429), ('answer', 'chat/completions', 200)]
    embeddings = [r for r in rows if r['endpoint'] == 'v1/embeddings']
    assert len(embeddings) == 3 and all(r['end'] - r['acquired'] >= 0.04 for r in rows)
    # One slot: the second and third embedding waited for the earlier ones.
    assert sorted(r['acquired'] - r['start'] for r in embeddings)[-1] >= 0.08
    # Recorded holding intervals never overlap beyond the single slot.
    assert summarize({'p': saved})['concurrency']['peak_in_flight'] == 1


def test_failed_requests_release_and_record_the_exception(tmp_path, request):
    import httpx
    from rag_eval.model_gate import install_model_gate, remove_gates
    request.addfinalizer(lambda: remove_gates(tmp_path / 'gate'))

    def handler(request):
        raise httpx.ConnectError('down')
    observer = SpeedObserver()
    with install_model_gate(tmp_path / 'gate', 1, observer=observer):
        for _ in range(2):  # the slot is released after the failure
            with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(httpx.ConnectError):
                client.post('http://model.invalid/v1/chat/completions', json={})
    assert [row[-1] for row in observer.snapshot()['requests']['rows']] == ['ConnectError', 'ConnectError']


def snapshot(epoch, phases, rows, slots=2):
    """rows: [phase, endpoint, start, gate wait, service, outcome], stored as instants."""
    rows = [[p, e, start, start + wait, start + wait + service, o] for p, e, start, wait, service, o in rows]
    return dict(format='sn-speed-diagnostics-v1', started_epoch=epoch, wall_seconds=phases[-1][1] + phases[-1][2],
                phases=[dict(name=n, start=s, seconds=d) for n, s, d in phases], gate_slots=slots,
                requests=dict(columns=['phase', 'endpoint', 'start', 'acquired', 'end', 'outcome'], rows=rows))


def test_summary_merges_partitions_and_rebuilds_gate_occupancy():
    a = snapshot(1000.0, [('setup', 0, 1), ('preparation', 1, 10), ('answer', 11, 5)],
                 [['preparation', 'chat/completions', 1, 0, 4, 200], ['answer', 'chat/completions', 11, 0, 5, 200]])
    # Starts 2 s later; its second request queues 2 s until a's first ends.
    b = snapshot(1002.0, [('setup', 0, 1), ('preparation', 1, 3), ('answer', 4, 20)],
                 [['preparation', 'chat/completions', 0, 0, 4, 200], ['preparation', 'chat/completions', 1, 2, 2, 429],
                  ['answer', 'chat/completions', 4, 0, 20, 200]])
    summary = summarize({'0001': a, '0002': b, '0003': None})
    assert summary['partitions'] == 2
    assert summary['phases']['answer'] == dict(max=20, sum=25, slowest_partition='0002')
    assert summary['phases']['preparation'] == dict(max=10, sum=13, slowest_partition='0001')
    assert summary['slowest_partitions'][0]['name'] == '0002'
    assert summary['gate'] == dict(slots=2, acquisitions=5, waited_seconds_sum=2, waited_seconds_max=2,
                                   waited_share=round(2 / 37, 4))
    prep = summary['requests']['preparation']['chat/completions']
    assert prep['count'] == 3 and prep['outcomes'] == {'200': 2, '429': 1}
    assert prep['gate_wait_seconds']['max'] == 2
    # Holding: a [1001,1005) [1011,1016); b [1002,1006) [1005,1007) [1006,1026). A slot freed and
    # taken at the same instant is never counted twice; both slots are full on [1002,1007) and [1011,1016).
    assert summary['concurrency'] == dict(peak_in_flight=2, peak_waiting=1, gate_full_seconds=10.0,
                                          seconds_with_waiters=2.0)


def concurrency(rows, slots):
    return summarize({'p': snapshot(0.0, [('answer', 0, 10)], [['answer', 'chat', *r, 200] for r in rows],
                                    slots=slots)})['concurrency']


def test_requests_that_never_waited_are_never_counted_as_queued():
    # No gate: 20 requests at the same instant, none waits.
    assert concurrency([(0.0, 0.0, 1.0)] * 20, None) == dict(
        peak_in_flight=20, peak_waiting=0, gate_full_seconds=None, seconds_with_waiters=0.0)


def test_a_zero_length_service_never_makes_in_flight_negative():
    # A request served in no time beside one holding the only slot for 2 s.
    result = concurrency([(0.0, 0.0, 0.0), (0.0, 0.0, 2.0)], 1)
    assert result == dict(peak_in_flight=1, peak_waiting=0, gate_full_seconds=2.0, seconds_with_waiters=0.0)


def test_a_slot_handed_over_within_rounding_is_not_counted_twice():
    # B takes the single slot as A gives it back; their instants differ below 1 ms.
    result = concurrency([(0.0, 0.0, 1.0000004), (0.5, 0.5000001, 0.9999999)], 1)
    assert result['peak_in_flight'] == 1 and result['peak_waiting'] == 1
    assert result['gate_full_seconds'] == 2.0 and result['seconds_with_waiters'] == 0.5


def test_execution_summary_merges_partitions_and_retry_runs(tmp_path, monkeypatch):
    from test_workbench_seeded_execution import fake_scoring, finish, spec_for
    from rag_eval.workbench.notebook_job import SUMMARY, execute
    _, payload, prep = spec_for(tmp_path, retry_rounds=1)
    case_ids = prep['input']['selection']['case_ids']

    def plan(**kwargs):
        ids = Path(kwargs['case_id_file']).read_text().split()
        root = kwargs['output_root']
        return [dict(partition_id=payload['selection']['partition_ids'][case_ids.index(cid)], case_ids=[cid],
                     run_dir=str(root / cid), suite='alce', mode='chunk') for cid in ids]

    def batch(jobs, *, output_root, parallel, after_run=None, **kwargs):
        rows = []
        for job in jobs:
            retried = 'retry-1' in str(output_root)
            finish(Path(job['run_dir']), 'error' if job['case_ids'][0] == case_ids[0] and not retried else 'success')
            Path(job['run_dir'], FILENAME).write_text(json.dumps(snapshot(
                0.0, [('setup', 0, 1), ('preparation', 1, 0 if retried else 30), ('answer', 31, 7)],
                [['answer', 'chat/completions', 31, 0, 7, 200]])))
            rows.append(dict(run_dir=job['run_dir'], exit_code=0))
            if after_run is not None:
                after_run(job, rows[-1])
        return dict(exit_code=0, runs=rows)
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', plan)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    fake_scoring(monkeypatch, lambda run, cid: 1.0)
    assert execute(payload) == 0
    speed = json.loads((Path(payload['run_dir']) / SUMMARY).read_text())['speed']
    assert speed['partitions'] == len(case_ids) + 1  # every partition plus the retry run of the first
    assert speed['phases']['preparation']['max'] == 30
    assert speed['requests']['answer']['chat/completions']['count'] == len(case_ids) + 1
    assert speed['execution_wall_seconds'] >= 0


def test_partition_cli_saves_speed_diagnostics_beside_the_run(tmp_path, monkeypatch, request):
    import httpx
    from rag_eval import notebook_runner, run_report
    from rag_eval.model_gate import remove_gates
    request.addfinalizer(lambda: remove_gates(tmp_path / 'gate'))
    from test_notebook_cli_launch import _script, captured_argv
    _, argv = captured_argv(tmp_path)

    def execute(*, run, **kwargs):
        run.mkdir()
        (run / 'state.json').write_text('{"phase": "finished"}')
        (run / 'preparation-cache.json').write_text('{"lock_waited_seconds": 2.5}')
        phase('preparation')
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={}))) as client:
            client.post('http://model.invalid/v1/embeddings', json={})
        phase('answer')
    monkeypatch.setattr(notebook_runner, 'execute', execute)
    monkeypatch.setattr(run_report, 'write_report', lambda *a, **k: {'runs': []})
    monkeypatch.setattr(sys, 'argv', argv + ['--model-gate', str(tmp_path / 'gate'), '--model-gate-slots', '3'])
    assert _script().main() == 0
    saved = load(tmp_path / 'run')
    assert [p['name'] for p in saved['phases']] == ['setup', 'preparation', 'answer', 'reporting']
    assert saved['gate_slots'] == 3 and saved['preparation_cache_lock_waited_seconds'] == 2.5
    assert saved['requests']['rows'][0][:2] == ['preparation', 'v1/embeddings']
    assert httpx.Client.send.__name__ == 'send'  # transports restored


def test_speed_report_merges_executions(tmp_path, capsys, monkeypatch):
    import importlib.util
    spec = importlib.util.spec_from_file_location('speed_report', Path(__file__).resolve().parents[1] / 'scripts/speed_report.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for relative in ('e1/partitions/r1', 'e1/retry-1/0001/r1', 'e2/partitions/r2'):
        (tmp_path / relative).mkdir(parents=True)
        (tmp_path / relative / FILENAME).write_text(json.dumps(snapshot(
            0.0, [('setup', 0, 1), ('answer', 1, 3)], [['answer', 'chat/completions', 1, 0, 3, 200]])))
    monkeypatch.setattr(sys, 'argv', ['speed_report.py', str(tmp_path / 'e1'), str(tmp_path / 'e2')])
    assert module.main() == 0
    merged = json.loads(capsys.readouterr().out)
    assert merged['partitions'] == 3 and merged['concurrency']['peak_in_flight'] == 3


def test_a_speed_summary_failure_never_fails_a_scored_execution(tmp_path, monkeypatch):
    from test_workbench_seeded_execution import fake_scoring, finish, spec_for
    from rag_eval.workbench.notebook_job import SUMMARY, execute
    _, payload, prep = spec_for(tmp_path)
    case_ids = prep['input']['selection']['case_ids']

    def plan(**kwargs):
        ids = Path(kwargs['case_id_file']).read_text().split()
        return [dict(partition_id=payload['selection']['partition_ids'][case_ids.index(cid)], case_ids=[cid],
                     run_dir=str(kwargs['output_root'] / cid), suite='alce', mode='chunk') for cid in ids]

    def batch(jobs, *, output_root, parallel, after_run=None, **kwargs):
        rows = []
        for job in jobs:
            finish(Path(job['run_dir']))
            rows.append(dict(run_dir=job['run_dir'], exit_code=0))
            if after_run is not None:
                after_run(job, rows[-1])
        return dict(exit_code=0, runs=rows)

    def broken(observations):
        raise ValueError('malformed speed file')
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', plan)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    monkeypatch.setattr('rag_eval.speed_diagnostics.summarize', broken)
    fake_scoring(monkeypatch, lambda run, cid: 1.0)
    assert execute(payload) == 0
    summary = json.loads((Path(payload['run_dir']) / SUMMARY).read_text())
    assert summary['scored'] == summary['planned'] and summary['speed']['error_type'] == 'ValueError'
