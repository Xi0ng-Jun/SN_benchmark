"""Deterministic qualitative review packets from validated paired comparisons.

Selection is stratified by task and observed metric direction. It is a review
aid, not a representative sample for estimating quality or a new score. Human
annotations start empty and remain separate from immutable scored artifacts.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
import json
from math import fsum
from pathlib import Path
import re

from .artifacts import digest, save_json
from .benchmark_comparison import _read, compare_submissions
from .benchmark_official import prepare_inputs
from .notebook_bundle import load_bundle
from .identity import fingerprint


def _stratum(left, right):
    if right > left:
        return 'right_higher'
    if right < left:
        return 'left_higher'
    return 'equal_zero' if left == 0 else 'equal_nonzero'


def _selection(report, metrics, per_stratum, seed):
    assignments, strata = [], []
    for comparison in report['comparisons']:
        pair = {k: comparison[k] for k in ('left_method_id', 'right_method_id')}
        for metric in metrics:
            buckets = defaultdict(list)
            for row in comparison['paired']:
                if metric in row['differences']:
                    buckets[(row['task'], _stratum(row['left'][metric], row['right'][metric]))].append(row)
            for (task, stratum), rows in sorted(buckets.items()):
                # Do not rank by difference magnitude: avoid selecting only
                # dramatic examples while making selection reproducible.
                ordered = sorted(rows, key=lambda row: (fingerprint(dict(seed=seed, metric=metric,
                                                                          case_id=row['case_id'])), row['case_id']))
                selected = ordered[:per_stratum]
                strata.append(dict(**pair, metric=metric, task=task, stratum=stratum,
                                   eligible_cases=len(rows), selected_cases=len(selected)))
                for row in selected:
                    assignment = dict(**pair, metric=metric, task=task, stratum=stratum,
                                      case_id=row['case_id'], left_score=row['left'][metric],
                                      right_score=row['right'][metric], difference=row['differences'][metric],
                                      direction='right minus left')
                    assignment['assignment_id'] = fingerprint(assignment)
                    assignments.append(assignment)
    return assignments, strata


def _task_comparisons(report, metrics):
    """Describe all eligible paired rows, independently of review sampling.

    These are arithmetic per-case means, not new batch scorer estimates.
    Conditional metrics keep their eligible denominator within each task;
    an empty eligible set has no mean, not a zero score.
    """
    summaries = []
    for comparison in report['comparisons']:
        tasks = defaultdict(list)
        for row in comparison['paired']:
            tasks[row['task']].append(row)
        for task, rows in sorted(tasks.items()):
            for metric in metrics:
                eligible = [row for row in rows if metric in row['differences']]
                count = len(eligible)
                directions = dict(left_higher=0, right_higher=0, equal_zero=0, equal_nonzero=0)
                for row in eligible:
                    directions[_stratum(row['left'][metric], row['right'][metric])] += 1
                summaries.append(dict(
                    left_method_id=comparison['left_method_id'], right_method_id=comparison['right_method_id'],
                    task=task, metric=metric, case_count=len(rows), eligible_cases=count,
                    left_mean=fsum(row['left'][metric] for row in eligible) / count if count else None,
                    right_mean=fsum(row['right'][metric] for row in eligible) / count if count else None,
                    mean_difference=fsum(row['differences'][metric] for row in eligible) / count if count else None,
                    direction='right minus left', direction_counts=directions))
    return summaries


def _bridge_answers(prepared, cases):
    if prepared['suite'] == 'qasper':
        return {c['case_id']: prepared['predictions'].get(c['sample_id'], {}).get('answer') for c in cases}
    if prepared['suite'] == 'qmsum':
        return {r['case_id']: r['prediction'] for r in prepared['pairs']}
    if prepared['suite'] == 'alce':
        return {a['case_id']: row['output'] for a, row in zip(prepared['audit'], prepared['data'])}
    field = 'model_answer' if prepared['suite'] == 'multihop_rag' else 'answer'
    return {row['case_id']: row[field] for row in prepared['data']}


def _observed_evidence(record):
    # Keep genuine observation fields, not arbitrary complete product records
    # or recursively duplicated raw imports. Original artifact links remain.
    result = {key: deepcopy(record[key]) for key in (
        'predicted_evidence', 'predicted_supporting_facts', 'citation_index_to_document_id',
        'shown_documents', 'retrieval', 'native_source', 'request_sha256', 'raw_generation') if key in record}
    for key in ('qasper_evidence', 'hotpot_evidence'):
        if key in record:
            result[key] = {k: deepcopy(record[key][k]) for k in ('policy', 'status', 'reason', 'projection')
                           if k in record[key]}
    return result


def _answer_observation(row):
    record = row.get('record', {})
    for key in ('raw_generation', 'answer'):
        if isinstance(record.get(key), str):
            return dict(field='record.' + key, text=record[key])
    return dict(field='prediction', text=row['prediction'])


def _literal(value):
    """Display source text as data, including answers containing Markdown."""
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, indent=2)
    fence = '`' * max(3, max((len(m[0]) + 1 for m in re.finditer(r'`+', text)), default=3))
    return fence + '\n' + text + '\n' + fence


def _markdown(review, cases):
    def cell(value):
        return str(value).replace('|', '\\|').replace('\n', ' ').replace('`', '&#96;')

    names = {m['method_id']: m['name'] for m in review['methods']}
    lines = ['# Paired comparison review', '',
             f'Suite: {review["suite"]}; full comparison scope: {review["case_count"]} cases.', '',
             'These are deterministically selected review examples, not a quality estimate. '
             'Higher automatic scores do not establish better answers or causal method gains.', '',
             'Selection uses task × metric × score direction. Each nonempty stratum contributes '
             f'up to {review["selection"]["per_stratum"]} cases, ordered by a seeded hash; '
             'stratum sizes are preserved in review.json.', '',
             'Gold and references below are review-only. Never feed this packet into generation. '
             'Original score artifacts are unchanged; annotations.jsonl starts unreviewed.', '',
             'The official bridge answer is before any later author CLI preprocessing, such as ALCE first-line handling.', '']
    lines += ['## Full comparison scope by task', '', review['task_summary_interpretation'], '']
    pairs = defaultdict(list)
    for row in review['task_comparisons']:
        pairs[(row['left_method_id'], row['right_method_id'])].append(row)
    for (left, right), rows in pairs.items():
        lines += [f'Left: {cell(names[left])}; right: {cell(names[right])}.', '',
                  '| Task | Metric | Eligible/task | Left mean | Right mean | Right minus left | Left higher | Right higher | Equal zero | Equal nonzero |',
                  '| --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |']
        for row in rows:
            values = [(f'{row[key]:+.6f}' if key == 'mean_difference' else f'{row[key]:.6f}')
                      if row[key] is not None else 'unavailable'
                      for key in ('left_mean', 'right_mean', 'mean_difference')]
            counts = row['direction_counts']
            lines.append(f'| {cell(row["task"])} | {cell(row["metric"])} | '
                         f'{row["eligible_cases"]}/{row["case_count"]} | ' + ' | '.join(values) + ' | ' +
                         ' | '.join(str(counts[key]) for key in
                                    ('left_higher', 'right_higher', 'equal_zero', 'equal_nonzero')) + ' |')
        lines.append('')
    for assignment in review['assignments']:
        case = cases[assignment['case_id']]
        lines += ['## Review ' + assignment['assignment_id'][:12], '',
                  _literal(dict(case_id=case['case_id'], task=case['task'], metric=assignment['metric'],
                                stratum=assignment['stratum'], left=names[assignment['left_method_id']],
                                right=names[assignment['right_method_id']],
                                left_score=assignment['left_score'], right_score=assignment['right_score'],
                                right_minus_left=assignment['difference'])), '',
                  'Question:', '', _literal(case['question']), '', 'References:', '', _literal(case['references']), '']
        for side in ('left', 'right'):
            method = next(m for m in case['methods'] if m['method_id'] == assignment[side + '_method_id'])
            lines += [side.capitalize() + ' earliest saved answer observation '
                      '(' + method['answer_observation']['field'] + '):', '',
                      _literal(method['answer_observation']['text']), '',
                      side.capitalize() + ' submitted answer:', '', _literal(method['submitted_answer']), '',
                      side.capitalize() + ' official bridge answer:', '', _literal(method['official_bridge_answer']), '']
        lines += ['Case metadata, gold annotations, observed evidence, metrics and source artifact pointers: '
                  f'[case JSON]({case["case_file"]}).', '',
                  'Review question: Does the answer address the question and match the references? '
                  'Does the saved evidence support it? Does the automatic metric explain the apparent difference? '
                  'Record a reasoned judgment with evidence; leave unavailable observations unresolved.', '']
    return '\n'.join(lines)


def write_review(bundle_directory, comparison_path, output_dir, *, metrics, per_stratum=3, seed=0):
    bundle_path, comparison_path, output = map(lambda p: Path(p).resolve(),
                                              (bundle_directory, comparison_path, output_dir))
    if output.exists():
        raise FileExistsError('Review output must be a fresh directory')
    if (type(per_stratum) is not int or per_stratum < 1 or type(seed) is not int
            or not isinstance(metrics, (list, tuple)) or not metrics
            or any(not isinstance(metric, str) for metric in metrics) or len(set(metrics)) != len(metrics)):
        raise ValueError('Explicit distinct metrics, positive sample count and integer seed required')
    saved, report_file = _read(comparison_path)
    if saved.get('report_id') != fingerprint({k: v for k, v in saved.items() if k != 'report_id'}):
        raise ValueError('Comparison report hash changed')
    entries = []
    for method in saved['methods']:
        entry = {}
        for key in ('submission', 'scores'):
            source = method['files'][key]
            if digest(source['path']) != source['sha256']:
                raise ValueError('Comparison source artifact changed: ' + key)
            entry[key] = source['path']
        entries.append(entry)
    inputs = [bundle_path, comparison_path] + [Path(p).resolve() for e in entries for p in e.values()]
    if output.is_relative_to(bundle_path) or any(path.is_relative_to(output) for path in inputs):
        raise ValueError('Review output must not overlap input artifacts')
    # Reuse formal admission: exact scope, scorer, status, eligible-case sets,
    # source hashes and pair calculations are all checked before sampling.
    report = compare_submissions(bundle_path, entries)
    if report != saved:
        raise ValueError('Comparison no longer matches reconstructed source artifacts')
    if set(metrics) - set(report['paired_metrics']):
        raise ValueError('Review selection requires shared per-case metrics; batch-only/pending metrics are not eligible')
    assignments, strata = _selection(report, sorted(metrics), per_stratum, seed)
    selected = {a['case_id'] for a in assignments}
    bundle = load_bundle(bundle_path)
    cases = {c['case_id']: dict(case_id=c['case_id'], sample_id=c['sample_id'], task=c['task'],
                              group_id=c['group_id'], question=c['question'], references=deepcopy(c['references']),
                              gold=deepcopy(c['gold']), material_document_ids=list(c['material_document_ids']),
                              methods=[], case_file=f'cases/{fingerprint(c["case_id"])}.json')
             for c in bundle['cases'] if c['case_id'] in selected}
    for entry, method in zip(entries, report['methods']):
        submission, _ = _read(entry['submission'])
        scores, _ = _read(entry['scores'])
        answers = _bridge_answers(prepare_inputs(bundle, submission), bundle['cases'])
        score_rows = {r['case_id']: r for r in scores['per_case']}
        for row in submission['predictions']:
            if row['case_id'] in selected:
                cases[row['case_id']]['methods'].append(dict(
                    method_id=method['method_id'], name=method['method']['name'], status=row['status'],
                    comparison_category=method['comparison_category'], submitted_answer=row['prediction'],
                    answer_observation=_answer_observation(row), input_policy=method['method']['input_policy'],
                    citation_mapping_status=method['method']['configuration'].get('citation_mapping_status', 'not_declared'),
                    official_bridge_answer=answers.get(row['case_id']), metrics=score_rows[row['case_id']]['metrics'],
                    observed_evidence=_observed_evidence(row.get('record', {})),
                    source_artifacts=deepcopy(method['files'])))
    review = dict(format='benchmark-case-review-v1', suite=report['suite'], comparison_id=report['report_id'],
                  comparison_file=report_file, bundle_id=report['bundle_id'], bundle_path=str(bundle_path),
                  scope=report['scope'], case_count=report['case_count'], scorer_id=report['scorer_id'],
                  methods=[dict(method_id=m['method_id'], name=m['method']['name'],
                                comparison_category=m['comparison_category']) for m in report['methods']],
                  selection=dict(metrics=sorted(metrics), per_stratum=per_stratum, seed=seed,
                                 unit='task × paired metric × score direction', order='SHA256(seed, metric, case_id)',
                                 purpose='qualitative review only; no population estimate'),
                  task_comparisons=_task_comparisons(report, sorted(metrics)),
                  task_summary_interpretation=(
                      'Task summaries use all eligible cases in the declared comparison scope, not the sampled '
                      'review examples. Means are arithmetic per-case estimates in native metric units; they '
                      'do not replace original batch estimates such as Perl ROUGE. Conditional metrics retain '
                      'their own denominators; unavailable means are not zero. Score-direction counts describe '
                      'metric differences, not semantic correctness. No task-level significance test is computed.'),
                  strata=strata, assignments=assignments, cases=[])
    output.mkdir(parents=True, exist_ok=False)
    for case in cases.values():
        path = output / case['case_file']
        save_json(path, case)
        review['cases'].append(dict(case_id=case['case_id'], path=case['case_file'], sha256=digest(path)))
    review['review_id'] = fingerprint(review)
    save_json(output / 'review.json', review)
    (output / 'review.md').write_text(_markdown(review, cases), encoding='utf-8')
    with (output / 'annotations.jsonl').open('x', encoding='utf-8') as stream:
        for assignment in assignments:
            stream.write(json.dumps(dict(review_id=review['review_id'], assignment_id=assignment['assignment_id'],
                                         case_id=assignment['case_id'], status='unreviewed', reviewer=None,
                                         judgment=None, explanation=None, evidence=None), ensure_ascii=False) + '\n')
    return review
