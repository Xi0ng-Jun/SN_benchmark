"""Parent/root comparison records over real iteration trees; runs and flips are stubbed."""
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_eval.workbench.comparisons import Admission, ComparisonService
from test_workbench_iterations import BUDGET, approved, env, finish, iteration  # noqa: F401  (fixture)


class Views:
    """execution_runs/admission/flips over per-execution case scores."""

    def __init__(self):
        self.scores, self.partitions, self.bad_runs, self.unpaired = {}, {}, set(), set()
        self.flip_calls, self.admission_calls = [], []

    def execution_runs(self, execution_id):
        return [SimpleNamespace(id=f'{execution_id}-{i}', suite='qasper', mode='chunk', case_ids=tuple(cases))
                for i, cases in enumerate(self.partitions.get(execution_id, [('a', 'b')]), 1)]

    def admission(self, run_id):
        self.admission_calls.append(run_id)
        return dict(status='not_admitted', reason='3/3 error') if run_id in self.bad_runs else dict(
            status='admitted', reason=None)

    def flips(self, left, right):
        self.flip_calls.append((left, right))
        base = dict(left_run=left, right_run=right, metric='answer_f1', rows=[])
        if (left, right) in self.unpaired:
            return dict(base, admission='not_admitted', note='scorer identity differs')
        before, after = self.scores[left.rsplit('-', 1)[0]], self.scores[right.rsplit('-', 1)[0]]
        for case in sorted(before):
            delta = after[case] - before[case]
            base['rows'].append(dict(case_id=case, left=before[case], right=after[case], delta=delta,
                                     flip='better' if delta > 0 else 'worse' if delta < 0 else 'same'))
        return dict(base, admission='admitted', note=None)


@pytest.fixture
def tree(env):  # noqa: F811
    views = Views()
    env['service'].admission = Admission(views)
    return dict(env=env, views=views, tree=iteration(env),
                comparisons=ComparisonService(env['repo'], env['service'], views))


def ran(t, candidate_id, scores, key, *, state='finished'):
    service = t['env']['service']
    attempt = service.run(candidate_id, budget=BUDGET, actor='alice', request_key=key)['attempts'][-1]
    t['views'].scores[attempt['execution_id']] = scores
    if state == 'finished':
        finish(t['env'], service.candidate(candidate_id))
    elif state == 'failed':
        t['env']['executions']._finish(attempt['execution_id'], state='failed', exit_code=1,
                                       error_type='RuntimeError', run_state=dict(phase='failed'))
    return attempt


def child(t, parent_id, key, scores, *, state='finished'):
    service = t['env']['service']
    candidate = service.derive(parent_id, proposal_id=approved(t['env'], t['tree'], parent_id), actor='alice',
                               request_key=key)
    Path(candidate['worktree']['path'], 'backend/app/__init__.py').write_text(f'VALUE = "{key}"\n')
    service.freeze(candidate['id'], actor='alice')
    ran(t, candidate['id'], scores, key + '-run', state=state)
    return candidate['id']


def rows(t):
    with t['env']['repo'].connect() as conn:
        return conn.execute('SELECT COUNT(*) FROM candidate_comparisons').fetchone()[0]


