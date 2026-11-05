"""Prompt policy checks; these do not prove that a live agent follows the policy."""
from rag_eval.workbench.briefs import v4_analyst_brief, v4_implementer_brief


def analyst():
    return v4_analyst_brief(dict(id='task', title='Iteration'), tool='sn-eval', candidate_id='parent')


def implementer():
    return v4_implementer_brief(dict(id='task'), tool='sn-eval', hypothesis_id='hypothesis',
                                candidate_id='candidate', worktree='/sn')


def test_analyst_prioritizes_bounded_candidates_without_requiring_causal_proof():
    text = analyst()
    assert 'small, reversible answer-path changes' in text
    assert '1–2 cheap checks' in text
    assert 'Full causal proof is not a prerequisite' in text
    assert 'Do not invent cards to fill a quota' in text
    assert 'Never seek gold answers, benchmark validation or test cases' in text
    assert 'benchmark-specific branches' in text


def test_analyst_uses_failed_history_to_revise_the_hypothesis_not_repeat_it():
    text = analyst()
    assert 'Do not repeat rejected or already-satisfied changes' in text
    assert 'materially revised hypothesis' in text
    assert 'inconclusive preflight is not a measured rejection' in text
    assert 'what changed and how it addresses the earlier failure' in text


def test_implementer_can_try_a_supported_small_change_with_explicit_uncertainty():
    text = implementer()
    assert '1–2 cheap checks' in text
    assert 'prefer implement/inconclusive' in text
    assert 'Do not skip solely because causal attribution remains uncertain' in text
    assert 'If the hypothesis is refuted or already satisfied' in text
    assert 'skip when no concrete change is justified' in text
    assert 'Both implement and skip require 1–32 concrete checks actually attempted' in text
    assert 'benchmark-specific branches' in text
    assert 'Never seek gold answers, benchmark validation or test cases' in text
