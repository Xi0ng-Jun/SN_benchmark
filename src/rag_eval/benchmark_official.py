"""Fixed-source scoring for complete, explicitly scoped benchmark submissions.

No network, model import or inference occurs on import or during preparation.
Text metrics execute verified upstream functions. ALCE model/distribution metrics
require a separate explicit batch CLI invocation; QMSum uses the author-confirmed
Perl ROUGE path, not Python rouge-score under an official-paper label.
"""
from __future__ import annotations

import ast
from collections import Counter
from copy import deepcopy
import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import string
import subprocess
import sys
from urllib.request import urlopen

from .artifacts import save_json
from .benchmark_submission import validate_submission
from .notebook_scoring import body_text
from .starter_protocol import fingerprint
from .multihop_official import SOURCE as MULTIHOP_RETRIEVAL_SOURCE, SOURCE_NAME as MULTIHOP_RETRIEVAL_SOURCE_NAME

SOURCES = {
    MULTIHOP_RETRIEVAL_SOURCE_NAME: MULTIHOP_RETRIEVAL_SOURCE,
    'qasper_evaluator.py': {
        'url': 'https://raw.githubusercontent.com/allenai/qasper-led-baseline/afd0fb96bf78ce8cd8157639c6f6a6995e4f9089/scripts/evaluator.py',
        'sha256': '781aba7cd8e524bef4f0a1b4bf3504e5b02cb1d8d5bf32a8f0a89dfa83e86bfe'},
    'multihop_qa_evaluate.py': {
        'url': 'https://raw.githubusercontent.com/yixuantt/MultiHop-RAG/c1c1287aa60a94acf9c4d20c891c9cd611a0f6e8/qa_evaluate.py',
        'sha256': 'a518bc37d94d2e84cc9012b41a758567e308dceea92aba14cda44a1364626ae7'},
    'alce_eval.py': {
        'url': 'https://raw.githubusercontent.com/princeton-nlp/ALCE/246c476a4edfc564266b7346b6e29ef4861ae937/eval.py',
        'sha256': 'e0f63bf865cacc64d7390fc62669c82b2653b1628f40051de0efdf5518064091'},
    'alce_utils.py': {
        'url': 'https://raw.githubusercontent.com/princeton-nlp/ALCE/246c476a4edfc564266b7346b6e29ef4861ae937/utils.py',
        'sha256': '92e6900b5350f7da4dc179f3d9f498f73d976377a412e8c244db8339ad7976b9'},
}


def validate_metrics(metrics):
    if not isinstance(metrics, dict):
        raise ValueError('Metrics must be an object')
    for name, value in metrics.items():
        if (not isinstance(name, str) or not name or type(value) not in (int, float)
                or not math.isfinite(value) or value < 0
                or (name != 'upstream_map_at_10' and value > 1)):
            raise ValueError('Metrics require finite 0..1 values; upstream_map_at_10 may exceed 1')
    return metrics


def scorer_identity(profile, dependencies):
    """Keep observable provenance while comparing dependency content across hosts."""
    identity = deepcopy(dependencies)
    if 'rouge' in identity:
        rouge = identity['rouge']
        rouge.pop('rouge_home', None)
        rouge.pop('perl_path', None)
        # PERL5LIB search order matters, the installation prefix does not.
        rouge['perl5lib'] = list(rouge.get('perl5lib', {}).values())
    return {'profile': profile, 'dependencies': identity}


def _bridge_dependencies(suite):
    files = ['benchmark_official.py', 'notebook_scoring.py']
    packages = []
    if suite == 'alce':
        files += ['notebook_alce.py']
        packages += ['numpy']
    elif suite == 'qmsum':
        files += ['qmsum_official.py']
    elif suite == 'multihop_rag':
        files += ['multihop_official.py']
    elif suite == 'qasper':
        files += ['qasper_evidence.py']
    return {'bridge': {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                       for name in files},
            'python': sys.version.split()[0],
            'packages': {name: importlib.metadata.version(name) for name in packages}}


