"""LCD role instructions preserve execution order and evidence boundaries."""
from rag_eval.workbench.briefs import analyst_brief, implementer_brief
from rag_eval.workbench.clusters import FIELDS


TASK = {'id': 'task', 'title': 'Iteration', 'goal': 'Improve retrieval'}
CARD = {'title': 'Retrieval', 'component': 'retrieval', 'mechanism': 'Rerank chunks',
        'implementer_brief': 'Rerank retrieved chunks.',
        'contract': {'cluster_id': 'retrieval_gap', 'predicted_flips': ['private-case'], 'guards': []}}


def brief(card=CARD, **kwargs):
    return implementer_brief(TASK, card, worktree='/sn', tool='sn-eval',
                             hypothesis_id='hypothesis', start_dirty=[], candidate_id='candidate', **kwargs)


def test_cluster_implementation_sequence_and_frozen_attempt_count():
    text = brief(k_base=4)
    steps = ['node freeze candidate', 'node cluster-run candidate',
             'cluster result retrieval_gap', 'node run candidate --attempts 4']
    positions = [text.index(step) for step in steps]
    assert positions == sorted(positions)
    assert text.index('leak-check') < positions[0]
    assert '禁止全量套件' in text
    assert '冻结预算内' in text and '自动确认' in text
    assert 'private-case' not in text
    assert 'predicted_flips' not in text


def test_default_attempt_count_and_no_cluster_sequence():
    card = {key: value for key, value in CARD.items() if key != 'contract'}
    text = brief(card)
    assert 'node run candidate --attempts 2' in text
    assert 'node cluster-run' not in text and 'cluster result' not in text


def test_integration_only_merges_and_skips_cluster_execution():
    text = brief(dict(CARD, component='integration'))
    assert '不加新改动' in text
    assert 'node cluster-run' not in text
    assert 'node run candidate --attempts 2' in text


def test_analyst_has_complete_mechanical_cluster_and_contract_instructions():
    text = analyst_brief(TASK, eval_root='/eval', tool='sn-eval', worktree=None,
                         cwd='/eval', candidates=[('candidate', 'commit', '/sn')])
    for field in FIELDS:
        assert field in text
    for token in ('cluster propose --file cluster.json', 'missing_signature', 'violation_signature',
                  'all_of', 'any_of', '"not"', 'retrieval_context.*.text',
                  'metrics.<official_primary_metric>', '写入即冻结', 'predicted_flips',
                  'guards', '不重叠', '|C|·(1−q)·max(m, v, π)', 'null', '|C|·(1−q)·π'):
        assert token in text


def test_implementer_waits_for_its_cluster_result_in_one_call():
    text = brief()
    assert 'cluster result retrieval_gap --wait 1200' in text
    assert text.index('node cluster-run candidate') < text.index('--wait 1200') < text.index('node run candidate')
    assert '不要写 shell sleep' in text and 'infrastructure_error' in text and '停止' in text
    assert '--wait' not in brief({key: value for key, value in CARD.items() if key != 'contract'})