def test_reference_is_the_first_admitted_attempt_and_later_attempts_never_move_it(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    ran(t, root, {'a': .9, 'b': .9}, 'base-1', state='failed')
    unadmitted = ran(t, root, {'a': .9, 'b': .9}, 'base-2')
    t['views'].bad_runs.add(unadmitted['execution_id'] + '-1')
    first = ran(t, root, {'a': .2, 'b': .2}, 'base-3')
    ran(t, root, {'a': .8, 'b': .8}, 'base-4')
    view = t['env']['service'].candidate(root)
    assert view['state'] == 'evaluated'
    assert view['reference'] == dict(attempt=3, execution_id=first['execution_id'])
    assert [a['admission']['status'] for a in view['attempts']] == [
        'not_admitted', 'not_admitted', 'admitted', 'admitted']
    assert view['attempts'][1]['admission']['reason'].endswith('3/3 error')
    # Terminal executions are judged once.
    t['env']['service'].candidate(root)
    assert len(t['views'].admission_calls) == 3


def test_a_child_of_the_root_has_one_immutable_record_for_both_columns(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    ran(t, root, {'a': .5, 'b': .5, 'c': .5}, 'base')
    a = child(t, root, 'a', {'a': .7, 'b': .5, 'c': .4})
    first = t['comparisons'].for_candidate(a, actor='alice')
    parent = first['parent']
    assert parent['admission']['status'] == 'admitted' and parent['metric'] == 'answer_f1'
    assert parent['counts'] == dict(better=1, worse=1, same=1, unpaired=0)
    assert parent['mean']['delta'] == pytest.approx(.1 / 3) and parent['regressed'] == ['c']
    assert parent['reference']['candidate_id'] == root and parent['subject']['attempt'] == 1
    assert first['root'] == dict(kind='root', status='same_as_parent', comparison_id=parent['id'])
    again = t['comparisons'].for_candidate(a, actor='alice')
    assert again['parent'] == parent and len(t['views'].flip_calls) == 1 and rows(t) == 1
    assert t['comparisons'].get(parent['id']) == parent
    assert t['comparisons'].for_candidate(root, actor='alice')['parent']['status'] == 'none'


def test_a_grandchild_records_parent_and_root_separately(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    ran(t, root, {'a': .5, 'b': .5}, 'base')
    a = child(t, root, 'a', {'a': .9, 'b': .6})
    b = child(t, a, 'b', {'a': .8, 'b': .4})
    both = t['comparisons'].for_candidate(b, actor='alice')
    assert both['parent']['reference']['candidate_id'] == a and both['parent']['regressed'] == ['a', 'b']
    assert both['root']['reference']['candidate_id'] == root and both['root']['regressed'] == ['b']
    assert both['root']['counts'] == dict(better=1, worse=1, same=0, unpaired=0)
    assert both['parent']['content_hash'] != both['root']['content_hash']


def test_an_unevaluated_side_is_unavailable_and_nothing_is_stored(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    ran(t, root, {'a': .5, 'b': .5}, 'base')
    a = child(t, root, 'a', {'a': .6, 'b': .6}, state='running')
    parent = t['comparisons'].for_candidate(a, actor='alice')['parent']
    assert parent['status'] == 'unavailable' and a in parent['reason'] and rows(t) == 0
    finish(t['env'], t['env']['service'].candidate(a))
    assert t['comparisons'].for_candidate(a, actor='alice')['parent']['admission']['status'] == 'admitted'


def test_a_pair_that_fails_admission_is_recorded_without_rows(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    base = ran(t, root, {'a': .5, 'b': .5}, 'base')
    a = child(t, root, 'a', {'a': .9, 'b': .9})
    subject = t['env']['service'].candidate(a)['reference']['execution_id']
    t['views'].unpaired.add((base['execution_id'] + '-1', subject + '-1'))
    parent = t['comparisons'].for_candidate(a, actor='alice')['parent']
    assert parent['admission']['status'] == 'not_admitted' and 'scorer identity differs' in parent['admission']['reason']
    assert parent['rows'] == [] and parent['mean']['delta'] is None and rows(t) == 1


def test_partition_sets_must_match(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    ran(t, root, {'a': .5, 'b': .5}, 'base')
    service = t['env']['service']
    a = service.derive(root, proposal_id=approved(t['env'], t['tree'], root), actor='alice', request_key='a')['id']
    Path(service.candidate(a)['worktree']['path'], 'backend/app/__init__.py').write_text('VALUE = 9\n')
    service.freeze(a, actor='alice')
    attempt = service.run(a, budget=BUDGET, actor='alice', request_key='a-run')['attempts'][-1]
    t['views'].partitions[attempt['execution_id']] = [('a',), ('b',)]
    finish(t['env'], service.candidate(a))
    parent = t['comparisons'].for_candidate(a, actor='alice')['parent']
    assert parent['admission'] == dict(status='not_admitted', reason='两端的分区集合不一致')
    assert t['views'].flip_calls == []


def test_a_pinned_comparison_is_computed_but_never_stored(tree):
    t, root = tree, tree['tree']['root_candidate_id']
    ran(t, root, {'a': .5, 'b': .5}, 'base')
    a = child(t, root, 'a', {'a': .9, 'b': .5})
    b = child(t, root, 'b', {'a': .5, 'b': .7})
    pinned = t['comparisons'].pinned(b, a)
    assert pinned['kind'] == 'pinned' and pinned['stored'] is False and 'id' not in pinned
    assert pinned['counts'] == dict(better=1, worse=1, same=0, unpaired=0) and rows(t) == 0
