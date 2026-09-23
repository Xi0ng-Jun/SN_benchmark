"""Attach explicit ALCE model scores as a new auditable run, never overwrite."""
from __future__ import annotations

import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json, save_jsonl
from .notebook_alce import OFFICIAL_REVISION, export_case
from .notebook_bundle import load_bundle
from .notebook_runner import plan_rows
from .starter_protocol import fingerprint
from .starter_results import result_record
from .starter_runner import read_rows


def _export_payload(run):
    bundle = load_bundle(run / 'input')
    cases = {c['case_id']: c for c in bundle['cases']}
    exports = [export_case(cases[o['case_id']], o['product_record']) for o in read_rows(run / 'outputs.jsonl')
               if o['status'] == 'success' and o['output_available']]
    if not exports:
        raise ValueError('No successful ALCE outputs to score')
    return {'data': [x['item'] for x in exports], 'audit': [x['audit'] for x in exports]}


def export_run(run, output):
    from .starter_report import load_run
    run, output = Path(run).resolve(), Path(output).resolve()
    loaded = load_run(run)
    if loaded['manifest']['suite'] != 'alce':
        raise ValueError('Only ALCE runs can be exported')
    if output.exists():
        raise ValueError('Use a new export file')
    save_json(output, _export_payload(run))
    return output


def _read_official(run, directory, planned):
    invocation = json.loads((directory / 'invocation.json').read_text(encoding='utf-8'))
    result = json.loads((directory / 'scores.json').read_text(encoding='utf-8'))
    execution = json.loads((directory / 'execution.json').read_text(encoding='utf-8'))
    payload = json.loads((directory / 'input.json').read_text(encoding='utf-8'))
    if execution.get('status') != 'completed' or execution.get('returncode') != 0:
        raise ValueError('Official scoring did not finish successfully')
    if (invocation != result['invocation'] or digest(directory / 'input.json') != invocation['input_sha256']
            or invocation['source']['revision'] != OFFICIAL_REVISION or payload != _export_payload(run)):
        raise ValueError('Official scores refer to different inputs or source')
    source_files = invocation['source'].get('files', {})
    if not {'eval.py', 'utils.py'} <= source_files.keys():
        raise ValueError('Official source snapshot incomplete')
    for name, checksum in source_files.items():
        if not name.endswith('.py'):
            continue
        snapshot = (directory / 'official-source' / name).resolve()
        if not snapshot.is_relative_to((directory / 'official-source').resolve()) or digest(snapshot) != checksum:
            raise ValueError('Official source snapshot hash mismatch')
    allowed = {'citations': ['alce_citation_rec_official_v1', 'alce_citation_prec_official_v1'],
               'claims': ['alce_eli5_claims_official_v1']}
    if not invocation['metrics'] or not set(invocation['metrics']) <= allowed.keys():
        raise ValueError('Only planned citations/claims scores can be attached; QA is a standalone diagnostic')
    expected = {(audit['case_id'], 'product.notebook.' + metric)
                for audit in payload['audit'] for kind in invocation['metrics'] for metric in allowed[kind]}
    plans = {(p['case_id'], p['scorer']): p for p in planned}
    observed = {}
    for row in result['scores']:
        key = row['case_id'], row['scorer']
        if key in observed or key not in expected or key not in plans or row['task'] != plans[key]['task']:
            raise ValueError('Duplicate, unexpected or unplanned official score')
        if row['status'] != 'scored':
            raise ValueError('Official result must be a valid scored value')
        result_record(plans[key], status='scored', score=row['score'], output_available=True)
        observed[key] = row
    if set(observed) != expected:
        raise ValueError('Official result coverage incomplete')
    return invocation, observed


def _scoring_identity(invocation):
    # Input bytes differ between modes; model/code/formula must match for pairing.
    return {k: v for k, v in invocation.items() if k != 'input_sha256'}


