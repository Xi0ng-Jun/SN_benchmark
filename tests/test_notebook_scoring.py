"""Offline hand-calculated scorer and citation conversion checks."""
import copy
import importlib.metadata
import json
import subprocess
import sys
from pathlib import Path

import pytest

from rag_eval.notebook_scoring import metric_specs, score_case
from rag_eval.notebook_alce import export_case, freeze_official_checkout, run_official


def case(suite, task='', **gold):
    return {'case_id': 'c1', 'suite': suite, 'task': task, 'question': 'Question?',
            'gold': gold, 'references': [], 'material_document_ids': ['d1'],
            'gold_document_ids': ['d1']}


def record(answer, **kw):
    return {'status': 'success', 'answer': answer, **kw}


def test_qasper_best_annotation_body_only_and_official_empty_behavior():
    item = case('qasper', annotations=[{'answer': 'red blue blue', 'evidence': [], 'type': 'extractive'},
                                     {'answer': 'green', 'evidence': [], 'type': 'extractive'}])
    result = score_case(item, record('red blue [k1]'), 'product.notebook.qasper_answer_token_f1_body_v1')
    assert result['score'] == pytest.approx(.8)
    assert result['details']['scoring_body'] == 'red blue '
    assert score_case(item, record('red blue [1]'), 'product.notebook.qasper_answer_token_f1_body_v1')['score'] == pytest.approx(2/3)
    item['gold']['annotations'] = [{'answer': '', 'evidence': []}]
    assert score_case(item, record('[k1]'), 'product.notebook.qasper_answer_token_f1_body_v1')['score'] == 0


def test_error_and_clarification_never_become_zero_scores():
    item = case('qasper', annotations=[{'answer': 'yes'}])
    for status in ['error', 'clarification', 'no_answer']:
        result = score_case(item, {'status': status, 'answer': 'yes'}, 'product.notebook.qasper_answer_token_f1_body_v1')
        assert result['score'] is None
        assert result['status'] == 'unscored'


def test_multihop_matches_official_whitespace_tokens_and_answer_extraction():
    item = case('multihop_rag', answer='blue whale')
    scorer = 'product.notebook.multihop_official_weak_match_body_v1'
    assert score_case(item, record('BLUE unrelated [k1]'), scorer)['score'] == 1
    assert score_case(item, record('blue,'), scorer)['score'] == 0
    assert score_case(item, record('blue whale. The answer to the question is "green"'), scorer)['score'] == 0


def test_context_paragraphs_require_whole_text_and_reliable_source_mapping():
    item = case('qasper', annotations=[{'answer': 'yes', 'evidence': ['Entire first paragraph.', 'Second paragraph.']}],
                paragraphs=[{'id': 'p1', 'text': 'Entire first paragraph.'}, {'id': 'p2', 'text': 'Second paragraph.'}, {'id': 'p3', 'text': 'Distractor.'}])
    scorer = 'product.notebook.qasper_context_paragraph_f1_v1'
    obs = record('yes', context_supported=True, retrieval_context=['Entire first paragraph. Distractor.', 'Second'],
                 source_ids=['s1', 's1'], source_to_document={'s1': 'd1'})
    result = score_case(item, obs, scorer)
    assert result['score'] == .5
    assert result['details']['ranking_available'] is False
    obs['source_ids'] = ['s1']
    assert score_case(item, obs, scorer)['score'] is None
    obs['source_ids'] = ['s1', 's1']
    obs['source_to_document'] = {'s1': 'other'}
    assert score_case(item, obs, scorer)['score'] == 0


def test_multihop_fact_recall_is_source_bound_and_null_query_is_na():
    item = case('multihop_rag', evidence=[{'document_id': 'd1', 'fact': 'Fact one'}, {'document_id': 'd2', 'fact': 'Fact two'}])
    scorer = 'product.notebook.multihop_context_fact_recall_v1'
    obs = record('yes', context_supported=True, retrieval_context=['Fact one Fact two'], retrieved_document_ids=['d1'])
    assert score_case(item, obs, scorer)['score'] == .5
    item['task'] = 'null_query'
    assert score_case(item, obs, scorer)['status'] == 'not_applicable'


