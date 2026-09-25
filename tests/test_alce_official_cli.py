import math
import json
import sys

import pytest

from rag_eval.alce_official_cli import alce_arguments, parse_metrics, inspect_cache


def test_task_filename_is_relative_so_parent_qampari_does_not_change_asqa_task():
    args = alce_arguments('asqa')
    assert args == ['--f', 'asqa.json', '--citations', '--at_most_citations', '3', '--qa', '--mauve']
    assert alce_arguments('qampari') == ['--f', 'qampari.json', '--citations', '--at_most_citations', '3']
    assert '--claims_nli' in alce_arguments('eli5')
    with pytest.raises(ValueError):
        alce_arguments('../asqa')


def asqa_scores():
    return {'length': 50, 'str_em': 25, 'str_hit': 10, 'rougeLsum': 35,
            'QA-EM': 20, 'QA-F1': 25, 'QA-Hit': 5, 'mauve': 70,
            'citation_rec': 90, 'citation_prec': 85}


def test_full_batch_scales_percent_metrics_and_preserves_distribution_metric():
    metrics, diagnostics = parse_metrics('asqa', asqa_scores())
    assert metrics['mauve'] == .7
    assert metrics['citation_rec'] == .9
    assert diagnostics == {'length': 50}


@pytest.mark.parametrize('mutation', ['missing', 'nan', 'out_of_range', 'bool', 'extra'])
def test_incomplete_or_malformed_official_output_is_not_a_result(mutation):
    values = asqa_scores()
    if mutation == 'missing':
        del values['citation_prec']
    elif mutation == 'extra':
        values['unsupported'] = 1
    else:
        values['mauve'] = {'nan': math.nan, 'out_of_range': 101, 'bool': True}[mutation]
    with pytest.raises(ValueError):
        parse_metrics('asqa', values)


def test_model_cache_requires_resolved_revision_and_weights(tmp_path):
    with pytest.raises(ValueError, match='cache|Cache'):
        inspect_cache(tmp_path, 'qampari')
    model = tmp_path / 'models--google--t5_xxl_true_nli_mixture'
    (model / 'refs').mkdir(parents=True)
    (model / 'refs/main').write_text('a' * 40)
    snapshot = model / 'snapshots' / ('a' * 40)
    snapshot.mkdir(parents=True)
    (snapshot / 'config.json').write_text('{}')
    with pytest.raises(ValueError, match='weights'):
        inspect_cache(tmp_path, 'qampari')


def test_citation_eligibility_uses_official_preprocessing_and_segmentation():
    from rag_eval.alce_official_cli import citation_eligibility_program
    source = '''
def main():
    for i in range(len(data)):
        data[i]['output'] = data[i]['output'].strip().split("\\n")[0]
        data[i]['output'] = data[i]['output'].replace("<|im_end|>", "")
def compute_autoais(data, qampari=False):
    for item in data:
        if qampari:
            sents = [item['question'] + " " + x.strip() for x in item['output'].split(",")]
        else:
            sents = sent_tokenize(item['output'])
        if len(sents) == 0:
            continue
'''
    program = citation_eligibility_program(source)
    def eligible(qampari):
        scope = dict(data=[{'question':'q','output':'answer.'}, {'question':'q','output':'<|im_end|>\nignored'}],
                     qampari=qampari, sent_tokenize=lambda x: [x] if x else [])
        exec(program, scope)
        return scope['eligible_indices']
    assert eligible(False) == [0]
    assert eligible(True) == [0, 1]


def test_real_child_process_preserves_input_and_records_eligible_scope(tmp_path, monkeypatch):
    """Exercise process/file boundaries, with no model implementation or inference."""
    from rag_eval import benchmark_official
    from rag_eval.alce_official_cli import score_alce_batch
    packages = tmp_path / 'boundary_packages'
    packages.mkdir()
    (packages / 'numpy.py').write_text('class random:\n    seed = staticmethod(lambda n: None)\n')
    (packages / 'torch.py').write_text('def manual_seed(n): pass\n')
    (packages / 'nltk.py').write_text('''
class data:
    path = ['unhashed-global-tokenizer-directory']
def sent_tokenize(s):
    import os
    assert data.path == [os.environ['NLTK_DATA']], 'Ambient NLTK search paths must be excluded'
    return [s] if s else []
''')
    monkeypatch.setenv('PYTHONPATH', str(packages))
    monkeypatch.setattr('rag_eval.alce_official_cli.inspect_cache', lambda cache, task: {'test-only': {}})
    source = '''
import json, argparse, os
def compute_autoais(data, qampari=False):
    for item in data:
        if qampari:
            sents = item['output'].split(',')
        else:
            sents = sent_tokenize(item['output'])
        if len(sents) == 0: continue
def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--f')
    args, rest = parser.parse_known_args()
    assert args.f == 'asqa.json'
    assert os.environ['HF_HUB_OFFLINE'] == '1'
    assert os.environ['TRANSFORMERS_OFFLINE'] == '1'
    assert rest == ['--citations', '--at_most_citations', '3', '--qa', '--mauve']
    data = json.load(open(args.f))['data']
    assert data[1]['output'] == '<|im_end|>\\nignored'
    for i in range(len(data)):
        data[i]['output'] = data[i]['output'].strip().split('\\n')[0]
        data[i]['output'] = data[i]['output'].replace('<|im_end|>', '')
    json.dump(SCORES, open(args.f + '.score', 'w'))
if __name__ == '__main__': main()
'''.replace('SCORES', repr(asqa_scores()))
    monkeypatch.setattr(benchmark_official, '_source', lambda directory, name: source if name == 'alce_eval.py' else '')
    nltk = tmp_path / 'nltk-resources'
    (nltk / 'tokenizers').mkdir(parents=True)
    prepared = {'suite': 'alce', 'task': 'asqa', 'case_ids': ['q1', 'q2'], 'data': [
        {'question': 'q', 'output': 'supported [1].'},
        {'question': 'q', 'output': '<|im_end|>\nignored'}]}
    output = tmp_path / 'qampari-parent' / 'run'
    result = score_alce_batch(prepared, source_directory=tmp_path, output_dir=output,
                              hf_cache=tmp_path, nltk_data=nltk, python=sys.executable)
    assert result['metric_case_ids']['citation_rec'] == ['q1']
    assert result['metric_case_ids']['mauve'] == ['q1', 'q2']
    assert json.loads((output / 'asqa.json').read_text())['data'] == prepared['data']
    assert result['metrics']['citation_prec'] == .85
