"""Batch orchestration must retain the entire requested scope on failure."""
import copy
import json
from pathlib import Path
import sys

import pytest

from test_notebook_data_v3 import freeze, qasper_raw


def installed(tmp_path):
    from rag_eval.bundle_index import install_bundle
    raw = qasper_raw()
    raw['other'] = copy.deepcopy(raw['p'])
    raw['other']['qas'][0]['question_id'] = 'other'
    raw['p']['qas'].append(copy.deepcopy(raw['p']['qas'][0]))
    raw['p']['qas'][-1]['question_id'] = 'second'
    bundle = freeze(tmp_path / 'input', 'qasper', raw)
    path = tmp_path / 'input/bundle'
    refs = install_bundle(path, tmp_path / 'store')
    ids = tmp_path / 'ids.txt'
    ids.write_text('qasper:other\nqasper:second\nqasper:q\n')
    return bundle, path, refs, ids


def plan(tmp_path, **kwargs):
    from rag_eval.notebook_batch import plan_batch
    bundle, path, refs, ids = installed(tmp_path)
    return plan_batch(bundle=path, artifact_root=tmp_path / 'store',
                      artifact_index_id=refs['index']['id'], case_id_file=ids,
                      mode='chunk', output_root=tmp_path / 'batch',
                      project_root=tmp_path / 'synthetic-project', **kwargs), bundle


def test_plan_groups_selected_cases_and_forwards_only_ids(tmp_path):
    jobs, bundle = plan(tmp_path, python=sys.executable,
                        model_config=tmp_path / 'experiment.toml')
    assert [j['case_ids'] for j in jobs] == [['qasper:q', 'qasper:second'], ['qasper:other']]
    assert [j['partition_id'] for j in jobs] == [p['partition_id'] for p in bundle['partitions']]
    assert len({j['run_dir'] for j in jobs}) == 2
    for job in jobs:
        assert not Path(job['run_dir']).exists()
        assert job['argv'][0] == sys.executable
        assert '--artifact-root' in job['argv'] and '--artifact-index-id' in job['argv']
        assert job['argv'][job['argv'].index('--request-revision') + 1] == 'notebook-request-v3'
        assert 'SECRET-GOLD' not in json.dumps(job)


@pytest.mark.parametrize('text', ['qasper:q\nunknown\n', 'qasper:q\nqasper:q\n', '\n', 'qasper:q \n'])
def test_invalid_selection_rejects_before_any_output_or_process(tmp_path, text):
    from rag_eval.notebook_batch import plan_batch
    _, path, refs, ids = installed(tmp_path)
    ids.write_text(text)
    with pytest.raises(ValueError):
        plan_batch(bundle=path, artifact_root=tmp_path / 'store',
                   artifact_index_id=refs['index']['id'], case_id_file=ids,
                   mode='chunk', output_root=tmp_path / 'batch', project_root=tmp_path / 'project')
    assert not (tmp_path / 'batch').exists()


@pytest.mark.parametrize('parallel', [1, 2])
def test_real_child_process_failure_is_recorded_without_retry_or_skipping(tmp_path, parallel):
    from rag_eval.notebook_batch import execute_batch
    jobs, _ = plan(tmp_path)
    # Replace only the model-calling boundary with an actual offline child.
    child = tmp_path / 'child.py'
    child.write_text("""import json, os, pathlib, sys
run=pathlib.Path(sys.argv[1]); run.mkdir(parents=True)
(run/'pid').write_text(str(os.getpid()))
(run/'state.json').write_text(json.dumps({'phase':'failed' if sys.argv[2]=='7' else 'finished'}))
print('Authorization: Bearer private-value https://private.invalid', file=sys.stderr)
raise SystemExit(int(sys.argv[2]))
""")
    for job, code in zip(jobs, (7, 0)):
        job['argv'] = [sys.executable, str(child), job['run_dir'], str(code)]
    result = execute_batch(jobs, output_root=tmp_path / 'batch', parallel=parallel)
    assert result['exit_code'] == 1
    assert sorted(r['exit_code'] for r in result['runs']) == [0, 7]
    assert len({(Path(j['run_dir']) / 'pid').read_text() for j in jobs}) == 2
    rows = [json.loads(line) for line in (tmp_path / 'batch/runs.local.jsonl').read_text().splitlines()]
    assert len(rows) == 2
    failed, = [r for r in rows if r['exit_code'] == 7]
    assert failed['state']['phase'] == 'failed'
    assert failed['ledger_backend'] == 'local-fallback; M0-B append failed'
    assert 'private-value' not in failed['stderr_tail'] and 'private.invalid' not in failed['stderr_tail']
    with pytest.raises(FileExistsError):
        execute_batch(jobs, output_root=tmp_path / 'batch', parallel=parallel)


