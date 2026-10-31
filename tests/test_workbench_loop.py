"""Durable orchestration exercised without models, agents, or execution processes."""
from collections import Counter
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import threading

import pytest

from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.loop import RoundService, validate_settings


class Crash(BaseException):
    pass


class Ports:
    def __init__(self, *, cards=1):
        self.settings = {}
        self.cards = cards
        self.receipts = {}
        self.effects = Counter()
        self.calls = []
        self.approved = set()
        self.agent_states = {}
        self.implementation_states = {}
        self.verdict = 'pass'
        self.deltas = {}
        self.confirms = {}
        self.rebases = {}
        self.batches = 0
        self.fresh_remaining = 100
        self.budget_exhausted = False
        self.crash = None
        self.measure_status = {}
        self.fresh_status = {}
        self.mutex = threading.Lock()

    def describe(self, iteration_id):
        return dict(task_id='task', workspace_id='workspace', root_candidate_id='C0',
                    settings=self.settings, k_base=2, evaluated_count=len(self.receipts) + 1,
                    fresh_remaining=self.fresh_remaining, budget_exhausted=self.budget_exhausted)

    def effect(self, kind, key, result):
        with self.mutex:
            self.calls.append((kind, key))
            if key not in self.receipts:
                self.receipts[key] = deepcopy(result)
                self.effects[kind] += 1
                if self.crash == kind:
                    self.crash = None
                    raise Crash(kind)
            return deepcopy(self.receipts[key])

    def measure(self, candidate_id, *, key, **kwargs):
        self.effect('measure', key, dict(candidate_id=candidate_id, **kwargs))
        return dict(status=self.measure_status.get(candidate_id, 'complete'), attempts=['original', 'infra-retry'])

    def agent(self, role, candidate_id, *, key, **kwargs):
        self.effect('agent', key, dict(role=role, candidate_id=candidate_id, **kwargs))
        return dict(status=self.agent_states.get(candidate_id, 'idle'), session_id=key)

    def select(self, iteration_id, baseline_id, **kwargs):
        return dict(clusters_count=self.cards, ranking=[dict(m=None, v=None, reason='prior fallback')],
                    cards=[dict(hypothesis_id=f'{baseline_id}-H{i}', cluster_id=f'K{i}', status='draft')
                           for i in range(self.cards)])

    def approve(self, hypothesis_id, *, key, **kwargs):
        self.effect('approve', key, dict(status='approved'))
        self.approved.add(hypothesis_id)
        return dict(status='approved')

    def card_status(self, hypothesis_id, **kwargs):
        return 'approved' if hypothesis_id in self.approved else 'draft'

    def derive(self, baseline_id, *, hypothesis_id, key, rebase_from=None, **kwargs):
        self.calls.append(('derive-parent', baseline_id))
        return self.effect('derive', key, hypothesis_id + '-C' + ('-R' if rebase_from else ''))

    def rebase(self, candidate_id, *, key, **kwargs):
        return self.effect('rebase', key, dict(status=self.rebases.get(candidate_id, 'applied')))

    def implementation(self, candidate_id, **kwargs):
        return dict(status=self.implementation_states.get(candidate_id, 'ready'))

    def screen(self, candidate_id, *, key, **kwargs):
        return self.effect('screen', key, dict(verdict=self.verdict, delta=self.deltas.get(candidate_id, .1)))

    def fresh(self, candidate_id, *, key, **kwargs):
        with self.mutex:
            if key not in self.receipts:
                self.batches += 1
            batch = self.receipts.get(key, {}).get('batch_id', f'f{self.batches}')
        self.effect('fresh', key, dict(candidate_id=candidate_id, batch_id=batch, **kwargs))
        return dict(status=self.fresh_status.get(candidate_id, 'complete'), batch_id=batch)

    def confirm(self, candidate_id, *, batch_ids, key, final, **kwargs):
        verdicts = self.confirms.get(candidate_id, ['holds'])
        verdict = verdicts[min(len(batch_ids), len(verdicts)) - 1]
        return self.effect('confirm', key, dict(verdict=verdict, delta=.05, batch_ids=batch_ids, final=final))

    def ledger(self, iteration_id):
        return dict(accounts=['primary', 'budget', 'cluster'], wishlist=['next'])


