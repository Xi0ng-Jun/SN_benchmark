import importlib.util

import pytest


def test_socratic_import_uses_author_query_order_and_retains_empty_predictions():
    assert importlib.util.find_spec('rag_eval.qmsum_socratic_import'), 'Socratic QMSum importer is missing'
    from rag_eval.qmsum_socratic_import import prepare_rows

    raw = [{'general_query_list': [{'query': 'Whole?', 'answer': 'Summary'}],
            'specific_query_list': [{'query': 'Why?', 'answer': 'Reason'}]}]
    # Do not rely on the incidental ordering of cases in a Python collection.
    bundle = {'cases': [
        {'case_id': 'qmsum:0:specific:0', 'sample_id': '0:specific:0',
         'question': 'Why?', 'gold': {'answer': 'Reason'}},
        {'case_id': 'qmsum:0:general:0', 'sample_id': '0:general:0',
         'question': 'Whole?', 'gold': {'answer': 'Summary'}},
    ]}
    rows, mapping = prepare_rows(bundle, raw, ['Generated summary', ''])
    assert [x['case_id'] for x in mapping] == ['qmsum:0:general:0', 'qmsum:0:specific:0']
    assert [x['prediction'] for x in rows] == ['Generated summary', '']
    assert rows[1]['status'] == 'no_answer'
    assert 'gold' not in rows[0] and 'answer' not in rows[0]
    assert mapping[1]['source_row'] == 1
    with pytest.raises(ValueError, match='count'):
        prepare_rows(bundle, raw, ['Generated summary'])
    raw[0]['specific_query_list'][0]['query'] = 'A different question?'
    with pytest.raises(ValueError, match='question'):
        prepare_rows(bundle, raw, ['Generated summary', ''])
