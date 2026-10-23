"""Synthetic arithmetic and privacy contracts for the LCD ledgers."""
from copy import deepcopy
import json
import math
from types import SimpleNamespace

import pytest

from rag_eval.workbench.ledger import LedgerService, LedgerStore, aggregate_ledger
from rag_eval.workbench.repository import WorkbenchRepository


def fixture():
    pool = [dict(batch_id='base', selection_hash='base-hash', n=2)]
    tree = dict(id='iteration1', task_id='task1', root_candidate_id='seed',
        protocol=dict(splits=dict(search=['search-secret'], validation=['val-a', 'val-b'], sealed=['sealed-secret']),
                      gate_rule=dict(tau=.5)),
        candidates=[dict(id='seed', frozen_commit='commit0'),
                    dict(id='a', frozen_commit='commit1', proposal_id='h1', decision=dict(decision='adopted')),
                    dict(id='alias', frozen_commit='commit1'), dict(id='b', frozen_commit='commit2', proposal_id='h2')])
    def record(name, subject, commit, delta):
        return dict(id=name, kind='parent', candidate_id=subject, reference_candidate_id='seed',
            created_at='2026-10-24T10:00:00', frozen_commits=dict(subject=commit, reference='commit0'),
            admission=dict(status='admitted'), validation=dict(n=2, delta=delta, eta=.25, incomplete=False,
                pool=deepcopy(pool), batches=[dict(pool[0], reference=.4, subject=.4+delta, delta=delta, eta=.25, incomplete=False)]),
            noise=dict(reference=dict(cases={'val-a':dict(p=.5, family='p1'), 'val-b':dict(p=.5, family='p2')})),
            verdict=dict(rho=1, tau=.5, verdict='accept'), rows=[dict(case_id='val-a', gold='private gold')])
    records = [record('c1', 'a', 'commit1', .2), record('c2', 'alias', 'commit1', .3), record('c3', 'b', 'commit2', .1)]
    hypotheses = [dict(id='h1', card=dict(contract=dict(cluster_id='cl1', predicted_flips=['search-secret'], guards=[]))),
                  dict(id='h2', card=dict(contract=dict(cluster_id='cl2', predicted_flips=['other-search'], guards=[])))]
    clusters = [dict(cluster_id='cl1', frozen_at='2026-10-24T09:00:00', scan=dict(members=['search-secret'], q=.2, m=.5, v=.25, n_cases=1)),
                dict(cluster_id='cl2', frozen_at='2026-10-24T09:00:00', scan=dict(members=['other-search'], q=.3, m=.4, v=.2, n_cases=1))]
    return tree, records, hypotheses, clusters


def test_arithmetic_commit_deduplication_and_no_hidden_details():
    tree, records, cards, clusters = fixture()
    before = deepcopy((tree, records, cards, clusters))
    out = aggregate_ledger(tree, records, cards, clusters, seed_search_mean=.625)
    assert set(out) == {'sampling_ledger', 'overfit_ledger', 'structure_ledger', 'sealed_score'}
    assert out['sampling_ledger'] == dict(k=1, verifier='none', seed_search_mean=.625, S_B=.625)
    overfit = out['overfit_ledger']
    assert overfit['n'] == 2 and overfit['n_eff'] == 2 and overfit['K'] == 3
    assert overfit['expected_bias'] == pytest.approx(.25 * math.sqrt(2*math.log(3)))
    assert out['sealed_score'] is None
    serialized = json.dumps(out)
    for private in ('val-a', 'val-b', 'sealed-secret', 'search-secret', 'private gold', 'members', 'predicted_flips'):
        assert private not in serialized
    assert (tree, records, cards, clusters) == before


