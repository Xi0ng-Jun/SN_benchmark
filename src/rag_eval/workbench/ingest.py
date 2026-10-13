"""Explicit, validated result imports and immutable review targets."""
from __future__ import annotations

import hashlib
import errno
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from ..benchmark_comparison import validate_scored_submission
from ..identity import fingerprint
from ..run_reader import RunReadContext


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode('utf-8')


def _read_object(path):
    payload = Path(path).read_bytes()
    def unique(pairs):
        value = {}
        for key, item in pairs:
            if key in value:
                raise ValueError('Duplicate JSON field: ' + key)
            value[key] = item
        return value
    value = json.loads(payload, object_pairs_hook=unique)
    if not isinstance(value, dict):
        raise ValueError('Expected JSON object')
    return value, hashlib.sha256(payload).hexdigest()


def _directory(state_root, result_id):
    if not isinstance(result_id, str) or not re.fullmatch('[0-9a-f]{64}', result_id):
        raise ValueError('Invalid result identity')
    return Path(state_root) / 'imports' / result_id


def import_result(bundle_directory, submission, scores, state_root):
    """Freeze validated facts once, outside the original artifacts.

    No automatic source discovery or score-batch selection. A snapshot preserves
    the exact consumed case/document data and full submission/score objects;
    source hashes and original identities are retained in its commit manifest.
    """
    bundle_directory = Path(bundle_directory).resolve()
    paths = [Path(submission).resolve(), Path(scores).resolve()]
    state_root = Path(state_root).resolve()
    if any(state_root.is_relative_to(p) or p.is_relative_to(state_root)
           for p in [bundle_directory, *paths]):
        raise ValueError('Workbench state must be outside source artifacts')
    saved, submission_hash = _read_object(paths[0])
    score, scores_hash = _read_object(paths[1])
    bundle = RunReadContext().bundle(bundle_directory)
    sub = saved
    per_metrics, denominators, eligible, batch_only = validate_scored_submission(bundle, sub, score)
    for path, expected in zip(paths, [submission_hash, scores_hash]):
        if hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise ValueError('Source changed during result import')
    scope = set(sub['case_ids'])
    cases = [c for c in bundle['cases'] if c['case_id'] in scope]
    doc_ids = {did for case in cases for did in case['material_document_ids']}
    snapshot = dict(format='sn-workbench-result-v1', bundle_manifest=bundle['manifest'],
                    bundle_case_count=len(bundle['cases']), cases=cases,
                    documents=[d for d in bundle['documents'] if d['id'] in doc_ids],
                    submission=sub, scores=score, denominators=denominators,
                    eligible=eligible, per_metrics=sorted(per_metrics), batch_only=sorted(batch_only),
                    source_hashes={'submission': submission_hash, 'scores': scores_hash})
    result_id = fingerprint(snapshot)
    target = _directory(state_root, result_id)
    if target.exists():
        read_snapshot(state_root, result_id)
        return read_result(state_root, result_id, include_cases=False)
    target.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.import-', dir=target.parent))
    try:
        payload = json_bytes(snapshot)
        manifest = dict(format='sn-workbench-import-v1', id=result_id,
                        sha256=hashlib.sha256(payload).hexdigest(),
                        sources={'bundle': str(bundle_directory), 'submission': str(paths[0]), 'scores': str(paths[1])})
        for name, content in [('snapshot.json', payload), ('COMMIT.json', json_bytes(manifest))]:
            with (staging / name).open('xb') as stream:
                os.fchmod(stream.fileno(), 0o600)
                stream.write(content); stream.flush(); os.fsync(stream.fileno())
        fd = os.open(staging, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
        try:
            staging.rename(target)
        except OSError as exc:
            if exc.errno not in {errno.EEXIST, errno.ENOTEMPTY}:
                raise
            read_snapshot(state_root, result_id)
        fd = os.open(target.parent, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    finally:
        if staging.exists():
            shutil.rmtree(staging)
    return read_result(state_root, result_id, include_cases=False)


def read_snapshot(state_root, result_id):
    target = _directory(state_root, result_id)
    manifest, _ = _read_object(target / 'COMMIT.json')
    snapshot, checksum = _read_object(target / 'snapshot.json')
    if (manifest.get('format') != 'sn-workbench-import-v1' or manifest.get('id') != result_id
            or manifest.get('sha256') != checksum or fingerprint(snapshot) != result_id):
        raise ValueError('Imported result hash mismatch')
    return snapshot


def read_result(state_root, result_id, *, include_cases=True):
    snapshot = read_snapshot(state_root, result_id)
    score, sub = snapshot['scores'], snapshot['submission']
    view = dict(id=result_id, suite=sub['suite'], scope=sub['scope'], method=sub['method'],
                submission_id=score['submission_id'], scores_id=score['scores_id'],
                scorer_id=score['scorer_id'], profile=score['profile'],
                case_count=len(sub['case_ids']), bundle_case_count=snapshot['bundle_case_count'],
                coverage=sub['coverage'], pending_metrics=score['pending_metrics'],
                metrics={name: {'value': value, 'denominator': snapshot['denominators'][name],
                                'kind': 'batch-only' if name in snapshot['batch_only'] else 'per-case'}
                         for name, value in score['metrics'].items()},
                source_hashes=snapshot['source_hashes'], source=snapshot['bundle_manifest']['source'],
                original_at=None, original_time_reason='Score artifact does not declare an execution timestamp',
                official_paper_reproduction=score.get('official_paper_reproduction', False))
    if include_cases:
        cases = {c['case_id']: c for c in snapshot['cases']}
        view['cases'] = [dict(case_id=r['case_id'], question=cases[r['case_id']]['question'],
                              task=r['task'], status=r['status'], metrics=r['metrics']) for r in score['per_case']]
    return view


def read_case(state_root, result_id, case_id):
    snapshot = read_snapshot(state_root, result_id)
    case = next((c for c in snapshot['cases'] if c['case_id'] == case_id), None)
    if case is None:
        raise ValueError('Case is outside this result')
    prediction = next(r for r in snapshot['submission']['predictions'] if r['case_id'] == case_id)
    row = next(r for r in snapshot['scores']['per_case'] if r['case_id'] == case_id)
    metric_states = {name: ('not-applicable' if case_id not in snapshot['eligible'][name]
                            else 'batch-only' if name in snapshot['batch_only'] else 'evaluated')
                     for name in snapshot['scores']['metrics']}
    record = prediction.get('record', {})
    answer_source = next(('record.' + key for key in ('raw_generation', 'answer', 'normalized_prediction')
                          if isinstance(record.get(key), str)), 'prediction')
    answer = (prediction['prediction'] if answer_source == 'prediction'
              else record[answer_source.removeprefix('record.')])
    # Genuine observations only. Absence means unobserved, never a failed stage.
    fields = ['context_block', 'retrieval_context', 'retrieval', 'predicted_evidence',
              'predicted_supporting_facts', 'citation_index_to_document_id', 'shown_documents',
              'qasper_evidence', 'hotpot_evidence', 'trace', 'captures', 'raw_prediction']
    evidence = [dict(id=key, label=key, value=record[key], completeness='unknown')
                for key in fields if record.get(key) not in (None, '', [], {})]
    documents = [dict(id=d['id'], title=d['title'], text_sha256=d['text_sha256'],
                      characters=len(d['text']), excerpt=d['text'][:1500], excerpt_end=min(1500, len(d['text'])))
                 for d in snapshot['documents'] if d['id'] in case['material_document_ids']]
    return dict(result_id=result_id, case_id=case_id, task=case['task'], question=case['question'],
                status=prediction['status'], answer=answer, answer_source=answer_source,
                prediction=prediction['prediction'], metrics=row['metrics'], metric_states=metric_states,
                references=case['references'], gold=case['gold'], documents=documents, evidence=evidence,
                unavailable=[key for key in fields if key not in {e['id'] for e in evidence}],
                source_refs={'snapshot': result_id, 'submission': snapshot['scores']['submission_id'],
                             'scores': snapshot['scores']['scores_id']},
                evidence_ids=['answer', 'prediction', 'reference', *[e['id'] for e in evidence],
                              *['document:' + d['id'] for d in documents]])


def read_document(state_root, result_id, document_id):
    snapshot = read_snapshot(state_root, result_id)
    document = next((d for d in snapshot['documents'] if d['id'] == document_id), None)
    if document is None:
        raise ValueError('Document is outside this result')
    return document