def attach_scores(run, official_dir, output):
    from .starter_report import load_run
    run, official_dir, output = [Path(p).resolve() for p in (run, official_dir, output)]
    if output.exists() or any(output.is_relative_to(p) or p.is_relative_to(output) for p in (run, official_dir)):
        raise ValueError('Use a new separate derived run directory')
    loaded = load_run(run)
    manifest = loaded['manifest']
    if manifest['suite'] != 'alce' or manifest['identity'].get('official_scoring'):
        raise ValueError('Attach once to an original ALCE run; score citations and claims together for ELI5')
    invocation, scores = _read_official(run, official_dir, loaded['planned'])
    output.mkdir(parents=True, exist_ok=False)
    shutil.copytree(run / 'input', output / 'input')
    shutil.copytree(official_dir, output / 'official-scoring')
    for name in ('product-bundle.json', 'outputs.jsonl', 'model-events.jsonl', 'preparation-usage.json', 'source-identity.json', 'runtime-identity.json'):
        if (run / name).exists():
            shutil.copyfile(run / name, output / name)
    save_json(output / 'base-manifest.json', manifest)
    save_jsonl(output / 'base-planned.jsonl', loaded['planned'])
    save_jsonl(output / 'base-scores.jsonl', loaded['scores'])
    identity = {**manifest['identity'], 'official_scoring': _scoring_identity(invocation)}
    protocol = fingerprint({**identity, 'mode': manifest['mode']})
    cases = load_bundle(output / 'input')['cases']
    ids = {p['case_id'] for p in loaded['planned']}
    planned = plan_rows([c for c in cases if c['case_id'] in ids], output.name, protocol, manifest['mode'])
    save_jsonl(output / 'planned.jsonl', planned)
    previous = {(s['case_id'], s['scorer']): s for s in loaded['scores']}
    values = []
    for plan in planned:
        key = plan['case_id'], plan['scorer']
        old = previous.get(key)
        value = scores.get(key, old)
        if value is None:
            continue
        values.append(result_record(plan, status=value['status'], score=value['score'], reason=value.get('reason'),
                      details=value.get('details'), output_available=True if key in scores else old['output_available'],
                      trace=old.get('trace') if old else None,
                      normalized_answer=old.get('normalized_answer') if old else None))
    save_jsonl(output / 'scores.jsonl', values)
    attachment = {'origin_run': str(run), 'files': {str(p.relative_to(output)): digest(p)
                  for p in sorted(output.rglob('*')) if p.is_file() and '__pycache__' not in p.parts}}
    save_json(output / 'manifest.json', {**manifest, 'run_id': output.name, 'identity': identity,
              'protocol_id': protocol, 'pairing_id': fingerprint(identity), 'planned_sha256': digest(output / 'planned.jsonl'),
              'scoring_attachment': attachment})
    # A derived scoring pass does not hide an interrupted or failed product run.
    save_json(output / 'state.json', loaded['state'])
    load_run(output)
    return output


def validate_attachment(run, manifest, planned):
    attachment = manifest.get('scoring_attachment')
    if not attachment:
        raise ValueError('Official scoring identity lacks saved attachment')
    files = attachment.get('files', {})
    required = {'base-manifest.json', 'base-planned.jsonl', 'base-scores.jsonl', 'outputs.jsonl', 'scores.jsonl',
                'official-scoring/input.json', 'official-scoring/invocation.json',
                'official-scoring/execution.json', 'official-scoring/scores.json'}
    if not required <= files.keys():
        raise ValueError('Official attachment is missing required artifact hashes')
    for name, checksum in files.items():
        path = (run / name).resolve()
        if not path.is_relative_to(run.resolve()) or digest(path) != checksum:
            raise ValueError('Official attachment artifact hash mismatch')
    base = json.loads((run / 'base-manifest.json').read_text(encoding='utf-8'))
    invocation, _ = _read_official(run, run / 'official-scoring', planned)
    expected = {**base['identity'], 'official_scoring': _scoring_identity(invocation)}
    if manifest['identity'] != expected or base['mode'] != manifest['mode']:
        raise ValueError('Official scoring configuration changed')