def test_fresh_deltas_pair_only_matching_batch_and_frozen_sources():
    tree, records, cards, clusters = fixture()
    fresh_pool = dict(batch_id='fresh1', selection_hash='fresh-hash', n=4)
    fresh = dict(id='f1', kind='fresh', candidate_id='a', reference_candidate_id='seed', batch_id='fresh1',
        frozen_commits=dict(subject='commit1', reference='commit0'), admission=dict(status='admitted'),
        mean=dict(delta=.05), attempts_used=dict(subject=[dict(execution_id='fresh-a')], reference=[dict(execution_id='fresh-seed')]), rows=[dict(case_id=f'fresh-secret-{i}', delta=.05) for i in range(4)])
    # The acceptance pool differs; show it, but do not subtract its aggregate delta.
    records.append(fresh)
    tree['protocol']['fresh_batches'] = [dict(fresh_pool, case_ids=[f'fresh-secret-{i}' for i in range(4)])]
    result = aggregate_ledger(tree, records, cards, clusters)['overfit_ledger']['accepted'][0]
    assert result['fresh'][0]['delta'] == .05
    assert result['fresh'][0]['observed_bias'] is None
    assert result['fresh'][0]['reason'] == 'no_matching_validation_batch'
    # A later comparison contains a measurement of exactly the fresh batch.
    later = deepcopy(records[0])
    later['id'] = 'later'
    later['attempts_used'] = dict(subject=[dict(execution_id='later-a')], reference=[dict(execution_id='later-seed')])
    later['validation']['pool'].append(fresh_pool)
    later['validation']['batches'].append(dict(fresh_pool, delta=.08, incomplete=False))
    later['validation']['n'] = 6
    records.append(later)
    paired = aggregate_ledger(tree, records, cards, clusters)['overfit_ledger']['accepted'][0]['fresh'][0]
    assert paired['validation_delta'] == .08 and paired['observed_bias'] == pytest.approx(.03)
    later['attempts_used'] = deepcopy(fresh['attempts_used'])
    same = aggregate_ledger(tree, records, cards, clusters)['overfit_ledger']['accepted'][0]['fresh'][0]
    assert same['observed_bias'] is None and same['reason'] == 'same_measurements'
    later['frozen_commits']['reference'] = 'different-source'
    assert aggregate_ledger(tree, records, cards, clusters)['overfit_ledger']['accepted'][0]['fresh'][0]['observed_bias'] is None


def test_structure_requires_accepted_candidate_later_matching_cluster_evidence():
    tree, records, cards, clusters = fixture()
    # Contract rho itself never resolves a frozen cluster.
    result = aggregate_ledger(tree, records, cards, clusters)['structure_ledger']
    assert [c['status'] for c in result['clusters']] == ['open', 'open']
    local = dict(id='local', kind='cluster', candidate_id='a', admission=dict(status='admitted'),
        created_at='2026-10-24T11:00:00', cluster=dict(cluster_id='cl1', rho=.75, tau=.5),
        rows=[dict(case_id='search-secret', left=0, right=1, delta=1)],
        noise={side: dict(k_base=4, cases={'search-secret': dict(n=4, successes=success)})
               for side, success in [('reference', 0), ('subject', 4)]})
    records.append(local)
    records[2]['verdict']['verdict'] = 'audit'
    result = aggregate_ledger(tree, records, cards, clusters)['structure_ledger']
    assert [c['status'] for c in result['clusters']] == ['resolved', 'open']
    assert result['audit_count'] == 1
    assert result['wishlist'] == [dict(cluster_id='cl2', q=.3, m=.4, v=.2)]
    assert result['contracts'][0]['rho'] == 1
    local['created_at'] = '2026-10-24T08:00:00'
    assert aggregate_ledger(tree, records, cards, clusters)['structure_ledger']['clusters'][0]['status'] == 'open'


def test_legacy_unknown_and_incomplete_scores_remain_unknown():
    tree, records, cards, clusters = fixture()
    for record in records:
        record['validation'].pop('pool')
        record['validation'].pop('batches')
        record.pop('frozen_commits')
    result = aggregate_ledger(tree, records, cards, clusters)
    assert result['overfit_ledger']['pool'] is None
    assert result['overfit_ledger']['expected_bias'] is None
    assert result['overfit_ledger']['K'] is None
    records[0]['validation']['incomplete'] = True
    assert aggregate_ledger(tree, records[:1], cards, clusters)['overfit_ledger']['eta'] is None
    assert aggregate_ledger(dict(id='old', candidates=[]), [], [], [])['sampling_ledger']['S_B'] is None


