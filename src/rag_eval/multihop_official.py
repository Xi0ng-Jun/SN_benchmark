"""Execute fixed MultiHop-RAG retrieval metrics over actual ordered passages.

The upstream metric is case-sensitive fact containment after deleting only ASCII
spaces/newlines. Its MAP uses newly found facts/rank, including duplicate-gold
denominator quirks; it is not conventional average precision. A loop reads eleven
hits, but only ranks <= 10 contribute. No metric formula is reimplemented here.

The bundle bridge currently accepts the reference runner's recorded BM25 ranking.
SN has no equivalent observed-ranking contract yet; whole-context document order
and context coverage cannot stand in for retrieval. Published upstream-format
files may use ``score_ranked_rows`` after their dataset/scope is separately audited.
There are no automatic downloads, model imports or calls.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import hashlib
import math
from pathlib import Path
import re
import sys

from .benchmark_submission import validate_submission


REVISION = 'c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8'
SOURCE_NAME = 'multihop_retrieval_evaluate.py'
SOURCE = {
    'url': f'https://raw.githubusercontent.com/yixuantt/MultiHop-RAG/{REVISION}/retrieval_evaluate.py',
    'sha256': '6734516f0d5f9385076f76bf4e08d54968541fdb05f519f1cf687c0b65ae0f90',
}
PROFILE = 'multihop-c1c1287-upstream-retrieval-v1'
METRICS = {'Hits@10': 'upstream_hits_at_10', 'Hits@4': 'upstream_hits_at_4',
           'MAP@10': 'upstream_map_at_10', 'MRR@10': 'upstream_mrr_at_10'}
QUESTION_TYPES = {'inference_query', 'comparison_query', 'temporal_query', 'null_query'}


def _dependencies():
    return dict(source_name=SOURCE_NAME, source=deepcopy(SOURCE), revision=REVISION,
                bridge_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
                python=sys.version.split()[0],
                matching='case-sensitive containment; delete ASCII space and newline only',
                loop_limit=11, contributing_rank_limit=10,
                map='sum(new distinct facts at rank / rank) / min(original gold count, 10); may exceed 1',
                null_queries='excluded; optional original-record limit precedes exclusion')


def _load_calculate(source_directory):
    data = (Path(source_directory) / SOURCE_NAME).read_bytes()
    if hashlib.sha256(data).hexdigest() != SOURCE['sha256']:
        raise ValueError('Official MultiHop retrieval source hash mismatch')
    tree = ast.parse(data.decode('utf-8'))
    nodes = [node for node in tree.body
             if isinstance(node, ast.FunctionDef) and node.name == 'calculate_metrics']
    if len(nodes) != 1:
        raise ValueError('Official MultiHop retrieval function contract changed')
    namespace = {}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), '<verified-multihop-retrieval>', 'exec'), namespace)
    return namespace['calculate_metrics']


def _metrics(calculate, rows):
    raw = calculate([[hit['text'] for hit in row['retrieval_list']] for row in rows],
                    [[item['fact'] for item in row['gold_list']] for row in rows])
    if set(raw) != set(METRICS):
        raise ValueError('Official retrieval metric names differ from the fixed contract')
    for name, value in raw.items():
        if (type(value) not in (int, float) or not math.isfinite(value) or value < 0
                or (name != 'MAP@10' and value > 1)):
            raise ValueError('Invalid official retrieval metric value')
    return {METRICS[name]: value for name, value in raw.items()}


def _gold(items):
    if not isinstance(items, list) or not items:
        raise ValueError('Non-null retrieval rows require nonempty gold_list')
    if any(not isinstance(item, dict) or not isinstance(item.get('fact'), str)
           or not item['fact'].strip() for item in items):
        raise ValueError('Gold facts must be nonempty strings')


def score_ranked_rows(rows, *, source_directory, limit=None):
    """Score already ordered upstream-format rows, without asserting provenance.

