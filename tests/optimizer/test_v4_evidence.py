"""Evidence preserves structured differences, paired coverage and preparation identity."""
from copy import deepcopy
import json
import re
from pathlib import Path

import pytest

from rag_eval.optimizer.adapter import Card, Diff, Version
from rag_eval.optimizer.sn_trajectory import diagnostic_view
from rag_eval.optimizer.v4.sn_adapter_v4 import SNAdapterV4, fit_blocks, render_pair
from rag_eval.workbench.briefs import v4_analyst_brief, v4_implementer_brief


def view(answer='first\nsecond', **overrides):
    out = diagnostic_view(dict(status='success', prediction=answer, product_record=dict(question='Why?\nExplain.')))
    out["answer"] = answer
    out.update(overrides)
    return out


def test_pair_compares_complete_multiline_fields_and_canonical_mapping_order():
    a = view(answer='same start\nA continuation: important\u2028still same field')
    b = view(answer='same start\nB continuation: different')
    text = render_pair('p1', a, b)
    assert 'A | answer:' in text and 'B | answer:' in text
    assert 'A continuation: important' in text and 'B continuation: different' in text
    assert '\\nA continuation' in text
    fitted = fit_blocks([text], 1500)
    assert fitted.count('A | answer:') == 1 and fitted.count('B | answer:') == 1
    a['intent'] = dict(confidence=.5, planned_queries=['a', 'b'])
    b = deepcopy(a)
    b['intent'] = dict(reversed(list(deepcopy(a['intent']).items())))
    assert 'A and B are identical' in render_pair('p2', a, b)
    b['intent']['planned_queries'].reverse()
    assert 'A | intent:' in render_pair('p3', a, b)
    a['steps'] = [dict(step='retrieve', count=0), dict(step='synthesis', citations=1)]
    b = deepcopy(a)
    b['steps'].reverse()
    assert 'B | steps:' in render_pair('p-step-order', a, b)
    b = deepcopy(a)
    b['question'] = 'Actually another question'
    assert 'B | question:' in render_pair('p4', a, b)


def test_budget_keeps_all_pairs_both_sides_and_explicit_truncation():
    long = 'Detailed observed answer with citations and multiline explanations.\n' * 150
    blocks = [render_pair(f'p{i}', view(long), view(long + 'changed')) for i in range(8)]
    blocks += [render_pair('p8', view(long), view(long)), render_pair('p9', None, view(long))]
    for budget in (1800, 4000, 12000):
        text = fit_blocks(blocks, budget)
        assert len(text) <= budget
        assert '[truncated]' in text
        assert re.findall(r'^### (p\d+)$', text, re.M) == [f'p{i}' for i in range(10)]
        for body in re.split(r'^### p\d+\n', text, flags=re.M)[1:]:
            assert 'A | ' in body and 'B | ' in body
    with pytest.raises(ValueError, match='budget'):
        fit_blocks(blocks, 10)
    assert len(fit_blocks(['### p1\nA | answer: "x"\nB | answer: "y"\n'], 100)) <= 100


def test_review_budget_includes_large_card_and_diff(tmp_path, monkeypatch):
    adapter = SNAdapterV4(dict(iteration_id='iteration', task_id='task', run_id='run'),
                          reviewer_view_budget=2200, evidence_dir=tmp_path)
    monkeypatch.setattr(adapter, 'diagnostic_views', lambda trajectories: {t: view(str(s) + ' result ' * 1000)
                                                                        for t, s in trajectories.items()})
    def poll(role, cid, call):
        verdict = re.search(r'to the file (\S+) and nothing else', call['text']).group(1)
        Path(verdict).write_text(json.dumps(dict(pairs=[])))
    monkeypatch.setattr(adapter, '_poll_agent', poll)
    pairs = [dict(pair=f'p{i}', task='hidden-case', A=['parent'], B=['candidate']) for i in range(8)]
    adapter.review(Card('failure ' * 2000, 'hypothesis ' * 2000), Diff('diff line\n' * 4000), pairs,
                   candidate=Version('v', metadata=dict(candidate_id='candidate')), key='review')
    text = Path(adapter.last_review['evidence_path']).read_text()
    assert len(text) <= 2200 and '[truncated]' in text
    assert len(re.findall(r'^### p\d+$', text, re.M)) == 8
    assert len(re.findall(r'^[AB] \| answer:', text, re.M)) == 16
    assert 'hidden-case' not in text


