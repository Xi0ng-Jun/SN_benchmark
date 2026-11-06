"""Independent counterexamples from the D1 review."""
import pytest

from rag_eval.optimizer.adapter import EvalResult
from rag_eval.optimizer.v4 import agent_context, decide
from rag_eval.optimizer.v4.config import V4Config
from .v4_mock import ScriptedRun, V4Mock


def test_admitted_output_without_judge_score_is_not_a_primary_zero():
    candidate = EvalResult({}, {'case': [{'status': 'success', 'prediction': 'answer'}]}, {}, {'case': 'unscored'})
    reference = EvalResult({'case': .8}, {}, {}, {})
    result = decide.paired_delta(candidate, reference, ['case'])
    assert decide.own_score(candidate, 'case') is None
    assert result['n'] == 0 and result['dropped'] == 1 and result['delta'] is None
    assert result['own_failures'] == 0


@pytest.mark.parametrize('label', ['检索重试', '回答语言', 'Résumé rerank'])
def test_direction_keeps_non_ascii_identity(label):
    normalised = agent_context.normalise_direction(label)
    assert normalised != 'unnamed'
    assert agent_context.normalise_direction(normalised) == normalised
    assert agent_context.normalise_direction('检索重试') != agent_context.normalise_direction('回答语言')


def test_large_gain_is_reviewed_and_gaming_flag_survives_adoption():
    mock = V4Mock([[dict(name='large', delta=.15)]], reviewer='gaming')
    run = ScriptedRun(mock, mock.seed, V4Config(time_budget_hours=1),
                      search=mock.search, validation=mock.validation)
    run.run()
    record = run.rounds[0]['records']['0']
    assert record['delta_val'] >= record['se_val']
    assert mock.calls['review'] == 1
    assert record['mechanism']['tier'] == 'unconfirmed'
    assert 'needs_human_review' in record['flags']
    assert record['label'] == 'adopted'


def test_safeguard_means_pair_tasks_before_equal_weight_averaging():
    details = dict(a=dict(subject=[100, 100], reference=[100]),
                   b=dict(subject=[10000], reference=[10000]),
                   unmatched=dict(subject=[1000000], reference=[]))
    paired, subject, reference = decide.paired_safeguard_means(details)
    assert set(paired) == {'a', 'b'}
    assert subject == reference == 5050


@pytest.mark.parametrize('missing', [1, 5])
def test_remeasurement_requires_two_scores_per_retained_task(missing, tmp_path):
    class Mock(V4Mock):
        def evaluate_subset(self, version, reference, subset, task_ids, k):
            result, parent = super().evaluate_subset(version, reference, subset, task_ids, k)
            if version.content_hash != 'seed' and k == 2:
                gone = set(self.validation[:missing])
                result.attempt_scores[1] = {t: v for t, v in result.attempt_scores[1].items() if t not in gone}
            return result, parent
    mock = Mock([[dict(name='large', delta=.15)]])
    run = ScriptedRun(mock, mock.seed, V4Config(time_budget_hours=1),
                      search=mock.search, validation=mock.validation, state_dir=tmp_path)
    run.run()
    record = run.rounds[0]['records']['0']
    remeasure = record['remeasure']
    if missing == 5:
        assert remeasure['status'] == 'run_failed' and not remeasure['passed']
        assert len(run.lineage) == 1
    else:
        assert remeasure['passed'] and remeasure['n_val'] == 39 and remeasure['dropped_val'] == 1
        baseline = run.parent_val(run.pointer())
        assert 'v00' not in baseline.per_task_scores
        assert all(len(values) >= 2 for values in decide.attempts_by_task(baseline).values())
        resumed = ScriptedRun(mock, mock.seed, run.cfg, search=mock.search,
                              validation=mock.validation, state_dir=tmp_path)
        try:
            resumed._calibrate()
            replayed = resumed.parent_val(resumed.pointer())
            assert 'v00' not in replayed.per_task_scores
            assert all(len(values) >= 2 for values in decide.attempts_by_task(replayed).values())
        finally:
            for pool in [*resumed._pools.values(), resumed._prefetch_pool, resumed._analysis_pool]:
                pool.shutdown(wait=True)


def test_sn_safeguards_ignore_unmatched_tasks_and_attempt_multiplicity(monkeypatch):
    from types import SimpleNamespace
    from rag_eval.optimizer.v4.sn_adapter_v4 import SNAdapterV4
    adapter = SNAdapterV4({})
    monkeypatch.setattr(adapter, '_require_round_ops', lambda _: SimpleNamespace(
        repository=None, service=SimpleNamespace(candidate=lambda cid: dict(id=cid))))
    monkeypatch.setattr(adapter, '_views', lambda: object())
    monkeypatch.setattr(adapter, '_infrastructure', lambda *args: [])
    monkeypatch.setattr(adapter, '_safeguard_observation',
                        lambda views, stub, task: dict(usage=dict(total_tokens={'a': 100, 'b': 10000, 'x': 1e6}[task]),
                                                       status='success'))
    candidate = EvalResult({}, dict(a=[{}, {}], b=[{}], x=[{}]), {}, {}, context=dict(candidate_id='c'))
    reference = EvalResult({}, dict(a=[{}], b=[{}]), {}, {}, context=dict(candidate_id='r'))
    observed = adapter.observe_safeguards(candidate, reference)
    assert observed['tokens_per_task_ratio']['subject'] == 1
    assert observed['tokens_per_task_ratio']['means'] == dict(subject=5050, reference=5050)
    assert set(observed['tokens_per_task_ratio']['tasks']) == {'a', 'b'}
    assert observed['answer_produced_rate']['subject'] == observed['answer_produced_rate']['reference'] == 1


def test_gaming_review_is_recorded_even_when_safeguard_blocks_adoption():
    mock = V4Mock([[dict(name='harmful', delta=.15)]], reviewer='gaming')
    mock.token_harm['harmful'] = 2.0
    run = ScriptedRun(mock, mock.seed, V4Config(time_budget_hours=1),
                      search=mock.search, validation=mock.validation)
    run.run()
    record = run.rounds[0]['records']['0']
    assert record['safeguards']['breached'] == ['tokens_per_task_ratio']
    assert mock.calls['review'] == 1
    assert 'needs_human_review' in record['flags']
    assert not record['qualification']['qualified'] and len(run.lineage) == 1
