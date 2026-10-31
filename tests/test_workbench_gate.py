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


# ---------------------------------------------------------------- lcd-v2
SAFEGUARDS = [dict(key='answer_language_match', direction='higher_is_better', margin=.02),
              dict(key='infrastructure_failure_rate', direction='lower_is_better', margin=.05),
              dict(key='cost_per_task', direction='lower_is_better', margin=.005)]


def paired(cases, *, eta=None):
    """cases: {case_id: (reference attempts, subject attempts)}; a validation-pool comparison."""
    noise = {side: estimate(dict(attempts=[dict(execution_id=f'{side}-{i}', rows=[
        dict(case_id=c, family='1', value=v[j][i]) for c, v in cases.items()]) for i in range(len(next(iter(cases.values()))[j]))]))
        for j, side in enumerate(('reference', 'subject'))}
    noise['eta'] = eta if eta is not None else noise['reference']['eta']
    rows = [dict(case_id=c, left=noise['reference']['cases'][c]['mean'], right=noise['subject']['cases'][c]['mean'],
                 role='validation') for c in cases]
    for r in rows:
        r['delta'] = r['right'] - r['left']
    delta = sum(r['delta'] for r in rows) / len(rows)
    return dict(rows=rows, noise=noise, admission=dict(status='admitted'), mean=dict(delta=delta),
                validation=dict(n=len(rows), delta=delta, eta=noise['eta'], incomplete=False),
                infrastructure={s: dict(failed=0, total=2 * len(rows), unobserved=0) for s in ('reference', 'subject')},
                safeguards=dict(answer_language_match=dict(reference=.97, subject=.98),
                                cost_per_task=dict(reference=.010, subject=.011)))


def r2d_candidate_a():
    """R2d candidate A's shape: proportional str_em, k=2, 8 predicted flips of which 6 cross .5."""
    t, tt = 1 / 3, 2 / 3
    cases = {'f0': ([0, 0], [1, 1])}
    cases.update({f'f{i}': ([t, t], [tt, tt]) for i in range(1, 6)})
    cases.update({f'f{i}': ([0, 0], [t, t]) for i in (6, 7)})
    cases.update({f'g{i}': ([.5, .5], [.5, .5]) for i in range(4)})
    cases.update({f'u{i}': ([t, tt], [tt, tt]) for i in range(40)})
    cases.update({f'w{i}': ([tt, tt], [t, tt]) for i in range(7)})
    cases.update({f'z{i}': ([i % 2, i % 2], [i % 2, i % 2]) for i in range(16)})
    contract = dict(predicted_flips=[f'f{i}' for i in range(8)], guards=[f'g{i}' for i in range(4)], tau=.5)
    return paired(cases, eta=.0204), contract


def test_r2d_candidate_a_is_audit_under_v1_but_holds_under_v2():
    comparison, contract = r2d_candidate_a()
    v1 = gate.judge(comparison, comparison['noise'], contract, gate.DEFAULT_RULE)
    crossed = [f for f in v1['flips'] if f['left'] < .5 <= f['right']]
    assert len(crossed) == 6 and v1['rho'] == .125 and round(v1['delta'], 3) == .118 and v1['eta'] == .0204
    assert v1['verdict'] == 'audit' and v1['reasons'] == ['gain_outside_contract']
    screened = gate.screen(comparison, gate.RULE_V2, safeguards=SAFEGUARDS)
    confirmed = gate.confirm(comparison, gate.RULE_V2, safeguards=SAFEGUARDS)
    assert screened['verdict'] == 'pass' and confirmed['verdict'] == 'holds'
    assert round(confirmed['delta'], 3) == .118 and confirmed['n'] == 75 and confirmed['k'] == 2
    assert confirmed['lower'] == pytest.approx(confirmed['delta'] - 1.65 * confirmed['se']) and confirmed['lower'] > 0
    assert confirmed['evidence']['paired']['deltas']['f1'] == pytest.approx(1 / 3)
    assert [s['breached'] for s in confirmed['safeguards']] == [False, False, False]


def gains(values):
    return paired({f'c{i}': ([.5], [.5 + g]) for i, g in enumerate(values)})


