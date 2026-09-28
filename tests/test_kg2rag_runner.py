import importlib.util
from copy import deepcopy
from types import SimpleNamespace


def bridge():
    assert importlib.util.find_spec('rag_eval.kg2rag_runner'), 'KG2RAG runner missing'
    from rag_eval import kg2rag_runner
    return kg2rag_runner


def test_public_inputs_preserve_blank_sentence_and_ignore_gold():
    from rag_eval.notebook_data import adapt
    m = bridge()
    bundle = adapt('hotpotqa', [{'_id': 'q', 'question': 'Question?', 'answer': 'secret',
                               'context': [['Title', ['', 'Second.']]], 'type': 'bridge', 'level': 'hard',
                               'supporting_facts': [['Title', 1]]}],
                   adaptation_revision='notebook-data-v3')
    expected = [{'_id': 'q', 'question': 'Question?', 'context': [['Title', ['', 'Second.']]]}]
    assert m.public_samples(bundle) == expected
    changed = deepcopy(bundle)
    changed['cases'][0]['gold'] = {'answer': 'different', 'supporting_facts': [902]}
    assert m.public_samples(changed) == expected


def test_context_cache_does_not_reuse_same_title_with_different_sentences(tmp_path):
    m = bridge()
    calls = []
    def extract(llm, text):
        calls.append(text)
        return [[text, 'relation', 'entity']]
    first = m.context_kg('Title', ['First.', ''], tmp_path, extract, None)
    second = m.context_kg('Title', ['Different.', ''], tmp_path, extract, None)
    assert first != second
    assert calls == ['First.', 'Title: ', 'Different.', 'Title: ']
    assert m.context_kg('Title', ['First.', ''], tmp_path, extract, None) == first
    assert len(calls) == 4


def test_failed_generation_is_error_and_next_case_still_saved(tmp_path):
    m = bridge()
    samples = [{'_id': 'fail', 'question': 'Fail?', 'context': []},
               {'_id': 'ok', 'question': 'OK?', 'context': []}]
    def process(args, sample, kg):
        if sample['_id'] == 'fail':
            raise RuntimeError('inference failed')
        return sample['_id'], '', []
    mod = SimpleNamespace(process_sample=process)
    rows = m.run_samples(samples, mod, SimpleNamespace(), lambda *_: [], None, tmp_path)
    assert [r['status'] for r in rows] == ['error', 'no_answer']
    assert rows[0]['record']['error']['type'] == 'RuntimeError'
    assert rows[1]['record']['predicted_supporting_facts'] == []
    assert len((tmp_path / 'events.jsonl').read_text().splitlines()) == 2


def test_train_demonstrations_cannot_overlap_evaluated_questions():
    import pytest
    m = bridge()
    demos = {'split': 'train', 'examples': [{'id': 'train', 'question': 'Same?', 'answer': 'yes'}]}
    with pytest.raises(ValueError, match='overlap'):
        m.qa_prompt(demos, [{'_id': 'test', 'question': 'Same?'}])


def test_reranker_identity_includes_weights_in_hf_cache_and_setup_failure(tmp_path):
    import json
    import pytest
    m = bridge()
    model = tmp_path / '.cache/huggingface/model'
    model.mkdir(parents=True)
    weights = model / 'model.safetensors'
    weights.write_bytes(b'fixture weights, not a model')
    output = tmp_path / 'run'
    (output / 'adapted-source').mkdir(parents=True)
    # Stop before any model imports, after the real runtime identity is saved.
    (output / 'adapted-source/kg_rag_distractor.py').write_text("raise RuntimeError('fixture stop')\n")
    method = dict(name='adapted-kg2rag-fixture', kind='reference', citation_style='none',
                  model_identity={'model': 'fixture'}, input_policy='distractor context',
                  configuration={'comparison_category': 'controlled-rerun'})
    manifest = dict(implementation_sha256='fixture', adapted_source_hashes={},
                    demonstrations_sha256='fixture', case_ids=['hotpotqa:q'])
    bundle = {'manifest': {'suite': 'hotpotqa'}, 'cases': [{'case_id': 'hotpotqa:q'}]}
    args = SimpleNamespace(reranker=str(model), model_name='fixture', embed_model_name='fixture',
                           top_k=10, use_tpt=False, reranker_device='cpu')
    with pytest.raises(RuntimeError, match='fixture stop'):
        m.execute_prepared(bundle, [], manifest, output, args, method)
    recorded = json.loads((output / 'method.json').read_text())
    import hashlib
    assert recorded['configuration']['reranker_files'] == {
        'model.safetensors': hashlib.sha256(weights.read_bytes()).hexdigest()}
    assert json.loads((output / 'manifest.json').read_text())['state'] == 'failed'
