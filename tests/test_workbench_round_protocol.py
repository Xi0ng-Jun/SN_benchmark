"""Round options are frozen protocol, including requests submitted through sn-eval."""
import pytest

from test_workbench_iterations import env, iteration
from rag_eval.workbench.action_requests import normalize
from rag_eval.workbench.loop import ROUND_DEFAULTS
from rag_eval.workbench.repository import Conflict


def test_round_settings_freeze_defaults_and_explicit_overrides(env):
    created = iteration(env, round_settings=dict(b=3, card_approval='manual', infra_retry=2))
    expected = dict(ROUND_DEFAULTS, b=3, card_approval='manual', infra_retry=2)
    assert created['protocol']['round_settings'] == expected
    assert iteration(env, round_settings=expected)['id'] == created['id']
    with pytest.raises(Conflict):
        iteration(env, round_settings=dict(b=1))
    defaults = iteration(env, request_key='defaults')
    assert defaults['protocol']['round_settings'] == ROUND_DEFAULTS


@pytest.mark.parametrize('settings', [dict(b=7), dict(typo=1), dict(agent_timeout_seconds=0), [], False])
def test_invalid_round_settings_refused(env, settings):
    with pytest.raises(ValueError):
        iteration(env, round_settings=settings)


def test_iteration_action_accepts_frozen_round_and_execution_options():
    params = dict(title='loop', start_commit='a'*40, input_id='x', expected_bundle_id='b'*64,
                  case_ids=['c1'], mode='chunk', round_settings=dict(card_approval='manual'),
                  execution=dict(preparation_policy='deferred-notebook-metadata-v1'))
    assert normalize('iteration-create', params) == params


def test_iteration_action_accepts_pre_registered_gate_rule_and_safeguards():
    # lcd-v2 iterations are created through `sn-eval iteration create --file`, which goes through normalize.
    safeguards = [dict(key='infrastructure_failure_rate', direction='lower_is_better', margin=.1),
                  dict(key='cost_per_task', direction='lower_is_better', margin=.5),
                  dict(key='answer_language_match', direction='higher_is_better', margin=.05)]
    params = dict(title='loop', start_commit='a'*40, input_id='x', expected_bundle_id='b'*64,
                  case_ids=['c1'], mode='chunk', gate_rule='lcd-v2', safeguards=safeguards)
    assert normalize('iteration-create', params) == params
