"""Offline ALCE export and explicit local official model scorer bridge.

export_case(case, record) -> {item, audit}: item retains official gold fields,
question, docs in original candidate order, and converted output. audit records
raw_answer and every conversion/error. Only actual response.anchors keys whose
source_id maps via source_to_document may acquire a valid ALCE citation number.

CLI export accepts JSON list of {case, record} and writes {data, audit} JSON.
CLI score accepts that export, a clean pinned local ALCE checkout, local model
folders and an explicit --allow-model-inference. No model/data/dependency download
is permitted. A fresh output directory keeps exported input, frozen source code,
model file hashes, subprocess logs and per-case model scores. Scores are 0..1.
The bridge calls official functions directly and never the CLI's answer-trimming
preprocessing. Separate scoring artifacts do not mutate existing run journals.
"""
from __future__ import annotations

import argparse
import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys

OFFICIAL_REVISION = '246c476a4edfc564266b7346b6e29ef4861ae937'
OFFICIAL_URL = 'https://github.com/princeton-nlp/ALCE'


def export_case(case, record):
    if case.get('suite') != 'alce':
        raise ValueError('ALCE case required')
    if record.get('status') != 'success' or not isinstance(record.get('answer'), str) or not record['answer'].strip():
        raise ValueError('successful nonempty product answer required')
    candidates = case['candidate_documents']
    positions, equivalent = {}, {}
    for i, doc in enumerate(candidates, 1):
        if doc['id'] in positions:
            original = candidates[positions[doc['id']] - 1]
            if (doc['title'], doc['text']) != (original['title'], original['text']):
                raise ValueError('duplicate ALCE candidate identity has different title/text')
        positions.setdefault(doc['id'], i)
        equivalent.setdefault(doc['id'], []).append(i)
    if not candidates:
        raise ValueError('ALCE candidates unavailable')
    source_map = record.get('source_to_document', {})
    anchors = {}
    for anchor in record.get('response', {}).get('anchors', []):
        key = anchor.get('key')
        if isinstance(key, str):
            anchors.setdefault(key, []).append(anchor)
    conversions, errors = [], []
    invalid = len(candidates) + 1

    def replace(match):
        marker = match.group(0)
        if marker.startswith('[k'):
            key = marker[1:-1]
            observed = anchors.get(key, [])
            if 'anchor_documents' in record:
                verified = record['anchor_documents'].get(key)
                docs = {verified} if observed else set()
                # An explicit source contradicting the verified object is not repaired.
                docs.update(source_map.get(a['source_id']) for a in observed if a.get('source_id'))
            else:
                docs = {source_map.get(a.get('source_id')) for a in observed}
            valid = len(docs) == 1 and next(iter(docs)) in positions
            document = next(iter(docs)) if valid else None
            index = positions[document] if valid else invalid
            conversion = {'key': key, 'marker': marker, 'offset': match.start(), 'document_id': document,
                          'official_index': index, 'valid': valid,
                          'equivalent_candidate_indices': equivalent.get(document, [])}
            if not valid:
                errors.append({**conversion, 'reason': 'missing, ambiguous or noncandidate anchor/source mapping'})
            conversions.append(conversion)
            return f'[{index}]'
        # Even a numeric-looking original citation has no proven SN anchor link.
        errors.append({'key': marker[1:], 'marker': marker, 'offset': match.start(),
                       'official_index': invalid, 'reason': 'numeric citation has no observed SN anchor relation'})
        return f'[{invalid}'

    output = re.sub(r'\[k\d+\]|\[\d+', replace, record['answer'])
    item = copy.deepcopy(case['gold'])
    item.update(question=case['question'], docs=copy.deepcopy(candidates), output=output)
    audit = {'case_id': case['case_id'], 'task': case['task'], 'raw_answer': record['answer'],
             'converted_answer': output, 'conversions': conversions, 'mapping_errors': errors,
             'candidate_document_ids': [doc['id'] for doc in candidates],
             'invalid_citation_index': invalid, 'body_truncated': False}
    return {'item': item, 'audit': audit}