Required fields: question_type, retrieval_list[{text}], gold_list[{fact}].
case_id defaults to original row index for published files. Order, duplicates,
strings and all ranks are retained. Missing versus empty retrieval_list differs:
missing is invalid; an explicitly observed empty list receives zero. ``limit``
has the upstream CLI's before-null-filter semantics. No data means no metrics.
"""
    if not isinstance(rows, list):
        raise ValueError('Official retrieval rows must be a list')
    if limit is not None and (type(limit) is not int or limit <= 0):
        raise ValueError('limit must be a positive integer')
    chosen, seen, eligible, excluded = rows[:limit], set(), [], []
    for index, original in enumerate(chosen):
        if not isinstance(original, dict) or original.get('question_type') not in QUESTION_TYPES:
            raise ValueError('Unknown MultiHop retrieval question type')
        case_id = original.get('case_id', str(index))
        if not isinstance(case_id, str) or not case_id or case_id in seen:
            raise ValueError('Retrieval case IDs must be nonempty and unique')
        seen.add(case_id)
        if original['question_type'] == 'null_query':
            excluded.append(case_id)
            continue
        _gold(original.get('gold_list'))
        retrieved = original.get('retrieval_list')
        if (not isinstance(retrieved, list) or any(not isinstance(hit, dict)
                or not isinstance(hit.get('text'), str) for hit in retrieved)):
            raise ValueError('retrieval_list must contain ordered passage text strings')
        eligible.append({**original, 'case_id': case_id})
    metrics, individual = {}, []
    if eligible:
        calculate = _load_calculate(source_directory)
        metrics = _metrics(calculate, eligible)
        individual = [dict(case_id=row['case_id'], metrics=_metrics(calculate, [row])) for row in eligible]
    return dict(profile=PROFILE, status='complete' if eligible else 'not_applicable',
                metrics=metrics, per_case=individual, dependencies=_dependencies(),
                coverage=dict(planned=len(chosen), eligible=len(eligible), excluded_null=len(excluded),
                              scored=len(eligible)), excluded_case_ids=excluded)


def _public_parts(document, cache):
    if document['id'] not in cache:
        units = document.get('source_units')
        if (not isinstance(units, list) or len(units) != 2
                or any(not isinstance(unit, dict) or not isinstance(unit.get('text'), str) for unit in units)):
            raise ValueError('MultiHop public metadata/body source units required')
        parts = {unit.get('kind'): unit['text'] for unit in units}
        if set(parts) != {'metadata', 'body'}:
            raise ValueError('MultiHop public metadata/body source units required')
        cache[document['id']] = (parts['metadata'], parts['body'],
                                 list(re.finditer(r'\S+', parts['body'])))
    return cache[document['id']]


def _span(value, label):
    if (not isinstance(value, list) or len(value) != 2
            or any(type(n) is not int for n in value) or not 0 <= value[0] < value[1]):
        raise ValueError('Invalid ranked passage ' + label)
    return value


def _reference_passages(retrieval, case, documents, cache, configuration):
    if retrieval.get('stage') != 'reference-context-selection':
        raise ValueError('Reference retrieval observation has an unknown stage')
    ranked, available = retrieval.get('ranked'), retrieval.get('available_units')
    top_k = configuration.get('top_k')
    if (not isinstance(ranked, list) or type(available) is not int or available < 0
            or type(top_k) is not int or top_k <= 0 or len(ranked) != min(top_k, available)):
        raise ValueError('Observed reference ranking contradicts its top-k or available units')
    chunks = configuration.get('chunks', {})
    window, overlap = chunks.get('window'), chunks.get('overlap')
    if type(window) is not int or type(overlap) is not int or not 0 <= overlap < window:
        raise ValueError('Invalid reference chunk configuration')
    available_documents = set(case['material_document_ids'])
    passages, seen, previous = [], set(), math.inf
    for rank, hit in enumerate(ranked, 1):
        if not isinstance(hit, dict):
            raise ValueError('Each ranked observation must be an object')
        unit_id, score = hit.get('unit_id'), hit.get('score')
        if (not isinstance(unit_id, str) or not unit_id or unit_id in seen
                or type(hit.get('rank')) is not int or hit['rank'] != rank
                or type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= previous):
            raise ValueError('Reference ranking requires distinct units, contiguous ranks and descending finite scores')
        seen.add(unit_id)
        previous = score
        document_id = hit.get('document_id')
        if document_id not in available_documents or document_id not in documents:
            raise ValueError('Ranked passage is outside the public case documents')
        metadata, body, tokens = _public_parts(documents[document_id], cache)
        start, end = _span(hit.get('token_span'), 'token_span')
        left, right = _span(hit.get('body_character_span'), 'body_character_span')
        if (start >= len(tokens) or end > len(tokens) or start % (window - overlap)
                or end != min(start + window, len(tokens))
                or left != tokens[start].start() or right != tokens[end - 1].end()
                or hit.get('body_text') != body[left:right]
                or hit.get('text') != metadata + '\n\n' + body[left:right]):
            raise ValueError('Ranked passage differs from its original public body/token spans')
        passages.append(dict(text=hit['text'], score=score))
    return passages


def prepare_ranked_inputs(bundle, submission):
    """Validate observed ranking and prove passage identity without reading gold to repair it."""
    submission = validate_submission(bundle, submission)
    if (bundle['manifest'].get('adaptation_revision') != 'notebook-data-v3'
            or submission['suite'] != 'multihop_rag'):
        raise ValueError('Official retrieval bridge requires a MultiHop notebook-data-v3 bundle')
    cases = {case['case_id']: case for case in bundle['cases']}
    documents = {document['id']: document for document in bundle['documents']}
    method = submission['method']
    supported = method['kind'] == 'reference' and method['configuration'].get('strategy') == 'bm25'
    data, audit, cache, eligible, excluded = [], [], {}, 0, 0
    for row in submission['predictions']:
        case = cases[row['case_id']]
        question_type = case['gold']['question_type']
        if question_type not in QUESTION_TYPES:
            raise ValueError('Unknown MultiHop question type')
        if question_type == 'null_query':
            excluded += 1
            audit.append(dict(case_id=row['case_id'], status='excluded', reason='null_query'))
            continue
        eligible += 1
        evidence = case['gold']['evidence']
        _gold(evidence)
        retrieval = row.get('retrieval')
        if not supported or retrieval is None:
            audit.append(dict(case_id=row['case_id'], status='pending',
                              reason='unsupported_observed_ranking_contract' if not supported else 'missing_ranking'))
            continue
        if not isinstance(retrieval, dict):
            raise ValueError('Malformed observed retrieval')
        passages = _reference_passages(retrieval, case, documents, cache, method['configuration'])
        data.append(dict(case_id=row['case_id'], query=case['question'], question_type=question_type,
                         retrieval_list=passages, gold_list=deepcopy(evidence)))
        audit.append(dict(case_id=row['case_id'], status='observed', ranked_count=len(passages),
                          stage=retrieval['stage'], passage_mapping='verified-public-metadata-and-body-spans'))
    return dict(profile=PROFILE, scope=submission['scope'], case_ids=submission['case_ids'], data=data, audit=audit,
                coverage=dict(planned=len(submission['predictions']), eligible=eligible, excluded_null=excluded,
                              scored=len(data), missing_ranking=eligible-len(data), complete=bool(eligible) and len(data)==eligible))


def score_multihop_retrieval(bundle, submission, *, source_directory):
    """Return official metrics only when every eligible case has an observed ranking.

Generation errors do not erase already observed retrieval. Incomplete coverage
keeps ``metrics`` empty and exposes ``observed_metrics`` plus per-case diagnostics.
No available ranking means pending without trying to load unavailable source code.
"""
    prepared = prepare_ranked_inputs(bundle, submission)
    result = score_ranked_rows(prepared['data'], source_directory=source_directory)
    coverage = prepared['coverage']
    status = ('complete' if coverage['complete'] else 'not_applicable' if not coverage['eligible']
              else 'partial' if coverage['scored'] else 'pending')
    return dict(profile=PROFILE, scope=prepared['scope'], status=status, case_ids=prepared['case_ids'],
                metrics=result['metrics'] if coverage['complete'] else {}, observed_metrics=result['metrics'],
                per_case=result['per_case'], coverage=coverage, audit=prepared['audit'],
                dependencies=result['dependencies'],
                pending_metrics=[] if status in {'complete', 'not_applicable'} else list(METRICS.values()))
