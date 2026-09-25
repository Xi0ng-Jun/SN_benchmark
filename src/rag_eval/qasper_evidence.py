"""Public-source projection of final SN citations onto original QASPER units.

Gold annotations never enter this module. Runtime capture is read-only; replay
needs only frozen public documents and the saved snapshot, not a live database.
Unknown model references are false positives. Missing/ambiguous observations
are mapping errors, never silently empty predictions or excluded questions.
"""
from __future__ import annotations

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import re
import sqlite3

from .starter_protocol import fingerprint


POLICY = 'sn-qasper-final-citations-visible-units-v1'
# Product citation_markers.MARKER_RE: grouped/Chinese markers are one citation
# surface. Repeated mentions are references to the same predicted object.
MARKERS = re.compile(r'(?:\[(?:k\d+\s*[,，]\s*)*k\d+\]|【(?:k\d+\s*[,，]\s*)*k\d+】)')


class MappingError(ValueError):
    """A stable content-free error code, distinct from a bad model reference."""


def identity():
    return dict(policy=POLICY, implementation_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())


def public_catalogues(raw, documents):
    """Reconstruct exact public source offsets, including repeated paragraphs.

    Does not inspect qas or annotations. The original adapter's bytes and public
    units must agree; this derived catalogue does not change the frozen bundle.
    """
    result = {}
    wanted = {d['id']: d for d in documents}
    for paper_id, paper in raw.items():
        did = 'qasper-doc-' + fingerprint(paper_id)
        if did not in wanted:
            continue
        pieces, units = [], []
        offset = 0

        def add(text, uid=None, kind=None):
            nonlocal offset
            if pieces:
                offset += 2
            start = offset
            pieces.append(text)
            offset += len(text)
            if uid is not None:
                units.append(dict(id=uid, kind=kind, text=text, start=start, end=offset))

        add(paper['title'])
        if paper.get('abstract'):
            add('Abstract')
            add(paper['abstract'], 'abstract', 'abstract')
        for i, section in enumerate(paper['full_text']):
            if section.get('section_name'):
                add(section['section_name'])
            for j, text in enumerate(section['paragraphs']):
                if text.strip():
                    add(text, f'{i}:{j}', 'paragraph')
        for i, figure in enumerate(paper.get('figures_and_tables', [])):
            if figure['caption'].strip():
                add(figure['caption'], f'figure_or_table:{i}', 'caption')
        doc = wanted[did]
        if '\n\n'.join(pieces) != doc['text']:
            raise MappingError('public_document_reconstruction_mismatch')
        public = [{k: u[k] for k in ('id', 'kind', 'text')} for u in units]
        if public != [u for u in doc['source_units'] if u['text'].strip()]:
            raise MappingError('public_units_mismatch')
        result[did] = dict(document_id=did, document_sha256=doc['document_sha256'],
                           paper_id=paper_id, units=units)
    if result.keys() != wanted.keys():
        raise MappingError('missing_public_paper')
    return result


def _keys(answer):
    return list(dict.fromkeys(key.strip() for m in MARKERS.finditer(answer)
                             for key in re.split('[,，]', m[0][1:-1])))


def _observation(record):
    return {k: deepcopy(record.get(k)) for k in ('status', 'answer', 'response', 'captures')}


def _captures(record):
    calls = record.get('captures')
    if not isinstance(calls, list):
        raise MappingError('missing_synthesis_capture')
    success = [c for c in calls if c.get('succeeded') and c.get('answer', '').strip()]
    if not success:
        raise MappingError('missing_synthesis_capture')
    if success[-1].get('sectioned'):
        # Section handles must be globally unique. Resolve each final reference
        # against its own successful section, never an unrelated earlier draft.
        selected = [c for c in success if c.get('sectioned')]
    else:
        selected = [success[-1]]
        if selected[0]['answer'].strip() != record['answer'].strip():
            raise MappingError('final_answer_differs_from_synthesis')
    return selected