@pytest.fixture
def setup(tmp_path):
    repo, ports = WorkbenchRepository(tmp_path), Ports()
    clock = [100.0]
    service = RoundService(repo, ports, clock=lambda: clock[0])
    return repo, ports, clock, service


def advance(service, rid, *, until=None, limit=100):
    for _ in range(limit):
        state = service.tick(rid)
        if until and until(state):
            return state
        if state['status'] in {'completed', 'failed', 'stopped'}:
            return state
    raise AssertionError('round did not reach expected state: ' + repr(state))


def test_one_round_and_restart_after_every_tick(setup):
    repo, ports, clock, service = setup
    state = service.start('I', actor='human', request_key='one')
    for _ in range(100):
        service = RoundService(repo, ports, clock=lambda: clock[0])
        state = service.tick(state['id'])
        if state['status'] == 'completed':
            break
    assert state['status'] == 'completed'
    assert state['stop_reason'] == 'rounds_exhausted'
    assert state['baseline_candidate_id'] == 'C0-H0-C'
    assert ports.effects == dict(measure=2, agent=2, approve=1, derive=1, screen=1, fresh=1, confirm=1)
    assert state['rounds'][0]['ledger']['wishlist'] == ['next']
    candidate = state['rounds'][0]['candidates'][0]
    assert candidate['screen']['verdict'] == 'pass' and candidate['confirm']['verdict'] == 'holds'
    assert candidate['status'] == 'accepted' and candidate['confirm_batches'] == ['f1']
    assert state['lineage'] == [dict(candidate_id='C0-H0-C', parent_candidate_id='C0', round=1,
                                     screen_delta=.1, confirm_delta=.05, attribution=None)]
    assert state['stop'] == dict(category='resources_exhausted', reason='rounds_exhausted', detail=None,
                                 no_gain_streak=0, process_failure_streak=0)
    assert state['rounds'][0]['ledger']['stop'] == state['stop']
    assert service.start('I', actor='human', request_key='one')['id'] == state['id']
    baseline = next(v for k, v in ports.receipts.items() if k.endswith('/baseline'))
    assert baseline['override'] is True and baseline['actor'] == 'human'


def test_two_rounds_carry_baseline_and_new_invocation(setup):
    _, ports, _, service = setup
    state = advance(service, service.start('I', actor='human', request_key='two', rounds=2)['id'])
    assert len(state['rounds']) == 2
    assert state['rounds'][1]['baseline_candidate_id'] == 'C0-H0-C'
    new = service.start('I', actor='human', request_key='three')
    assert new['baseline_candidate_id'] == state['baseline_candidate_id']
    assert new['id'] != state['id']


def test_manual_wait_is_resumable_without_duplicate_agents(setup):
    repo, ports, clock, service = setup
    ports.settings['card_approval'] = 'manual'
    state = advance(service, service.start('I', actor='human', request_key='manual')['id'],
                    until=lambda s: s['status'] == 'waiting_approval')
    assert ports.effects['derive'] == 0
    resumed = RoundService(repo, ports, clock=lambda: clock[0])
    assert resumed.start('I', actor='human', request_key='resume')['id'] == state['id']
    ports.approved.add('C0-H0')
    state = advance(resumed, state['id'])
    assert state['status'] == 'completed'
    assert ports.effects['agent'] == 2


