"""Sealed observations never enter ordinary task or execution summaries."""
import json
from types import SimpleNamespace

import pytest

from rag_eval.workbench.run_views import RunEntry, RunViews
from test_workbench_iterations import env, iteration  # noqa: F401


def make_views(env, tmp_path, monkeypatch, count):
    tree = iteration(env)
    with env['repo'].connect(write=True) as conn:
        protocol = tree['protocol']
        protocol['splits']['sealed'] = ['sealed-secret']
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), tree['id']))
    entries = [RunEntry('ordinary-1', tmp_path / 'ordinary', task_id=tree['task_id'], case_ids=('qasper:q',))]
    entries += [RunEntry(f'sealed-{i}', tmp_path / f'sealed-{i}', task_id=tree['task_id'],
                         case_ids=('sealed-secret',)) for i in range(1, count + 1)]
    binding = dict(task_id=tree['task_id'], worktree_path=str(tmp_path), branch='test', base_revision='base')
    service = SimpleNamespace(repository=env['repo'], binding=lambda tid: binding,
                              read=lambda tid: dict(binding=binding))
    views = RunViews(entries, worktree_service=service)
    def load(identity):
        return dict(id=identity, title=identity, suite='qasper', mode='chunk',
                    state=dict(terminal=True, phase='finished'), official=dict(metrics=dict(answer_f1=.75)),
                    attention=dict(count=0), provenance=dict(source={}))
    monkeypatch.setattr(views, '_load', load)
    monkeypatch.setattr(views, '_discover_executions', lambda: None)
    return views, tree, binding


@pytest.mark.parametrize('count', [1, 21])
def test_task_summaries_omit_sealed_runs_and_preserve_ordinary_runs(env, tmp_path, monkeypatch, count):
    views, tree, binding = make_views(env, tmp_path, monkeypatch, count)
    assert [r['id'] for r in views._runs_for_binding(binding)] == ['ordinary-1']
    assert [r['id'] for r in views.task_changes(tree['task_id'])['runs']] == ['ordinary-1']
    assert views.task_overview(tree['task_id'])['runs'] == ['ordinary-1']
    context = json.loads(views.task_runs_context(tree['task_id'])[1])
    assert [r['id'] for r in context['runs']] == ['ordinary-1']
    assert 'sealed' not in json.dumps(context)
    # Unbound/fallback registrations follow the same policy before collapsing.
    monkeypatch.setattr(views, 'task_overview', lambda tid: dict(status='unbound', runs=[]))
    assert [r['id'] for r in json.loads(views.task_runs_context(tree['task_id'])[1])['runs']] == ['ordinary-1']


def test_collapsed_execution_summary_refuses_sealed_before_reading_scores(env, tmp_path, monkeypatch):
    views, tree, binding = make_views(env, tmp_path, monkeypatch, 21)
    monkeypatch.setattr(views, '_execution_record', lambda eid: pytest.fail('Sealed score must not be read'))
    with pytest.raises(ValueError, match='sealed'):
        views._execution_summary('sealed', 21)


@pytest.mark.parametrize('cache_kind', ['persisted', 'memory'])
def test_ordinary_admission_refuses_sealed_before_cache_reuse(env, tmp_path, monkeypatch, cache_kind):
    from dataclasses import replace
    from rag_eval.workbench.comparisons import Admission, ADMISSION_CACHE, ADMISSION_FORMAT, _signature
    views, tree, binding = make_views(env, tmp_path, monkeypatch, 1)
    entry = replace(views._runs['sealed-1'], scores=tmp_path / 'scores.json')
    views._runs['sealed-1'] = entry
    result = dict(status='admitted', reason=None)
    (tmp_path / ADMISSION_CACHE).write_text(json.dumps(dict(
        format=ADMISSION_FORMAT, signature=_signature([entry]), result=result)))
    measurement = dict(execution_id='sealed', execution_state='finished', run_dir=str(tmp_path))
    # The dedicated capability's isolated reader may reuse formal admission.
    assert Admission(RunViews([entry]))(measurement) == result
    ordinary = Admission(views)
    if cache_kind == 'memory':
        ordinary._cache['sealed'] = result
    with pytest.raises(ValueError, match='sealed'):
        ordinary(measurement)
