"""P9: model-scheduling failures during preparation are infrastructure failures, not degraded data."""
import json
import sqlite3

import pytest

from rag_eval.preparation_diagnostics import (INFRASTRUCTURE_ERROR_TYPES, model_call_errors,
                                              preparation_diagnostics)
from rag_eval.run_report import load_run
from test_prepared_seed_retry import fake_sn  # noqa: F401
from test_preparation_cache import sn  # noqa: F401


_CALLS = iter(range(1, 10**6))


def event(workload, status='completed', error_type=None):
    """A submission and its terminal event, shaped like the real model-service journal."""
    from rag_eval.identity import fingerprint
    service = dict(service_id='general', kind='chat', protocol='openai', model='fixture-chat',
                   endpoint_sha256='a' * 64, credential_variable_name=None, credential_variable_configured=False,
                   max_concurrency=2, top_p=None, generation='b' * 64)
    context = dict(workload_id=workload, support_id='mdl-fixture000000000', priority='background')
    snapshot = fingerprint(dict(service=service, context=context))
    call_id = next(_CALLS)
    base = dict(call_id=call_id, service=service, context=context, snapshot_sha256=snapshot,
                recorded_at='2026-10-26T00:00:00+00:00')
    terminal = dict(base, kind='model_service_future_terminal', phase='future', status=status, error_type=error_type)
    return [dict(base, kind='model_service_submission_attempt', phase='submission'), terminal]


@pytest.fixture
def journaled(sn, monkeypatch):
    """The fake preparation also leaves model-service journal events behind."""
    from rag_eval import benchmark_runtime
    original = benchmark_runtime.prepare_notebook
    sn.events = []

    def prepare(repo, cell, *args, **kwargs):
        result = original(repo, cell, *args, **kwargs)
        with (cell.parent / 'model-events.jsonl').open('a') as stream:
            for pair in sn.events:
                for row in pair:
                    stream.write(json.dumps(row) + '\n')
        return result
    monkeypatch.setattr(benchmark_runtime, 'prepare_notebook', prepare)
    return sn


def test_queue_full_fails_preparation_as_infrastructure_and_leaves_no_cache_entry(journaled):
    journaled.events = [event('source_summary'), event('source_summary', 'error', 'ModelQueueFull'),
                        event('paper_metadata', 'error', 'ModelQueueFull')]
    run = journaled.cached('degraded')
    outputs = load_run(run)['outputs']
    assert outputs and all(row['status'] == 'error' for row in outputs)
    record = outputs[0]['product_record']
    assert record['error_type'] == 'PreparationInfrastructureError' and record['failed_phase'] == 'importing'
    assert 'ModelQueueFull x2' in record['error_message']
    state = json.loads((run / 'state.json').read_text())
    assert state['phase'] == 'finished_with_errors' and state['error_type'] == 'PreparationInfrastructureError'
    cell = json.loads((run / 'product-artifacts/state.json').read_text())
    assert cell['prepared'] is False and cell['preparation_infrastructure_errors'] == {'ModelQueueFull': 2}
    assert json.loads((run / 'preparation-cache.json').read_text())['status'] == 'miss'
    assert not [p for p in journaled.cache.iterdir() if not p.name.startswith('.')]  # nothing published
    # Nothing was asked of the degraded notebook.
    attempts = (run / 'product-artifacts/attempts.jsonl').read_text()
    assert journaled.prepared == 1 and '"finished"' in attempts
    # The next attempt prepares again instead of copying anything.
    journaled.events = []
    again = journaled.cached('clean')
    assert journaled.prepared == 2 and json.loads((again / 'preparation-cache.json').read_text())['store']['status'] == 'stored'
    assert load_run(again)['outputs'][0]['status'] == 'success'


def test_malformed_responses_stay_part_of_the_measurement(journaled):
    journaled.events = [event('source_summary', 'error', 'MalformedModelResponse'), event('paper_metadata')]
    run = journaled.cached('malformed')
    assert load_run(run)['outputs'][0]['status'] == 'success'
    diagnostics = json.loads((run / 'preparation-diagnostics.json').read_text())
    assert diagnostics['model_call_errors'] == {'MalformedModelResponse': 1}
    assert diagnostics['infrastructure_errors'] == {}
    assert json.loads((run / 'preparation-cache.json').read_text())['store']['status'] == 'stored'


@pytest.mark.parametrize('error_type', sorted(INFRASTRUCTURE_ERROR_TYPES))
def test_every_infrastructure_type_fails_preparation(journaled, error_type):
    journaled.events = [event('source_summary', 'error', error_type)]
    run = journaled.cached('infra')
    assert load_run(run)['outputs'][0]['product_record']['error_type'] == 'PreparationInfrastructureError'


def test_diagnostics_count_fallback_summaries_and_errors_by_workload(tmp_path):
    run = tmp_path / 'run'
    (run / 'runtime').mkdir(parents=True)
    db = sqlite3.connect(run / 'runtime/database.db')
    with db:
        db.execute('CREATE TABLE sources (id TEXT, notebook_id TEXT, summary TEXT)')
        db.executemany('INSERT INTO sources VALUES (?,?,?)', [
            ('a', 'nb', '3 parsed text element(s). Herons fly.'), ('b', 'nb', 'Herons fly south.'),
            ('c', 'nb', '1 parsed text element(s). x'), ('d', 'other', '2 parsed text element(s). y')])
    db.close()
    pairs = [event('source_summary', 'error', 'MalformedModelResponse'), event('source_summary', 'error', 'ModelQueueFull'),
             event('paper_metadata', 'error', 'ModelQueueFull'), event('chunk_embedding')]
    (run / 'model-events.jsonl').write_text(''.join(json.dumps(row) + '\n' for pair in pairs for row in pair) + '{"torn')
    diagnostics = preparation_diagnostics(run, 'nb')
    assert diagnostics['sources'] == 3 and diagnostics['fallback_summaries'] == 2
    assert diagnostics['model_call_errors'] == {'MalformedModelResponse': 1, 'ModelQueueFull': 2}
    assert diagnostics['model_call_errors_by_workload'] == {
        'MalformedModelResponse:source_summary': 1, 'ModelQueueFull:paper_metadata': 1,
        'ModelQueueFull:source_summary': 1}
    assert diagnostics['infrastructure_errors'] == {'ModelQueueFull': 2}
    assert model_call_errors(tmp_path / 'missing') == ({}, {})