def fetch_sources(directory):
    """Explicit acquisition of small fixed public scoring scripts, never weights."""
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name, identity in SOURCES.items():
        target = directory / name
        if target.exists():
            payload = target.read_bytes()
        else:
            with urlopen(identity['url'], timeout=45) as response:
                payload = response.read()
        if hashlib.sha256(payload).hexdigest() != identity['sha256']:
            raise ValueError('Official scoring source hash mismatch: ' + name)
        if not target.exists():
            with target.open('xb') as handle:
                handle.write(payload)
    save_json(directory / 'sources.json', SOURCES)
    return directory


def _source(directory, name):
    path = Path(directory) / name
    content = path.read_bytes()
    if hashlib.sha256(content).hexdigest() != SOURCES[name]['sha256']:
        raise ValueError('Official source differs from fixed revision: ' + name)
    return content.decode('utf-8')


def _functions(source, names, namespace=None):
    scope = dict(Counter=Counter, re=re, string=string)
    scope.update(namespace or {})
    tree = ast.parse(source)
    selected = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name in names]
    if {n.name for n in selected} != set(names):
        raise ValueError('Official function contract changed')
    exec(compile(ast.Module(body=selected, type_ignores=[]), '<verified-official-source>', 'exec'), scope)
    return scope


def prepare_inputs(bundle, submission):
    submission = validate_submission(bundle, submission)
    if bundle['manifest'].get('adaptation_revision') != 'notebook-data-v3':
        raise ValueError('New official comparisons require complete-input notebook-data-v3 bundles')
    cases = {c['case_id']: c for c in bundle['cases']}
    rows = submission['predictions']
    for row in rows:
        if set(row.get('record', {})) & {'gold', 'gold_document_ids', 'expected_answer', 'references'}:
            raise ValueError('Generation record contains gold scoring labels; use request-v3')
    suite = submission['suite']
    result = dict(suite=suite, coverage=submission['coverage'], scope=submission['scope'],
                  case_ids=submission['case_ids'], audit=[])
    style = submission['method']['citation_style']
    if suite == 'qasper':
        from .qasper_evidence import identity as evidence_identity, verify_evidence
        gold, predicted, evidence_supported = {}, {}, True
        mapping_errors = []
        policy = submission['method']['configuration'].get('qasper_evidence') if submission['method']['kind'] == 'sn' else None
        if policy is not None and policy != evidence_identity():
            raise ValueError('QASPER evidence policy identity differs from this projector')
        documents = {d['id']: d for d in bundle['documents']} if policy is not None else {}
        for row in rows:
            case = cases[row['case_id']]
            sample = case['sample_id']
            references = deepcopy(case['gold']['annotations'])
            for ref in references:
                ref['evidence'] = ([] if ref['type'] == 'none' else
                                   [x for x in ref['evidence'] if 'FLOAT SELECTED' not in x])
            gold[sample] = references
            record = row.get('record', {})
            evidence = record.get('predicted_evidence')
            saved = record.get('qasper_evidence')
            if saved is not None and policy is None:
                raise ValueError('QASPER evidence snapshot requires declared method policy identity')
            if submission['method']['kind'] == 'sn' and evidence is not None and policy is None:
                raise ValueError('SN evidence requires declared method policy and replayable snapshot')
            if policy is not None and row['status'] not in {'missing', 'error'}:
                if not isinstance(saved, dict) or {k: saved.get(k) for k in policy} != policy:
                    raise ValueError('QASPER evidence snapshot differs from method policy identity')
                if row['status'] != record.get('status') or (row['status'] == 'success' and row['prediction'] != record.get('answer')):
                    raise ValueError('QASPER evidence answer observation differs from submitted answer')
                if saved.get('status') == 'error':
                    if evidence is not None:
                        raise ValueError('QASPER evidence mapping error cannot carry partial evidence')
                    mapping_errors.append(row['case_id'])
                else:
                    did, = case['material_document_ids']
                    replayed = verify_evidence(record, documents[did], bundle['qasper_evidence_catalogues'][did])
                    if evidence != replayed:
                        raise ValueError('QASPER evidence differs from replayed projection')
            if evidence is not None and (not isinstance(evidence, list) or any(not isinstance(x, str) for x in evidence)):
                raise ValueError('Predicted QASPER evidence must be a list of original paragraph strings')
            evidence_supported &= evidence is not None
            if row['status'] not in {'missing', 'error'}:
                predicted[sample] = dict(answer=body_text(row['prediction']) if style == 'sn' else row['prediction'],
                                         evidence=evidence if evidence is not None else [])
        result.update(profile='qasper-v03-full-text-answer-v1', gold=gold, predictions=predicted,
                      evidence_supported=evidence_supported, evidence_mapping_errors=mapping_errors, text_evidence_only=True)
    elif suite == 'multihop_rag':
        result.update(profile='multihop-c1c1287-upstream-weak-match-v1', data=[
            dict(case_id=row['case_id'], query=cases[row['case_id']]['question'],
                 question_type=cases[row['case_id']]['gold']['question_type'],
                 gold_answer=cases[row['case_id']]['gold']['answer'],
                 model_answer=(body_text(row['prediction']) if style == 'sn' else row['prediction'])
                              if row['status'] not in {'missing', 'error'} else '') for row in rows])
    elif suite == 'alce':
        from .notebook_alce import export_case
        tasks = {cases[row['case_id']]['task'] for row in rows}
        if len(tasks) != 1:
            raise ValueError('One ALCE subtask required per official CLI batch')
        result.update(profile='alce-246c476-cli-v1', task=tasks.pop(), data=[])
        for row in rows:
            case = cases[row['case_id']]
            record = row.get('record', {})
            if row['status'] == 'success' and style == 'sn':
                if record.get('answer') != row['prediction'] or record.get('status') != 'success':
                    raise ValueError('SN answer differs from its observed citation record')
                converted = export_case(case, record)
                item, audit = converted['item'], converted['audit']
            else:
                item = deepcopy(case['gold'])
                text = row['prediction'] if row['status'] == 'success' else ''
                allowed = record.get('citation_index_to_document_id', {})
                candidates = case['candidate_documents']
                conversions = []
                def citation(match):
                    index = int(match.group(1))
                    valid = (0 < index <= len(candidates)
                             and allowed.get(str(index)) == candidates[index - 1]['id'])
                    target = index if valid else len(candidates) + 1
                    conversions.append(dict(original_index=index, official_index=target, valid=valid))
                    return '[' + str(target)
                # AutoAIS recognizes numeric prefixes even without a closing ].
                text = re.sub(r'\[(\d+)', citation, text)
                item.update(question=case['question'], docs=deepcopy(candidates), output=text)
                audit = dict(case_id=row['case_id'], raw_answer=row['prediction'], converted_answer=text,
                             status=row['status'], conversions=conversions)
            result['data'].append(item)
            result['audit'].append(audit)
    elif suite == 'qmsum':
        result.update(profile='qmsum-author-rouge155-hmnet-seg-v1', pairs=[
            dict(case_id=row['case_id'], reference=cases[row['case_id']]['gold']['answer'],
                 prediction=(body_text(row['prediction']) if style == 'sn' else row['prediction'])
                            if row['status'] not in {'missing', 'error'} else '') for row in rows])
    else:
        raise ValueError('Unsupported benchmark suite')
    return result


