"""Run flips reuse frozen admission and keep failed sides readable."""
import importlib
import json
from pathlib import Path
import subprocess
import sys

import pytest

from test_benchmark_comparison import examples, rehash, save


def api():
    return importlib.import_module('rag_eval.flip_report')


def runs(tmp_path):
    bundle, entries, submissions, scores = examples(tmp_path)
    paths = []
    for i, submission in enumerate(submissions):
        path = tmp_path / f'run-{i}'; path.mkdir()
        save(path / 'manifest.json', {'run_id': path.name, 'identity': {'source': bundle['manifest']}})
        (path / 'outputs.jsonl').write_text(''.join(json.dumps(p) + '\n' for p in submission['predictions']))
        save(path / 'submission.json', submission)
        save(path / 'scores.json', scores[i])
        paths.append(path)
    return paths, scores


def test_two_runs_use_core_pairing_and_report_every_case(tmp_path):
    paths, _ = runs(tmp_path)
    report = api().compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'admitted'
    assert report['summary']['rougeL'] == {'better': 1, 'worse': 0, 'unchanged': 1, 'unavailable': 0}
    assert len(report['cases']) == 2
    first = report['cases'][0]
    assert first['before_status'] == first['after_status'] == 'success'
    assert first['metrics']['rougeL']['before'] == .1
    assert first['metrics']['rougeL']['after'] == pytest.approx(.3)
    assert first['metrics']['rougeL']['direction'] == 'better'
    assert 'mauve' not in first['metrics']  # batch-only never becomes a per-case value
    assert report['comparison']['comparisons'][0]['paired'][0]['differences']['rougeL'] == pytest.approx(.2)