def test_holding_confirmation_advances_parent_and_rebases_queued_sibling(setup):
    _, ports, _, service = setup
    ports.cards = 2
    ports.deltas = {'C0-H0-C': .1, 'C0-H1-C': .3}
    state = advance(service, service.start('I', actor='human', request_key='rebase')['id'])
    first, second, rebased = state['rounds'][0]['candidates']
    # Largest validation Δ confirms first; the sibling never confirms against a stale parent.
    assert second['status'] == 'accepted'
    assert first['status'] == 'needs_rebase' and first['rebased_as'] == 2
    assert first['reason'] == 'parent advanced to C0-H1-C'
    assert rebased['rebase_from'] == 'C0-H0-C' and rebased['parent_candidate_id'] == 'C0-H1-C'
    assert ('derive-parent', 'C0-H1-C') in ports.calls
    assert rebased['status'] == 'accepted' and rebased['candidate_id'] == 'C0-H0-C-R'
    assert state['baseline_candidate_id'] == 'C0-H0-C-R'
    assert [n['candidate_id'] for n in state['lineage']] == ['C0-H1-C', 'C0-H0-C-R']
    assert not any('merge' in key for _, key in ports.calls)
    assert ports.effects['fresh'] == 2 and ports.effects['rebase'] == 1


def test_stalled_sibling_does_not_block_ready_candidate(setup):
    _, ports, clock, service = setup
    ports.cards = 2
    ports.agent_states['C0-H0-C'] = 'running'
    ports.implementation_states['C0-H0-C'] = 'running'
    ports.settings['agent_timeout_seconds'] = 10
    state = advance(service, service.start('I', actor='human', request_key='parallel')['id'],
                    until=lambda s: bool(s['rounds'][0].get('candidates')) and
                    any(c['phase'] == 'queued' for c in s['rounds'][0]['candidates']))
    candidates = state['rounds'][0]['candidates']
    assert candidates[1]['screen']['verdict'] == 'pass'
    assert candidates[0]['phase'] == 'implementation'
    clock[0] += 11
    state = advance(service, state['id'])
    assert state['rounds'][0]['candidates'][0]['status'] == 'stalled'
    assert state['baseline_candidate_id'] == 'C0-H1-C'


@pytest.mark.parametrize('kind', ['measure', 'agent', 'approve', 'derive', 'screen', 'fresh', 'confirm', 'rebase'])
def test_crash_after_side_effect_recovers_receipt_without_duplicate_dispatch(setup, kind):
    repo, ports, clock, service = setup
    ports.cards = 2
    ports.crash = kind
    rid = service.start('I', actor='human', request_key='crash')['id']
    with pytest.raises(Crash):
        advance(service, rid)
    state = advance(RoundService(repo, ports, clock=lambda: clock[0]), rid)
    assert state['status'] == 'completed'
    assert ports.effects == dict(measure=4, agent=4, approve=2, derive=3, screen=3, fresh=2, confirm=2, rebase=1)


def test_idle_without_artifacts_and_unknown_agent_are_stalled(setup):
    _, ports, clock, service = setup
    ports.implementation_states['C0-H0-C'] = 'running'
    state = advance(service, service.start('I', actor='human', request_key='idle')['id'],
        until=lambda s: bool(s['rounds'][0]['candidates']) and bool(s['rounds'][0]['candidates'][0].get('followups')))
    clock[0] = state['rounds'][0]['candidates'][0]['agent_deadline'] + 1
    state = advance(service, state['id'])
    candidate = state['rounds'][0]['candidates'][0]
    assert candidate['status'] == 'stalled'
    assert 'timeout' in candidate['reason']
    assert ports.effects['screen'] == 0


def test_inflight_execution_survives_idle_agent(setup):
    _, ports, _, service = setup
    ports.implementation_states['C0-H0-C'] = 'running'
    original = ports.implementation
    ports.implementation = lambda cid, **kwargs: dict(original(cid), execution_in_flight=True)
    state = advance(service, service.start('I', actor='human', request_key='inflight')['id'],
                    until=lambda s: ports.effects['agent'] == 2)
    assert state['status'] == 'running'
    ports.implementation_states['C0-H0-C'] = 'ready'
    assert advance(service, state['id'])['baseline_candidate_id'] == 'C0-H0-C'


