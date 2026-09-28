from copy import deepcopy
import importlib.util
import json

import pytest


def adapter():
    assert importlib.util.find_spec('rag_eval.alce_vanilla'), 'ALCE VANILLA runner missing'
    from rag_eval import alce_vanilla
    return alce_vanilla


def case():
    return dict(case_id='alce:asqa:1', task='asqa', question='Which bird?', gold={'secret': 'Falcon'},
                candidate_documents=[dict(id=f'd{i}', title=f'Title{i}', text=f'Text{i}', answer='secret')
                                     for i in range(1, 7)])


def renderer(item, **kwargs):
    # Deliberately render every field: a leaked label must affect this output.
    return json.dumps(item, sort_keys=True)


def test_prompt_sees_only_question_and_top_five_public_documents():
    m = adapter()
    c = case()
    r = m.make_request(c, {}, 'DEMO\n', renderer)
    item = json.loads(r['prompt'].removeprefix('DEMO\n'))
    assert set(item) == {'question', 'docs'}
    assert len(item['docs']) == 5
    assert item['docs'][0] == {'title': 'Title1', 'text': 'Text1'}
    assert r['citation_index_to_document_id'] == {str(i): f'd{i}' for i in range(1, 6)}
    changed = deepcopy(c)
    changed['gold'] = {'secret': 'Different answer'}
    assert m.make_request(changed, {}, 'DEMO\n', renderer) == r


def test_demonstrations_cannot_overlap_even_outside_selected_case_scope():
    m = adapter()
    with pytest.raises(ValueError, match='overlap'):
        m.check_demo_overlap({'demos': [{'question': '  WHICH bird? '}]}, [case()])


def test_generation_preserves_shown_citations_and_distinguishes_error_blank_and_budget(tmp_path):
    m = adapter()
    requests = [dict(case_id=f'alce:asqa:{i}', question='Which bird?', prompt=p,
                     docs=[dict(title='T', text='Text')], citation_index_to_document_id={'1': 'd1'})
                for i, p in enumerate(['answer', 'blank', 'fail', 'long'])]
    class LLM:
        class Tokenizer:
            @staticmethod
            def tokenize(prompt):
                return [0] * (4096 if prompt == 'long' else 3)
            def __call__(self, prompt):
                return {'input_ids': self.tokenize(prompt)}
        tokenizer = Tokenizer()
        def generate(self, prompt, max_tokens):
            assert max_tokens == 300
            if prompt == 'fail':
                raise RuntimeError('fixture failure')
            if prompt == 'long':
                pytest.fail('exhausted context must not reach generation')
            return 'Falcon [1] and [6].<|im_end|>End.' if prompt == 'answer' else '  '
    rows = m.generate_rows(requests, LLM(), tmp_path)
    assert [r['status'] for r in rows] == ['success', 'no_answer', 'error', 'error']
    assert rows[0]['prediction'] == 'Falcon [1] and [6].'
    assert rows[0]['record']['citation_index_to_document_id'] == {'1': 'd1'}
    assert rows[2]['record']['error']['stage'] == 'generation'
    assert rows[3]['record']['error']['stage'] == 'context_budget'
    assert len((tmp_path / 'events.jsonl').read_text().splitlines()) == 4
    assert json.loads((tmp_path / 'native-output.json').read_text())['data'][0]['docs'] == requests[0]['docs']
    from rag_eval.benchmark_official import prepare_inputs
    from rag_eval.benchmark_submission import build_submission
    c = case()
    rows[0]['case_id'] = c['case_id']
    bundle = dict(manifest=dict(suite='alce', adaptation_revision='notebook-data-v3'), cases=[c])
    method = dict(name='fixture', kind='reference', citation_style='numeric', model_identity={'test': True},
                  input_policy='top5', configuration={})
    prepared = prepare_inputs(bundle, build_submission(bundle, method=method, predictions=[rows[0]]))
    assert prepared['data'][0]['output'] == 'Falcon [1] and [7].'


def test_generation_budget_includes_llama_bos_without_rejecting_viable_prompt(tmp_path):
    m = adapter()
    class Tokenizer:
        def tokenize(self, prompt):
            return [4] * 3900
        def __call__(self, prompt):
            return {'input_ids': [1] + [4] * 3900}
    class LLM:
        tokenizer = Tokenizer()
        def generate(self, prompt, max_tokens):
            assert max_tokens == 195
            return 'Answer [1].'
    request = dict(case_id='one', question='Q', prompt='long but viable', docs=[],
                   citation_index_to_document_id={})
    row, = m.generate_rows([request], LLM(), tmp_path)
    assert row['status'] == 'success'
    assert row['record']['input_tokens'] == 3901
    assert row['record']['max_new_tokens'] == 195