def test_self_comparison_uses_original_identities(tmp_path):
    paths, scores = runs(tmp_path)
    report = api().compare_runs_flips(paths[0], paths[0], bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'admitted'
    assert report['self_comparison'] is True
    assert report['summary']['rougeL']['unchanged'] == 2
    assert report['before']['scores_id'] == scores[0]['scores_id']
    assert report['after']['scores_id'] == scores[0]['scores_id']


def test_worse_and_no_answer_status_are_not_filtered(tmp_path):
    paths, scores = runs(tmp_path)
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.benchmark_submission import build_submission
    from rag_eval.identity import fingerprint
    from rag_eval.notebook_bundle import load_bundle
    bundle = load_bundle(tmp_path / 'bundle')
    submission = json.loads((paths[1] / 'submission.json').read_text())
    submission['predictions'][0].update(status='no_answer', prediction='')
    submission = build_submission(bundle, method=submission['method'], predictions=submission['predictions'])
    scores[1].update(submission_id=fingerprint(submission), coverage=submission['coverage'],
                     prepared_id=fingerprint(prepare_inputs(bundle, submission)))
    scores[1]['per_case'][0].update(status='no_answer', metrics={'rougeL': 0.0})
    rehash(scores[1]); save(paths[1] / 'scores.json', scores[1]); save(paths[1] / 'submission.json', submission)
    (paths[1] / 'outputs.jsonl').write_text(''.join(json.dumps(p) + '\n' for p in submission['predictions']))
    report = api().compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['summary']['rougeL'] == {'better': 0, 'worse': 1, 'unchanged': 1, 'unavailable': 0}
    assert report['cases'][0]['after_status'] == 'no_answer'


def test_score_sheet_for_other_answers_cannot_be_attached_to_run(tmp_path):
    paths, _ = runs(tmp_path)
    outputs = [json.loads(line) for line in (paths[1] / 'outputs.jsonl').read_text().splitlines()]
    outputs[0]['prediction'] = 'Different observed answer'
    (paths[1] / 'outputs.jsonl').write_text(''.join(json.dumps(p) + '\n' for p in outputs))
    report = api().compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'not_admitted'
    assert 'answer differs' in report['after']['reason']
    assert report['after']['readable'] is True


def test_missing_case_is_still_readable_as_pending_without_zero_score(tmp_path):
    paths, scores = runs(tmp_path)
    all_ids = scores[1]['case_ids']
    save(paths[1] / 'state.json', {'phase': 'running'})
    (paths[1] / 'planned.jsonl').write_text(''.join(json.dumps({'case_id': cid}) + '\n' for cid in all_ids))
    output, _ = (paths[1] / 'outputs.jsonl').read_text().splitlines()
    (paths[1] / 'outputs.jsonl').write_text(output + '\n')
    report = api().compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'not_admitted'
    pending, = [r for r in report['after']['cases'] if r['case_id'] == all_ids[1]]
    assert pending['status'] == 'pending' and pending['metrics'] is None


def test_output_directory_never_overlaps_source_run(tmp_path):
    paths, _ = runs(tmp_path)
    with pytest.raises(ValueError, match='outside'):
        api().write_flip_report(*paths, paths[0] / 'flips', bundle_directory=tmp_path / 'bundle')
    assert not (paths[0] / 'flips').exists()


def test_report_cannot_be_written_inside_immutable_store(tmp_path):
    paths, _ = runs(tmp_path)
    store = tmp_path / 'store'
    (store / 'objects' / 'bundle').mkdir(parents=True)
    with pytest.raises(ValueError, match='shared artifact store'):
        api().write_flip_report(*paths, store / 'flips', bundle_directory=tmp_path / 'bundle')
    assert not (store / 'flips').exists()


def test_no_outputs_yet_preserves_every_planned_pending_case(tmp_path):
    paths, scores = runs(tmp_path)
    (paths[1] / 'outputs.jsonl').unlink()
    (paths[1] / 'scores.json').unlink()
    save(paths[1] / 'state.json', {'phase': 'running'})
    (paths[1] / 'planned.jsonl').write_text(''.join(json.dumps({'case_id': cid}) + '\n' for cid in scores[1]['case_ids']))
    report = api().compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'not_admitted'
    assert report['after']['readable'] is True
    assert len(report['after']['cases']) == 2
    assert all(r['status'] == 'pending' and r['metrics'] is None for r in report['after']['cases'])


@pytest.mark.parametrize('mutation', ['scorer', 'scope', 'corrupt_score', 'missing_scores', 'diagnostic'])
def test_admission_failure_preserves_both_readable_runs(tmp_path, mutation):
    paths, scores = runs(tmp_path)
    if mutation == 'scorer':
        from rag_eval.benchmark_official import scorer_identity
        from rag_eval.identity import fingerprint
        scores[1]['dependencies']['python'] = 'different'
        scores[1]['scorer_identity'] = scorer_identity(scores[1]['profile'], scores[1]['dependencies'])
        scores[1]['scorer_id'] = fingerprint(scores[1]['scorer_identity'])
        rehash(scores[1]); save(paths[1] / 'scores.json', scores[1])
    elif mutation == 'scope':
        scores[1]['case_ids'] = scores[1]['case_ids'][:1]
        rehash(scores[1]); save(paths[1] / 'scores.json', scores[1])
    elif mutation == 'corrupt_score':
        scores[1]['metrics']['rougeL'] = .99
        save(paths[1] / 'scores.json', scores[1])
    elif mutation == 'missing_scores':
        (paths[1] / 'scores.json').unlink()
    else:
        manifest = json.loads((paths[1] / 'manifest.json').read_text())
        manifest['diagnostic'] = True; save(paths[1] / 'manifest.json', manifest)
    report = api().compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'not_admitted'
    assert report['admission']['reason']
    assert report['before']['readable'] and report['after']['readable']
    assert len(report['before']['cases']) == len(report['after']['cases']) == 2
    assert report['cases'] == [] and report['summary'] == {}


def test_ledger_rows_and_cli_write_readable_json_markdown_even_when_blocked(tmp_path):
    paths, _ = runs(tmp_path)
    (paths[1] / 'scores.json').unlink()
    script = Path(__file__).resolve().parents[1] / 'scripts/compare_runs_flips.py'
    ledger = tmp_path / 'ledger.jsonl'
    ledger.write_text(''.join(json.dumps({'run_dir': str(p)}) + '\n' for p in paths))
    command = [sys.executable, str(script), '--ledger', str(ledger), '--rows', '1', '2',
               '--bundle', str(tmp_path / 'bundle'), '--output', str(tmp_path / 'report')]
    completed = subprocess.run(command, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path / 'report/report.json').read_text())
    assert report['admission']['status'] == 'not_admitted'
    assert 'not_admitted' in (tmp_path / 'report/report.md').read_text()
    assert 'success' in (tmp_path / 'report/report.md').read_text()
    before = (tmp_path / 'report/report.json').read_bytes()
    completed = subprocess.run(command, text=True, capture_output=True)
    assert completed.returncode != 0
    assert (tmp_path / 'report/report.json').read_bytes() == before


