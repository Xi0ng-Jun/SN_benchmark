"""Seeded generation, answer retries and adoption of finished runs in one execution."""
import json
from pathlib import Path

import pytest

from rag_eval.workbench.jobs_api import build_execution_spec
from rag_eval.workbench.job_execution import ExecutionRejected
from rag_eval.workbench.notebook_job import SUMMARY, execute
from test_workbench_notebook_job import prepared, settings


def spec_for(tmp_path, **extra):
    jobs, job, prep, args = prepared(tmp_path)
    config = dict(settings(tmp_path, args), **extra)
    spec = build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'], config,
                                request_key='seeded', budget={'timeout_seconds': 600})
    return spec, json.loads(spec['argv'][-1]), prep


def fake_scoring(monkeypatch, values):
    def export(bundle, runs, output, *, case_ids, context=None):
        output.mkdir(parents=True)
        path = output / 'submission.json'
        path.write_text(json.dumps({'case_ids': case_ids, 'run': str(runs[0])}))
        return path

    def score(bundle, submission, prepared, *, source_directory, output_dir):
        output_dir.mkdir(parents=True)
        rows = [dict(case_id=cid, metrics={'str_em': values(submission['run'], cid)}) for cid in submission['case_ids']]
        result = dict(suite='alce', per_case=rows)
        (output_dir / 'scores.json').write_text(json.dumps(result))
        return result
    monkeypatch.setattr('rag_eval.benchmark_submission.export_sn_runs', export)
    monkeypatch.setattr('rag_eval.benchmark_official.score_prepared', score)
    monkeypatch.setattr('rag_eval.benchmark_official.prepare_inputs', lambda bundle, submission: {})
    monkeypatch.setattr('rag_eval.notebook_bundle.load_bundle', lambda path: {'cases': []})


def finish(run, status='success', prepared_notebook=True, statuses=None):
    run.mkdir(parents=True)
    (run / 'product-artifacts').mkdir()
    (run / 'product-artifacts/state.json').write_text(json.dumps({'prepared': prepared_notebook}))
    statuses = statuses or {run.name: status}
    (run / 'outputs.jsonl').write_text(''.join(json.dumps({'case_id': cid, 'status': value}) + '\n'
                                               for cid, value in statuses.items()))
    status = 'error' if 'error' in statuses.values() else 'success'
    (run / 'state.json').write_text(json.dumps({'phase': 'finished' if status == 'success' else 'finished_with_errors'}))
    (run / 'manifest.json').write_text('{}')


def test_run_maps_stay_out_of_argv_and_are_hash_checked(tmp_path):
    _, _, prep = spec_for(tmp_path / 'probe')
    _, payload, _ = spec_for(tmp_path, parallel=8, retry_rounds=2,
                             seed_runs={cid: '/runs/' + cid for cid in prep['input']['selection']['case_ids']})
    assert payload['parallel'] == 8 and payload['retry_rounds'] == 2
    assert 'seed_runs' not in payload and payload['run_maps']['keys'] == ['seed_runs']
    saved = Path(payload['run_maps']['path'])
    assert json.loads(saved.read_text())['seed_runs']
    saved.write_text('{"seed_runs": {}}')
    with pytest.raises(ValueError, match='Run map changed'):
        execute(payload)
    with pytest.raises(ExecutionRejected, match='every planned case'):
        spec_for(tmp_path / 'other', seed_runs={'unknown': '/runs/x'})
    wider = dict({cid: '/runs/' + cid for cid in prep['input']['selection']['case_ids']}, extra='/runs/extra')
    _, narrowed, _ = spec_for(tmp_path / 'wider', seed_runs=wider)
    assert 'extra' not in json.loads(Path(narrowed['run_maps']['path']).read_text())['seed_runs']
    with pytest.raises(ExecutionRejected, match='parallel'):
        spec_for(tmp_path / 'third', parallel=0)