def test_preparation_symbols_are_retained_and_uncertainty_is_honest(tmp_path):
    adapter = SNAdapterV4({}, evidence_dir=tmp_path)
    paths = adapter.preparation_paths()
    assert 'backend/app/services/prompts.py::concept_description_prompt' in paths
    assert 'backend/app/services/prompts.py' not in paths
    def change(path, body='@@ -1 +1 @@\n-old\n+new\n'):
        return Diff(f'diff --git a/{path} b/{path}\n--- a/{path}\n+++ b/{path}\n{body}')
    assert adapter.touches_preparation(change('backend/app/services/chunking.py')) is True
    assert adapter.touches_preparation(change('backend/app/services/answerer.py')) is False
    assert adapter.touches_preparation(change('backend/app/services/prompts.py')) is None
    assert adapter.touches_preparation(change('backend/app/services/prompts.py',
                                             '@@ -1 +1 @@\n-def concept_description_prompt():\n+def concept_description_prompt():\n')) is True
    assert adapter.touches_preparation(Diff('')) is None
    path, _ = adapter._preparation_file()
    guidance = path.read_text() + adapter._evidence_extra()
    assert '::concept_description_prompt' in guidance
    assert 'every evaluation' not in guidance and 'more than an hour' not in guidance


def test_agent_briefs_allow_search_scores_without_validation_or_test_access():
    analyst = v4_analyst_brief(dict(id='t', title='task'), tool='sn', candidate_id='p')
    implementer = v4_implementer_brief(dict(id='t'), tool='sn', hypothesis_id='h', candidate_id='c', worktree='/repo')
    for text in (analyst, implementer):
        assert 'search scores' in text.lower()
        assert 'validation or test' in text.lower()
        assert 'every evaluation' not in text and 'more than an hour' not in text
    assert 'no gold answers, scores' not in analyst


def test_safeguard_cache_requires_terminal_output_provenance_and_rechecks_access(tmp_path):
    from types import SimpleNamespace
    adapter = SNAdapterV4({})
    output = tmp_path / 'outputs.jsonl'
    state = tmp_path / 'state.json'
    row = dict(case_id='case', prediction='answer', product_record=dict(question='question',
               response=dict(answer='answer'), usage=dict(total_tokens=100)))
    output.write_text(json.dumps(row) + '\n')
    state.write_text(json.dumps(dict(phase='running')))
    calls = []
    checks = []
    def observation(run_id, case_id):
        calls.append((run_id, case_id))
        current = json.loads(output.read_text())
        rec = current['product_record']
        return dict(question=rec['question'], prediction=current['prediction'], response=rec['response'],
                    usage=rec['usage'], status='mutable-status')
    views = SimpleNamespace(_entry=lambda run_id: SimpleNamespace(run_dir=tmp_path),
                            _assert_unsealed=lambda run_id, case_id: checks.append(case_id), observation=observation)
    stub = dict(run_id='run', execution_id='execution')
    adapter._safeguard_observation(views, stub, 'case')
    adapter._safeguard_observation(views, stub, 'case')
    assert len(calls) == 2 and not adapter._safeguard_cache
    state.write_text(json.dumps(dict(phase='finished')))
    adapter._safeguard_observation(views, stub, 'case')
    cached = adapter._safeguard_observation(views, stub, 'case')
    assert len(calls) == 3 and len(checks) == 4
    assert 'status' not in cached
    cached['usage']['total_tokens'] = -1
    assert adapter._safeguard_observation(views, stub, 'case')['usage']['total_tokens'] == 100
    row['product_record']['usage']['total_tokens'] = 200
    output.write_text(json.dumps(row) + '\n')
    assert adapter._safeguard_observation(views, stub, 'case')['usage']['total_tokens'] == 200
    assert len(calls) == 4
    # A sealed access policy still refuses a cached observation.
    def sealed(*args):
        raise ValueError('sealed')
    views._assert_unsealed = sealed
    with pytest.raises(ValueError, match='sealed'):
        adapter._safeguard_observation(views, stub, 'case')