def test_infra_retry_setting_flows_to_measure_and_fresh_without_replacing_attempts(setup):
    _, ports, _, service = setup
    ports.settings['infra_retry'] = 2
    state = advance(service, service.start('I', actor='human', request_key='retry')['id'])
    for key, value in ports.receipts.items():
        if '/measure' in key or key.endswith('/baseline') or key.endswith('/fresh'):
            assert value['infra_retry'] == 2
    assert state['rounds'][0]['candidates'][0]['measurement']['attempts'] == ['original', 'infra-retry']


def test_two_no_gain_cycles_stop_and_history_survives_invocations(setup):
    _, ports, _, service = setup
    ports.verdict = 'fail'
    first = advance(service, service.start('I', actor='human', request_key='no1')['id'])
    second = advance(service, service.start('I', actor='human', request_key='no2', rounds=5)['id'])
    assert first['no_gain_streak'] == 1 and first['rounds'][0]['outcome'] == 'no_gain'
    assert first['rounds'][0]['candidates'][0]['status'] == 'screen_failed'
    assert second['stop_reason'] == 'no_gain' and second['stop']['category'] == 'no_gain'
    assert len(second['rounds']) == 2 and second['previous_no_gain_streak'] == 1
    assert ports.effects['fresh'] == 0


def test_fresh_does_not_hold_keeps_parent(setup):
    _, ports, _, service = setup
    ports.confirms['C0-H0-C'] = ['does_not_hold']
    state = advance(service, service.start('I', actor='human', request_key='not-hold', rounds=2)['id'])
    candidate = state['rounds'][0]['candidates'][0]
    assert candidate['status'] == 'rejected' and candidate['screen']['verdict'] == 'pass'
    assert state['baseline_candidate_id'] == 'C0' and state['lineage'] == []
    assert state['rounds'][1]['baseline_candidate_id'] == 'C0'
    assert state['stop_reason'] == 'no_gain'


@pytest.mark.parametrize('extra,verdicts,status,batches', [
    (1, ['insufficient_evidence', 'holds'], 'accepted', ['f1', 'f2']),
    (1, ['insufficient_evidence', 'insufficient_evidence'], 'rejected', ['f1', 'f2']),
    (0, ['insufficient_evidence', 'holds'], 'rejected', ['f1'])])
def test_insufficient_evidence_takes_one_pooled_batch_when_preregistered(setup, extra, verdicts, status, batches):
    _, ports, _, service = setup
    ports.settings['confirm_extra_batches'] = extra
    ports.confirms['C0-H0-C'] = verdicts
    state = advance(service, service.start('I', actor='human', request_key='insufficient')['id'])
    candidate = state['rounds'][0]['candidates'][0]
    assert candidate['status'] == status and candidate['confirm_batches'] == batches
    assert candidate['confirm']['batch_ids'] == batches and candidate['confirm']['final'] is True
    assert state['baseline_candidate_id'] == ('C0-H0-C' if status == 'accepted' else 'C0')
    finals = [r['final'] for k, r in ports.receipts.items() if '/confirm/' in k]
    assert finals == ([False, True] if extra else [True])


def test_unmeasurable_confirmation_adopts_nothing(setup):
    _, ports, _, service = setup
    ports.confirms['C0-H0-C'] = ['unmeasurable']
    state = advance(service, service.start('I', actor='human', request_key='unmeasurable')['id'])
    assert state['rounds'][0]['candidates'][0]['status'] == 'unmeasurable'
    assert state['baseline_candidate_id'] == 'C0'


def test_rebase_conflict_is_discarded_and_recorded(setup):
    _, ports, _, service = setup
    ports.cards = 2
    ports.deltas = {'C0-H0-C': .1, 'C0-H1-C': .3}
    ports.rebases['C0-H0-C-R'] = 'conflict'
    state = advance(service, service.start('I', actor='human', request_key='conflict')['id'])
    rebased = state['rounds'][0]['candidates'][2]
    assert rebased['status'] == 'rebase_conflict' and rebased['rebase']['status'] == 'conflict'
    assert state['baseline_candidate_id'] == 'C0-H1-C'
    assert ports.effects['fresh'] == 1


