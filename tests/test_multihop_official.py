"""Fixed upstream retrieval semantics and observed-ranking provenance."""
import base64
import copy
import hashlib
import importlib.util
import json
import zlib

import pytest

from rag_eval.benchmark_reference import plan_reference, reference_config
from rag_eval.benchmark_submission import build_submission
from rag_eval.notebook_bundle import prepare


# Exact 4179 upstream bytes, acquired via GitHub contents API at c1c1287.
# Compression keeps its original whitespace/hash without network-dependent tests.
SOURCE_BYTES = zlib.decompress(base64.b64decode(
    'eJydV0tv4zYQvvtXDNyDrKyj2AsfCqMCuigaFEWbBtu9pYbAWLTNXYnUklTaNMh/7wxJPS0HbX2wJXL4zTdvWpSV0haUmQn/9Nko'
    '2TwfC/XYPDN9rJg2fAb4mc1yfoA9K/Z1wSzPSm612JuFpl/+xPOsEMaaJRxVEZ7jrTt5EtZkzGbrVbZXtbSQwmqwsRmtl6wK8gSD'
    'yw87v651b8UtHZSGloHXDULC36L6F8RG5A4FOyL0LSuCyUOOk9vsiWt25Fml+V4YoWRm6rI1xDEU2thM84I/MYkPTH7B/TsleU9E'
    '5pmj3hlGn2ZJWF4mmlcF2/PFHOZLmM/jbuEP6VecM0iWPEBndy1S64v/C9cCeMyOOvkfTVp2Ellzhsu6RO9Y3oXiYbte75ZgLNM2'
    'XffCQB9xACafFy5MZ3r9Cqkb7NPLCCZAOUd/l8J6db49HfhPuuaToog2FUVhXBin4S+Hnn4uqWlIby6DniXkRdrfwA+qrGrLoc1O'
    'YBbsCYk7ReRN9/a15vp5EmNYmGcGToXjMnU08K3gMpmDVHYo09bGZdg+z/DwDtZvireoCasqLvMu6eLJc115szaOXtPN5XhO94Z3'
    '6TlaV/IYs6bDQuiwl8J01lYRWUi7GCV2PNHKJuU3I/FhF24cNW3UDZRCLgounSPjJVZd3EMKfbvBWKP8ZEFN1xnHhgur2Dup76DA'
    'pXWUwndgReEcJbgZTR+M2ZnPbqAhHSbDcDD1jmzePNE6C0+gQxZD58WXTqFjBqeCo6bk3QGsmFpLeGldG/2E9L5fr6Jtz7blaHvT'
    '2930Nn/9cO+Ptmz7mx8/hs2GpN98nfmbQMmEzDBKxeIgCp5JVvIlFKIUNqWuGJpypSnFDtEt5jDJbeGlFX+NvBf+FPYECjOjjxTp'
    'aIkTZK9yIY9pVNvD9bdRDMx4mJZnzixD79EVJikUyx1GHFLlF6IDj8+gtDgKyQr4+fff7tCLe6VzXMwxXx451hcnVItJI48g66LI'
    'XKkFQZMEuE+n2sD1tTMS3q+ArK8xEQ2WJwd8ETmlnrFYFgZYqRCN+D2stu9XOPZwFp64DmD2xCT2EGmFrEmtPWlVH08OimwATDZR'
    '4ACGExq9V0XB9xYHOOr1mtDJgRnWjeeEPYIa6HAqBQ85Ilsnt2tyqX8/6q5ZXdbB6Kbl7lYE1KGj7vwhaqzO7HPFox2kKUSdH6Nh'
    '7w5Gd0NrxKRpEw/lQ2T5XxbxSLkbB6grSLPCSUe7XddoOuZ9jAPbjzFaQXd81niRnDci01H3qRzdqS7S1GQQt8bBZVWbDkkUjw9d'
    'R3CFkYv7JmMhzwY18qM/T8nwQtU/YhK/DhWTmrOWGFqh7y6hLab/8dbeoN4TL5ePfVTnRve+JD41J5cGgYQmqFnEY6cd5i9e4hXr'
    '3x3aJpvD67zzx2zgrCty1QwDkrlukGUuobLM9ZwspJP7a6LRuuZvSvJBH/HGKe2921nk3Oy1qCgv03njXLwD+VXfdpivNTyPerAB'
    'JIGTB09YnmcsoCKxaxLGvkRZnhqr6db7tRaa56n7W7CEEy+qNLolzDtEjN5Eq7AfvIWGbZbVhU3nqrZ4j8NreYOPwULT7+n8ZQ1d'
    'Lw89q9GFXl5CA07Nomv7Hj84i48y3Xc036BwTMNdv50aWJx3TowNB/NFYDHmcRI5NZ4wsqTMDLzdDzHH5GmrkV6Ts8bmrom9Lbwt'
    '9274AY9rrfSisRvKGtk+4lGolBFWPFHSWn7kOhqpGw6XJntbf4QR1or2U7gbiO32skfUC9Jdps8WUzD1QvTcbtA4ywiBfER/yRP6'
    'WigvlnxWeNuiJ5yTVwkJR/G454xYG3eZdnmDBtDZPvdDGM8k1Oketu3hwB+a9g+R4vcD'))