def test_store_rejects_traversal_and_symlink_escape(tmp_path):
    store = LedgerStore(tmp_path / 'state')
    body = aggregate_ledger(dict(candidates=[]), [], [], [])
    store.write('iteration1', body)
    saved = tmp_path / 'state/ledgers/iteration1.json'
    assert json.loads(saved.read_text()) == body
    assert saved.stat().st_mode & 0o777 == 0o600
    for identity in ('../escape', '/absolute', '.', 'bad/name'):
        with pytest.raises(ValueError):
            store.write(identity, body)
    other = tmp_path / 'other'
    other.mkdir()
    (saved.parent / 'linked.json').symlink_to(other / 'outside.json')
    with pytest.raises(ValueError):
        store.write('linked', body)
    (saved.parent / 'temp.json.tmp').symlink_to(other / 'outside.json')
    with pytest.raises(ValueError):
        store.write('temp', body)
    escaped = tmp_path / 'escaped'
    escaped.mkdir()
    (escaped / 'ledgers').symlink_to(other)
    with pytest.raises(ValueError):
        LedgerStore(escaped).write('safe', body)


def test_service_reads_saved_records_and_optional_sealed_score_without_dispatch(tmp_path):
    repo = WorkbenchRepository(tmp_path / 'state')
    tree, records, cards, clusters = fixture()
    # Empty source executions mean the seed score is unknown; reading cannot launch one.
    iterations = SimpleNamespace(iteration=lambda identity: tree)
    views = SimpleNamespace(execution_runs=lambda identity: pytest.fail('no execution should be read'))
    service = LedgerService(repo, iterations, views)
    result = service.read('iteration1')
    assert result['sealed_score'] is None and result['sampling_ledger']['S_B'] is None
    with repo.connect(write=True) as conn:
        conn.execute('CREATE TABLE iteration_sealed_claims(iteration_id TEXT, score_json TEXT)')
        conn.execute('INSERT INTO iteration_sealed_claims VALUES(?,?)', ('iteration1', json.dumps(dict(mean=.7, n=2))))
    assert service.read('iteration1')['sealed_score'] == dict(mean=.7, n=2)
    assert json.loads((repo.root / 'ledgers/iteration1.json').read_text())['sealed_score'] == dict(mean=.7, n=2)


def test_sealed_summary_does_not_copy_case_or_gold_details():
    result = aggregate_ledger(dict(candidates=[]), [], [], [], sealed_score=dict(
        status='finished', metric='answer_f1', n=1, mean=.7, rows=[dict(case_id='hidden', gold='private')]))
    assert result['sealed_score'] == dict(status='finished', metric='answer_f1', n=1, mean=.7)


def test_contract_subset_cannot_resolve_unmeasured_frozen_members():
    tree, records, cards, clusters = fixture()
    clusters[0]['scan']['members'].append('unmeasured-member')
    records.append(dict(id='local', kind='cluster', candidate_id='a', admission=dict(status='admitted'),
        created_at='2026-10-24T11:00:00', cluster=dict(cluster_id='cl1', rho=1, tau=.5),
        rows=[dict(case_id='search-secret', left=0, right=1, delta=1)],
        noise={side: dict(k_base=4, cases={'search-secret': dict(n=4, successes=success)})
               for side, success in [('reference', 0), ('subject', 4)]}))
    assert aggregate_ledger(tree, records, cards, clusters)['structure_ledger']['clusters'][0]['status'] == 'open'


