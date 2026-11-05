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


def test_analyst_reads_evidence_in_one_call_and_previews_clusters():
    text = analyst_brief(TASK, eval_root='/eval', tool='sn-eval', worktree=None,
                         cwd='/eval', candidates=[('candidate', 'commit', '/sn')])
    assert '$S evidence --parent <基准候选>' in text and '--failed-only' in text and '--fields' in text
    assert text.index('$S evidence') < text.index('$S cluster preview --file cluster.json') < text.index(
        '$S cluster propose --file cluster.json')
    assert '不要逐题调用 case' in text and 'runs --all' in text and '按需查阅' in text


def v2_protocol():
    from rag_eval.workbench.gate import RULE_V2
    return dict(gate_rule=dict(RULE_V2), safeguards=[
        dict(key='answer_language_match', direction='higher_is_better', margin=.05),
        dict(key='infrastructure_failure_rate', direction='lower_is_better', margin=.1),
        dict(key='cost_per_task', direction='lower_is_better', margin=.5)])


def v2_analyst(**kwargs):
    return analyst_brief(TASK, eval_root='/eval', tool='sn-eval', worktree=None, cwd='/eval',
                         candidates=[('candidate', 'commit', '/sn')], protocol=v2_protocol(), **kwargs)


def test_v1_protocol_keeps_the_original_contract_and_ranking_text():
    from rag_eval.workbench.gate import DEFAULT_RULE
    default = analyst_brief(TASK, eval_root='/eval', tool='sn-eval', worktree=None, cwd='/eval',
                            candidates=[('candidate', 'commit', '/sn')])
    assert analyst_brief(TASK, eval_root='/eval', tool='sn-eval', worktree=None, cwd='/eval',
                         candidates=[('candidate', 'commit', '/sn')], protocol=dict(gate_rule=DEFAULT_RULE)) == default


def test_v2_analyst_writes_mechanism_with_the_narrower_trajectory_whitelist():
    from rag_eval.workbench.gate import TRAJECTORY_FIELDS
    text = v2_analyst()
    lcd = text[text.index('## LCD'):text.index('## 提交假设卡草稿')]
    for token in ('slice_predicate', 'manipulation', 'slice_effect', '"decrease"|"increase"', 'magnitude',
                  '父版本轨迹', '新鲜批次', '合同不决定采纳', '归因标签', '0.84', '1.65',
                  'answer_language_match', 'infrastructure_failure_rate', 'cost_per_task', '不要为了好兑现把切片写窄'):
        assert token in lcd, token
    allowed = lcd[lcd.index('只能用：'):lcd.index('不能用：')]
    excluded = lcd[lcd.index('不能用：'):lcd.index('——它们来自')]
    assert all(field in allowed for field in TRAJECTORY_FIELDS)
    for field in sorted(FIELDS - TRAJECTORY_FIELDS) + ['metrics.<official_primary_metric>', 'case_id']:
        assert field in excluded and field not in allowed
    # No v1 contract requirement and no v1 ranking estimate.
    assert '"predicted_flips":[' not in text and 'predicted_flips 非空' not in text
    assert '|C|·(1−q)·max(m, v, π)' not in text and '|C|·(1−q)·π' not in text
    assert '|C|·(1 − 父版本在簇内的原始主分均值)' in lcd
    assert '只作披露' in text and '不影响采纳' in text


def test_v2_brief_examples_pass_the_real_v2_validators():
    import json
    from rag_eval.workbench.gate import RULE_V2, validate_mechanism
    from rag_eval.workbench.hypotheses import validate_card
    text = v2_analyst()
    start = text.index('```json', text.index('格式示例')) + len('```json')
    card = json.loads(text[start:text.index('```', start)])
    out = validate_card(card, RULE_V2)
    assert out['predictions'] == [] and out['contract'] == validate_mechanism(card['contract'])
    lcd = text[text.index('## LCD'):]
    start = lcd.index('```json', lcd.index('机制假设 `"contract"`')) + len('```json')
    example = json.loads(lcd[start:lcd.index('```', start)])
    assert validate_mechanism(example['contract']) and example['cluster_id'] == 'retrieval_gap'