def api():
    assert importlib.util.find_spec('rag_eval.multihop_official'), 'official retrieval bridge is missing'
    from rag_eval import multihop_official
    return multihop_official


@pytest.fixture
def source_dir(tmp_path):
    assert len(SOURCE_BYTES) == 4179
    assert hashlib.sha256(SOURCE_BYTES).hexdigest() == '6734516f0d5f9385076f76bf4e08d54968541fdb05f519f1cf687c0b65ae0f90'
    directory = tmp_path / 'sources'
    directory.mkdir()
    (directory / 'multihop_retrieval_evaluate.py').write_bytes(SOURCE_BYTES)
    return directory


def row(facts, passages, *, case_id='q1', question_type='inference_query'):
    return dict(case_id=case_id, query='Question', question_type=question_type,
                retrieval_list=[dict(text=t) for t in passages], gold_list=[dict(fact=f) for f in facts])


def frozen(tmp_path):
    directory = tmp_path / 'data'
    directory.mkdir()
    raw = [dict(query='apple', answer='yes', question_type='inference_query',
                evidence_list=[dict(title='News', fact='apple pear')]),
           dict(query='kiwi', answer='yes', question_type='comparison_query',
                evidence_list=[dict(title='News', fact='kiwi mango')]),
           dict(query='unanswerable', answer='null', question_type='null_query', evidence_list=[])]
    corpus = [dict(title='News', url='https://example.org/news', source='Public\n\nNews',
                   body='apple pear orange banana kiwi mango plum peach')]
    metadata = dict(dataset='multihop_rag', split='train', revision='fixture',
                    source_url='https://example.org/data', license='fixture')
    for name, value in [('raw.json', raw), ('corpus.json', corpus), ('source.json', metadata)]:
        (directory / name).write_text(json.dumps(value))
    return prepare('multihop_rag', directory/'raw.json', directory/'source.json', directory/'bundle',
                   corpus_path=directory/'corpus.json', adaptation_revision='notebook-data-v3')


def submission(bundle, *, strategy='bm25', maximum=10000, keep=None, kind='reference'):
    config = reference_config(strategy, top_k=2, max_context_chars=maximum, chunk_window=2, chunk_overlap=0)
    requests = plan_reference(bundle, configuration=config)
    predictions = [dict(case_id=r['case_id'], status='error', prediction='', retrieval=r['retrieval'])
                   for r in requests if keep is None or r['case_id'] in keep]
    method = dict(name=strategy, kind=kind, citation_style='none', model_identity={'model_id': 'fixture'},
                  input_policy='frozen-source-documents', configuration=config)
    return build_submission(bundle, method=method, predictions=predictions)


def test_original_nonstandard_map_duplicate_facts_and_rank11(source_dir):
    bridge = api()
    result = bridge.score_ranked_rows([
        row(['alpha', 'beta', 'beta'], ['alpha', 'alpha', 'beta']),
        row(['alpha'], ['no'] * 10 + ['alpha'], case_id='q2'),
    ], source_directory=source_dir)
    assert result['metrics'] == pytest.approx(dict(upstream_hits_at_10=.5, upstream_hits_at_4=.5,
        upstream_map_at_10=2/9, upstream_mrr_at_10=.5))
    assert set(result['per_case'][1]['metrics'].values()) == {0}
    assert result['dependencies']['source'] == bridge.SOURCE


def test_exact_ascii_space_newline_case_and_tab_matching(source_dir):
    result = api().score_ranked_rows([
        row(['ab'], ['a \nb'], case_id='space'),
        row(['ab'], ['a\tb'], case_id='tab'),
        row(['Alpha'], ['alpha'], case_id='case'),
    ], source_directory=source_dir)
    assert result['metrics']['upstream_hits_at_10'] == pytest.approx(1/3)


def test_upstream_map_can_exceed_one_without_clipping(source_dir):
    facts = [f'fact{i}!' for i in range(11)]
    result = api().score_ranked_rows([row(facts, [' '.join(facts)])], source_directory=source_dir)
    assert result['metrics']['upstream_map_at_10'] == 1.1


@pytest.mark.parametrize('rank', [4, 5, 10])
def test_independent_hit_cutoffs_and_reciprocal_rank(source_dir, rank):
    result = api().score_ranked_rows([row(['fact'], ['no'] * (rank-1) + ['fact'])],
                                    source_directory=source_dir)
    assert result['metrics'] == pytest.approx(dict(upstream_hits_at_10=1, upstream_hits_at_4=int(rank <= 4),
        upstream_map_at_10=1/rank, upstream_mrr_at_10=1/rank))