def test_asqa_official_substring_presence_not_word_match():
    item = case('alce', 'asqa', qa_pairs=[{'short_answers': ['York']}, {'short_answers': ['green', 'blue']}])
    obs = record('Yorkshire [k9]')
    assert score_case(item, obs, 'product.notebook.alce_asqa_str_em_body_v1')['score'] == .5
    assert score_case(item, obs, 'product.notebook.alce_asqa_str_hit_body_v1')['score'] == 0


def test_qampari_duplicates_aliases_and_top5_are_official_formula():
    item = case('alce', 'qampari', answers=[['alpha', 'a1'], ['beta'], ['gamma'], ['delta'], ['epsilon'], ['zeta']])
    obs = record('alpha [k1], a1, beta, gamma, delta, epsilon, wrong.')
    expected = {'prec': 6/7, 'rec': 5/6, 'rec_top5': 1, 'f1': 60/71, 'f1_top5': 12/13}
    for key, value in expected.items():
        assert score_case(item, obs, f'product.notebook.alce_qampari_{key}_body_v1')['score'] == pytest.approx(value)


def test_model_metrics_explicitly_deferred_and_roles_are_not_na():
    item = case('alce', 'eli5', claims=['A claim'])
    specs = metric_specs(item)
    primary = next(s for s in specs if s['metric_role'] == 'primary')
    assert primary['scorer'] == 'product.notebook.alce_eli5_claims_official_v1'
    for spec in specs:
        result = score_case(item, record('A claim [k1]'), spec['scorer'])
        assert result['status'] == 'unscored'
        assert result['score'] is None


def test_qmsum_missing_or_wrong_rouge_dependency_is_error(monkeypatch):
    item = case('qmsum', 'general', answer='cats run')
    def missing(name):
        raise importlib.metadata.PackageNotFoundError(name)
    monkeypatch.setattr(importlib.metadata, 'version', missing)
    result = score_case(item, record('cats run'), 'product.notebook.qmsum_rouge1_f1_body_v1')
    assert result['status'] == 'error'
    assert 'rouge-score==0.1.2' in result['reason']
    monkeypatch.setattr(importlib.metadata, 'version', lambda name: '0.0.0')
    assert score_case(item, record('cats run'), 'product.notebook.qmsum_rouge1_f1_body_v1')['status'] == 'error'


def test_qmsum_real_pinned_rouge_and_specific_turn_coverage():
    item = case('qmsum', 'specific', answer='cats run fast', relevant_text_span=[[0, 0], [2, 2]],
                turns=[{'id': 0, 'speaker': 'A', 'content': 'First turn.'}, {'id': 1, 'speaker': 'B', 'content': 'Other turn.'}, {'id': 2, 'speaker': 'A', 'content': 'Last turn.'}])
    try:
        installed = importlib.metadata.version('rouge-score')
    except importlib.metadata.PackageNotFoundError:
        installed = None
    result = score_case(item, record('cat runs [k1]'), 'product.notebook.qmsum_rouge1_f1_body_v1')
    if installed == '0.1.2':
        assert result['score'] == pytest.approx(.8)
    else:
        assert result['status'] == 'error'
    obs = record('summary', context_supported=True, retrieval_context=['A: First turn.'], retrieved_document_ids=['d1'])
    assert score_case(item, obs, 'product.notebook.qmsum_context_turn_recall_v1')['score'] == .5


def alce_case():
    item = case('alce', 'asqa', qa_pairs=[{'question': 'q', 'short_answers': ['yes']}])
    item['candidate_documents'] = [{'id': 'd2', 'title': 'Two', 'text': 'second'}, {'id': 'd1', 'title': 'One', 'text': 'first'}]
    return item


