"""Agent feedback contracts, independent of a real model's willingness to follow prompts."""
import json

from rag_eval.optimizer import adapter as contract
from rag_eval.optimizer.v4.config import V4Config
from rag_eval.optimizer.v4.loop import V4Run
from rag_eval.optimizer.v4.sn_adapter_v4 import SNAdapterV4
from .v4_mock import ScriptedRun, V4Mock


def config(**kw):
    kw.setdefault("time_budget_hours", 1)
    kw.setdefault("b", 2)
    return V4Config(**kw)


def execute(mock, cfg=None, path=None):
    run = ScriptedRun(mock, mock.seed, cfg or config(), search=mock.search, validation=mock.validation,
                      state_dir=path)
    return run, run.run()


def report():
    return dict(candidate_id='unfrozen', hypothesis_id='r1-redundant', parent_candidate_id='seed',
                decision='skip', reason='already_satisfied', summary='The empty-list guard already returns early.',
                checks=[dict(kind='source', target='backend/query.py:18', observation='guard precedes model call')])


def test_preflight_skip_is_not_frozen_or_measured_and_next_analyst_gets_reason(tmp_path):
    class Mock(V4Mock):
        def implement(self, card, workspace):
            if card.failure_mode_desc == 'redundant':
                raise contract.ImplementationDeclined(report())
            return super().implement(card, workspace)
    mock = Mock([[dict(name='redundant', delta=.4), dict(name='useful', delta=.1)], []])
    run, reason = execute(mock, config(speculative_analysis=False), tmp_path)
    rec = run.rounds[0]['records']['0']
    assert rec['stage'] == 'preflight_skipped' and rec['label'] == 'preflight_skipped'
    assert rec['preflight']['reason'] == 'already_satisfied'
    assert all('redundant' not in version for version in mock.chain)
    assert all('redundant' not in row[0] for row in mock.measured)
    # Script has 2 rounds, so after adoption in round 1, round 2 drafts no cards → script_end
    assert reason == 'script_end' and run.lineage[-1]['version'] == 'seed+r1-useful'
    entry = next(e for e in mock.last_history if e['title'] == 'redundant')
    assert entry['implementation_feedback']['summary'] == 'The empty-list guard already returns early.'
    persisted = json.loads((tmp_path / 'state.json').read_text())
    assert persisted['rounds'][0]['records']['0']['preflight'] == report()


def test_low_gain_still_records_cheap_mechanism_observation_without_reviewer():
    mock = V4Mock([[dict(name='changed', delta=.00001, predicate=True)]])
    run, _ = execute(mock, config(speculative_analysis=False))
    # v4.1: tiny Δ < c_mech·SE still runs mechanism check but never qualifies
    assert mock.calls.get('review', 0) == 0
    entry = next(e for e in run.history if e['title'] == 'changed')
    assert entry['outcome'] == 'mechanism_changed_gain_insufficient'
    assert not any(key in entry for key in ('delta_val', 'se_val', 'candidate_rate', 'reviewer'))


def test_adopted_inventory_survives_history_pressure_and_excludes_future_parent(tmp_path):
    mock = V4Mock([[dict(name='first change', delta=.1)], [dict(name='second change', delta=.1)], []])
    run, _ = execute(mock, config(speculative_analysis=False))
    run.history += [dict(parent='seed', title=f'old failed direction {i}', label='below_threshold') for i in range(100)]
    adapter = SNAdapterV4({}, evidence_dir=tmp_path)
    first = run.versions['seed+r1-first change']
    rows = run.history_for(first)
    inventory = [e for e in rows if e['label'] == 'current_change']
    assert [e['title'] for e in inventory] == ['first change']
    text, _, omitted = adapter._analysis_history(rows, 800, 'first-parent')
    assert omitted > 0
    files = list(tmp_path.glob('adopted-*.md'))
    assert len(files) == 1 and str(files[0]) in text
    contents = files[0].read_text()
    assert 'first change' in contents and 'second change' not in contents
    assert 'delta_val' not in contents and 'se_val' not in contents


def test_validation_error_text_never_enters_analyst_history():
    mock = V4Mock([[dict(name='broken', delta=.1)]])
    mock.fail_once['rest:broken'] = contract.EvaluationUnavailable('SECRET validation question; score=0.123')
    run, _ = execute(mock, config(speculative_analysis=False))
    entry = next(e for e in run.history if e['title'] == 'broken')
    assert entry['outcome'] == 'execution_unavailable'
    assert 'SECRET' not in json.dumps(entry) and '0.123' not in json.dumps(entry)


def test_speculative_analysis_sees_completed_skip_not_mislabeled_in_progress():
    class Mock(V4Mock):
        def implement(self, card, workspace):
            if card.failure_mode_desc == 'redundant':
                raise contract.ImplementationDeclined(report())
            return super().implement(card, workspace)

        def analyse(self, trajectories, n_cards, **kwargs):
            if kwargs['round_id'] == 2:
                self.next_history = kwargs['history']
            return super().analyse(trajectories, n_cards, **kwargs)

    mock = Mock([[dict(name='redundant'), dict(name='experiment', delta=0)], []])
    execute(mock, config(b=1))
    completed = next(e for e in mock.next_history if e['title'] == 'redundant')
    assert completed['label'] == 'preflight_skipped'
    assert completed['implementation_feedback']['source'] == 'implementer_report_before_measurement'


def test_repair_skip_preserves_prior_attempt_but_projects_new_unfrozen_candidate():
    class Mock(V4Mock):
        def repair(self, card, parent, failed, **kwargs):
            raise contract.ImplementationDeclined(dict(report(), candidate_id='repair-unfrozen'))
    mock = Mock([[dict(name='needs-repair', delta=.1, predicate=True, smoke_behaviour_fails=True)]])
    run, _ = execute(mock, config(smoke=True, smoke_behaviour_check=True, speculative_analysis=False))
    rec = run.rounds[0]['records']['0']
    assert rec['stage'] == 'preflight_skipped' and rec['attempt'] == 2
    assert not rec.get('candidate')
    assert rec['prior_attempts'][0]['candidate'] == 'seed+r1-needs-repair'
    from rag_eval.optimizer.v4.run_view import _record
    projected = _record(rec, {h: v.metadata for h, v in run.versions.items()})
    assert projected['candidate_id'] == 'repair-unfrozen'