def test_explicit_empty_retrieval_is_an_observation_with_zero_scores(source_dir):
    result = api().score_ranked_rows([row(['fact'], [])], source_directory=source_dir)
    assert result['status'] == 'complete' and set(result['metrics'].values()) == {0}


def test_published_rows_without_case_ids_keep_original_record_indices(source_dir):
    records = [row([], [], question_type='null_query'), row(['fact'], ['fact'])]
    for record in records:
        record.pop('case_id')
    result = api().score_ranked_rows(records, source_directory=source_dir)
    assert result['per_case'][0]['case_id'] == '1' and result['excluded_case_ids'] == ['0']


def test_limit_precedes_null_filter_and_empty_denominator_stays_explicit(source_dir):
    rows = [row([], [], question_type='null_query'), row(['fact'], ['fact'], case_id='q2')]
    result = api().score_ranked_rows(rows, source_directory=source_dir, limit=1)
    assert result['metrics'] == {} and result['status'] == 'not_applicable'
    assert result['coverage'] == dict(planned=1, eligible=0, excluded_null=1, scored=0)


@pytest.mark.parametrize('change', ['empty_gold', 'blank_gold', 'bad_text', 'duplicate_id'])
def test_official_input_rejects_invalid_alignment_and_gold(source_dir, change):
    rows = [row(['fact'], ['fact'])]
    if change == 'empty_gold':
        rows[0]['gold_list'] = []
    elif change == 'blank_gold':
        rows[0]['gold_list'] = [dict(fact=' \n ')]
    elif change == 'bad_text':
        rows[0]['retrieval_list'][0]['text'] = 123
    else:
        rows.append(copy.deepcopy(rows[0]))
    with pytest.raises(ValueError):
        api().score_ranked_rows(rows, source_directory=source_dir)


def test_unverified_source_is_never_executed(source_dir):
    (source_dir/'multihop_retrieval_evaluate.py').write_bytes(SOURCE_BYTES + b'\n')
    with pytest.raises(ValueError, match='hash'):
        api().score_ranked_rows([row(['fact'], ['fact'])], source_directory=source_dir)


def test_reference_scores_real_ranking_before_budget_even_when_generation_failed(tmp_path, source_dir):
    bundle = frozen(tmp_path)
    observed = submission(bundle, maximum=1)
    assert observed['coverage']['error'] == 3
    assert all(not p['retrieval']['selected'] for p in observed['predictions'])
    result = api().score_multihop_retrieval(bundle, observed, source_directory=source_dir)
    assert result['coverage']['scored'] == 2 and result['coverage']['excluded_null'] == 1
    assert result['coverage']['complete'] and result['metrics']['upstream_hits_at_10'] == 1
    assert result['pending_metrics'] == []
    prepared = api().prepare_ranked_inputs(bundle, observed)
    assert prepared['data'][0]['retrieval_list'][0]['text'] == observed['predictions'][0]['retrieval']['ranked'][0]['text']


def test_missing_ranking_never_becomes_zero_or_complete_average(tmp_path, source_dir):
    bundle = frozen(tmp_path)
    observed = submission(bundle, keep=[bundle['cases'][0]['case_id']])
    result = api().score_multihop_retrieval(bundle, observed, source_directory=source_dir)
    assert result['metrics'] == {} and result['observed_metrics']['upstream_hits_at_10'] == 1
    assert result['coverage']['eligible'] == 2 and result['coverage']['scored'] == 1
    assert not result['coverage']['complete'] and result['pending_metrics']
    assert result['status'] == 'partial'


@pytest.mark.parametrize('strategy,kind', [('full-context', 'reference'), ('bm25', 'sn')])
def test_full_context_and_unadapted_sn_have_no_observed_retrieval(tmp_path, strategy, kind):
    bundle = frozen(tmp_path)
    result = api().score_multihop_retrieval(bundle, submission(bundle, strategy=strategy, kind=kind),
                                          source_directory=tmp_path/'absent-source')
    assert result['status'] == 'pending' and not result['metrics'] and not result['observed_metrics']
    assert result['coverage']['scored'] == 0 and result['pending_metrics']


@pytest.mark.parametrize('change', ['body', 'characters', 'tokens', 'text', 'rank', 'score', 'document'])
def test_retrieved_passage_must_match_public_source_and_observed_rank(tmp_path, change):
    bundle = frozen(tmp_path)
    observed = submission(bundle)
    hit = observed['predictions'][0]['retrieval']['ranked'][0]
    if change == 'body':
        hit['body_text'] += ' gold fact'
    elif change == 'characters':
        hit['body_character_span'][0] += 1
    elif change == 'tokens':
        hit['token_span'][0] += 1
    elif change == 'text':
        hit['text'] = hit['body_text']
    elif change == 'rank':
        hit['rank'] = 2
    elif change == 'score':
        hit['score'] = -999
    else:
        hit['document_id'] = 'unavailable-source'
    with pytest.raises(ValueError):
        api().prepare_ranked_inputs(bundle, observed)
