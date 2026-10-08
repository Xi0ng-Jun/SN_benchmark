"""Project final SN citations to Hotpot sentence positions, without gold labels.

Reuse the existing citation/SQLite/source-span boundary. A cited chunk predicts
all visible original sentences, including false positives; retrieval context
alone never predicts support. Snapshots make official scoring database-free.
"""
from __future__ import annotations

from pathlib import Path
import re

from .artifacts import digest
from .identity import fingerprint
from . import qasper_evidence as source

POLICY = 'sn-hotpot-final-citations-visible-sentences-v1'
MappingError = source.MappingError


def identity():
    return dict(policy=POLICY, implementation_sha256=digest(Path(__file__)),
                source_projection=source.identity())


def public_catalogues(documents):
    result = {}
    if len({d['id'] for d in documents}) != len(documents) or len({d['title'] for d in documents}) != len(documents):
        raise MappingError('ambiguous_public_documents')
    for document in documents:
        position, units = len(document['title']), []
        for index, unit in enumerate(document['source_units']):
            if unit['id'] != f'sentence:{index}' or unit['sent_id'] != index or unit['kind'] != 'sentence':
                raise MappingError('invalid_public_sentence_order')
            position += 2
            start = position
            position += len(unit['text'])
            units.append(dict(unit, start=start, end=position))
        if '\n\n'.join([document['title'], *[u['text'] for u in units]]) != document['text']:
            raise MappingError('public_document_reconstruction_mismatch')
        result[document['id']] = dict(document_id=document['id'], title=document['title'],
                                      document_sha256=document['document_sha256'], units=units)
    return result


def _citation_units(binding, document, catalogue, snapshot):
    """Expand the observed source object using its exact displayed prefix."""
    obj, displayed = binding['object'], binding['displayed']
    if obj['source_id'] != snapshot['source_id'] or snapshot['text_sha256'] != document['text_sha256']:
        raise MappingError('snapshot_source_mismatch')
    oid = obj['object_id']
    if obj['object_type'] == 'chunk':
        chunk = snapshot['chunks'][oid]
        if chunk['source_id'] != snapshot['source_id']:
            raise MappingError('chunk_source_mismatch')
        elements = [snapshot['elements'][eid] for eid in chunk['element_ids']]
        body = '\n'.join(e['text'].strip() for e in elements)
        full = chunk['text']
        prefix = len(full) - len(body)
        if prefix < 0 or not full.endswith(body) or (prefix and not re.fullmatch(r'\[[^\n]*\] ', full[:prefix])):
            raise MappingError('chunk_not_original_element_sequence')
        length = source._visible_length(displayed, full, binding['followed_by_handle'])
    else:
        elements = [snapshot['elements'][oid]]
        full, prefix = elements[0]['text'], 0
        label = (f"[source-element][{obj.get('tier', 'personal')}] {obj.get('source_title', '')} · "
                 f"{obj.get('location_label') or obj.get('name', '')} — ")
        if not displayed.startswith(label):
            raise MappingError('unsupported_element_rendering')
        length = source._visible_length(displayed[len(label):], full, binding['followed_by_handle'])
    selected, cursor = set(), prefix
    # Empty/whitespace slots retain their IDs, but display no sentence content.
    units = [u for u in catalogue['units'] if u['text'].strip()]
    for element in elements:
        if element['source_id'] != snapshot['source_id']:
            raise MappingError('snapshot_element_source_mismatch')
        rendered = element['text'].strip() if obj['object_type'] == 'chunk' else element['text']
        visible = min(len(rendered), max(0, length - cursor))
        if visible:
            selected.update(u['sent_id'] for u in source._source_extent(
                {**element, 'text': rendered}, visible, document, units))
        cursor += len(rendered) + 1
    return sorted(selected), dict(key=binding['key'], document_id=document['id'], object_id=oid,
                                  visible_chars=length, object_chars=len(full))


