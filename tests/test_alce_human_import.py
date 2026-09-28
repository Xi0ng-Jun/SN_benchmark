import importlib.util


def test_human_eval_import_maps_exact_questions_and_excludes_aggregate_and_labels():
    assert importlib.util.find_spec('rag_eval.alce_human_import'), 'ALCE human-eval answer importer is missing'
    from rag_eval.alce_human_import import prepare_method

    bundle = {'cases': [{'case_id': 'alce:asqa:7', 'question': 'Which bird?', 'task': 'asqa'},
                        {'case_id': 'alce:asqa:3', 'question': 'Where?', 'task': 'asqa'}]}
    source = {
        'native-id-90': {'id': 'native-id-90', 'question': 'Where?', 'output': '', 'utility_score': 5},
        'overall_results': {'overall_precision_score': 1},
        'native-id-42': {'id': 'native-id-42', 'question': 'Which bird?',
                         'output': 'Falcon [4].\nExtra.', 'overall_recall_score': 1, 'sentences': []},
    }
    method, rows, mappings, audit = prepare_method(
        bundle, 'asqa-gpt-35-turbo-gtr-shot2-ndoc5-42-azure.json', source, source_sha256='f' * 64)
    assert len(rows) == 2
    assert [m['case_id'] for m in mappings] == ['alce:asqa:3', 'alce:asqa:7']
    assert rows[0]['prediction'] == '' and rows[0]['status'] == 'no_answer'
    assert rows[1]['prediction'] == 'Falcon [4].\nExtra.'
    assert 'overall_recall_score' not in rows[1] and 'utility_score' not in rows[0]
    assert rows[1]['record']['native_source']['question_id'] == 'native-id-42'
    assert method['configuration']['citation_mapping_status'] == 'unavailable'
    assert audit['excluded_aggregate_keys'] == ['overall_results']