def _sha(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def freeze_official_checkout(checkout):
    """Validate the pinned clean checkout without importing any official code."""
    checkout = Path(checkout).resolve()
    def git(*args):
        return subprocess.run(['git', '-C', str(checkout), *args], text=True, capture_output=True, check=True).stdout.strip()
    try:
        revision = git('rev-parse', 'HEAD')
    except subprocess.CalledProcessError as exc:
        raise ValueError('official ALCE revision unavailable') from exc
    if revision != OFFICIAL_REVISION:
        raise ValueError(f'official ALCE revision must be {OFFICIAL_REVISION}; found {revision}')
    if git('status', '--porcelain', '--untracked-files=all'):
        raise ValueError('official ALCE checkout must be clean, including untracked files')
    paths = git('ls-files').splitlines()
    if not {'eval.py', 'utils.py'} <= set(paths):
        raise ValueError('official ALCE eval.py/utils.py missing')
    hashes = {name: _sha(checkout / name) for name in paths if (checkout / name).is_file()}
    return {'repository': OFFICIAL_URL, 'revision': revision, 'files': hashes}


def _model_identity(path):
    root = Path(path).resolve()
    if not root.is_dir():
        raise ValueError(f'local model directory unavailable: {root}')
    files = {str(p.relative_to(root)): _sha(p) for p in sorted(root.rglob('*')) if p.is_file()}
    if not files:
        raise ValueError('local model directory is empty')
    return {'path': str(root), 'files': files}


def _write(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + '\n', encoding='utf-8')


def run_official(export_path, checkout, output_dir, *, metrics, allow_model_inference=False,
                 autoais_model=None, qa_model=None, python_executable=None):
    """Explicit model run. Metrics: citations, claims, qa. Returns result JSON path."""
    if not allow_model_inference:
        raise ValueError('explicit allow_model_inference is required')
    metrics = list(dict.fromkeys(metrics))
    if not metrics or not set(metrics) <= {'citations', 'claims', 'qa'}:
        raise ValueError('metrics must select citations, claims and/or qa')
    source = freeze_official_checkout(checkout)
    payload = json.loads(Path(export_path).read_text(encoding='utf-8'))
    if not payload.get('data') or len(payload['data']) != len(payload.get('audit', [])):
        raise ValueError('export must contain aligned nonempty data and audit lists')
    for audit in payload['audit']:
        if 'claims' in metrics and audit['task'] != 'eli5':
            raise ValueError('claims scoring requires only ELI5 exports')
        if 'qa' in metrics and audit['task'] != 'asqa':
            raise ValueError('QA scoring requires only ASQA exports')
    models = {}
    if set(metrics) & {'citations', 'claims'}:
        if not autoais_model:
            raise ValueError('local autoais_model directory required')
        models['autoais'] = _model_identity(autoais_model)
    if 'qa' in metrics:
        if not qa_model:
            raise ValueError('local qa_model directory required')
        models['qa'] = _model_identity(qa_model)
    output = Path(output_dir).resolve()
    output.mkdir(parents=True, exist_ok=False)
    code = output / 'official-source'
    code.mkdir()
    for name in source['files']:
        if name.endswith('.py'):
            dest = code / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(Path(checkout) / name, dest)
            if _sha(dest) != source['files'][name]:
                raise ValueError('official code changed during freezing')
    _write(output / 'input.json', payload)
    invocation = {'source': source, 'models': models, 'metrics': metrics, 'input_sha256': _sha(output / 'input.json'),
                  'bridge_sha256': _sha(__file__), 'network_policy': 'HF_HUB_OFFLINE=1; TRANSFORMERS_OFFLINE=1',
                  'body_preprocessing': 'entire converted SN answer; no official CLI truncation', 'score_scale': '0..1',
                  'python_executable': str(python_executable or sys.executable)}
    _write(output / 'invocation.json', invocation)
    env = {**os.environ, 'HF_HUB_OFFLINE': '1', 'TRANSFORMERS_OFFLINE': '1', 'HF_DATASETS_OFFLINE': '1'}
    command = [str(python_executable or sys.executable), str(Path(__file__).resolve()), '_bridge', str(output)]
    with (output / 'stdout.log').open('w') as stdout, (output / 'stderr.log').open('w') as stderr:
        result = subprocess.run(command, env=env, stdout=stdout, stderr=stderr, check=False)
    _write(output / 'execution.json', {'returncode': result.returncode, 'status': 'completed' if result.returncode == 0 else 'error'})
    if result.returncode:
        raise RuntimeError(f'official ALCE scoring failed; inspect {output / "stderr.log"}')
    return output / 'scores.json'


def _bridge(output):
    output = Path(output)
    invocation = json.loads((output / 'invocation.json').read_text())
    payload = json.loads((output / 'input.json').read_text())
    sys.path.insert(0, str(output / 'official-source'))
    spec = importlib.util.spec_from_file_location('notebook_official_alce', output / 'official-source' / 'eval.py')
    official = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(official)
    if 'autoais' in invocation['models']:
        official.AUTOAIS_MODEL = invocation['models']['autoais']['path']
    if 'qa' in invocation['models']:
        official.QA_MODEL = invocation['models']['qa']['path']
    scores = []
    for item, audit in zip(payload['data'], payload['audit']):
        result = {}
        if 'citations' in invocation['metrics']:
            result.update(official.compute_autoais([copy.deepcopy(item)], qampari=audit['task'] == 'qampari', at_most_citations=None))
        if 'claims' in invocation['metrics']:
            result['claims_nli'] = official.compute_claims([copy.deepcopy(item)])
        if 'qa' in invocation['metrics']:
            clean = copy.deepcopy(item)
            clean['output'] = official.remove_citations(clean['output'])
            result.update(official.compute_qa([clean]))
        mapping = {'citation_rec': 'alce_citation_rec_official_v1', 'citation_prec': 'alce_citation_prec_official_v1',
                   'claims_nli': 'alce_eli5_claims_official_v1'}
        for name, value in result.items():
            scores.append({'case_id': audit['case_id'], 'task': audit['task'],
                           'scorer': 'product.notebook.' + mapping.get(name, f'alce_{name.lower()}_official_v1'),
                           'status': 'scored', 'score': float(value) / 100, 'reason': None,
                           'details': {'official_metric': name, 'official_value': float(value), 'source_revision': OFFICIAL_REVISION,
                                       'body_truncated': False, 'mapping_errors': audit['mapping_errors']}})
    _write(output / 'scores.json', {'scores': scores, 'invocation': invocation})


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    export = commands.add_parser('export', help='convert JSON list of {case, record} to audited official input')
    export.add_argument('--input', required=True)
    export.add_argument('--output', required=True)
    score = commands.add_parser('score', help='explicit offline local official model inference')
    score.add_argument('--input', required=True)
    score.add_argument('--checkout', required=True)
    score.add_argument('--output-dir', required=True)
    score.add_argument('--metrics', nargs='+', choices=['citations', 'claims', 'qa'], required=True)
    score.add_argument('--allow-model-inference', action='store_true', required=True)
    score.add_argument('--autoais-model')
    score.add_argument('--qa-model')
    score.add_argument('--python-executable')
    bridge = commands.add_parser('_bridge', help=argparse.SUPPRESS)
    bridge.add_argument('output_dir')
    args = parser.parse_args(argv)
    if args.command == 'export':
        rows = json.loads(Path(args.input).read_text(encoding='utf-8'))
        exports = [export_case(row['case'], row['record']) for row in rows]
        if Path(args.output).exists():
            raise ValueError('refusing to overwrite existing export')
        _write(args.output, {'data': [row['item'] for row in exports], 'audit': [row['audit'] for row in exports]})
    elif args.command == 'score':
        run_official(args.input, args.checkout, args.output_dir, metrics=args.metrics,
                     allow_model_inference=args.allow_model_inference, autoais_model=args.autoais_model,
                     qa_model=args.qa_model, python_executable=args.python_executable)
    else:
        _bridge(args.output_dir)


if __name__ == '__main__':
    main()