def project(record, documents, catalogues, snapshot):
    by_id = {d['id']: d for d in documents}
    selected, invalid, audits = set(), [], []
    sources = {s['source_id']: did for did, s in snapshot.items()}
    if len(sources) != len(snapshot):
        raise MappingError('ambiguous_source_ownership')
    for binding in source._bindings(record):
        key = binding['key']
        if binding.get('invalid'):
            invalid.append(key)
            audits.append(binding)
            continue
        did = sources.get(binding['object']['source_id'])
        if did not in by_id:
            raise MappingError('reference_outside_question_context')
        sentence_ids, audit = _citation_units(binding, by_id[did], catalogues[did], snapshot[did])
        selected.update((did, i) for i in sentence_ids)
        audit['sentence_ids'] = sentence_ids
        if not sentence_ids:
            invalid.append(key)
            audit['invalid'] = 'no_public_sentence_content'
        audits.append(audit)
    facts = [[d['title'], u['sent_id']] for d in documents for u in catalogues[d['id']]['units']
             if (d['id'], u['sent_id']) in selected]
    prefix = '\0invalid-sn-support:'
    while any(d['title'].startswith(prefix) for d in documents):
        prefix += ':'
    facts += [[prefix + fingerprint(key), -1] for key in invalid]
    return dict(predicted_supporting_facts=facts, invalid_keys=invalid, citations=audits)


def capture_evidence(repo, record, documents, mapping):
    result = dict(**identity(), observation_id=fingerprint(source._observation(record)))
    try:
        catalogues = public_catalogues(documents)
        result['catalogue_id'] = fingerprint(catalogues)
        if record.get('status') not in {'success', 'no_answer', 'clarification'}:
            raise MappingError('generation_incomplete')
        bindings = source._bindings(record)
        by_source = {}
        for document in documents:
            imported = mapping.get(document['id'])
            if imported is None:
                raise MappingError('missing_import_mapping')
            sid = imported['source_id']
            if sid in by_source:
                raise MappingError('ambiguous_source_ownership')
            by_source[sid] = document
        # Only cited objects are read; unrelated retrieval candidates stay out.
        grouped = {}
        for binding in bindings:
            if 'object' in binding:
                sid = binding['object']['source_id']
                if sid not in by_source:
                    raise MappingError('reference_outside_question_context')
                grouped.setdefault(sid, []).append(binding)
        snapshot = {by_source[sid]['id']: source._snapshot(repo, group, by_source[sid], mapping)
                    for sid, group in grouped.items()}
        projection = project(record, documents, catalogues, snapshot)
        result.update(status='complete', snapshot=snapshot, snapshot_id=fingerprint(snapshot), projection=projection)
    except Exception as exc:
        result.update(status='error', reason=str(exc) if isinstance(exc, MappingError) else 'capture_dependency_failed',
                      error_type=type(exc).__name__)
    finally:
        if hasattr(repo, 'close_local'):
            try:
                repo.close_local()
            except Exception as exc:
                result = dict(**identity(), status='error', reason='capture_cleanup_failed', error_type=type(exc).__name__)
    return result


def verify_evidence(record, documents):
    saved = record.get('hotpot_evidence', {})
    if saved.get('policy') != POLICY or saved.get('status') != 'complete':
        raise MappingError('evidence_capture_not_complete')
    catalogues = public_catalogues(documents)
    if (saved.get('catalogue_id') != fingerprint(catalogues)
            or saved.get('observation_id') != fingerprint(source._observation(record))):
        raise MappingError('evidence_observation_changed')
    if saved.get('snapshot_id') != fingerprint(saved.get('snapshot')):
        raise MappingError('evidence_snapshot_changed')
    projected = project(record, documents, catalogues, saved['snapshot'])
    if projected != saved.get('projection'):
        raise MappingError('evidence_projection_changed')
    return projected['predicted_supporting_facts']


def validate_prediction(record, documents, policy, status, answer):
    """One replay contract for saved SN runs and official submissions."""
    if policy != identity():
        raise ValueError('Hotpot evidence policy identity differs from this projector')
    saved = record.get('hotpot_evidence', {})
    if {k: saved.get(k) for k in policy} != policy:
        raise ValueError('Hotpot evidence snapshot differs from method policy identity')
    if status != record.get('status') or (status == 'success' and answer != record.get('answer')):
        raise ValueError('Hotpot evidence answer differs from saved observation')
    facts = record.get('predicted_supporting_facts')
    if saved.get('status') == 'error':
        if facts is not None:
            raise ValueError('Hotpot mapping error cannot carry partial supporting facts')
        return None
    replayed = verify_evidence(record, documents)
    if facts != replayed:
        raise ValueError('Hotpot supporting facts differ from replayed projection')
    return facts
