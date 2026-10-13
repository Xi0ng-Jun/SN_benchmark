"""Shared seams preserve execution failures and offline diagnostic coverage."""
import json
from pathlib import Path
import pytest

from rag_eval.artifacts import digest
from rag_eval.run_ledger import append_run, query_runs
from rag_eval.run_report import write_report
from test_run_ledger import _run
from test_experiment_dashboard import make_run

REAL_RUN = (Path(__file__).resolve().parents[1] / 'apps/sn-workbench/test-results/'
            'source-execution-20261012/e5-s3-real-run-20261013/attempt-1/cli-run')


def test_failed_execution_is_saved_and_stderr_is_redacted(tmp_path):
    ledger = tmp_path / 'runs.jsonl'
    row = append_run(ledger, _run(tmp_path, phase='failed'), exit_code=7,
                     stderr_summary='failed https://private.invalid\nAuthorization: Bearer secret')
    assert row['exit_code'] == 7 and row['state'] == 'failed'
    assert 'failed' in row['stderr_summary']
    assert 'private.invalid' not in row['stderr_summary']
    assert 'Bearer secret' not in row['stderr_summary']
    assert query_runs(ledger)[0] == row


def test_old_ledger_rows_keep_unknown_execution_fields_without_rewriting(tmp_path):
    ledger = tmp_path / 'runs.jsonl'
    original = json.dumps({'run_dir': '/saved/run', 'state': 'finished'}) + '\n'
    ledger.write_text(original)
    row, = query_runs(ledger)
    assert row['exit_code'] is None and row['stderr_summary'] is None
    assert ledger.read_text() == original


def test_real_report_exposes_observed_metrics_and_never_mutates_run(tmp_path):
    before = {n: digest(REAL_RUN / n) for n in ('manifest.json', 'outputs.jsonl', 'model-events.jsonl')}
    report = write_report([REAL_RUN], tmp_path / 'report', partition_only=True)
    metrics = report['runs'][0]['stage_metrics']
    assert metrics['metric_role'] == 'diagnostic'
    assert metrics['summary']['context_precision']['denominator'] == 16
    assert metrics['summary']['context_precision']['numerator'] == 1
    assert metrics['summary']['gold_evidence_recall']['value'] == 1
    assert report['stage_metrics']['groups'][0]['planned'] == 1
    assert '阶段指标（diagnostic）' in (tmp_path / 'report/report.md').read_text()
    assert all(digest(REAL_RUN / n) == h for n, h in before.items())
    assert json.loads((tmp_path / 'report/summary.json').read_text()) == report


def test_report_stage_summary_keeps_unanswered_planned_case(tmp_path):
    run = make_run(tmp_path)
    report = write_report([run], tmp_path / 'report')
    group, = report['stage_metrics']['groups']
    assert group['planned'] == 2
    assert group['status_counts'] == {'success': 1, 'pending': 1}
    assert group['refusal_rate']['unobserved'] == 1


@pytest.mark.parametrize('marker', [{'diagnostic': True}, {'kind': 'diagnostic/oracle'}])
def test_official_export_rejects_diagnostic_marker_before_writing(tmp_path, marker):
    from rag_eval.benchmark_submission import export_sn_runs
    run = make_run(tmp_path)
    manifest = json.loads((run / 'manifest.json').read_text())
    (run / 'manifest.json').write_text(json.dumps({**manifest, **marker}))
    with pytest.raises(ValueError, match='diagnostic|oracle'):
        export_sn_runs(run / 'input', [run], tmp_path / 'official')
    assert not (tmp_path / 'official').exists()