def test_consecutive_process_failures_stop_as_process_failure(setup):
    _, ports, _, service = setup
    ports.implementation_states['C0-H0-C'] = 'failed'
    state = advance(service, service.start('I', actor='human', request_key='process', rounds=5)['id'])
    assert [r['outcome'] for r in state['rounds']] == ['process_failure', 'process_failure']
    assert state['stop_reason'] == 'process_failure'
    assert state['stop']['category'] == 'process_failure' and state['stop']['process_failure_streak'] == 2


@pytest.mark.parametrize('resource,reason', [('budget_exhausted', 'budget_exhausted'), ('fresh_remaining', 'fresh_exhausted')])
def test_resource_stopping_conditions_at_boundary(setup, resource, reason):
    _, ports, _, service = setup
    setattr(ports, resource, True if resource == 'budget_exhausted' else 0)
    state = advance(service, service.start('I', actor='human', request_key='empty', rounds=8)['id'])
    assert state['stop_reason'] == reason
    assert not ports.effects


def test_budget_exhaustion_drains_evaluated_candidate_and_keeps_ledger(setup):
    _, ports, _, service = setup
    ports.cards = 2
    ports.measure_status['C0-H1-C'] = 'budget_exhausted'
    state = advance(service, service.start('I', actor='human', request_key='budget', rounds=8)['id'])
    assert state['stop_reason'] == 'budget_exhausted'
    assert state['rounds'][0]['candidates'][0]['screen']['verdict'] == 'pass'
    assert state['rounds'][0]['candidates'][0]['status'] == 'unconfirmed_not_adopted'
    assert state['baseline_candidate_id'] == 'C0'
    assert state['rounds'][0]['ledger']


@pytest.mark.parametrize('status,reason', [('exhausted', 'fresh_exhausted'), ('budget_exhausted', 'budget_exhausted')])
def test_unconfirmed_candidate_is_never_adopted(setup, status, reason):
    _, ports, _, service = setup
    ports.fresh_status['C0-H0-C'] = status
    state = advance(service, service.start('I', actor='human', request_key='fresh', rounds=8)['id'])
    assert state['stop_reason'] == reason and state['stop']['category'] == 'resources_exhausted'
    candidate = state['rounds'][0]['candidates'][0]
    assert candidate['status'] == 'unconfirmed_not_adopted' and candidate['reason'] == reason
    assert state['baseline_candidate_id'] == 'C0' and ports.effects['confirm'] == 0


def test_fresh_timeout_is_unconfirmed_and_counts_as_process_failure(setup):
    _, ports, clock, service = setup
    ports.fresh_status['C0-H0-C'] = 'running'
    rid = service.start('I', actor='human', request_key='fresh-timeout')['id']
    state = advance(service, rid, until=lambda s: bool(s['rounds'][0].get('candidates'))
                    and s['rounds'][0]['candidates'][0]['phase'] == 'confirm')
    clock[0] = state['rounds'][0]['candidates'][0]['fresh_deadline'] + 1
    state = advance(service, rid)
    candidate = state['rounds'][0]['candidates'][0]
    assert candidate['status'] == 'unconfirmed_not_adopted' and candidate['reason'] == 'fresh execution timeout'
    assert state['rounds'][0]['outcome'] == 'process_failure' and state['baseline_candidate_id'] == 'C0'


def test_stop_is_durable_and_dispatch_free_after_restart(setup):
    repo, ports, clock, service = setup
    state = service.start('I', actor='human', request_key='stop')
    service.tick(state['id'])
    count = ports.effects.copy()
    service.stop(state['id'], actor='operator')
    resumed = RoundService(repo, ports, clock=lambda: clock[0])
    assert resumed.tick(state['id'])['status'] == 'stopped'
    assert ports.effects == count