def _alce_text(prepared, source_directory):
    import numpy as np
    utils_source = _source(source_directory, 'alce_utils.py')
    eval_source = _source(source_directory, 'alce_eval.py')
    functions = _functions(utils_source, ['normalize_answer', 'remove_citations'], {'np': np})
    functions = _functions(eval_source, ['exact_presence', 'compute_str_em', 'compute_qampari_f1'], functions)
    # Execute the two actual preprocessing assignments from the verified CLI.
    main = next(n for n in ast.parse(eval_source).body if isinstance(n, ast.FunctionDef) and n.name == 'main')
    assignments = [n for n in ast.walk(main) if isinstance(n, ast.Assign)
                   and ('.strip().split(' in ast.unparse(n.value) or '<|im_end|>' in ast.unparse(n.value))]
    if len(assignments) != 2:
        raise ValueError('Official ALCE preprocessing contract changed')
    data = deepcopy(prepared['data'])
    code = compile(ast.Module(body=assignments, type_ignores=[]), '<official-cli-preprocessing>', 'exec')
    for i, item in enumerate(data):
        exec(code, {'data': data, 'i': i})
        item['output'] = functions['remove_citations'](item['output'])
    def score(items):
        if prepared['task'] == 'asqa':
            em, hit = functions['compute_str_em'](items)
            return {'str_em': float(em) / 100, 'str_hit': float(hit) / 100}
        if prepared['task'] == 'qampari':
            return {key: float(value) / 100 for key, value in functions['compute_qampari_f1'](items).items()
                    if key != 'num_preds'}
        return {}
    return score(data), [score([item]) for item in data], data