def _bindings(record):
    """Bind final markers to observed producer keys before any SQL lookup."""
    keys = _keys(record['answer'])
    if not keys:
        return []
    calls = _captures(record)
    for call in calls:
        if not isinstance(call.get('id_map'), dict) or not isinstance(call.get('context_block'), str):
            raise MappingError('missing_synthesis_key_or_context_observation')
    anchors = {}
    for a in record.get('response', {}).get('anchors', []):
        if a.get('key') in anchors:
            raise MappingError('duplicate_final_anchor')
        anchors[a.get('key')] = a
    result = []
    for key in keys:
        matches = [c for c in calls if key in c.get('id_map', {})]
        if len(matches) > 1:
            raise MappingError('ambiguous_synthesis_key')
        if not matches:
            if key in anchors:
                raise MappingError('anchor_missing_from_capture')
            result.append(dict(key=key, invalid='unknown_model_reference'))
            continue
        call = matches[0]
        mapped = call['id_map'][key]
        if call.get('sectioned') and key not in _keys(call['answer']):
            raise MappingError('reference_not_selected_in_section')
        # parse_anchors drops an entire mixed group when any member is unknown.
        # Keep this observable invalid selection rather than inventing anchors.
        if key not in anchors:
            groups = [m[0] for m in MARKERS.finditer(record['answer'])
                      if key in [x.strip() for x in re.split('[,，]', m[0][1:-1])]]
            if all(any(x.strip() not in call['id_map'] for x in re.split('[,，]', g[1:-1])) for g in groups):
                result.append(dict(key=key, invalid='rejected_mixed_reference_group'))
                continue
            raise MappingError('missing_final_anchor')
        anchor = anchors[key]
        for field in ('object_id', 'object_type', 'source_id', 'element_id'):
            if anchor.get(field, '') != mapped.get(field, ''):
                raise MappingError('anchor_capture_identity_mismatch')
        if mapped.get('object_type') not in {'chunk', 'element'}:
            raise MappingError('unsupported_evidence_object_type')
        block = call.get('context_block')
        if not isinstance(block, str):
            raise MappingError('missing_context_block')
        boundaries = [m for m in re.finditer(r'(?m)^(k\d+): ', block) if m[1] in call['id_map']]
        # A source paragraph can contain fake line-leading handles. Do not let
        # an ambiguous split silently change what the model actually saw.
        found = [i for i, m in enumerate(boundaries) if m[1] == key]
        if len(found) != 1:
            raise MappingError('ambiguous_or_missing_context_handle')
        i = found[0]
        start = boundaries[i].end()
        end = boundaries[i + 1].start() - 1 if i + 1 < len(boundaries) else len(block)
        result.append(dict(key=key, object=deepcopy(mapped), displayed=block[start:end],
                           followed_by_handle=i + 1 < len(boundaries)))
    return result


def _row(connection, table, oid, fields):
    # Table/column names below are internal constants, never supplied by data.
    rows = connection.execute('SELECT ' + ','.join(fields) + ' FROM ' + table + ' WHERE id=?', (oid,)).fetchall()
    if len(rows) != 1:
        raise MappingError('missing_or_duplicate_' + table + '_object')
    return dict(zip(fields, rows[0]))


def _snapshot(repo, bindings, document, mapping):
    if document['id'] not in mapping:
        raise MappingError('missing_import_mapping')
    imported = mapping[document['id']]
    source_id = imported['source_id']
    expected = hashlib.sha256(document['text'].encode()).hexdigest()
    if imported.get('text_sha256') != expected:
        raise MappingError('imported_document_identity_mismatch')
    snapshot = dict(source_id=source_id, text_sha256=expected, chunks={}, elements={})
    bound = [b for b in bindings if 'object' in b]
    if not bound:
        return snapshot
    connection = repo._connect()
    source = _row(connection, 'sources', source_id, ['id', 'file_path'])
    if Path(source['file_path']).read_bytes() != document['text'].encode():
        raise MappingError('imported_source_changed')
    for binding in bound:
        obj = binding['object']
        if obj.get('source_id') != source_id:
            raise MappingError('reference_outside_public_paper')
        oid = obj['object_id']
        if obj['object_type'] == 'chunk':
            chunk = _row(connection, 'chunks', oid, ['id', 'source_id', 'text', 'section_path', 'element_ids'])
            if chunk['source_id'] != source_id:
                raise MappingError('chunk_source_mismatch')
            chunk['element_ids'] = json.loads(chunk['element_ids'])
            if not chunk['element_ids'] or len(set(chunk['element_ids'])) != len(chunk['element_ids']):
                raise MappingError('invalid_chunk_elements')
            snapshot['chunks'][oid] = chunk
            eids = chunk['element_ids']
        else:
            eids = [oid]
        for eid in eids:
            if eid not in snapshot['elements']:
                element = _row(connection, 'source_elements', eid, ['id', 'source_id', 'text', 'metadata'])
                if element['source_id'] != source_id:
                    raise MappingError('element_source_mismatch')
                metadata = json.loads(element.pop('metadata'))
                if metadata.get('description'):
                    raise MappingError('folded_image_description_needs_source_map')
                element.update(start=metadata.get('char_start'), end=metadata.get('char_end'))
                snapshot['elements'][eid] = element
    return snapshot


