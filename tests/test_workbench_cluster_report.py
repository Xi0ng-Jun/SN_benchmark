"""The cluster report reads frozen clusters read-only and renders them for people."""
import json
from pathlib import Path

import pytest

from rag_eval.workbench import cluster_report
from test_workbench_clusters import frozen_cluster, observation  # noqa: F401
from test_workbench_iterations import env  # noqa: F401


def test_database_only_report_shows_cluster_rule_and_members(frozen_cluster):
    f = frozen_cluster
    state = f['env']['repo'].root
    data = cluster_report.collect(state)
    assert data['iteration']['id'] == f['tree']['id'] and data['warnings'] == []
    [cluster] = data['clusters']
    assert cluster['cluster_id'] == 'c01' and cluster['stats']['n_cases'] == len(cluster['members'])
    assert all(len(m['attempts']) == 2 for m in cluster['members'])
    html = cluster_report.render(data)
    assert 'Missing context before synthesis' in html and '检索上下文精度' in html and '不大于 0' in html
    assert 'class="dot' in html and f['secret'] not in html


class FakeClient:
    def __init__(self, url, token):
        self.calls = []

    def call(self, method, path, payload=None):
        assert method == 'GET'
        self.calls.append(path)
        return FakeClient.routes(path)


def test_server_data_adds_ranking_card_verdict_and_gold_without_sealed_cases(frozen_cluster, monkeypatch):
    f = frozen_cluster
    state = Path(f['env']['repo'].root)
    if not (state / 'access-token').is_file():
        (state / 'access-token').write_text('token')
    tree, hypothesis = f['tree'], f['proposal']['id']
    execution = json.loads(json.dumps(cluster_report._read_clusters(state, None)[1][0]['scan']['executions']))[0]

    def routes(path):
        if path == f"/api/iterations/{tree['id']}":
            return dict(task_id=tree['task_id'], title='R-test', protocol=dict(
                success_threshold=.5, input=dict(suite='qasper'),
                splits=dict(search=['qasper:q', 'qasper:q2'], validation=[], sealed=['qasper:q2'])))
        if path.endswith('/rounds'):
            return [dict(id='inv', settings=dict(b=1, pi=.3, max_clusters=4), rounds=[dict(
                number=1, selection=dict(ranking=[dict(cluster_id='c01', n_cases=1, q=.5, m=None, v=None, pi=.3,
                                                       score=.15, hypothesis_id=hypothesis)]),
                cards=[dict(hypothesis_id=hypothesis, cluster_id='c01')],
                candidates=[dict(cluster_id='c01', candidate_id='cand', phase='done', status='done',
                                 verdict=dict(verdict='accept', rho=1.0, delta=.02, eta=.01))])])]
        if path.endswith('/hypotheses'):
            return [dict(id=hypothesis, status='approved', card=f['proposal_card'])]
        if path == f'/api/run-views/executions/{execution}/cases':
            return dict(rows=[dict(case_id='qasper:q', run_id=execution + '-1')])
        if path.startswith(f'/api/run-views/runs/{execution}-1/case'):
            return dict(gold=dict(annotations=[dict(answer='the reranker stage')]))
        raise AssertionError(path)

    monkeypatch.setattr(FakeClient, 'routes', staticmethod(routes), raising=False)
    monkeypatch.setattr('rag_eval.workbench.operator_client.Client', FakeClient)
    data = cluster_report.collect(state, url='http://workbench', examples=2)
    [cluster] = data['clusters']
    assert [m['case_id'] for m in cluster['members']] == ['qasper:q']  # the sealed member is never shown
    assert cluster['members'][0]['gold'] == ['the reranker stage']
    assert cluster['rounds'][0]['selected'] and cluster['rounds'][0]['candidate']['verdict'] == 'accept'
    assert cluster['cards'][0]['predicted_flips'] == ['qasper:q']
    html = cluster_report.render(data)
    assert '第 1 轮修复 · 通过' in html and 'the reranker stage' in html and '预测 1 道题会从错变对' in html


def test_signature_wording_and_escaping():
    signature = {'all_of': [dict(kind='regex', field='response.answer', pattern='<b>'),
                            {'not': dict(kind='range', field='metrics.str_em', min=1)}]}
    text = cluster_report.describe(signature)
    assert '同时满足以下全部' in text and '不满足' in text and '答案得分 str_em' in text and '&lt;b&gt;' in text
    data = dict(format=cluster_report.FORMAT, generated_at='now', iteration=dict(id='i' * 32, success_threshold=.5),
                settings={}, warnings=[], clusters=[dict(
                    cluster_id='k1', description='<script>x</script>', frozen_at='2026-10-30T00:00:00',
                    signature=signature, missing_signature=signature, violation_signature=signature,
                    primary='str_em', success_threshold=.5, stats={}, rounds=[], cards=[], members=[])])
    assert '<script>x' not in cluster_report.render(data)


def test_exported_json_renders_elsewhere(frozen_cluster, tmp_path):
    state = frozen_cluster['env']['repo'].root
    out, dump = tmp_path / 'report.html', tmp_path / 'report.json'
    cluster_report.main(['--state', str(state), '--out', str(out), '--json-out', str(dump)])
    again = tmp_path / 'again.html'
    cluster_report.main(['--from-json', str(dump), '--out', str(again)])
    assert again.read_text() == out.read_text() and 'c01' in out.read_text()
    with pytest.raises(SystemExit):
        cluster_report.main(['--from-json', str(out)])