def test_large_cluster_resolution_is_not_limited_by_contract_card_size():
    tree, records, cards, clusters = fixture()
    members = [f'search-{i}' for i in range(201)]
    clusters[0]['scan']['members'] = members
    records.append(dict(id='large', kind='cluster', candidate_id='a', admission=dict(status='admitted'),
        created_at='2026-10-24T11:00:00', cluster=dict(cluster_id='cl1', rho=1, tau=.5),
        rows=[dict(case_id=cid, left=0, right=1, delta=1) for cid in members],
        noise={side: dict(k_base=4, cases={cid: dict(n=4, successes=success) for cid in members})
               for side, success in [('reference', 0), ('subject', 4)]}))
    assert aggregate_ledger(tree, records, cards, clusters)['structure_ledger']['clusters'][0]['status'] == 'resolved'


def test_sampling_k_one_uses_first_admitted_base_search_attempt(tree):
    from test_workbench_comparisons import ran
    fixture_tree = tree
    root = fixture_tree['tree']['root_candidate_id']
    ran(fixture_tree, root, {'a': .2, 'b': .4}, 'seed1')
    ran(fixture_tree, root, {'a': .8, 'b': 1.}, 'seed2')
    service = fixture_tree['env']['service']
    frozen = service.iteration(fixture_tree['tree']['id'])
    frozen['protocol'] = dict(frozen.get('protocol') or {}, splits=dict(search=['a'], validation=['b']))
    iterations = SimpleNamespace(iteration=lambda identity: frozen)
    ledger = LedgerService(fixture_tree['env']['repo'], iterations, fixture_tree['views']).read(frozen['id'])
    assert ledger['sampling_ledger']['seed_search_mean'] == pytest.approx(.2)


from test_workbench_comparisons import tree  # noqa: E402,F401  synthetic fixture dependency
from test_workbench_iterations import env  # noqa: E402,F401


def test_unimplemented_contract_is_reported_unknown_and_cannot_resolve_cluster():
    tree, records, cards, clusters = fixture()
    cards.append(dict(id='pending', card=dict(contract=dict(cluster_id='cl1', predicted_flips=['search-secret'], guards=[]))))
    contracts = aggregate_ledger(tree, records, cards, clusters)['structure_ledger']['contracts']
    assert contracts[-1] == dict(hypothesis_id='pending', candidate_id=None, cluster_id='cl1', comparison_id=None,
                                 rho=None, tau=None, verdict=None)


def test_fresh_pool_requires_actual_rows_to_match_immutable_batch():
    tree, records, cards, clusters = fixture()
    tree['protocol']['fresh_batches'] = [dict(batch_id='fresh1', selection_hash='fresh-hash', case_ids=['expected'])]
    records.append(dict(id='wrong', kind='fresh', candidate_id='a', reference_candidate_id='seed', batch_id='fresh1',
        frozen_commits=dict(subject='commit1', reference='commit0'), admission=dict(status='admitted'),
        mean=dict(delta=.9), rows=[dict(case_id='different', delta=.9)]))
    fresh = aggregate_ledger(tree, records, cards, clusters)['overfit_ledger']['accepted'][0]['fresh'][0]
    assert fresh['delta'] is None and fresh['observed_bias'] is None
    assert fresh['reason'] == 'incomplete_fresh_batch'


def test_integrated_candidate_full_comparison_can_resolve_frozen_cluster():
    tree, records, cards, clusters = fixture()
    tree['candidates'].append(dict(id='integrated', frozen_commit='integrated-commit', decision=dict(decision='adopted')))
    records.append(dict(id='full', kind='parent', candidate_id='integrated', reference_candidate_id='seed',
        admission=dict(status='admitted'), created_at='2026-10-24T11:00:00', verdict=dict(tau=.5, verdict='accept'),
        rows=[dict(case_id='search-secret', left=0, right=1, delta=1)],
        noise={side: dict(k_base=4, cases={'search-secret': dict(n=4, successes=success)})
               for side, success in [('reference', 0), ('subject', 4)]}))
    cluster = aggregate_ledger(tree, records, cards, clusters)['structure_ledger']['clusters'][0]
    assert cluster['status'] == 'resolved'
    assert cluster['resolved_by'][0]['reference_candidate_id'] == 'seed'
    records[-1]['verdict'].pop('tau')
    assert aggregate_ledger(tree, records, cards, clusters)['structure_ledger']['clusters'][0]['status'] == 'open'