def test_external_append_seam_receives_each_terminal_row(tmp_path):
    from rag_eval.notebook_batch import execute_batch
    jobs, _ = plan(tmp_path)
    for j in jobs:
        j['argv'] = [sys.executable, '-c', 'raise SystemExit(0)']
    rows = []
    result = execute_batch(jobs, output_root=tmp_path / 'batch', append_run=rows.append)
    assert result['exit_code'] == 0 and len(rows) == 2
    assert not (tmp_path / 'batch/runs.local.jsonl').exists()


def test_pinned_index_mismatch_and_existing_run_are_never_reused(tmp_path):
    from rag_eval.notebook_batch import plan_batch, execute_batch
    jobs, _ = plan(tmp_path)
    Path(jobs[0]['run_dir']).mkdir(parents=True)
    with pytest.raises(ValueError, match='new'):
        execute_batch(jobs, output_root=tmp_path / 'new-batch')
    _, path, _, ids = installed(tmp_path / 'other')
    with pytest.raises(ValueError, match='index'):
        plan_batch(bundle=path, artifact_root=tmp_path / 'other/store', artifact_index_id='wrong',
                   case_id_file=ids, mode='chunk', output_root=tmp_path / 'x', project_root=tmp_path / 'project')


def test_append_failure_and_spawn_failure_are_local_facts_and_do_not_stop_other_jobs(tmp_path):
    from rag_eval.notebook_batch import execute_batch
    jobs, _ = plan(tmp_path)
    jobs[0]['argv'] = [str(tmp_path / 'nonexistent-executable')]
    jobs[1]['argv'] = [sys.executable, '-c', 'raise SystemExit(0)']
    def reject(row):
        raise ValueError('ledger boundary failed')
    result = execute_batch(jobs, output_root=tmp_path / 'batch', append_run=reject)
    assert result['exit_code'] == 1 and len(result['runs']) == 2
    rows = [json.loads(line) for line in (tmp_path / 'batch/runs.local.jsonl').read_text().splitlines()]
    assert {r['exit_code'] for r in rows} == {None, 0}
    assert all(r['ledger_append_error'] == 'ValueError' for r in rows)
    failed = next(r for r in rows if r['exit_code'] is None)
    assert failed['execution_status'] == 'spawn_failed'
    assert failed['failed_phase'] == 'spawn'
    assert 'No such file' in failed['error_message']
    assert 'FileNotFoundError' in Path(failed['stderr_path']).read_text()
    assert not Path(failed['run_dir']).exists()


def test_batch_preserves_full_sanitized_stderr_when_child_does_not_initialize_run(tmp_path):
    from rag_eval.notebook_batch import execute_batch
    jobs, _ = plan(tmp_path)
    job = jobs[0]
    job['argv'] = [sys.executable, '-c',
        'import sys; print("first failure line\\n" + "detail\\n" * 2000 + '
        '"https://private.invalid api_key=hidden\\nlast failure line", file=sys.stderr); raise SystemExit(2)']
    result = execute_batch([job], output_root=tmp_path / 'batch', append_run=lambda row: None)
    row, = result['runs']
    assert row['state'] is None and not Path(job['run_dir']).exists()
    saved = Path(row['stderr_path']).read_text()
    assert 'first failure line' in saved and 'last failure line' in saved
    assert len(saved) > 4096 and len(row['stderr_tail']) <= 4096
    assert 'last failure line' in row['stderr_tail']
    assert 'private.invalid' not in saved and 'hidden' not in saved


