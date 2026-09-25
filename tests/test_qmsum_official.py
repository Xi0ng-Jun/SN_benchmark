"""The process fixture tests our boundary; public HMNet outputs calibrate ROUGE."""
import importlib
import json
import os
import shutil

import pytest


def score(*args, **kwargs):
    return importlib.import_module('rag_eval.qmsum_official').score_qmsum_rouge(*args, **kwargs)


@pytest.fixture
def rouge_home(tmp_path):
    if not shutil.which('perl'):
        pytest.skip('Perl required for the scorer process boundary test')
    home = tmp_path / 'ROUGE home'
    (home / 'data').mkdir(parents=True)
    (home / 'data' / 'smart_common_words.txt').write_text('the\na\n')
    (home / 'data' / 'WordNet-2.0.exc.db').write_bytes(b'fixture')
    lines = ['---------------------------------------------']
    for metric, value in [('1', '.5'), ('2', '.25'), ('L', '.4')]:
        for stat in ['R', 'P', 'F']:
            lines.append(f'1 ROUGE-{metric} Average_{stat}: {value} (95%-conf.int. 0.1 - 0.9)')
        lines.append(f'1 ROUGE-{metric} Eval 1.1 R:{value} P:{value} F:{value}')
        lines.append('---------------------------------------------')
    script = "print <<'OUTPUT';\n" + '\n'.join(lines) + '\nOUTPUT\n'
    (home / 'ROUGE-1.5.5.pl').write_text(script)
    return home


def test_real_process_keeps_inputs_maps_cases_and_reads_average_f(tmp_path, rouge_home):
    result = score([{'case_id': 'a/b', 'prediction': 'A < B.\nC & D.', 'reference': 'A < B.'}],
                   rouge_home=rouge_home, output_dir=tmp_path / 'run')
    assert result['metrics'] == {'rouge1': .5, 'rouge2': .25, 'rougeL': .4}
    assert result['per_case'][0]['case_id'] == 'a/b'
    assert result['per_case'][0]['metrics']['rougeL'] == .4
    assert result['pair_count'] == 1
    assert result['segmentation'] == 'newline'
    assert len(result['input_sha256']) == 64
    assert result['dependencies']['files']['ROUGE-1.5.5.pl']['sha256']
    command = result['command']
    assert command[command.index('-c'):-1] == ['-c', '95', '-r', '1000', '-n', '2', '-m', '-d', '-a']
    assert json.loads((tmp_path / 'run' / 'result.json').read_text()) == result
    assert 'Average_F: .5' in result['stdout']
    files = list((tmp_path / 'run' / 'system').glob('*.txt'))
    assert len(files) == 1
    assert files[0].read_text() == 'A < B.\nC & D.\n'
    assert result['input_format'] == 'SPL'


@pytest.mark.parametrize('pairs', [[], [{'case_id': '', 'prediction': '', 'reference': 'x'}],
    [{'case_id': 'x', 'prediction': None, 'reference': 'x'}],
    [{'case_id': 'x', 'prediction': '', 'reference': 7}],
    [{'case_id': 'x', 'prediction': '', 'reference': 'x'}] * 2])
def test_invalid_pair_identity_or_text_does_not_create_run(tmp_path, pairs):
    with pytest.raises(ValueError):
        score(pairs, rouge_home=tmp_path / 'missing', output_dir=tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


def test_missing_dependency_has_actionable_failure_without_download(tmp_path):
    with pytest.raises(FileNotFoundError, match='ROUGE-1.5.5'):
        score([{'case_id': 'x', 'prediction': '', 'reference': 'x'}],
              rouge_home=tmp_path / 'missing', output_dir=tmp_path / 'run')
    assert not (tmp_path / 'run').exists()


def test_existing_run_is_not_overwritten(tmp_path, rouge_home):
    run = tmp_path / 'run'
    run.mkdir()
    (run / 'keep').write_text('original')
    with pytest.raises(FileExistsError):
        score([{'case_id': 'x', 'prediction': '', 'reference': 'x'}],
              rouge_home=rouge_home, output_dir=run)
    assert (run / 'keep').read_text() == 'original'


def test_failed_perl_preserves_stderr_and_never_emits_a_score(tmp_path, rouge_home):
    (rouge_home / 'ROUGE-1.5.5.pl').write_text('die "missing XML module";')
    with pytest.raises(RuntimeError, match='missing XML module'):
        score([{'case_id': 'x', 'prediction': '', 'reference': 'x'}],
              rouge_home=rouge_home, output_dir=tmp_path / 'run')
    assert 'missing XML module' in (tmp_path / 'run' / 'stderr.txt').read_text()
    assert not (tmp_path / 'run' / 'result.json').exists()


@pytest.mark.parametrize('bad', ['NaN', 'inf', '1.2', '-0.1'])
def test_corrupt_official_stdout_is_rejected(tmp_path, rouge_home, bad):
    source = rouge_home / 'ROUGE-1.5.5.pl'
    source.write_text(source.read_text().replace('Average_F: .5', f'Average_F: {bad}'))
    with pytest.raises(ValueError, match='ROUGE'):
        score([{'case_id': 'x', 'prediction': '', 'reference': 'x'}],
              rouge_home=rouge_home, output_dir=tmp_path / 'run')


def test_hmnet_sentence_rule_is_explicit_and_preserves_empty_predictions(tmp_path, rouge_home):
    result = score([{'case_id': 'x', 'prediction': '', 'reference': 'Dr. Smith spoke. Then left? Yes.'}],
                   rouge_home=rouge_home, output_dir=tmp_path / 'run', segmentation='hmnet_regex')
    assert result['segmentation'] == 'hmnet_regex'
    reference = next((tmp_path / 'run' / 'reference').glob('*.txt')).read_text()
    assert reference.splitlines() == ['Dr. Smith spoke.', 'Then left?', 'Yes.']
    assert result['empty_prediction_count'] == 1


@pytest.mark.skipif(not os.environ.get('QMSUM_ROUGE_HOME'), reason='explicit local official ROUGE required')
def test_official_perl_preserves_denominator_empty_answer_and_sentence_lcs(tmp_path):
    result = score([
        {'case_id': 'exact', 'prediction': 'Cats run fast.', 'reference': 'Cats run fast.'},
        {'case_id': 'empty', 'prediction': '', 'reference': 'Cats run fast.'},
        {'case_id': 'reordered', 'prediction': 'Dogs swim well. Cats run fast.',
         'reference': 'Cats run fast. Dogs swim well.'},
    ], rouge_home=os.environ['QMSUM_ROUGE_HOME'], output_dir=tmp_path / 'real', segmentation='hmnet_regex')
    assert result['pair_count'] == 3
    rows = {row['case_id']: row['metrics'] for row in result['per_case']}
    assert rows['exact'] == {'rouge1': 1., 'rouge2': 1., 'rougeL': 1.}
    assert rows['empty'] == {'rouge1': 0., 'rouge2': 0., 'rougeL': 0.}
    assert rows['reordered']['rougeL'] == 1.