def test_v2_screen_and_confirm_three_states_and_unmeasurable():
    weak = gains([.3, -.1, .2, 0., .1, -.2, .25, .05, .1, 0.])
    assert gate.screen(weak, gate.RULE_V2, safeguards=SAFEGUARDS)['verdict'] == 'pass'
    assert gate.confirm(weak, gate.RULE_V2, safeguards=SAFEGUARDS)['verdict'] == 'insufficient_evidence'
    worse = gains([-.3, -.2, -.25, -.1, -.3, -.2])
    assert gate.screen(worse, gate.RULE_V2, safeguards=SAFEGUARDS)['verdict'] == 'fail'
    result = gate.confirm(worse, gate.RULE_V2, safeguards=SAFEGUARDS)
    assert result['verdict'] == 'does_not_hold' and result['upper'] <= 0 and result['stage'] == 'confirm'
    better = gains([.3, .2, .25, .1, .3, .2])
    assert gate.confirm(better, gate.RULE_V2, safeguards=SAFEGUARDS)['verdict'] == 'holds'
    # Safeguards win over a positive bound: breach, unobserved, infrastructure derived from counts.
    better['safeguards']['cost_per_task']['subject'] = .02
    result = gate.confirm(better, gate.RULE_V2, safeguards=SAFEGUARDS)
    assert result['verdict'] == 'unmeasurable' and result['reasons'] == ['safeguard_breached:cost_per_task']
    assert result['lower'] > 0 and result['safeguards'][2]['change'] == pytest.approx(.01)
    better['safeguards'].pop('answer_language_match')
    better['infrastructure']['subject']['failed'] = 3
    assert gate.screen(better, gate.RULE_V2, safeguards=SAFEGUARDS)['reasons'] == [
        'safeguard_unobserved:answer_language_match', 'safeguard_breached:infrastructure_failure_rate',
        'safeguard_breached:cost_per_task']
    unpaired = gains([.3, .2])
    unpaired['admission'] = dict(status='not_admitted')
    unpaired['rows'][0].update(right=None, delta=None)
    assert gate.confirm(unpaired, gate.RULE_V2, safeguards=SAFEGUARDS)['reasons'] == [
        'pair_not_admitted', 'incomplete_pairs', 'insufficient_paired_cases']


def test_v2_rule_and_safeguard_slots_are_closed():
    comparison = gains([.1, .2])
    for rule in [gate.DEFAULT_RULE, dict(gate.RULE_V2, z_confirm=0), dict(gate.RULE_V2, statistic='binary'),
                 dict(gate.RULE_V2, extra=1)]:
        with pytest.raises(ValueError, match='Unsupported gate rule'):
            gate.confirm(comparison, rule, safeguards=SAFEGUARDS)
    with pytest.raises(ValueError, match='Unsupported gate rule'):
        gate.judge(comparison, comparison['noise'], dict(predicted_flips=['c0'], guards=[]), gate.RULE_V2)
    assert gate.DEFAULT_RULE == dict(id='lcd-v1', tau=.5, eta='rerun_se', band='jeffreys90')
    for bad in [SAFEGUARDS[1:], SAFEGUARDS[:2], [dict(SAFEGUARDS[0], margin=-1)] + SAFEGUARDS[1:],
                [dict(SAFEGUARDS[0], direction='up')] + SAFEGUARDS[1:], SAFEGUARDS + SAFEGUARDS[:1], None]:
        with pytest.raises(ValueError, match='Safeguards'):
            gate.screen(comparison, gate.RULE_V2, safeguards=bad)


ABSTAIN = dict(kind='regex', field='response.answer', pattern='(?i)cannot answer')
MECHANISM = dict(slice_predicate=ABSTAIN, manipulation=dict(direction='decrease', threshold=.5),
                 slice_effect=dict(direction='positive', magnitude=[.05, .3]))


def test_mechanism_contract_is_strict():
    out = gate.validate_mechanism(MECHANISM)
    assert out['manipulation'] == dict(feature=ABSTAIN, direction='decrease', threshold=.5)
    assert gate.validate_mechanism(dict(MECHANISM, slice_effect=dict(direction='positive')))['slice_effect']['magnitude'] is None
    nested = {'all_of': [ABSTAIN, {'not': dict(kind='range', field='usage.total_tokens', min=5000)}]}
    assert gate.validate_mechanism(dict(MECHANISM, slice_predicate=nested))['slice_predicate'] == nested
    bad = [dict(MECHANISM, extra=1), {k: v for k, v in MECHANISM.items() if k != 'slice_effect'},
           dict(MECHANISM, slice_predicate=dict(kind='range', field='metrics.answer_f1', max=.5)),
           dict(MECHANISM, slice_predicate=dict(kind='range', field='deterministic.evidence_hit', max=0)),
           dict(MECHANISM, slice_predicate=dict(kind='regex', field='case_id', pattern='x')),
           dict(MECHANISM, slice_predicate={'any_of': [ABSTAIN, dict(kind='range', field='stage_metrics.gold_evidence_recall.value', max=0)]}),
           dict(MECHANISM, manipulation=dict(direction='down', threshold=.5)),
           dict(MECHANISM, manipulation=dict(direction='decrease', threshold=1.5)),
           dict(MECHANISM, manipulation=dict(direction='decrease', threshold=True)),
           dict(MECHANISM, manipulation=dict(direction='decrease', threshold=.5, feature='P')),
           dict(MECHANISM, slice_effect=dict(direction='negative')),
           dict(MECHANISM, slice_effect=dict(direction='positive', magnitude=[.3, .1])),
           dict(MECHANISM, slice_effect=dict(direction='positive', magnitude=[float('nan'), .1]))]
    for value in bad:
        with pytest.raises(ValueError, match='contract invalid'):
            gate.validate_mechanism(value)