def score_prepared(bundle, submission, prepared, *, source_directory, output_dir, rouge_home=None,
                   alce_runtime=None):
    """Text/Perl scoring by default; full ALCE inference requires explicit runtime."""
    submission = validate_submission(bundle, submission)
    if prepared != prepare_inputs(bundle, submission):
        raise ValueError('Prepared inputs differ from frozen bundle and submission')
    if alce_runtime is not None and prepared['suite'] != 'alce':
        raise ValueError('ALCE runtime is only valid for ALCE submissions')
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=False)
    save_json(output_dir / 'prepared.json', prepared)
    save_json(output_dir / 'submission.json', submission)
    save_json(output_dir / 'bundle-manifest.json', bundle['manifest'])
    metrics, individual, pending, metric_case_ids = {}, [], [], {}
    rows = submission['predictions']
    suite = prepared['suite']
    dependencies = _bridge_dependencies(suite)
    if suite == 'qasper':
        source = _source(source_directory, 'qasper_evaluator.py')
        functions = _functions(source, ['normalize_answer', 'token_f1_score', 'paragraph_f1_score', 'evaluate'])
        def score(gold, predictions):
            values = functions['evaluate'](gold, predictions)
            result = {'answer_f1': values['Answer F1']}
            if prepared['evidence_supported']:
                result['evidence_f1'] = values['Evidence F1']
            return result
        metrics = score(prepared['gold'], prepared['predictions'])
        samples = {c['case_id']: c['sample_id'] for c in bundle['cases']}
        for row in rows:
            sample = samples[row['case_id']]
            pred = {sample: prepared['predictions'][sample]} if sample in prepared['predictions'] else {}
            individual.append(score({sample: prepared['gold'][sample]}, pred))
        dependencies['sources'] = {'qasper_evaluator.py': SOURCES['qasper_evaluator.py']}
        if not prepared['evidence_supported']:
            pending.append('evidence_f1: mapping errors; no partial denominator' if prepared.get('evidence_mapping_errors')
                           else 'evidence_f1: method has no explicit predicted paragraph evidence')
    elif suite == 'multihop_rag':
        source = _source(source_directory, 'multihop_qa_evaluate.py')
        functions = _functions(source, ['has_intersection', 'extract_answer', 'calculate_metrics'])
        for item in prepared['data']:
            score = functions['calculate_metrics']([functions['extract_answer'](item['model_answer'])], [item['gold_answer']])[0]
            individual.append({'upstream_weak_match_accuracy': score})
        metrics = {'upstream_weak_match_accuracy': sum(x['upstream_weak_match_accuracy'] for x in individual) / len(individual)}
        dependencies['sources'] = {'multihop_qa_evaluate.py': SOURCES['multihop_qa_evaluate.py']}
        from .multihop_official import score_multihop_retrieval
        retrieval = score_multihop_retrieval(bundle, submission, source_directory=source_directory)
        save_json(output_dir / 'retrieval.json', retrieval)
        dependencies['retrieval'] = retrieval['dependencies']
        metrics.update(retrieval['metrics'])
        pending += retrieval['pending_metrics']
        metric_case_ids.update({name: [r['case_id'] for r in retrieval['per_case']] for name in retrieval['metrics']})
    elif suite == 'alce':
        metrics, individual, normalized = _alce_text(prepared, source_directory)
        dependencies['sources'] = {k: SOURCES[k] for k in ('alce_eval.py', 'alce_utils.py')}
        dependencies['at_most_citations'] = 3
        dependencies['preprocessing'] = 'verified official CLI assignments; numeric citations removed for text metrics'
        save_json(output_dir / (prepared['task'] + '.json'), {'data': prepared['data']})
        save_json(output_dir / 'normalized-text.json', normalized)
        pending = ['citation_rec', 'citation_prec']
        if prepared['task'] == 'asqa':
            pending += ['qa', 'mauve', 'rougeLsum']
        elif prepared['task'] == 'eli5':
            pending += ['claims_nli', 'mauve', 'rougeLsum']
        if alce_runtime is not None:
            from .alce_official_cli import score_alce_batch
            batch = score_alce_batch(prepared, source_directory=source_directory,
                                     output_dir=output_dir / 'official-cli', **alce_runtime)
            for key, value in metrics.items():
                if not math.isclose(batch['metrics'][key], value, abs_tol=1e-12):
                    raise ValueError('Official full CLI differs from prepared text scoring: ' + key)
            metrics = batch['metrics']
            metric_case_ids.update(batch['metric_case_ids'])
            dependencies['alce_batch'] = batch['dependencies']
            pending = []
    else:
        if rouge_home is None:
            raise ValueError('QMSum requires an explicit ROUGE-1.5.5 distribution')
        from .qmsum_official import score_qmsum_rouge
        result = score_qmsum_rouge(prepared['pairs'], rouge_home=rouge_home,
                                  output_dir=output_dir / 'rouge', segmentation='hmnet_regex')
        metrics = result['metrics']
        per_id = {r['case_id']: r['metrics'] for r in result['per_case']}
        individual = [per_id[row['case_id']] for row in rows]
        dependencies['rouge'] = result['dependencies']
        dependencies['segmentation'] = result['segmentation']
    validate_metrics(metrics)
    if len(individual) != len(rows):
        raise ValueError('Official per-case coverage differs from frozen scope')
    cases = {c['case_id']: c for c in bundle['cases']}
    per_case = [dict(case_id=row['case_id'], status=row['status'], group_id=cases[row['case_id']]['group_id'],
                     task=cases[row['case_id']]['task'], metrics=validate_metrics(scores))
                for row, scores in zip(rows, individual)]
    result = dict(format='benchmark-scored-submission-v1', bundle_id=submission['bundle_id'],
                  submission_id=fingerprint(submission), suite=suite, scope=submission['scope'],
                  case_ids=submission['case_ids'], method=submission['method'], method_id=submission['method_id'],
                  coverage=submission['coverage'], profile=prepared['profile'], dependencies=dependencies,
                  prepared_id=fingerprint(prepared),
                  scorer_identity=scorer_identity(prepared['profile'], dependencies),
                  scorer_id=fingerprint(scorer_identity(prepared['profile'], dependencies)),
                  metrics=metrics, per_case=per_case, pending_metrics=pending,
                  metric_case_ids={name: metric_case_ids.get(name, submission['case_ids']) for name in metrics},
                  metric_denominators={name: len(metric_case_ids.get(name, rows)) for name in metrics},
                  official_paper_reproduction=False)
    result['scores_id'] = fingerprint(result)
    save_json(output_dir / 'scores.json', result)
    return result
