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


def finish(run, status='success', prepared_notebook=True):
    run.mkdir(parents=True)
    (run / 'product-artifacts').mkdir()
    (run / 'product-artifacts/state.json').write_text(json.dumps({'prepared': prepared_notebook}))
    (run / 'outputs.jsonl').write_text(json.dumps({'status': status}) + '\n')
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


def test_failed_answers_are_asked_again_on_their_own_notebook(tmp_path, monkeypatch):
    _, payload, prep = spec_for(tmp_path, retry_rounds=3, parallel=4)
    case_ids = prep['input']['selection']['case_ids']
    calls = []

    def plan(**kwargs):
        ids = Path(kwargs['case_id_file']).read_text().split()
        seeds = json.loads(Path(kwargs['seed_prepared_map']).read_text()) if kwargs.get('seed_prepared_map') else None
        calls.append(dict(output=kwargs['output_root'].name, ids=ids, seeds=seeds,
                          policy=kwargs['preparation_policy'], change=kwargs['seed_allow_source_change']))
        return [dict(partition_id=pid, case_ids=[cid], run_dir=str(kwargs['output_root'] / cid), suite='alce',
                     mode='chunk') for pid, cid in zip(payload['selection']['partition_ids'], ids)] \
            if kwargs['output_root'].name == 'partitions' else \
            [dict(partition_id=payload['selection']['partition_ids'][case_ids.index(cid)], case_ids=[cid],
                  run_dir=str(kwargs['output_root'] / cid), suite='alce', mode='chunk') for cid in ids]

    def batch(jobs, *, output_root, parallel, ledger_path):
        assert parallel == 4
        for job in jobs:
            # The first case fails twice, then succeeds on the third ask.
            first = job['case_ids'][0] == case_ids[0]
            fail = first and output_root.name in ('partitions', 'retry-1')
            finish(Path(job['run_dir']), 'error' if fail else 'success')
        return dict(exit_code=0, runs=[dict(run_dir=j['run_dir'], exit_code=0) for j in jobs])
    monkeypatch.setattr('rag_eval.notebook_batch.plan_batch', plan)
    monkeypatch.setattr('rag_eval.notebook_batch.execute_batch', batch)
    fake_scoring(monkeypatch, lambda run, cid: 1.0 if 'retry-2' in run else 0.5)

    assert execute(payload) == 0
    run = Path(payload['run_dir'])
    assert [c['output'] for c in calls] == ['partitions', 'retry-1', 'retry-2']
    assert calls[1]['ids'] == [case_ids[0]] and calls[1]['seeds'] == {case_ids[0]: str(run / 'partitions' / case_ids[0])}
    assert calls[1]['policy'] == 'from-seed'
    plan_saved = json.loads((run / 'batch-plan.json').read_text())
    assert plan_saved[0]['run_dir'] == str(run / 'retry-2' / case_ids[0])
    assert [j['official_name'] for j in plan_saved] == ['%04d' % i for i in range(1, len(plan_saved) + 1)]
    assert json.loads((run / 'retry-rounds.json').read_text())[-1] == dict(round=2, partitions=1, still_failed=0)
    summary = json.loads((run / SUMMARY).read_text())
    assert summary['metric'] == 'str_em' and summary['scored'] == summary['planned'] == len(case_ids)
    assert summary['mean'] == pytest.approx((1.0 + 0.5 * (len(case_ids) - 1)) / len(case_ids))
    assert json.loads((run / 'state.json').read_text())['phase'] == 'finished'


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