def test_only_failed_cases_are_asked_again_and_successes_are_kept(tmp_path, monkeypatch):
    _, payload, prep = spec_for(tmp_path, retry_rounds=3, parallel=4)
    case_ids = prep['input']['selection']['case_ids']
    calls = []

    def plan(**kwargs):
        ids = Path(kwargs['case_id_file']).read_text().split()
        seeds = json.loads(Path(kwargs['seed_prepared_map']).read_text()) if kwargs.get('seed_prepared_map') else None
        root = kwargs['output_root']
        calls.append(dict(output=root.name if root.name == 'partitions' else root.parent.name, ids=ids, seeds=seeds,
                          policy=kwargs['preparation_policy'], change=kwargs['seed_allow_source_change']))
        if root.name == 'partitions':
            return [dict(partition_id=pid, case_ids=[cid], run_dir=str(root / cid), suite='alce', mode='chunk')
                    for pid, cid in zip(payload['selection']['partition_ids'], ids)]
        return [dict(partition_id=payload['selection']['partition_ids'][case_ids.index(cid)], case_ids=[cid],
                     run_dir=str(root / cid), suite='alce', mode='chunk') for cid in ids]

    def batch(jobs, *, output_root, parallel, after_run=None, **kwargs):
        rows = []
        for job in jobs:
            # The first case fails twice, then succeeds on the third ask.
            fail = job['case_ids'][0] == case_ids[0] and 'retry-2' not in str(output_root)
            finish(Path(job['run_dir']), 'error' if fail else 'success')
            rows.append(dict(run_dir=job['run_dir'], exit_code=0))
            if after_run is not None:
                after_run(job, rows[-1])
        return dict(exit_code=0, runs=rows)
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', plan)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    fake_scoring(monkeypatch, lambda run, cid: 1.0 if 'retry-2' in run else 0.5)

    assert execute(payload) == 0
    run = Path(payload['run_dir'])
    assert [c['output'] for c in calls] == ['partitions', 'retry-1', 'retry-2']
    assert calls[1]['ids'] == [case_ids[0]] and calls[1]['seeds'] == {case_ids[0]: str(run / 'partitions' / case_ids[0])}
    assert calls[2]['ids'] == [case_ids[0]] and calls[1]['policy'] == 'from-seed'
    plan_saved = json.loads((run / 'batch-plan.json').read_text())
    # The original run stays the partition's run; retries are listed beside it.
    assert plan_saved[0]['run_dir'] == str(run / 'partitions' / case_ids[0])
    assert [r['selected_case_ids'] for r in plan_saved[0]['retries']] == [[], [case_ids[0]]]
    assert all(not job['retries'] for job in plan_saved[1:])
    assert [j['official_name'] for j in plan_saved] == ['%04d' % i for i in range(1, len(plan_saved) + 1)]
    rounds = json.loads((run / 'retry-rounds.json').read_text())
    assert rounds['rounds'][-1] == dict(round=2, partitions=1, still_failed=0)
    assert rounds['partitions']['0001']['selection'] == {case_ids[0]: str(run / 'retry-2/0001' / case_ids[0])}
    # Both answers stay on disk.
    assert (run / 'partitions' / case_ids[0] / 'outputs.jsonl').read_text().count('error') == 1
    summary = json.loads((run / SUMMARY).read_text())
    assert summary['metric'] == 'str_em' and summary['scored'] == summary['planned'] == len(case_ids)
    assert summary['mean'] == pytest.approx((1.0 + 0.5 * (len(case_ids) - 1)) / len(case_ids))
    assert json.loads((run / 'state.json').read_text())['phase'] == 'finished'


def test_case_errors_without_retries_still_finish_and_are_scored(tmp_path, monkeypatch):
    _, payload, prep = spec_for(tmp_path, parallel=2)
    case_ids = prep['input']['selection']['case_ids']
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', lambda **kw: [
        dict(partition_id=pid, case_ids=[cid], run_dir=str(kw['output_root'] / cid), suite='alce', mode='chunk')
        for cid, pid in zip(case_ids, payload['selection']['partition_ids'])])

    def batch(jobs, *, output_root, parallel, after_run=None, **kwargs):
        rows = []
        for job in jobs:
            finish(Path(job['run_dir']), 'error' if job['case_ids'][0] == case_ids[0] else 'success')
            # A partition with case errors exits non-zero but reached its terminal phase.
            rows.append(dict(run_dir=job['run_dir'], exit_code=2 if job['case_ids'][0] == case_ids[0] else 0))
            after_run(job, rows[-1])
        return dict(exit_code=1, runs=rows)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    fake_scoring(monkeypatch, lambda run, cid: 0.5)
    assert execute(payload) == 0
    run = Path(payload['run_dir'])
    state = json.loads((run / 'state.json').read_text())
    assert state['phase'] == 'finished' and state['generation_failures'] == []
    assert json.loads((run / SUMMARY).read_text())['partitions_with_case_errors'] == 1


def adoption(tmp_path, monkeypatch, name):
    from rag_eval.artifact_store import ArtifactStore
    _, _, prep = spec_for(tmp_path / (name + '-probe'))
    case_ids = prep['input']['selection']['case_ids']
    finished = tmp_path / (name + '-finished')
    adopted = {cid: str(finished / cid) for cid in case_ids}
    _, payload, _ = spec_for(tmp_path / name, adopt_runs=adopted)
    source = json.loads((ArtifactStore(payload['artifact_root']).resolve(payload['sn_ref']) / 'object.json')
                        .read_text())['identity']
    for cid, pid in zip(case_ids, payload['selection']['partition_ids']):
        finish(finished / cid)
        (finished / cid / 'manifest.json').write_text(json.dumps(dict(mode='chunk', identity=dict(
            notebook_context=dict(partition_id=pid, request_revision='notebook-request-v3'),
            code=dict(head_revision=source.get('head_revision'), head_tree=source.get('head_tree'),
                      source_manifest_sha256='older-capture-format')))))
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', lambda **kw: [
        dict(partition_id=pid, case_ids=[cid], run_dir=str(kw['output_root'] / cid), suite='alce', mode='chunk')
        for cid, pid in zip(case_ids, payload['selection']['partition_ids'])])
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch',
                        lambda *a, **k: pytest.fail('adoption must not generate'))
    fake_scoring(monkeypatch, lambda run, cid: 0.25)
    return payload, adopted, finished, case_ids


