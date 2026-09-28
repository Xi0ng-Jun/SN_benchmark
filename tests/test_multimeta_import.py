import importlib.util

import pytest


def test_multimeta_joins_by_query_preserves_empty_output_and_checks_actual_prompt():
    assert importlib.util.find_spec('rag_eval.multimeta_import'), 'native Multi-Meta importer is missing'
    from rag_eval.multimeta_import import prepare_rows

    bundle = {'cases': [
        {'case_id': 'one', 'question': 'First?', 'gold': {
            'answer': 'A', 'question_type': 'inference_query', 'evidence': [{'fact': 'fact A'}]}},
        {'case_id': 'two', 'question': 'Second?', 'gold': {
            'answer': 'null', 'question_type': 'null_query', 'evidence': []}},
    ]}
    # The author files need not share order. Gold observations are not model answers.
    rankings = [
        {'query': 'Second?', 'answer': 'null', 'question_type': 'null_query',
         'retrieval_list': [], 'gold_list': []},
        {'query': 'First?', 'answer': 'A', 'question_type': 'inference_query',
         'retrieval_list': [{'text': 'fact A', 'score': .5}], 'gold_list': [{'fact': 'fact A'}]},
    ]
    prefix = ("You will be provided with questions followed by some context from different sources. "
              "Please answer the question based on the context. The answer to the question is a word or entity. "
              "If the provided information is insufficient to answer the question, respond 'Insufficient Information'. "
              "Answer directly without explanation.")
    answers = [
        {'query': 'First?', 'model_answer': '', 'gold_answer': 'A', 'question_type': 'inference_query',
         'prompt': prefix + '\n\nQuestion:First?\n\nContext:\n\nfact A'},
        {'query': 'Second?', 'model_answer': 'Insufficient Information', 'gold_answer': 'null',
         'question_type': 'null_query', 'prompt': prefix + '\n\nQuestion:Second?\n\nContext:\n\n'},
    ]
    rows, mappings, audit = prepare_rows(bundle, answers, rankings, model='palm',
                                         answer_sha256='a' * 64, retrieval_sha256='b' * 64)
    assert [m['case_id'] for m in mappings] == ['one', 'two']
    assert rows[0]['prediction'] == ''
    assert rows[0]['status'] == 'no_answer'  # Observed empty output remains in the denominator.
    assert rows[0]['retrieval']['source_row'] == 1
    assert rows[0]['retrieval']['ranked'] == [{'text': 'fact A', 'score': .5}]
    assert 'gold_answer' not in rows[0]
    assert audit['empty_answers'] == 1
    answers[0]['prompt'] += 'unobserved extra context'
    with pytest.raises(ValueError, match='prompt'):
        prepare_rows(bundle, answers, rankings, model='palm',
                     answer_sha256='a' * 64, retrieval_sha256='b' * 64)