def test_v2_iteration_duties_review_confirmation_and_rebase_not_merges():
    text = v2_analyst(tree=dict(section='## 迭代'))
    assert 'rebase 复盘' in text and '归因标签' in text and '合并建议' not in text and '逐题预测兑现' not in text


def test_v2_implementer_learns_adoption_is_net_utility_not_contract():
    text = brief(dict(CARD, cluster_id='retrieval_gap', contract={}), protocol=v2_protocol())
    assert '是否采纳只看验证池筛选与新鲜批次确认上的整体净效用' in text
    assert '合同核对' not in text and '只是簇内诊断' in text
    assert '合同核对' in brief()


def test_method_v3_briefs_are_short_and_name_exactly_the_auto_run_commands():
    import re
    from rag_eval.workbench import auto_runs
    from rag_eval.workbench.briefs import (V3_ANALYST_COMMANDS, V3_IMPLEMENTER_COMMANDS, v3_analyst_brief,
                                           v3_implementer_brief)
    tool = '/srv/benchmark-deepeval/scripts/sn-eval --state /srv/benchmark-deepeval/var/sn-workbench-server/state'
    task = dict(id='9f0c1e2d3b4a49e8a7f6e5d4c3b2a190', title='SN iteration 2026-11 · method-v3')
    analyst = v3_analyst_brief(task, tool=tool, candidate_id='c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0c0')
    implementer = v3_implementer_brief(task, tool=tool, hypothesis_id='h' * 32, candidate_id='c' * 32,
                                       worktree='/srv/worktrees/sn-cand-' + 'c' * 32)
    assert len(analyst) <= 2200 and len(implementer) <= 2000
    named = lambda text: set(re.findall(r'`\$S ([a-z-]+(?: draft)?)', text))  # noqa: E731
    assert named(analyst) == set(V3_ANALYST_COMMANDS) == auto_runs.ANALYST_ALLOWED
    assert named(implementer) == set(V3_IMPLEMENTER_COMMANDS) == auto_runs.IMPLEMENTER_ALLOWED
    assert '--node c0c0' in analyst and 'proposal draft --parent c0c0' in analyst and 'evidence file' in analyst
    assert 'uncommitted' in implementer and 'freeze' in implementer


def test_only_v3_analysts_and_implementers_take_the_v3_brief(tmp_path):
    import pytest
    from test_workbench_agent_creations import setup
    _, task, _, store = setup(tmp_path)
    with pytest.raises(ValueError, match='method-v3 brief'):
        store.prepare(task['id'], 'allowed', actor='alice', request_key='k1', brief='method-v3')
    with pytest.raises(ValueError, match='method-v3 brief'):
        store.prepare(task['id'], 'allowed', actor='alice', request_key='k2', role='analyst', brief='other')
    intent = store.prepare(task['id'], 'allowed', actor='alice', request_key='k3', role='analyst', brief='method-v3')
    assert intent['brief'] == 'method-v3'
    assert 'brief' not in store.prepare(task['id'], 'allowed', actor='alice', request_key='k4', role='analyst')


def test_method_v4_reviewer_brief_and_creation_rules(tmp_path):
    import pytest
    from rag_eval.workbench import auto_runs
    from rag_eval.workbench.briefs import v4_reviewer_brief
    from test_workbench_agent_creations import setup
    text = v4_reviewer_brief(dict(id='t', title='SN · method-v4'), candidate_id='c' * 32)
    assert 'verdict file' in text and '"gaming"' in text and 'evidence file' in text
    assert auto_runs.REVIEWER_ALLOWED == frozenset()
    _, task, _, store = setup(tmp_path)
    with pytest.raises(ValueError, match='method-v4'):
        store.prepare(task['id'], 'allowed', actor='alice', request_key='r1', role='analyst', brief='method-v4')
    with pytest.raises(ValueError):
        store.prepare(task['id'], 'allowed', actor='alice', request_key='r2', role='reviewer', brief='method-v4')
    with pytest.raises(ValueError, match='method-v4'):
        store.prepare(task['id'], 'allowed', actor='alice', request_key='r3', role='reviewer', brief='method-v3')
