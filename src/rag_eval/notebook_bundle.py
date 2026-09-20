"""Content-frozen notebook benchmark inputs and indivisible material scopes."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json, save_jsonl
from .notebook_data import VERSION, SUITES, ADAPTATION_REVISION, LEGACY_ADAPTATION, adapt
from .starter_protocol import fingerprint, require_text


LEGACY_REQUEST_REVISION = 'notebook-request-v1'
REQUEST_REVISIONS = (LEGACY_REQUEST_REVISION, 'notebook-request-v2')


def _read_data(path, suite):
    text = Path(path).read_text(encoding='utf-8')
    if suite == 'qmsum':
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    return json.loads(text)


def _source(source, suite):
    if not isinstance(source, dict):
        raise ValueError('Source metadata must be an object')
    for field in ('dataset', 'split', 'revision', 'source_url', 'license'):
        require_text(source.get(field), field)
    if suite == 'alce':
        for field in ('task', 'retriever', 'variant'):
            require_text(source.get(field), field)
    if suite == 'multihop_rag' and source['split'] != 'train':
        raise ValueError('MultiHop-RAG publishes train only, not an independent test split')


def _build(suite, raw, corpus, source, cap, adaptation_revision=ADAPTATION_REVISION):
    _source(source, suite)
    if type(cap) is not int or cap < 1:
        raise ValueError('Document capacity must be a positive integer')
    data = adapt(suite, raw, corpus=corpus, task=source.get('task'), adaptation_revision=adaptation_revision)
    groups = {}
    for case in data['cases']:
        case.update(dataset=source['dataset'], split=source['split'])
        group = groups.setdefault(case['group_id'], dict(partition_id=case['group_id'], case_ids=[], document_ids=[]))
        group['case_ids'].append(case['case_id'])
        group['document_ids'] = list(dict.fromkeys(group['document_ids'] + case['material_document_ids']))
        if len(group['document_ids']) > cap:
            raise ValueError('Complete material set exceeds document capacity; raise capacity explicitly, never split or truncate it')
    data['partitions'] = list(groups.values())
    return data


def prepare(suite, raw_path, source_path, output, *, corpus_path=None, max_documents=40):
    if suite not in SUITES:
        raise ValueError('Unknown notebook benchmark')
    output = Path(output).resolve()
    if output.exists():
        raise ValueError('Use a new bundle directory')
    source = json.loads(Path(source_path).read_text(encoding='utf-8'))
    # Validate before creating output; never write a partial apparently usable bundle.
    data = _build(suite, _read_data(raw_path, suite),
                  _read_data(corpus_path, 'multihop_rag') if corpus_path else None, source, max_documents)
    output.mkdir(parents=True, exist_ok=False)
    shutil.copyfile(raw_path, output / 'raw-data')
    if corpus_path:
        shutil.copyfile(corpus_path, output / 'raw-corpus')
    save_json(output / 'source.json', source)
    # Reconstruct from copied bytes to catch source changes while preparing.
    copied = _build(suite, _read_data(output / 'raw-data', suite),
                    _read_data(output / 'raw-corpus', 'multihop_rag') if corpus_path else None, source, max_documents)
    if copied != data:
        raise ValueError('Source changed during copying')
    for field in ('cases', 'documents', 'decisions', 'partitions'):
        save_jsonl(output / (field + '.jsonl'), data[field])
    files = ['raw-data', 'source.json', 'cases.jsonl', 'documents.jsonl', 'decisions.jsonl', 'partitions.jsonl']
    if corpus_path:
        files.append('raw-corpus')
    manifest = dict(format=VERSION, protocol_version=VERSION, suite=suite, source=source, adaptation_revision=ADAPTATION_REVISION,
                    max_documents=max_documents, files={name: digest(output / name) for name in files},
                    selected_cases=len(data['cases']), excluded_cases=sum(d['status'] == 'excluded' for d in data['decisions']),
                    partition_count=len(data['partitions']), release_gate=False,
                    scope='complete corpus' if suite == 'multihop_rag' else 'per paper/meeting/candidate set')
    save_json(output / 'manifest.json', manifest)
    return dict(data, manifest=manifest)


def load_bundle(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if manifest.get('format') != VERSION or manifest.get('protocol_version') != VERSION or manifest.get('suite') not in SUITES:
        raise ValueError('Unsupported notebook bundle')
    expected = {'raw-data', 'source.json', 'cases.jsonl', 'documents.jsonl', 'decisions.jsonl', 'partitions.jsonl'}
    if manifest['suite'] == 'multihop_rag':
        expected.add('raw-corpus')
    if set(manifest.get('files', {})) != expected:
        raise ValueError('Unexpected bundle files')
    for name, checksum in manifest['files'].items():
        if digest(directory / name) != checksum:
            raise ValueError('Frozen bundle hash mismatch: ' + name)
    source = json.loads((directory / 'source.json').read_text(encoding='utf-8'))
    if source != manifest['source']:
        raise ValueError('Source metadata changed')
    rebuilt = _build(manifest['suite'], _read_data(directory / 'raw-data', manifest['suite']),
                     _read_data(directory / 'raw-corpus', 'multihop_rag') if 'raw-corpus' in expected else None,
                     source, manifest['max_documents'], manifest.get('adaptation_revision', LEGACY_ADAPTATION))
    for field in ('cases', 'documents', 'decisions', 'partitions'):
        stored = [json.loads(line) for line in (directory / (field + '.jsonl')).read_text(encoding='utf-8').splitlines()]
        if stored != rebuilt[field]:
            raise ValueError('Frozen ' + field + ' differ from canonical source')
    if (manifest['selected_cases'] != len(rebuilt['cases']) or manifest['partition_count'] != len(rebuilt['partitions'])
            or manifest['excluded_cases'] != sum(d['status'] == 'excluded' for d in rebuilt['decisions'])
            or manifest.get('release_gate') is not False):
        raise ValueError('Bundle counts or release policy changed')
    return dict(rebuilt, manifest=manifest)


def partition_bundle(bundle, partition_id, *, request_revision=LEGACY_REQUEST_REVISION):
    if request_revision not in REQUEST_REVISIONS:
        raise ValueError('Unknown notebook request revision')
    partitions = {p['partition_id']: p for p in bundle['partitions']}
    if partition_id not in partitions:
        raise ValueError('Unknown notebook partition')
    part = partitions[partition_id]
    cases = {c['case_id']: c for c in bundle['cases']}
    documents = {d['id']: d for d in bundle['documents']}
    questions = []
    for cid in part['case_ids']:
        case = cases[cid]
        # Question-side task instructions never contain annotations or gold evidence locations.
        instruction = {
            'qasper': 'Answer using the paper. Give a concise answer; if it is not answerable from the paper, answer Unanswerable.',
            'multihop_rag': 'Answer using the notebook articles. Give a concise answer; say if the sources are insufficient.',
            'qmsum': 'Summarize the meeting with respect to this query. Use only the meeting transcript.',
            'alce': ('Answer with a comma-separated list of answers, citing the notebook sources.' if case['task'] == 'qampari'
                     else 'Answer using the notebook sources and cite the supporting sources.'),
        }[case['suite']]
        if request_revision == 'notebook-request-v2':
            instruction = {
                'qmsum': 'Provide a query-focused summary using only the meeting transcript.',
                'qasper': 'Answer using the paper. Give a concise answer; if the question is not answerable from the paper, answer Unanswerable.',
            }.get(case['suite'], instruction)
        questions.append({**{k: case[k] for k in ('case_id', 'sample_id', 'suite', 'task', 'dataset', 'split',
                         'references', 'gold_document_ids', 'material_document_ids', 'material_role', 'product_protocol')},
                          'id': cid, 'question': case['question'] + '\n\n' + instruction,
                          'original_question': case['question'], 'expected_answer': case['references'][0] if case['references'] else ''})
        if request_revision != LEGACY_REQUEST_REVISION:
            questions[-1]['request_revision'] = request_revision
    result = dict(questions=questions, documents=[documents[d] for d in part['document_ids']],
                  decisions=[d for d in bundle['decisions'] if d['case_id'] in part['case_ids']])
    result['manifest'] = dict(protocol_version=VERSION, product_protocol=VERSION, suite=bundle['manifest']['suite'],
                             source_manifest_fingerprint=fingerprint(bundle['manifest']), partition_id=partition_id,
                             document_count=len(result['documents']), question_count=len(questions),
                             max_documents=bundle['manifest']['max_documents'],
                             **{k + '_sha256': fingerprint(v) for k, v in result.items()})
    # Omit the legacy field so already frozen product bundles rebuild identically.
    if request_revision != LEGACY_REQUEST_REVISION:
        result['manifest']['request_revision'] = request_revision
    return result