def test_alce_export_only_maps_observed_anchors_and_preserves_whole_body():
    item = alce_case()
    obs = record('Line 1 [k7].\nLine 2 [k3] [k404].\n\nLast paragraph.',
                 source_to_document={'s1': 'd1', 's2': 'd2'},
                 response={'anchors': [{'key': 'k7', 'source_id': 's1'}, {'key': 'k3', 'source_id': 's2'}]})
    before = copy.deepcopy((item, obs))
    result = export_case(item, obs)
    assert result['item']['output'] == 'Line 1 [2].\nLine 2 [1] [3].\n\nLast paragraph.'
    assert result['item']['docs'] == item['candidate_documents']
    assert result['audit']['raw_answer'] == obs['answer']
    assert result['audit']['mapping_errors'][0]['key'] == 'k404'
    assert (item, obs) == before


def test_alce_ambiguous_anchor_and_unproven_numeric_citation_are_invalid():
    obs = record('Yes [k1] [1] [0].', source_to_document={'s1': 'd1', 's2': 'd2'},
                 response={'anchors': [{'key': 'k1', 'source_id': 's1'}, {'key': 'k1', 'source_id': 's2'}]})
    result = export_case(alce_case(), obs)
    assert result['item']['output'] == 'Yes [3] [3] [3].'
    assert len(result['audit']['mapping_errors']) == 3


def test_alce_export_rejects_duplicate_candidate_identity():
    item = alce_case()
    item['candidate_documents'][1]['id'] = 'd2'
    with pytest.raises(ValueError, match='duplicate'):
        export_case(item, record('yes'))


def test_official_checkout_rejects_unpinned_revision_before_import(tmp_path):
    subprocess.run(['git', 'init', '-q', str(tmp_path)], check=True)
    (tmp_path / 'eval.py').write_text('raise RuntimeError("must not import")')
    with pytest.raises(ValueError, match='revision'):
        freeze_official_checkout(tmp_path)


def test_alce_uses_verified_object_mapping_and_rejects_source_conflicts():
    obs = record('Yes [k1] [k2] [k9].', source_to_document={'s1': 'd1'},
                 anchor_documents={'k1': 'd2', 'k2': 'd2', 'k9': 'd1'},
                 response={'anchors': [{'key': 'k1', 'object_id': 'chunk-1'}, {'key': 'k2', 'source_id': 's1'}]})
    result = export_case(alce_case(), obs)
    assert result['item']['output'] == 'Yes [1] [3] [3].'
    assert [e['key'] for e in result['audit']['mapping_errors']] == ['k2', 'k9']


def test_official_runner_requires_explicit_inference_before_any_checkout(tmp_path):
    with pytest.raises(ValueError, match='explicit'):
        run_official(tmp_path / 'missing.json', tmp_path, tmp_path / 'out', metrics=['citations'])


def test_official_subprocess_preserves_body_and_freezes_artifacts(tmp_path, monkeypatch):
    import hashlib
    import rag_eval.notebook_alce as alce
    source = tmp_path / 'source'
    source.mkdir()
    program = '''import os
assert os.environ['HF_HUB_OFFLINE'] == '1'
def compute_autoais(rows, **kwargs):
    assert rows[0]['output'] == 'First [2].\\nSecond.\\n\\nFinal.'
    assert kwargs == {'qampari': False, 'at_most_citations': None}
    return {'citation_rec': 75.0, 'citation_prec': 50.0}
'''
    (source / 'eval.py').write_text(program)
    (source / 'utils.py').write_text('')
    identity = {'repository': alce.OFFICIAL_URL, 'revision': alce.OFFICIAL_REVISION,
                'files': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in source.iterdir()}}
    monkeypatch.setattr(alce, 'freeze_official_checkout', lambda path: identity)
    model = tmp_path / 'model'
    model.mkdir()
    (model / 'config.json').write_text('{}')
    exported = export_case(alce_case(), record('First [k1].\nSecond.\n\nFinal.',
        response={'anchors': [{'key': 'k1', 'source_id': 's1'}]}, source_to_document={'s1': 'd1'}))
    payload = {'data': [exported['item']], 'audit': [exported['audit']]}
    input_path = tmp_path / 'input.json'
    input_path.write_text(json.dumps(payload))
    output = tmp_path / 'out'
    path = run_official(input_path, source, output, metrics=['citations'],
                        allow_model_inference=True, autoais_model=model)
    results = json.loads(path.read_text())
    assert [s['score'] for s in results['scores']] == [.75, .5]
    assert results['scores'][0]['scorer'] == 'product.notebook.alce_citation_rec_official_v1'
    assert results['invocation']['source'] == identity
    assert results['invocation']['models']['autoais']['files']['config.json']
    assert json.loads((output / 'input.json').read_text()) == payload
    assert (output / 'official-source' / 'eval.py').read_text() == program
    assert json.loads((output / 'execution.json').read_text())['status'] == 'completed'
    with pytest.raises(FileExistsError):
        run_official(input_path, source, output, metrics=['citations'], allow_model_inference=True, autoais_model=model)