def _visible_length(displayed, full, followed_by_handle=False):
    """Accept exact visible prefix; never align a generated rewrite by similarity."""
    if displayed.startswith(full):
        trailing = displayed[len(full):]
        if not trailing or trailing.startswith('\n'):
            return len(full)
    if displayed.endswith('…') and full.startswith(displayed[:-1]):
        return len(displayed) - 1
    if full.startswith(displayed):
        return len(displayed)
    if followed_by_handle:
        # Partition headings belong to the following handle, not this clipped
        # object. Strip only the producer's known headings and blank separator.
        headings = ('Knowledge graph', 'Confirmed Memory', 'Derived chains', 'Retrieved chunks',
                    'Direct source elements', 'Exact-lookup passages', 'External evidence')
        suffix = re.search(r'\n+(?:\[(?:' + '|'.join(re.escape(h) for h in headings) + r')\]\n*)?$', displayed)
        if suffix:
            return _visible_length(displayed[:suffix.start()], full)
    raise MappingError('context_not_verbatim_prefix')


def _source_extent(element, visible, document, units):
    start, end = element['start'], element['end']
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= len(document['text']):
        raise MappingError('invalid_source_offsets')
    text = element['text']
    if not text or not 0 < visible <= len(text):
        return []
    raw = document['text'][start:end]
    overlapping = [u for u in units if u['start'] < end and u['end'] > start]
    if visible == len(text):
        # Full parsed element is visible; its source interval is its provenance.
        return overlapping
    # Whitespace folding has a deterministic character map back to raw bytes.
    # A partial Markdown transformation across several original paragraphs
    # needs an actual parser source map; never guess by fuzzy matching.
    tokens = list(re.finditer(r'\S+', raw))
    folded = ' '.join(m[0] for m in tokens)
    if text == folded:
        positions = []
        for i, token in enumerate(tokens):
            if i:
                positions.append(token.start() - 1)
            positions.extend(range(token.start(), token.end()))
        observed_end = start + positions[visible - 1] + 1
        return [u for u in overlapping if u['start'] < observed_end]
    if text in raw and raw.count(text) == 1:
        actual = start + raw.index(text)
        return [u for u in overlapping if u['start'] < actual + visible and u['end'] > actual]
    if len(overlapping) == 1:
        unit = overlapping[0]
        # Markdown block offsets include trailing blank lines. Accept only
        # whitespace outside this one original unit, never neighbouring prose.
        if not document['text'][start:unit['start']].strip() and not document['text'][unit['end']:end].strip():
            return overlapping
    if not overlapping:
        return []
    raise MappingError('partial_transformed_element_needs_source_map')


