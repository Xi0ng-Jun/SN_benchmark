"""Analyst evidence table and cluster preview: one call each, the analyst's visibility, nothing written."""
import json

import pytest

from rag_eval.workbench import agent_tools
from test_workbench_clusters import frozen_cluster, env  # noqa: F401

COLUMNS = ['status', 'metrics.answer_f1', 'prediction', 'response.grounded', 'response.evidence_level',
           'intent_preview.needs_clarification', 'deterministic.evidence_hit', 'deterministic.citation_valid_count',
           'deterministic.citation_invalid_count', 'stage_metrics.gold_evidence_recall.value',
           'stage_metrics.context_precision.value', 'stage_metrics.citation_precision.value', 'usage.total_tokens',
           'contexts']


@pytest.fixture
def analyst(frozen_cluster, monkeypatch, capsys):
    f = frozen_cluster
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (f['env']['repo'], f['views']))
    monkeypatch.setattr(agent_tools, 'iteration_service', lambda *a, **k: f['service'])

    def run(*argv, lines=False):
        capsys.readouterr()
        agent_tools.main(['--task', f['tree']['task_id'], '--node', f['tree']['root_candidate_id'], *argv])
        text = capsys.readouterr().out
        return (text, [json.loads(line) for line in text.splitlines()]) if lines else json.loads(text)
    f['run'] = run
    return f


def protocol(f, **splits):
    tree = f['tree']
    value = dict(tree['protocol'], splits=dict(tree['protocol']['splits'], **splits))
    if 'fresh' in splits:
        value['fresh_batches'] = [dict(batch_id='f1', case_ids=value['splits'].pop('fresh'))]
    with f['env']['repo'].connect(write=True) as conn:
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(value), tree['id']))


def rows(f, cases):
    """Every baseline execution plans ``cases``; scores per execution in order."""
    executions = [a['execution_id'] for a in f['service'].candidate(f['tree']['root_candidate_id'])['attempts']]
    for eid, scores in zip(executions, cases):
        f['views'].scores[eid] = scores
        f['views'].partitions[eid] = [tuple(scores)]
    return executions


def test_evidence_shows_only_search_cases_without_annotations(analyst):
    f = analyst
    protocol(f, search=['qasper:q'], validation=['qasper:q2'], sealed=['qasper:sealed'], fresh=['qasper:fresh'])
    executions = rows(f, [{'qasper:q': 0, 'qasper:q2': 1, 'qasper:fresh': 1, 'qasper:sealed': 1}] * 2)
    observed, original = [], f['views'].observation
    f['views'].observation = lambda run_id, cid: observed.append(cid) or original(run_id, cid)
    text, (header, *cases) = f['run']('evidence', lines=True)
    assert set(observed) == {'qasper:q'}  # hidden cases are never even read
    assert [c['case_id'] for c in cases] == ['qasper:q'] and header['n_cases'] == 1
    for hidden in ('qasper:q2', 'qasper:fresh', 'qasper:sealed', f['secret'], '"gold"', 'references', 'papers'):
        assert hidden not in text
    assert header['executions'] == executions and header['metric'] == 'answer_f1'
    assert header['attempt_columns'] == COLUMNS
    assert cases[0]['question'] == f['questions']['a']
    assert cases[0]['attempts'] == [['success', 0, 'wrong', False, 'low', None, False, 0, 0, 0, 0, None, 10, 1]] * 2
    assert cases[0]['mean'] == 0 and cases[0]['failed'] is True


def test_evidence_columns_failed_filter_and_stable_output(analyst):
    f = analyst
    rows(f, [{'qasper:q': 0, 'qasper:q2': 1}, {'qasper:q': 1, 'qasper:q2': 1}])
    text, (header, *cases) = f['run']('evidence', lines=True)
    assert text == f['run']('evidence', lines=True)[0]
    assert [(c['case_id'], c['mean'], c['failed']) for c in cases] == [('qasper:q', .5, True), ('qasper:q2', 1, False)]
    _, (_, *failed) = f['run']('evidence', '--failed-only', lines=True)
    assert [c['case_id'] for c in failed] == ['qasper:q']
    _, (narrow, *cases) = f['run']('evidence', '--fields', 'metrics.answer_f1,response.answer', lines=True)
    assert narrow['attempt_columns'] == ['metrics.answer_f1', 'response.answer']
    assert cases[0] == dict(case_id='qasper:q', mean=.5, failed=True, attempts=[[0, 'wrong'], [1, 'wrong']])
    with pytest.raises(ValueError, match='Unknown evidence fields'):
        f['run']('evidence', '--fields', 'gold.answer')
    long = 'x' * 400
    original = f['views'].observation
    f['views'].observation = lambda run_id, cid: dict(original(run_id, cid), prediction=long)
    _, (_, first, _) = f['run']('evidence', '--fields', 'prediction', lines=True)
    assert first['attempts'][0] == ['x' * 300 + '…']


def tables(repo):
    with repo.connect() as conn:
        names = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]
        return {name: conn.execute(f'SELECT count(*) FROM "{name}"').fetchone()[0] for name in names}


def test_preview_is_what_propose_freezes_and_writes_nothing(analyst, tmp_path):
    f = analyst
    card = dict(cluster_id='c02', description='Zero-score answers',
                signature=dict(kind='range', field='metrics.answer_f1', max=0),
                missing_signature=dict(kind='range', field='stage_metrics.gold_evidence_recall.value', min=0, max=0))
    path = tmp_path / 'cluster.json'
    path.write_text(json.dumps(card))
    before = tables(f['env']['repo'])
    preview = f['run']('cluster', 'preview', '--file', str(path))
    assert tables(f['env']['repo']) == before
    assert preview['preview'] is True and preview['cluster_id_taken'] is False
    assert preview['members'] == ['qasper:q', 'qasper:q2'] and preview['n_cases'] == 2
    frozen = f['run']('cluster', 'propose', '--file', str(path))
    strip = lambda out, keys: {k: v for k, v in out.items() if k not in keys}
    assert strip(preview, ('preview', 'cluster_id_taken')) == strip(frozen, ('frozen_from_execution', 'frozen_at', 'proposer'))
    assert f['run']('cluster', 'preview', '--file', str(path))['cluster_id_taken'] is True
    # Validation and the leak scan are the same code path as propose.
    path.write_text(json.dumps(dict(card, cluster_id='c03', description=f['questions']['a'])))
    with pytest.raises(ValueError, match='overlaps'):
        f['run']('cluster', 'preview', '--file', str(path))
    path.write_text(json.dumps(dict(card, cluster_id='c03', signature=dict(kind='regex', field='gold.answer', pattern='x'))))
    with pytest.raises(ValueError, match='whitelisted'):
        f['run']('cluster', 'preview', '--file', str(path))