def test_safeguard_infrastructure_is_always_reobserved(monkeypatch):
    from types import SimpleNamespace
    from rag_eval.optimizer.adapter import EvalResult
    adapter = SNAdapterV4({})
    monkeypatch.setattr(adapter, '_protocol', lambda: dict(safeguards=[
        dict(key='infrastructure_failure_rate', direction='decrease', margin=.1)]))
    monkeypatch.setattr(adapter, '_require_round_ops', lambda _: SimpleNamespace(
        repository=None, service=SimpleNamespace(candidate=lambda cid: dict(id=cid))))
    monkeypatch.setattr(adapter, '_views', lambda: object())
    monkeypatch.setattr(adapter, '_safeguard_observation', lambda *args: dict(question='q', prediction='a'))
    rows = dict(total=1, failed=0, unobserved=0)
    calls = []
    def infrastructure(*args):
        calls.append(args)
        return [dict(rows)]
    monkeypatch.setattr(adapter, '_infrastructure', infrastructure)
    result = EvalResult({}, dict(case=[dict(run_id='r')]), {}, {},
                        context=dict(candidate_id='parent', scopes=dict(case='scope')))
    assert adapter.observe_safeguards(result, result)['infrastructure_failure_rate']['reference'] == 0
    rows['failed'] = 1
    assert adapter.observe_safeguards(result, result)['infrastructure_failure_rate']['reference'] == 1
    assert len(calls) == 4


def test_realistic_long_traces_keep_each_changed_field_on_both_sides():
    paragraphs = 'The retrieved paper describes its datasets, protocol and limitations.\n' * 100
    a = view(paragraphs, steps=[dict(step='retrieve', query=paragraphs, count=0),
                               dict(step='reflect', reason=paragraphs, sufficient=False)],
             intent=dict(planned_queries=[paragraphs], needs_clarification=False))
    b = view(paragraphs + 'New conclusion.',
             steps=[dict(step='retrieve', query=paragraphs + ' revised query', count=4),
                    dict(step='reflect', reason=paragraphs, sufficient=True)],
             intent=dict(planned_queries=[paragraphs + ' more specific'], needs_clarification=False))
    blocks = [render_pair(f'p{i}', a, b) for i in range(6)] + [render_pair('p6', a, a)]
    text = fit_blocks(blocks, 3200)
    assert len(text) <= 3200 and text.count('[truncated]') > 0
    for i in range(6):
        body = text.split(f'### p{i}\n', 1)[1].split('\n### ', 1)[0]
        for key in ('intent', 'steps', 'answer'):
            assert f'A | {key}: ' in body and f'B | {key}: ' in body
    assert '### p6\n' in text and 'A | comparison: identical' in text


def test_card_evidence_passes_search_context_to_implementer(tmp_path):
    adapter = SNAdapterV4({}, evidence_dir=tmp_path)
    card = Card('missing retrieval', 'fix query', ref='card-id')
    path, digest, _ = adapter._write_evidence('analyst-search.md', 'search scores: 0.2\nobserved failure\n')
    adapter._cards_analysed([card], path, digest)
    extra = adapter._implementer_extra_text(card, {})
    assert str(path) in extra and digest in extra and 'search cases only' in extra
    assert 'every evaluation' not in extra and 'more than an hour' not in extra