def project(record, document, catalogue, snapshot):
    if catalogue['document_id'] != document['id'] or catalogue['document_sha256'] != document['document_sha256']:
        raise MappingError('catalogue_document_mismatch')
    if snapshot['text_sha256'] != hashlib.sha256(document['text'].encode()).hexdigest():
        raise MappingError('snapshot_document_mismatch')
    units, selected, invalid, audits = catalogue['units'], set(), [], []
    for binding in _bindings(record):
        key = binding['key']
        if binding.get('invalid'):
            invalid.append(key)
            audits.append(binding)
            continue
        obj, displayed = binding['object'], binding['displayed']
        if obj['source_id'] != snapshot['source_id']:
            raise MappingError('snapshot_source_mismatch')
        oid = obj['object_id']
        if obj['object_type'] == 'chunk':
            chunk = snapshot['chunks'][oid]
            elements = [snapshot['elements'][eid] for eid in chunk['element_ids']]
            body = '\n'.join(e['text'].strip() for e in elements)
            full = chunk['text']
            prefix = len(full) - len(body)
            if prefix < 0 or not full.endswith(body) or (prefix and not re.fullmatch(r'\[[^\n]*\] ', full[:prefix])):
                raise MappingError('chunk_not_original_element_sequence')
            length = _visible_length(displayed, full, binding['followed_by_handle'])
        else:
            elements = [snapshot['elements'][oid]]
            full = elements[0]['text']
            prefix = 0
            label = (f"[source-element][{obj.get('tier', 'personal')}] {obj.get('source_title', '')} · "
                     f"{obj.get('location_label') or obj.get('name', '')} — ")
            if not displayed.startswith(label):
                raise MappingError('unsupported_element_rendering')
            length = _visible_length(displayed[len(label):], full, binding['followed_by_handle'])
        selected_here = set()
        cursor = prefix
        for element in elements:
            if element['source_id'] != snapshot['source_id']:
                raise MappingError('snapshot_element_source_mismatch')
            rendered = element['text'].strip() if obj['object_type'] == 'chunk' else element['text']
            visible = min(len(rendered), max(0, length - cursor))
            if visible:
                selected_here.update(u['id'] for u in _source_extent({**element, 'text': rendered}, visible, document, units))
            cursor += len(rendered) + 1
        selected.update(selected_here)
        if not selected_here:
            # A heading-only selection is a real but incorrect evidence choice.
            invalid.append(key)
        audits.append(dict(key=key, object_id=oid, visible_chars=length, object_chars=len(full),
                           unit_ids=[u['id'] for u in units if u['id'] in selected_here],
                           invalid='no_public_evidence_unit' if not selected_here else None))
    prefix = '\x00invalid-sn-evidence:'
    while any(u['text'].startswith(prefix) for u in units):
        prefix += ':'
    prediction = [u['text'] for u in units if u['id'] in selected]
    prediction += [prefix + fingerprint(dict(key=key, document_id=document['id'])) for key in invalid]
    return dict(predicted_evidence=prediction, unit_ids=[u['id'] for u in units if u['id'] in selected],
                invalid_keys=invalid, citations=audits)


def capture_evidence(repo, record, document, catalogue, mapping):
    """Freeze bounded evidence without changing the answer on capture failure."""
    result = dict(**identity(), catalogue_id=fingerprint(catalogue), observation_id=fingerprint(_observation(record)))
    try:
        if record.get('status') not in {'success', 'no_answer', 'clarification'}:
            raise MappingError('generation_incomplete')
        bindings = _bindings(record)
        snapshot = _snapshot(repo, bindings, document, mapping)
        projection = project(record, document, catalogue, snapshot)
        result.update(status='complete', snapshot=snapshot, projection=projection)
        result['snapshot_id'] = fingerprint(snapshot)
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


def verify_evidence(record, document, catalogue):
    """Recompute from immutable observations; never trust an edited evidence list."""
    saved = record.get('qasper_evidence', {})
    if saved.get('policy') != POLICY or saved.get('status') != 'complete':
        raise MappingError('evidence_capture_not_complete')
    if saved.get('catalogue_id') != fingerprint(catalogue) or saved.get('observation_id') != fingerprint(_observation(record)):
        raise MappingError('evidence_observation_changed')
    if saved.get('snapshot_id') != fingerprint(saved.get('snapshot')):
        raise MappingError('evidence_snapshot_changed')
    projected = project(record, document, catalogue, saved['snapshot'])
    if projected != saved.get('projection'):
        raise MappingError('evidence_projection_changed')
    return projected['predicted_evidence']


def recover_saved_evidence(run, record, document, catalogue):
    """Explicit old-run export only; never import SN or open SQLite for writes."""
    class ReadOnlyRepository:
        connection = None

        def _connect(self):
            if self.connection is None:
                uri = (Path(run) / 'runtime/database.db').resolve().as_uri() + '?mode=ro'
                self.connection = sqlite3.connect(uri, uri=True)
            return self.connection

        def close_local(self):
            if self.connection is not None:
                self.connection.close()

    try:
        mapping = json.loads((Path(run) / 'product-artifacts/document-map.json').read_text())
    except (OSError, ValueError):
        return dict(**identity(), status='error', reason='recovery_import_mapping_unavailable')
    return capture_evidence(ReadOnlyRepository(), record, document, catalogue, mapping)