def test_actual_run_ledger_field_resolves_external_official_scores(tmp_path):
    from rag_eval.run_ledger import append_run
    paths, _ = runs(tmp_path)
    ledger = tmp_path / 'registered-runs.jsonl'
    entries = []
    for i, path in enumerate(paths):
        official = tmp_path / f'official-{i}'
        official.mkdir()
        for name in ('scores.json', 'submission.json'):
            (path / name).rename(official / name)
        entries.append(append_run(ledger, path, scores_path=official / 'scores.json'))
    report = api().compare_runs_flips(*entries, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'admitted'
    assert len(report['cases']) == 2
    with pytest.raises(ValueError, match='outside'):
        api().write_flip_report(*entries, tmp_path / 'official-0' / 'flips',
                               bundle_directory=tmp_path / 'bundle')


@pytest.mark.parametrize('phase', ['initializing', 'importing', 'asking', 'scoring'])
def test_actual_notebook_live_phases_keep_unobserved_cases_pending(tmp_path, phase):
    paths, scores = runs(tmp_path)
    (paths[1] / 'outputs.jsonl').unlink()
    (paths[1] / 'scores.json').unlink()
    save(paths[1] / 'state.json', {'phase': phase})
    (paths[1] / 'planned.jsonl').write_text(''.join(
        json.dumps({'case_id': cid}) + '\n' for cid in scores[1]['case_ids']))
    report = api().compare_runs_flips(*paths, bundle_directory=tmp_path / 'bundle')
    assert report['admission']['status'] == 'not_admitted'
    assert all(r['status'] == 'pending' and r['metrics'] is None for r in report['after']['cases'])


def test_saved_attempt_one_cli_self_comparison_is_unchanged_and_read_only(tmp_path):
    root = Path(__file__).resolve().parents[1]
    evidence = root / 'apps/sn-workbench/test-results/source-execution-20261012/e5-s3-real-run-20261013'
    run = evidence / 'attempt-1/cli-run'
    scores = evidence / 'official/scores/scores.json'
    if not scores.exists() or not run.exists():
        pytest.skip('Local saved attempt-1 artifact is not available')
    protected = [run / 'manifest.json', run / 'outputs.jsonl', scores, scores.parent / 'submission.json']
    before = {p: p.read_bytes() for p in protected}
    command = [sys.executable, str(root / 'scripts/compare_runs_flips.py'),
               '--runs', str(run), str(run), '--scores', str(scores), str(scores),
               '--output', str(tmp_path / 'attempt-self-flips')]
    completed = subprocess.run(command, text=True, capture_output=True)
    assert completed.returncode == 0, completed.stderr
    report = json.loads((tmp_path / 'attempt-self-flips/report.json').read_text())
    assert report['admission']['status'] == 'admitted'
    assert report['self_comparison'] is True
    assert len(report['cases']) == 1
    assert report['summary'] == {
        'answer_f1': {'better': 0, 'worse': 0, 'unchanged': 1, 'unavailable': 0},
        'evidence_f1': {'better': 0, 'worse': 0, 'unchanged': 1, 'unavailable': 0}}
    assert 'unchanged' in (tmp_path / 'attempt-self-flips/report.md').read_text()
    assert {p: p.read_bytes() for p in protected} == before