def test_alce_equivalent_duplicate_candidates_preserve_order_and_audit_indices():
    item = alce_case()
    item['candidate_documents'].append(copy.deepcopy(item['candidate_documents'][0]))
    result = export_case(item, record('yes [k1]', source_to_document={'s': 'd2'},
                                      response={'anchors': [{'key': 'k1', 'source_id': 's'}]}))
    assert result['item']['output'] == 'yes [1]'
    assert len(result['item']['docs']) == 3
    assert result['audit']['conversions'][0]['equivalent_candidate_indices'] == [1, 3]


@pytest.mark.parametrize('retain_captures', [False, True])
def test_reasoning_context_preamble_is_unbound_but_real_passage_scores(retain_captures):
    from rag_eval.system_capture import final_context
    captures = [{'succeeded': True, 'answer': 'Yes [k1]',
                 'context_block': '[Retrieved chunks]\nFact two\nk1: Fact one',
                 'id_map': {'k1': {'object_id': 'chunk', 'source_id': 's'}}}]
    obs = record('Yes [k1]', **final_context(captures), source_to_document={'s': 'd1'},
                 retrieved_document_ids=['d1'])
    if retain_captures:
        obs['captures'] = captures
    before = copy.deepcopy(obs)
    item = case('multihop_rag', evidence=[{'document_id': 'd1', 'fact': 'Fact one'},
                                        {'document_id': 'd1', 'fact': 'Fact two'}])
    result = score_case(item, obs, 'product.notebook.multihop_context_fact_recall_v1')
    assert result['status'] == 'scored'
    assert result['score'] == .5
    assert result['details']['unbound_context_indices'] == [0]
    assert obs == before


@pytest.mark.parametrize('mutation', ['text', 'block', 'handles', 'source', 'arbitrary_extra'])
def test_context_prefix_alignment_rejects_mismatched_saved_capture(mutation):
    from rag_eval.system_capture import final_context
    captures = [{'succeeded': True, 'answer': 'Yes [k1]',
                 'context_block': '[Retrieved chunks]\nk1: Fact one',
                 'id_map': {'k1': {'object_id': 'chunk', 'source_id': 's'}}}]
    obs = record('Yes [k1]', **final_context(captures), source_to_document={'s': 'd1', 'other': 'd1'},
                 retrieved_document_ids=['d1'], captures=captures)
    if mutation == 'text':
        obs['retrieval_context'][1] = 'k1: different passage'
    elif mutation == 'block':
        obs['context_block'] = 'Other prefix\nk1: Fact one'
    elif mutation == 'handles':
        obs['handles'] = ['k99']
    elif mutation == 'source':
        obs['source_ids'] = ['other']
    else:
        obs['retrieval_context'] = ['arbitrary', 'unbound', 'k1: Fact one']
    item = case('multihop_rag', evidence=[{'document_id': 'd1', 'fact': 'Fact one'}])
    result = score_case(item, obs, 'product.notebook.multihop_context_fact_recall_v1')
    assert result['status'] == 'not_applicable'
    assert result['score'] is None