def test_stop_during_side_effect_prevents_next_dispatch(setup):
    _, ports, _, service = setup
    rid = service.start('I', actor='human', request_key='mid-stop')['id']
    original = ports.measure
    def stop_after_measure(*args, **kwargs):
        result = original(*args, **kwargs)
        service.stop(rid, actor='operator')
        return result
    ports.measure = stop_after_measure
    state = service.tick(rid)
    assert state['status'] == 'stopped'
    assert ports.effects == dict(measure=1)


def test_concurrent_ticks_do_not_duplicate_side_effects(setup):
    repo, ports, clock, service = setup
    rid = service.start('I', actor='human', request_key='concurrent')['id']
    def worker(_):
        other = RoundService(repo, ports, clock=lambda: clock[0])
        for _ in range(50):
            other.tick(rid)
    with ThreadPoolExecutor(max_workers=4) as pool:
        list(pool.map(worker, range(4)))
    state = advance(service, rid)
    assert state['status'] == 'completed'
    assert ports.effects['agent'] == 2
    assert ports.effects['derive'] == 1


def test_execution_timeout_is_persisted_across_restart(setup):
    repo, ports, clock, service = setup
    ports.settings['execution_timeout_seconds'] = 5
    ports.measure_status['C0'] = 'running'
    state = service.tick(service.start('I', actor='human', request_key='timeout')['id'])
    clock[0] = state['rounds'][0]['baseline_deadline'] + 1
    state = advance(RoundService(repo, ports, clock=lambda: clock[0]), state['id'])
    assert state['status'] == 'failed'
    assert 'timeout' in state['reason']


@pytest.mark.parametrize('settings', [dict(b=0), dict(max_clusters=True), dict(pi=float('nan')),
                                    dict(card_approval='maybe'), dict(infra_retry=-1),
                                    dict(agent_timeout_seconds=0), dict(analyst_profile='')])
def test_settings_reject_invalid_values(settings):
    with pytest.raises(ValueError):
        validate_settings(settings)


def test_every_saved_checkpoint_can_resume_including_round_finalization(tmp_path):
    # A write may commit and then the process disappear before the next line.
    # Exercise each checkpoint, rather than enumerating only intended stages.
    reference_ports = Ports()
    reference = RoundService(WorkbenchRepository(tmp_path / 'reference'), reference_ports)
    count = [0]
    original = reference._save
    def counting(state):
        original(state)
        count[0] += 1
    reference._save = counting
    advance(reference, reference.start('I', actor='human', request_key='reference', rounds=2)['id'])
    for checkpoint in range(1, count[0] + 1):
        ports = Ports()
        repo = WorkbenchRepository(tmp_path / str(checkpoint))
        service = RoundService(repo, ports)
        rid = service.start('I', actor='human', request_key='crash', rounds=2)['id']
        saves = [0]
        save = service._save
        def crashing(state):
            save(state)
            saves[0] += 1
            if saves[0] == checkpoint:
                raise Crash('committed checkpoint')
        service._save = crashing
        with pytest.raises(Crash):
            advance(service, rid)
        recovered = advance(RoundService(repo, ports), rid)
        assert recovered['status'] == 'completed', checkpoint
        assert recovered['no_gain_streak'] == 0, checkpoint
        assert len(recovered['rounds']) == 2, checkpoint
        assert ports.effects == reference_ports.effects, checkpoint


def test_unknown_agent_does_not_trigger_another_agent_creation(setup):
    repo, ports, clock, service = setup
    ports.agent_states['C0-H0-C'] = 'unknown'
    ports.implementation_states['C0-H0-C'] = 'running'
    state = advance(service, service.start('I', actor='human', request_key='unknown')['id'])
    assert state['rounds'][0]['candidates'][0]['status'] == 'stalled'
    RoundService(repo, ports, clock=lambda: clock[0]).tick(state['id'])
    assert ports.effects['agent'] == 2


