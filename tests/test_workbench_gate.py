from copy import deepcopy

import pytest

from rag_eval.workbench import gate
from rag_eval.workbench.noise import estimate


def evidence(before, after, k=4):
    sides = {}
    for side, values in [('reference', before), ('subject', after)]:
        sides[side] = estimate(dict(attempts=[dict(execution_id=f'{side}-{i}', rows=[
            dict(case_id=c, family=c, value=v) for c, v in values.items()]) for i in range(k)]))
    sides['eta'] = sides['reference']['eta']
    rows = [dict(case_id=c, left=v, right=after[c], delta=after[c]-v) for c, v in before.items()]
    return dict(rows=rows, mean=dict(delta=sum(r['delta'] for r in rows)/len(rows)),
                admission=dict(status='admitted')), sides


@pytest.mark.parametrize('after,expected', [({'a': 1, 'b': 1}, 'accept'),
    ({'a': 0, 'b': 0}, 'reject'), ({'a': 0, 'b': 1}, 'audit')])
def test_gate_verdicts_and_every_arithmetic_input(after, expected):
    comparison, noise = evidence({'a': 0, 'b': 0}, after)
    verdict = gate.judge(comparison, noise, dict(predicted_flips=['a'], guards=[], tau=.5), gate.DEFAULT_RULE)
    assert verdict['verdict'] == expected
    assert verdict['rule_id'] == 'lcd-v1' and verdict['tau'] == .5
    assert verdict['rho'] == (1 if after['a'] else 0)
    assert verdict['flips'][0]['band'] > 0
    assert verdict['flips'][0]['reference']['n'] == 4


def test_gate_guards_missing_and_infrastructure_failures():
    c, n = evidence({'a': 0, 'b': 1}, {'a': 1, 'b': 0})
    contract = dict(predicted_flips=['a'], guards=['b'], tau=.5)
    assert gate.judge(c, n, contract, gate.DEFAULT_RULE)['guard_broken'] == ['b']
    assert gate.judge(c, n, contract, gate.DEFAULT_RULE)['verdict'] == 'reject'
    c['infrastructure'] = dict(reference=dict(failed=0, total=20), subject=dict(failed=15, total=20))
    result = gate.judge(c, n, contract, gate.DEFAULT_RULE)
    assert result['verdict'] == 'unmeasurable' and result['infrastructure']['delta'] == .75
    c.pop('infrastructure')
    n['subject']['k_below_base'] = True
    assert gate.judge(c, n, contract, gate.DEFAULT_RULE)['verdict'] == 'unmeasurable'
    n['subject']['k_below_base'] = False
    contract['predicted_flips'] = ['absent']
    assert gate.judge(c, n, contract, gate.DEFAULT_RULE)['verdict'] == 'unmeasurable'


def test_contract_closed_shape_and_finite_threshold():
    from rag_eval.workbench.hypotheses import validate_card
    from test_workbench_roles_hypotheses import CARD
    valid = dict(CARD, contract=dict(cluster_id='c01', predicted_flips=['a'], guards=['b'], tau=.5))
    assert validate_card(valid)['contract'] == valid['contract']
    for change in [dict(tau=float('nan')), dict(predicted_flips=[]), dict(extra=True), dict(guards=['a'])]:
        bad = deepcopy(valid)
        bad['contract'].update(change)
        with pytest.raises(ValueError):
            validate_card(bad)

from test_workbench_comparisons import tree, ran, child, env  # noqa: E402,F401


def test_gate_persistence_override_and_failed_attempts(tree):
    import json
    from rag_eval.workbench.hypotheses import HypothesisStore
    t, root = tree, tree['tree']['root_candidate_id']
    for i in range(2):
        ran(t, root, {'a': 0, 'b': 0}, f'base{i}')
    candidate = child(t, root, 'candidate', {'a': 1, 'b': 1})
    ran(t, candidate, {'a': 1, 'b': 1}, 'again')
    service, repo = t['env']['service'], t['env']['repo']
    with repo.connect(write=True) as conn:
        protocol = json.loads(conn.execute('SELECT protocol_json FROM iterations WHERE id=?', (t['tree']['id'],)).fetchone()[0])
        protocol['splits'].update(search=['a'], validation=['b'])
        conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(protocol), t['tree']['id']))
    proposal = service.candidate(candidate)['proposal_id']
    store = HypothesisStore(repo)
    # Register a synthetic preregistered contract; production drafts validate it.
    with repo.connect(write=True) as conn:
        card = store._get(conn, t['tree']['task_id'], proposal)['card']
        card['contract'] = dict(predicted_flips=['a'], guards=[], tau=.5)
        conn.execute('UPDATE hypotheses SET card=? WHERE id=?', (json.dumps(card), proposal))
    def check():
        comparison = t['comparisons'].for_candidate(candidate, actor='reader')['parent']
        result = store.record_check(t['tree']['task_id'], proposal, baseline_run=[], candidate_run=[],
            flips=dict(admission=comparison['admission']['status'], rows=comparison['rows']),
            leak=None, actor='checker', comparison_id=comparison['id'])
        return comparison, result
    comparison, checked = check()
    assert checked['verification']['verdict']['verdict'] == 'accept'
    first = service.candidate(candidate)['decision']
    assert first['decision'] == 'adopted' and first['actor'] == 'gate'
    assert t['comparisons'].get(comparison['id'])['verdict'] == first['gate_verdict']
    with pytest.raises(ValueError, match='note'):
        service.decide(candidate, 'rejected', actor='human')
    overridden = service.decide(candidate, 'rejected', actor='human', note='Review found a shortcut')['decision']
    assert overridden['gate_verdict'] == first['gate_verdict']
    check()
    assert service.candidate(candidate)['decision'] == overridden
    ran(t, candidate, {'a': 1, 'b': 1}, 'failure', state='failed')
    newer, _ = check()
    assert newer['id'] != comparison['id']
    assert newer['infrastructure']['subject']['failed'] > 0
    assert t['comparisons'].get(comparison['id'])['verdict'] == first['gate_verdict']


def test_failed_only_candidate_gets_unmeasurable_record(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    ran(t, root, {'a': 0, 'b': 0}, 'base')
    candidate = child(t, root, 'failed', {'a': 1, 'b': 1}, state='failed')
    record = t['comparisons'].for_candidate(candidate, actor='reader')['parent']
    assert record['id'] and record['rows'] == [] and record['mean']['delta'] is None
    assert gate.judge(record, record.get('noise') or {}, dict(predicted_flips=['a'], guards=[]),
                      gate.DEFAULT_RULE)['verdict'] == 'unmeasurable'