def test_cli_dry_run_and_metrics_only_never_invoke_models(tmp_path):
    import subprocess
    _, path, refs, ids = installed(tmp_path)
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, str(root / 'scripts/run_notebook_batch.py'), 'run',
        '--bundle', str(path), '--store', str(tmp_path / 'store'), '--index', refs['index']['id'],
        '--case-id-file', str(ids), '--mode', 'chunk', '--output-root', str(tmp_path / 'batch'),
        '--project-root', str(tmp_path / 'nonexistent-project'), '--dry-run'], capture_output=True, text=True)
    assert result.returncode == 0 and len(json.loads(result.stdout)['jobs']) == 2
    assert not (tmp_path / 'batch').exists()
    from test_stage_metrics import REAL_RUN
    before = {p.name: p.stat().st_mtime_ns for p in REAL_RUN.iterdir()}
    result = subprocess.run([sys.executable, str(root / 'scripts/run_notebook_batch.py'), 'metrics',
        '--run-dir', str(REAL_RUN), '--no-write'], capture_output=True, text=True)
    assert result.returncode == 0
    assert json.loads(result.stdout)['groups'][0]['context_precision']['value'] == 1 / 16
    assert {p.name: p.stat().st_mtime_ns for p in REAL_RUN.iterdir()} == before


@pytest.mark.parametrize('parallel', [1, 2])
def test_parallel_limit_bounds_actual_child_intervals(tmp_path, parallel):
    from rag_eval.notebook_batch import execute_batch
    jobs, _ = plan(tmp_path)
    jobs = [dict(jobs[i % 2], run_dir=str(tmp_path / 'batch' / str(i))) for i in range(4)]
    child = tmp_path / 'interval.py'
    child.write_text("""import json, pathlib, sys, time
run=pathlib.Path(sys.argv[1]); run.mkdir()
start=time.monotonic_ns(); time.sleep(.1)
(run/'interval.json').write_text(json.dumps([start,time.monotonic_ns()]))
""")
    for job in jobs:
        job['argv'] = [sys.executable, str(child), job['run_dir']]
    assert execute_batch(jobs, output_root=tmp_path / 'batch', parallel=parallel,
                         append_run=lambda row: None)['exit_code'] == 0
    boundaries = []
    for job in jobs:
        start, end = json.loads((Path(job['run_dir']) / 'interval.json').read_text())
        boundaries.extend([(start, 1), (end, -1)])
    active, maximum = 0, 0
    for _, delta in sorted(boundaries):
        active += delta
        maximum = max(maximum, active)
    assert maximum <= parallel


def test_batch_writes_stage_metrics_after_offline_child_saved_outputs(tmp_path):
    from rag_eval.notebook_batch import execute_batch
    from test_stage_metrics import saved_run
    jobs, _ = plan(tmp_path)
    source = saved_run(tmp_path, 'saved', 'no_answer')
    jobs = jobs[:1]
    jobs[0]['argv'] = [sys.executable, '-c',
        'import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2])', str(source), jobs[0]['run_dir']]
    result = execute_batch(jobs, output_root=tmp_path / 'batch')
    assert result['exit_code'] == 0
    metrics = json.loads((Path(jobs[0]['run_dir']) / 'stage-metrics.json').read_text())
    assert metrics['summary']['status_counts'] == {'no_answer': 1}
    rows = [json.loads(line) for line in (tmp_path / 'batch/runs.jsonl').read_text().splitlines()]
    assert rows[0]['stage_metrics']['refusal_rate']['value'] == 1
    assert rows[0]['state'] == 'finished'
    assert rows[0]['exit_code'] == 0 and rows[0]['stderr_summary'] == ''
    receipts = [json.loads(line) for line in (tmp_path / 'batch/batch-executions.jsonl').read_text().splitlines()]
    assert receipts[0]['exit_code'] == 0 and receipts[0]['ledger_backend'] == 'M0-B append_run'
    assert not (tmp_path / 'batch/runs.local.jsonl').exists()


def test_stage_metric_failure_retains_saved_answer_but_fails_batch_bookkeeping(tmp_path):
    from rag_eval.notebook_batch import execute_batch
    from test_stage_metrics import saved_run
    jobs, _ = plan(tmp_path)
    source = saved_run(tmp_path, 'saved', 'success')
    (source / 'outputs.jsonl').write_text('{invalid}\n')
    jobs = jobs[:1]
    jobs[0]['argv'] = [sys.executable, '-c',
        'import shutil,sys; shutil.copytree(sys.argv[1],sys.argv[2])', str(source), jobs[0]['run_dir']]
    result = execute_batch(jobs, output_root=tmp_path / 'batch')
    assert result['exit_code'] == 1 and result['runs'][0]['exit_code'] == 0
    assert result['runs'][0]['stage_metrics_error'] == 'ValueError'
    assert (Path(jobs[0]['run_dir']) / 'outputs.jsonl').read_text() == '{invalid}\n'