def test_idle_agent_execution_wait_uses_execution_deadline(setup):
    _, ports, clock, service = setup
    ports.settings.update(agent_timeout_seconds=5, execution_timeout_seconds=20)
    ports.implementation = lambda cid, **kwargs: dict(status='running', execution_in_flight=True)
    rid = service.start('I', actor='human', request_key='execution-wait')['id']
    state = advance(service, rid, until=lambda s: ports.effects['agent'] == 2)
    clock[0] += 6
    state = service.tick(rid)
    assert state['rounds'][0]['candidates'][0]['status'] == 'running'
    clock[0] = state['rounds'][0]['candidates'][0]['execution_deadline'] + 1
    state = advance(service, rid)
    assert 'execution timeout' in state['rounds'][0]['candidates'][0]['reason']


def test_port_exception_is_recorded_without_starving_sibling(setup):
    _, ports, _, service = setup
    ports.cards = 2
    implementation = ports.implementation
    def broken(cid, **kwargs):
        if cid == 'C0-H0-C':
            raise ValueError('invalid expected request')
        return implementation(cid)
    ports.implementation = broken
    state = advance(service, service.start('I', actor='human', request_key='failed-port')['id'])
    assert state['rounds'][0]['candidates'][0]['status'] == 'stalled'
    assert state['baseline_candidate_id'] == 'C0-H1-C'


@pytest.mark.parametrize('method', ['measure', 'agent', 'select', 'approve', 'ledger'])
def test_deterministic_global_port_error_is_durable_and_sanitized(setup, method):
    repo, ports, clock, service = setup
    calls = [0]
    def broken(*args, **kwargs):
        calls[0] += 1
        raise ValueError('private-service-address secret-token')
    setattr(ports, method, broken)
    state = advance(service, service.start('I', actor='human', request_key='broken')['id'])
    assert state['status'] == 'failed'
    assert 'ValueError' in state['reason']
    assert 'secret-token' not in str(state)
    RoundService(repo, ports, clock=lambda: clock[0]).tick(state['id'])
    assert calls[0] == 1


@pytest.mark.parametrize('settings', [dict(typo=3), dict(max_clusters=2, b=3)])
def test_settings_are_closed_and_selection_fits_cluster_limit(settings):
    with pytest.raises(ValueError):
        validate_settings(settings)


@pytest.mark.parametrize('evaluated_count,scope', [(1, None), (2, 'iteration')])
def test_analyst_binding_uses_evaluated_candidate_count(setup, evaluated_count, scope):
    _, ports, _, service = setup
    describe = ports.describe
    ports.describe = lambda iid: dict(describe(iid), evaluated_count=evaluated_count)
    advance(service, service.start('I', actor='human', request_key='scope')['id'])
    analyst = next(v for k, v in ports.receipts.items() if k.endswith('/analyst'))
    assert analyst['scope'] == scope
    assert analyst['candidate_id'] == 'C0'


def test_pending_implementation_budget_block_stops_round_but_drains_sibling(setup):
    _, ports, _, service = setup
    ports.cards = 2
    implementation = ports.implementation
    ports.implementation = lambda cid, **kwargs: (dict(status='running', budget_exhausted=True,
        awaiting_confirmation=True, reason='awaiting_request_confirmation')
        if cid == 'C0-H0-C' else implementation(cid))
    state = advance(service, service.start('I', actor='human', request_key='pending-budget', rounds=4)['id'])
    assert state['stop_reason'] == 'budget_exhausted'
    assert state['rounds'][0]['candidates'][0]['status'] == 'budget_exhausted'
    assert state['rounds'][0]['candidates'][1]['status'] == 'unconfirmed_not_adopted'
    assert state['rounds'][0]['ledger']


