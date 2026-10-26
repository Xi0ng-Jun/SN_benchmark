"""Regression scenarios for unattended round recovery, without external agents."""
import sqlite3
import pytest
from test_workbench_loop import setup, advance  # noqa: F401


def test_transient_phase_error_keeps_phase_until_recovered(setup):
    _, ports, clock, service = setup
    rid = service.start('I', actor='human', request_key='transient')['id']
    original = ports.describe
    ports.describe = lambda _: (_ for _ in ()).throw(sqlite3.OperationalError('private lock detail'))
    state = service.tick(rid)
    assert state['status'] == 'running'
    assert state['phase'] == 'boundary'
    assert state['transient_error']['type'] == 'OperationalError'
    ports.describe = original
    assert advance(service, rid)['status'] == 'completed'


def test_new_invocation_resets_streak_and_records_history(setup):
    _, ports, _, service = setup
    ports.verdict = 'reject'
    first = advance(service, service.start('I', actor='human', request_key='first', rounds=5)['id'])
    assert first['no_accept_streak'] == 2
    second = service.start('I', actor='human', request_key='second')
    assert second['no_accept_streak'] == 0
    assert second['previous_no_accept_streak'] == 2
    assert len(advance(service, second['id'])['rounds']) == 1


@pytest.mark.parametrize('merge_status', ['withdrawn', 'stalled', 'unmeasurable'])
def test_fresh_exhaustion_still_merges_and_falls_back_to_largest_delta(setup, merge_status):
    _, ports, _, service = setup
    ports.cards = 2
    ports.merge_status = merge_status
    ports.fresh_status = {'C0-H0-C': 'exhausted', 'C0-H1-C': 'exhausted'}
    ports.gate = lambda cid, **_: dict(verdict='accept', delta=.2 if 'H1' in cid else .1)
    result = advance(service, service.start('I', actor='human', request_key='merge')['id'])
    assert result['rounds'][0]['merge']['phase'] == 'done'
    assert result['baseline_candidate_id'] == 'C0-H1-C'
    assert result['rounds'][0]['baseline_selection']['reason'] == 'merge_fallback'


def test_idle_agent_gets_bounded_followup_and_full_deadline_after_execution(setup):
    _, ports, clock, service = setup
    ports.settings.update(agent_timeout_seconds=10, execution_timeout_seconds=20, infra_retry=1)
    ports.implementation = lambda *a, **k: dict(status='running', execution_in_flight=True)
    rid = service.start('I', actor='human', request_key='wake')['id']
    result = advance(service, rid, until=lambda s: bool(s['rounds'][0]['candidates']) and
        s['rounds'][0]['candidates'][0].get('waiting_execution'))
    clock[0] += 19
    ports.implementation = lambda *a, **k: dict(status='running', reason='cluster_result_not_read')
    result = service.tick(rid)
    c = result['rounds'][0]['candidates'][0]
    assert not c.get('waiting_execution')
    assert c['agent_deadline'] >= clock[0] + 10
    assert c['followups']['cluster_result_not_read'] == 1
    for _ in range(5):
        result = service.tick(rid)
    assert result['rounds'][0]['candidates'][0]['phase'] == 'implementation'
    assert result['rounds'][0]['candidates'][0]['followups']['cluster_result_not_read'] == 1
    clock[0] += 11
    assert advance(service, rid)['rounds'][0]['candidates'][0]['status'] == 'stalled'


def test_analysis_missing_cards_gets_followup_before_stall(setup):
    _, ports, clock, service = setup
    ports.cards = 0
    ports.settings['agent_timeout_seconds'] = 10
    rid = service.start('I', actor='human', request_key='analyst-wake')['id']
    for _ in range(8):
        result = service.tick(rid)
    assert result['status'] == 'running'
    assert result['rounds'][0]['followups']['cards_missing'] == 1
    clock[0] += 11
    assert advance(service, rid)['rounds'][0]['analysis_status'] == 'stalled'


