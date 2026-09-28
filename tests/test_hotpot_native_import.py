import importlib.util

import pytest


def test_native_hotpot_predictions_preserve_unknown_support_and_missing_answers():
    assert importlib.util.find_spec('rag_eval.hotpot_native_import'), 'native Hotpot importer is missing'
    from rag_eval.hotpot_native_import import prepare_rows

    bundle = {'cases': [{'sample_id': 'a', 'case_id': 'hotpotqa:a'},
                        {'sample_id': 'b', 'case_id': 'hotpotqa:b'},
                        {'sample_id': 'c', 'case_id': 'hotpotqa:c'}]}
    source = {'answer': {'b': '', 'a': 'Alpha'},
              'sp': {'a': [['Unknown title', 902], ['Unknown title', 902]], 'b': []}}
    rows, mappings = prepare_rows(bundle, source)
    assert [x['external_id'] for x in rows] == ['a', 'b']
    assert rows[0]['record']['predicted_supporting_facts'] == [
        ['Unknown title', 902], ['Unknown title', 902]]
    assert rows[1]['status'] == 'no_answer'
    assert rows[1]['record']['predicted_supporting_facts'] == []
    assert [x['case_id'] for x in mappings] == ['hotpotqa:a', 'hotpotqa:b']
    source['sp'].pop('b')
    assert 'predicted_supporting_facts' not in prepare_rows(bundle, source)[0][1]['record']
    source['answer']['outside'] = 'unexpected'
    with pytest.raises(ValueError, match='unknown'):
        prepare_rows(bundle, source)