def test_pending_implementation_confirmation_is_not_premature_idle_stall(setup):
    _, ports, _, service = setup
    ports.implementation = lambda cid, **kwargs: dict(status='running', awaiting_confirmation=True,
        reason='awaiting_request_confirmation', budget_exhausted=False)
    state = advance(service, service.start('I', actor='human', request_key='pending-transient')['id'],
                    until=lambda s: ports.effects['agent'] == 2)
    assert state['rounds'][0]['candidates'][0]['status'] == 'running'
    ports.implementation = lambda cid, **kwargs: dict(status='ready')
    assert advance(service, state['id'])['baseline_candidate_id'] == 'C0-H0-C'


@pytest.mark.parametrize('timeout', [0.5, 1.0, 86401, True])
def test_execution_timeout_matches_existing_runner_contract(timeout):
    with pytest.raises(ValueError):
        validate_settings(dict(execution_timeout_seconds=timeout))


def test_screen_new_validation_pool_gets_distinct_measurement_before_pass(setup):
    _, ports, _, service = setup
    original = ports.screen
    expanded = [False]
    def screen(cid, **kwargs):
        if not expanded[0]:
            return dict(verdict='pending', measurement_revision='fresh-batch-f1')
        return original(cid, **kwargs)
    def measure(cid, *, key, **kwargs):
        expanded[0] |= 'fresh-batch-f1' in key
        return Ports.measure(ports, cid, key=key, **kwargs)
    ports.screen, ports.measure = screen, measure
    state = advance(service, service.start('I', actor='human', request_key='pool-race')['id'])
    candidate = state['rounds'][0]['candidates'][0]
    assert candidate['status'] == 'accepted' and candidate['measurement_revision'] == 'fresh-batch-f1'
    keys = [key for key, receipt in ports.receipts.items()
            if isinstance(receipt, dict) and receipt.get('candidate_id') == 'C0-H0-C' and '/measure' in key]
    assert len(keys) == 2 and len(set(keys)) == 2


def test_gate_repeated_same_pending_revision_keeps_original_deadline(setup):
    _, ports, clock, service = setup
    ports.settings['execution_timeout_seconds'] = 3
    ports.screen = lambda *args, **kwargs: dict(verdict='pending', measurement_revision='f1')
    rid = service.start('I', actor='human', request_key='pending-gate')['id']
    state = advance(service, rid, until=lambda s: bool(s['rounds'][0]['candidates']) and
                    s['rounds'][0]['candidates'][0].get('measurement_revision') == 'f1')
    deadline = state['rounds'][0]['candidates'][0]['execution_deadline']
    service.tick(rid)
    state = service.tick(rid)
    assert state['rounds'][0]['candidates'][0]['execution_deadline'] == deadline
    clock[0] = deadline + 1
    state = advance(service, rid)
    assert state['rounds'][0]['candidates'][0]['status'] == 'stalled'


def test_cluster_measurement_identity_and_retry_settings_survive_restart(setup):
    repo, ports, clock, service = setup
    ports.settings.update(infra_retry=2, execution_timeout_seconds=30, agent_timeout_seconds=5)
    ports.agent_states['C0-H0-C'] = 'running'
    seen = []
    def implementation(cid, **kwargs):
        seen.append(dict(candidate_id=cid, **kwargs))
        return dict(status='running', execution_in_flight=True, reason='cluster_infrastructure_retry')
    ports.implementation = implementation
    rid = service.start('I', actor='human', request_key='cluster-retry')['id']
    state = advance(service, rid, until=lambda s: bool(seen))
    assert state['rounds'][0]['candidates'][0]['phase'] == 'implementation'
    assert seen[0]['key'].endswith('/candidate/C0-H0/cluster-measure')
    assert seen[0]['actor'] == 'human'
    assert seen[0]['infra_retry'] == 2
    assert seen[0]['timeout_seconds'] == 30
    clock[0] += 6
    resumed = RoundService(repo, ports, clock=lambda: clock[0])
    state = resumed.tick(rid)
    assert seen[1] == seen[0]
    assert state['rounds'][0]['candidates'][0]['status'] == 'running'
    ports.implementation = lambda cid, **kwargs: dict(status='ready')
    assert advance(resumed, rid)['baseline_candidate_id'] == 'C0-H0-C'