def test_resume_extends_deadlines_by_service_downtime_only(setup):
    repo, ports, clock, service = setup
    ports.measure_status['C0'] = 'running'
    ports.settings.update(execution_timeout_seconds=20, infra_retry=1)
    service.heartbeat()
    rid = service.start('I', actor='human', request_key='downtime')['id']
    result = service.tick(rid)
    deadline = result['rounds'][0]['baseline_deadline']
    assert deadline >= clock[0] + 40
    clock[0] += 1000
    service.resume()
    assert service.get(rid)['rounds'][0]['baseline_deadline'] == deadline + 1000
    assert service.tick(rid)['status'] == 'running'
    service.resume()
    assert service.get(rid)['rounds'][0]['baseline_deadline'] == deadline + 1000


def test_configured_second_followup_is_reachable_and_bounded(setup):
    _, ports, clock, service = setup
    ports.settings.update(agent_timeout_seconds=10, agent_followups_per_stage=2)
    ports.implementation = lambda *a, **k: dict(status='running', reason='full_base_runs_not_requested')
    rid = service.start('I', actor='human', request_key='two-followups')['id']
    state = advance(service, rid, until=lambda s: bool(s['rounds'][0]['candidates']) and
                    bool(s['rounds'][0]['candidates'][0].get('followups')))
    clock[0] += 11
    state = service.tick(rid)
    c = state['rounds'][0]['candidates'][0]
    assert c['phase'] == 'implementation'
    assert c['followups']['full_base_runs_not_requested'] == 2
    service.tick(rid)
    clock[0] += 11
    assert advance(service, rid)['rounds'][0]['candidates'][0]['status'] == 'stalled'


def test_terminal_execution_observation_restores_agent_window_even_after_wait_deadline(setup):
    _, ports, clock, service = setup
    ports.settings.update(agent_timeout_seconds=10, execution_timeout_seconds=20)
    ports.implementation = lambda *a, **k: dict(status='running', execution_in_flight=True)
    rid = service.start('I', actor='human', request_key='late-tick')['id']
    state = advance(service, rid, until=lambda s: bool(s['rounds'][0]['candidates']) and
                    bool(s['rounds'][0]['candidates'][0].get('waiting_execution')))
    clock[0] = state['rounds'][0]['candidates'][0]['execution_deadline'] + 1
    ports.implementation = lambda *a, **k: dict(status='running', reason='cluster_result_not_read')
    state = service.tick(rid)
    c = state['rounds'][0]['candidates'][0]
    assert c['phase'] == 'implementation'
    assert not c['waiting_execution']
    assert c['agent_deadline'] > clock[0]


def test_transient_failure_cannot_extend_phase_deadline(setup):
    _, ports, clock, service = setup
    ports.settings.update(execution_timeout_seconds=10, infra_retry=0)
    rid = service.start('I', actor='human', request_key='bounded-transient')['id']
    ports.measure = lambda *a, **k: (_ for _ in ()).throw(OSError('temporary'))
    state = service.tick(rid)
    assert state['status'] == 'running'
    clock[0] = state['rounds'][0]['baseline_deadline'] + 1
    ports.ledger = lambda *a: (_ for _ in ()).throw(OSError('temporary'))
    assert service.tick(rid)['status'] == 'failed'


def test_expired_execution_window_only_observes_implementation(setup):
    _, ports, clock, service = setup
    ports.settings.update(execution_timeout_seconds=10)
    observations = []
    def implementation(*args, **kwargs):
        observations.append(kwargs)
        return dict(status='running', execution_in_flight=True)
    ports.implementation = implementation
    rid = service.start('I', actor='human', request_key='observe-deadline')['id']
    state = advance(service, rid, until=lambda s: bool(s['rounds'][0]['candidates']) and
                    bool(s['rounds'][0]['candidates'][0].get('waiting_execution')))
    clock[0] = state['rounds'][0]['candidates'][0]['execution_deadline'] + 1
    state = service.tick(rid)
    assert observations[-1]['observe_only'] is True
    assert state['rounds'][0]['candidates'][0]['status'] == 'stalled'