def test_adoption_checks_identity_and_registers_only_listed_runs(tmp_path, monkeypatch):
    payload, adopted, finished, case_ids = adoption(tmp_path, monkeypatch, 'ok')
    assert execute(payload) == 0
    run = Path(payload['run_dir'])
    assert json.loads((run / 'adopted-runs.json').read_text()) == list(adopted.values())
    assert all(j['adopted'] for j in json.loads((run / 'batch-plan.json').read_text()))
    assert json.loads((run / SUMMARY).read_text())['adopted'] is True

    from rag_eval.workbench.run_views import RunViews
    record = dict(execution_id='exec', task_id='task', run_dir=str(run), state='finished', terminal=True)
    views = RunViews([], execution_records=lambda: [record])
    assert [Path(e.run_dir) for e in views.execution_runs('exec')] == [finished / cid for cid in case_ids]
    (run / 'adopted-runs.json').write_text('[]')
    assert RunViews([], execution_records=lambda: [record]).execution_runs('exec') == []


def test_adoption_refuses_a_run_from_different_source(tmp_path, monkeypatch):
    payload, adopted, finished, case_ids = adoption(tmp_path, monkeypatch, 'other')
    manifest = json.loads((finished / case_ids[0] / 'manifest.json').read_text())
    manifest['identity']['code']['head_revision'] = 'f' * 40
    (finished / case_ids[0] / 'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='SN source'):
        execute(payload)
    assert not (Path(payload['run_dir']) / 'adopted-runs.json').exists()


def test_whole_benchmark_execution_is_summarized_for_agents(tmp_path, monkeypatch):
    from rag_eval.workbench import run_views as module
    from rag_eval.workbench.agent_tools import task_run_ids
    payload, adopted, finished, case_ids = adoption(tmp_path, monkeypatch, 'many')
    assert execute(payload) == 0
    run = Path(payload['run_dir'])
    record = dict(execution_id='exec', task_id='task', run_dir=str(run), state='finished', terminal=True)
    views = module.RunViews([], execution_records=lambda: [record])
    monkeypatch.setattr(module, 'COLLAPSE_PARTITIONS', 0)
    monkeypatch.setattr(views, 'task_overview', lambda task_id: dict(status='unbound', runs=[]))
    monkeypatch.setattr(views, 'run', lambda run_id: pytest.fail('summarized executions must not build run views'))
    context = json.loads(views.task_runs_context('task')[1])
    [entry] = context['runs']
    assert entry['kind'] == 'execution' and entry['partitions'] == len(case_ids)
    assert entry['summary']['mean'] == pytest.approx(0.25)
    assert task_run_ids(views, 'task') == [f'exec-{i}' for i in range(1, len(case_ids) + 1)]
    cases = views.execution_cases('exec')
    assert cases['metric'] == 'str_em' and cases['cases'] == len(case_ids)
    assert {r['run_id'] for r in cases['rows']} == set(task_run_ids(views, 'task'))


def test_a_partition_is_scored_while_later_partitions_still_generate(tmp_path, monkeypatch):
    import threading
    _, payload, prep = spec_for(tmp_path, parallel=2)
    # Two synthetic partitions; plan and batch are replaced below.
    case_ids = ['alce:a', 'alce:b']
    payload = dict(payload, selection=dict(payload['selection'], case_ids=case_ids, partition_ids=['pa', 'pb']))
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', lambda **kw: [
        dict(partition_id=pid, case_ids=[cid], run_dir=str(kw['output_root'] / cid), suite='alce', mode='chunk')
        for cid, pid in zip(case_ids, payload['selection']['partition_ids'])])
    scored, events = threading.Event(), []
    fake_scoring(monkeypatch, lambda run, cid: (events.append(('scored', cid)), scored.set(), 0.5)[-1])

    def batch(jobs, *, output_root, parallel, after_run=None, **kwargs):
        rows = []
        for job in jobs:
            finish(Path(job['run_dir']))
            events.append(('generated', job['case_ids'][0]))
            rows.append(dict(run_dir=job['run_dir'], exit_code=0))
            after_run(job, rows[-1])
            if job is jobs[0]:
                assert scored.wait(10), 'first partition was not scored before the next one finished'
        return dict(exit_code=0, runs=rows)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    assert execute(payload) == 0
    assert events.index(('scored', case_ids[0])) < events.index(('generated', case_ids[1]))