def traced(comparison, abstains_parent, abstains_child):
    said = lambda yes: [dict(response=dict(answer='I cannot answer' if yes else 'Paris'))] * 2
    comparison['trajectories'] = {side: {r['case_id']: said(r['case_id'] in ids) for r in comparison['rows']}
                                  for side, ids in (('reference', abstains_parent), ('subject', abstains_child))}
    return comparison


def test_attribution_labels_never_change_the_verdict():
    values = [.4, .3, .4, .5, .3, .4, 0., .05, -.05, 0., .05, -.05]
    in_slice = {f'c{i}' for i in range(6)}
    supported = traced(gains(values), in_slice, set())
    result = gate.confirm(supported, gate.RULE_V2, safeguards=SAFEGUARDS, mechanism=MECHANISM)
    label = result['attribution']
    assert label['label'] == 'supported' and label['slice_size'] == 6 and label['outside_size'] == 6
    assert (label['manipulation_rate_parent'], label['manipulation_rate_candidate']) == (1, 0)
    assert label['delta_slice'] == pytest.approx(.38333, abs=1e-4) and label['delta_outside'] == pytest.approx(0)
    # The manipulation did not happen: refuted, same verdict.
    refuted = gate.confirm(traced(gains(values), in_slice, in_slice), gate.RULE_V2, safeguards=SAFEGUARDS, mechanism=MECHANISM)
    assert refuted['attribution']['label'] == 'refuted' and refuted['attribution']['manipulated'] is False
    assert refuted['verdict'] == result['verdict'] == 'holds'
    assert {k: v for k, v in refuted.items() if k != 'attribution'} == {k: v for k, v in result.items() if k != 'attribution'}
    # Gain mostly outside the slice: refuted.
    outside = gate.attribute(traced(gains(values[6:] + values[:6]), {'c0', 'c1', 'c2', 'c3', 'c4', 'c5', 'c6'}, set()), MECHANISM)
    assert outside['label'] == 'refuted' and outside['delta_slice'] > 0
    # Small slice and missing parent trajectories are insufficient.
    assert gate.attribute(traced(gains(values), {'c0'}, set()), MECHANISM)['label'] == 'insufficient'
    missing = traced(gains(values), in_slice, set())
    missing['trajectories']['reference'].pop('c9')
    assert gate.attribute(missing, MECHANISM)['unobserved'] == ['c9']
    assert gate.attribute(missing, MECHANISM)['label'] == 'insufficient'
    assert gate.confirm(supported, gate.RULE_V2, safeguards=SAFEGUARDS)['attribution'] is None


def test_card_contract_dispatches_on_gate_rule():
    from rag_eval.workbench.hypotheses import validate_card
    from test_workbench_roles_hypotheses import CARD
    v1 = dict(CARD, contract=dict(predicted_flips=['a'], guards=[]))
    v2 = dict(CARD, contract=MECHANISM)
    assert validate_card(v1)['contract']['predicted_flips'] == ['a']
    assert validate_card(v2, gate.RULE_V2)['contract']['manipulation']['feature'] == ABSTAIN
    with pytest.raises(ValueError, match='contract invalid'):
        validate_card(v1, gate.RULE_V2)
    with pytest.raises(ValueError, match='predicted_flips'):
        validate_card(v2, gate.DEFAULT_RULE)
    with pytest.raises(ValueError, match='Unsupported gate rule'):
        validate_card(v2, dict(id='lcd-v9'))


def test_draft_refuses_contract_ids_the_candidate_cannot_see(tree):
    import json
    from rag_eval.workbench.hypotheses import HypothesisStore
    from test_workbench_roles_hypotheses import CARD
    repo, task_id = tree['env']['repo'], tree['tree']['task_id']
    def protocol(**change):
        with repo.connect(write=True) as conn:
            p = json.loads(conn.execute('SELECT protocol_json FROM iterations WHERE id=?', (tree['tree']['id'],)).fetchone()[0])
            p.update(change)
            p['splits'].update(search=['a', 'case_5'], validation=['b', 'case_50'])
            conn.execute('UPDATE iterations SET protocol_json=? WHERE id=?', (json.dumps(p), tree['tree']['id']))
    protocol()
    store = HypothesisStore(repo)
    for flips in (['[withheld]5'], ['case_50'], ['b']):
        with pytest.raises(ValueError, match='contract invalid'):
            store.draft(task_id, dict(CARD, contract=dict(predicted_flips=flips, guards=[])), author='agent')
    assert store.draft(task_id, dict(CARD, contract=dict(predicted_flips=['case_5'], guards=['a'])), author='agent')['status'] == 'draft'
    protocol(gate_rule=gate.RULE_V2)
    with pytest.raises(ValueError, match='contract invalid'):
        store.draft(task_id, dict(CARD, contract=dict(predicted_flips=['case_5'], guards=[])), author='agent')
    assert store.draft(task_id, dict(CARD, contract=MECHANISM), author='agent')['card']['contract'] == gate.validate_mechanism(MECHANISM)
