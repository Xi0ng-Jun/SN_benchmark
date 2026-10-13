"""Evaluator-owned oracle inputs and isolated generation, never a formal run.

The runner must opt in explicitly and supply a generation-only callable. The
callable receives one public question plus its selected source units; it must
bypass retrieval and pass those units to synthesis. Its returned captures are
observations, whereas oracle-inputs.json is only the intended input. This
module never starts SN, imports a provider, or scores an answer.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path

from .artifacts import save_json, save_jsonl
from .artifact_store import reject_shared_output
from .identity import fingerprint
from .run_results import EventJournal
from .scoring_projection import validate_public_json

KIND = 'diagnostic/oracle'
FORMAT = 'sn-oracle-inputs-v1'
RUN_FORMAT = 'sn-oracle-run-v1'
QUESTION_FIELDS = {'case_id', 'sample_id', 'suite', 'task', 'dataset', 'split',
                   'material_document_ids', 'material_role', 'product_protocol',
                   'id', 'question', 'original_question', 'request_revision'}
OUTPUT_STATUSES = {'success', 'no_answer', 'clarification', 'error', 'missing', 'pending'}


def require_formal_run(manifest):
    """Export seam: call immediately after reading a manifest, before export.

    Raises ValueError for diagnostic/oracle artifacts, including incomplete
    ones. Each top-level diagnostic flag, diagnostic kind or oracle format
    independently excludes formal export; changing the ordinary mode/track
    cannot make it formal. Ordinary manifests are returned unchanged. No data
    is written. Exporters must invoke this before load_run's formal validation.
    """
    if not isinstance(manifest, dict):
        raise ValueError('Run manifest must be an object')
    identity = manifest.get('identity', {})
    if (manifest.get('format') in {RUN_FORMAT, FORMAT}
            or manifest.get('diagnostic') or str(manifest.get('kind', '')).startswith('diagnostic')
            or manifest.get('track') in {'oracle', KIND, 'diagnostic'}
            or isinstance(identity, dict) and identity.get('diagnostic')):
        raise ValueError('diagnostic/oracle run cannot be exported as a formal submission')
    return manifest


def require_formal_submission(method, predictions):
    """Keep explicit diagnostic provenance out of every formal answer sheet.

    build_submission and its read-back validator share this boundary, so an
    existing diagnostic sheet cannot bypass it via scoring or comparison.
    Inspect provenance fields, never answer/source text.
    """
    import re
    require_formal_run(method)
    configuration = method.get('configuration', {})
    require_formal_run(configuration)
    if isinstance(configuration.get('identity'), dict):
        require_formal_run(configuration['identity'])
    if re.search(r'(?:oracle|gold[ _-]?input|gold[ _-]?evidence|gold[ _-]?span)',
                 method.get('input_policy', ''), re.I):
        raise ValueError('diagnostic/oracle input policy cannot enter a formal submission')
    for row in predictions:
        if not isinstance(row, dict):
            continue  # ordinary row schema validation remains with its producer
        require_formal_run(row)
        for name in ('record', 'product_record'):
            if isinstance(row.get(name), dict):
                require_formal_run(row[name])


def _units(document):
    units = document.get('source_units')
    if units is None:
        units = [{'id': f'paragraph:{i}', 'text': text}
                 for i, text in enumerate(document['text'].split('\n\n')) if text.strip()]
    if (not isinstance(units, list) or any(not isinstance(u, dict)
            or not isinstance(u.get('id'), str) or not isinstance(u.get('text'), str) for u in units)
            or len({u['id'] for u in units}) != len(units)):
        raise ValueError('Invalid public source units')
    return units


def gold_evidence_from_cases(product_bundle, cases):
    """Project evaluator labels to locators, without passing answers onwards.

    QASPER: union of annotated paragraph texts, preserving duplicate source
    positions. HotpotQA: supporting sentence units. MultiHop: public paragraphs
    containing evidence facts (not the fact annotation itself). QMSum specific:
    inclusive annotated turn spans. ALCE and QMSum general have no supported
    gold passage labels and raise rather than treating candidate docs as gold.
    Unmappable evidence raises; callers must report the diagnostic as unavailable.
    """
    documents = {d['id']: d for d in product_bundle['documents']}
    result = {}
    for case in cases:
        cid, suite, gold = case['case_id'], case['suite'], case['gold']
        if cid in result:
            raise ValueError('Duplicate evaluator case')
        evidence = []
        if suite == 'qasper':
            did, = case['material_document_ids']
            for annotation in gold['annotations']:
                for text in annotation['evidence']:
                    matches = [u for u in _units(documents[did])
                               if ' '.join(u['text'].split()) == ' '.join(text.split())]
                    if not matches:
                        raise ValueError('Oracle gold evidence is unmappable to public units')
                    evidence.extend({'document_id': did, 'source_unit_id': u['id']} for u in matches)
        elif suite == 'hotpotqa':
            if gold.get('unmapped_supporting_facts'):
                raise ValueError('Oracle supporting facts are unmappable')
            evidence = [{k: e[k] for k in ('document_id', 'source_unit_id')}
                        for e in gold['supporting_facts']]
        elif suite == 'multihop_rag':
            for e in gold['evidence']:
                document = documents[e['document_id']]
                # Released body units can contain multiple paragraphs. Keep
                # the source paragraph, not a synthesized fact or entire body.
                body = [u['text'] for u in _units(document) if u.get('kind') == 'body']
                paragraphs = '\n\n'.join(body or [document['text']]).split('\n\n')
                matches = [p for p in paragraphs if ' '.join(e['fact'].split()) in ' '.join(p.split())]
                if not matches:
                    raise ValueError('Oracle evidence fact is unmappable to a source paragraph')
                evidence.extend({'document_id': e['document_id'], 'text': p} for p in matches)
        elif suite == 'qmsum' and case['task'] == 'specific':
            did, = case['material_document_ids']
            evidence = [{'document_id': did, 'source_unit_id': f'turn:{i}'}
                        for start, end in gold['relevant_text_span'] for i in range(start, end + 1)]
        else:
            raise ValueError('Oracle gold passage labels unavailable for this suite/task')
        result[cid] = evidence
    return result


def prepare_oracle_inputs(product_bundle, gold_evidence, *, case_ids=None):
    """Build per-case context using only verified public units selected by gold.

    gold_evidence maps case IDs to lists of {document_id, source_unit_id} or
    {document_id, text} locators. Text must equal a source unit or paragraph
    modulo whitespace. Missing labels are an error; an explicit [] is valid
    empty context. Selection retains frozen question order and never filters
    cases by evidence availability. This returns a new object, not a bundle
    usable by the ordinary full-corpus runner.
    """
    if product_bundle.get('manifest', {}).get('request_revision') != 'notebook-request-v3':
        raise ValueError('Oracle requires a gold-free notebook-request-v3 product bundle')
    questions = product_bundle['questions']
    all_ids = [q['case_id'] for q in questions]
    if not all_ids or len(set(all_ids)) != len(all_ids):
        raise ValueError('Oracle requires distinct public questions')
    if case_ids is None:
        case_ids = all_ids
    if (not isinstance(case_ids, (list, tuple)) or not case_ids
            or any(not isinstance(c, str) for c in case_ids)
            or len(set(case_ids)) != len(case_ids) or not set(case_ids) <= set(all_ids)):
        raise ValueError('Invalid oracle case scope')
    if not isinstance(gold_evidence, dict):
        raise ValueError('Oracle evidence must be a case-ID mapping')
    documents = {d['id']: d for d in product_bundle['documents']}
    if len(documents) != len(product_bundle['documents']):
        raise ValueError('Duplicate public document')
    requests = []
    for question in questions:
        if set(question) - QUESTION_FIELDS:
            raise ValueError('Oracle question contains unexpected or evaluator-only fields')
        cid = question['case_id']
        if cid not in case_ids:
            continue
        if not isinstance(question.get('question'), str) or not question['question'].strip():
            raise ValueError('Oracle public question must be nonempty text')
        labels = gold_evidence.get(cid)
        if not isinstance(labels, list):
            raise ValueError('Oracle case has no explicit gold evidence list')
        selected, seen = [], set()
        for label in labels:
            if not isinstance(label, dict) or set(label) not in (
                    {'document_id', 'source_unit_id'}, {'document_id', 'text'}):
                raise ValueError('Oracle evidence requires a public unit or paragraph locator')
            did = label['document_id']
            if did not in documents or did not in question['material_document_ids']:
                raise ValueError('Oracle evidence refers to an out-of-scope public document')
            units = _units(documents[did])
            if 'source_unit_id' in label:
                matches = [u for u in units if u['id'] == label['source_unit_id']]
            else:
                text = label['text']
                if not isinstance(text, str) or not text.strip():
                    raise ValueError('Oracle paragraph text must be nonempty')
                # Paragraph locators are needed for MultiHop body units.
                paragraphs = [{'id': f'paragraph:{i}', 'text': p}
                              for i, p in enumerate(documents[did]['text'].split('\n\n'))]
                matches = [u for u in units + paragraphs
                           if ' '.join(u['text'].split()) == ' '.join(text.split())]
                matches = matches[:1]
            if not matches:
                raise ValueError('Oracle gold evidence is unmappable to public units')
            for unit in matches:
                key = (did, unit['id'])
                if key not in seen:
                    selected.append(dict(document_id=did, source_unit_id=unit['id'], text=unit['text']))
                    seen.add(key)
        requests.append(dict(case_id=cid, question=deepcopy(question),
                             retrieval_context=[u['text'] for u in selected], context_units=selected))
    prepared = dict(format=FORMAT, kind=KIND, diagnostic=True,
                    source_product_id=fingerprint(product_bundle), requests=requests)
    validate_public_json(prepared)
    return prepared


def execute_oracle_track(source_run, gold_evidence, run_dir, *, generate, case_ids=None):
    """Explicit runner seam; generate(request) -> observed output dictionary.

    Creates a fresh diagnostic directory, manifest, input projection and plan
    before invoking generate. Each returned status/prediction/capture is fsynced
    before invoking the next case. Exceptions become error rows with only their
    type recorded; cancellation propagates after saving failed state. There is
    deliberately no score callback: subsequent diagnostic scoring reads saved
    answers separately. Do not pass notebook_runner.predictions as generate;
    that function performs retrieval and does not consume this context.
    Default case scope comes from the source run's explicit selected case IDs,
    falling back to its whole product partition only if none were recorded.
    """
    source_run, run_dir = Path(source_run).resolve(), Path(run_dir).resolve()
    if run_dir.is_relative_to(source_run) or source_run.is_relative_to(run_dir):
        raise ValueError('Oracle output must be isolated from the source run')
    reject_shared_output(run_dir, source=source_run)
    if not callable(generate):
        raise ValueError('Explicit generation-only callable required')
    product = json.loads((source_run / 'product-bundle.json').read_text(encoding='utf-8'))
    source_manifest = None
    if (source_run / 'manifest.json').exists():
        source_manifest = json.loads((source_run / 'manifest.json').read_text(encoding='utf-8'))
        if case_ids is None:
            case_ids = source_manifest.get('identity', {}).get('notebook_context', {}).get('case_ids')
    prepared = prepare_oracle_inputs(product, gold_evidence, case_ids=case_ids)
    run_dir.mkdir(parents=True, exist_ok=False)
    save_json(run_dir / 'manifest.json', dict(format=RUN_FORMAT, run_id=run_dir.name,
              kind=KIND, track='oracle', diagnostic=True, release_gate=False,
              source_run_id=source_manifest.get('run_id', source_run.name) if source_manifest else source_run.name,
              source_manifest_id=fingerprint(source_manifest) if source_manifest else None,
              source_product_id=prepared['source_product_id'],
              oracle_inputs_id=fingerprint(prepared), identity={'diagnostic': True, 'kind': KIND}))
    save_json(run_dir / 'oracle-inputs.json', prepared)
    save_jsonl(run_dir / 'planned.jsonl', [dict(case_id=r['case_id'], diagnostic=True) for r in prepared['requests']])
    state = dict(phase='running', diagnostic=True, planned=len(prepared['requests']), completed=0)
    save_json(run_dir / 'state.json', state)
    try:
        with EventJournal(run_dir / 'outputs.jsonl') as outputs:
            for request in prepared['requests']:
                try:
                    output = generate(deepcopy(request))
                    if (not isinstance(output, dict) or output.get('status') not in OUTPUT_STATUSES
                            or not isinstance(output.get('prediction'), str)
                            or output.get('case_id', request['case_id']) != request['case_id']
                            or output['status'] == 'success' and not output['prediction'].strip()):
                        raise ValueError('Invalid oracle generation output')
                    validate_public_json(output)
                except Exception as exc:
                    output = dict(status='error', prediction='', error_type=type(exc).__name__,
                                  reason='oracle generation failed')
                outputs(dict(output, case_id=request['case_id'], diagnostic=True, kind=KIND))
                state['completed'] += 1
                save_json(run_dir / 'state.json', state)
    except BaseException:
        save_json(run_dir / 'state.json', dict(state, phase='failed'))
        raise
    state['phase'] = 'finished'
    save_json(run_dir / 'state.json', state)
    return state


def read_oracle_run(run_dir):
    """Read-only dispatch seam for manifests with format=RUN_FORMAT.

    Returns diagnostic role, saved inputs/plan/state/outputs and case coverage.
    Intended context stays in inputs; outputs contain only observed generator
    returns. Absent answers remain null and are pending only while state says
    running/pending, otherwise missing. No ordinary run reconstruction or
    official score admission occurs. Invalid markers, input hashes, case IDs
    or duplicate outputs raise ValueError; absent files raise OSError.
    """
    run_dir = Path(run_dir).resolve()
    manifest = json.loads((run_dir / 'manifest.json').read_text(encoding='utf-8'))
    if (manifest.get('format') != RUN_FORMAT or manifest.get('kind') != KIND
            or manifest.get('diagnostic') is not True):
        raise ValueError('Explicit diagnostic/oracle manifest required')
    inputs = json.loads((run_dir / 'oracle-inputs.json').read_text(encoding='utf-8'))
    if (inputs.get('format') != FORMAT or inputs.get('kind') != KIND
            or inputs.get('diagnostic') is not True or fingerprint(inputs) != manifest.get('oracle_inputs_id')):
        raise ValueError('Oracle inputs differ from diagnostic manifest')
    planned = [json.loads(line) for line in (run_dir / 'planned.jsonl').read_text(encoding='utf-8').splitlines()]
    ids = [r['case_id'] for r in inputs['requests']]
    if (len(set(ids)) != len(ids) or any(not isinstance(p, dict) for p in planned)
            or [p.get('case_id') for p in planned] != ids):
        raise ValueError('Oracle plan differs from diagnostic inputs')
    outputs_path = run_dir / 'outputs.jsonl'
    outputs = ([json.loads(line) for line in outputs_path.read_text(encoding='utf-8').splitlines()]
               if outputs_path.exists() else [])
    by_id = {}
    for output in outputs:
        if (not isinstance(output, dict) or output.get('case_id') not in ids
                or output['case_id'] in by_id or output.get('diagnostic') is not True
                or output.get('kind') != KIND or output.get('status') not in OUTPUT_STATUSES
                or not isinstance(output.get('prediction'), str)):
            raise ValueError('Invalid or duplicate diagnostic output')
        by_id[output['case_id']] = output
    state = json.loads((run_dir / 'state.json').read_text(encoding='utf-8'))
    pending = state.get('phase') in {'running', 'pending', 'initialized'}
    cases = [dict(case_id=cid, status=by_id[cid]['status'], prediction=by_id[cid]['prediction']) if cid in by_id
             else dict(case_id=cid, status='pending' if pending else 'missing', prediction=None) for cid in ids]
    return dict(kind=KIND, diagnostic=True, metric_role='diagnostic', manifest=manifest,
                state=state, inputs=inputs, planned=planned, outputs=outputs, cases=cases)
