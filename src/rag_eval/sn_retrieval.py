"""Observe SN's native chunk selection before synthesis, without reading gold.

The producer sequence is the ranking: MMR, query quotas, native token limits and
source-graph enrichment have already acted. It is not a relevance-score sort,
the whole recall pool, or a reconstruction from final context/citations.
Only chunk mode has this contract. No product code or model inputs are changed.
"""
from __future__ import annotations

from contextlib import contextmanager
from functools import wraps
from inspect import signature
import math
from pathlib import Path

from .artifacts import digest
from .identity import fingerprint

POLICY = 'sn-multihop-chunk-selected-passages-v1'
BOUNDARY = '_activate_selected_source_graph'
STAGE = 'chunk-native-selection-before-synthesis'


def identity():
    return dict(policy=POLICY, stage=STAGE, boundary=BOUNDARY,
                order='native producer sequence; no score sort or deduplication',
                text='complete observed RetrievedChunk.text before synthesis rendering',
                implementation_sha256=digest(Path(__file__)))


def _snapshot(repo, hits, documents, mapping):
    public = {d['id']: d for d in documents}
    owners = {}
    for did, imported in mapping.items():
        sid = imported['source_id']
        if sid in owners:
            raise ValueError('Ambiguous imported source ownership')
        owners[sid] = did
    snapshot = dict(ranked=[], chunks={}, documents={})
    connection = repo._connect()
    for rank, hit in enumerate(hits, 1):
        cid, sid = hit['chunk_id'], hit['source_id']
        did = owners.get(sid)
        if did not in public or cid not in mapping[did].get('chunk_ids', []):
            raise ValueError('Selected chunk is outside imported public materials')
        document = public[did]
        if mapping[did]['text_sha256'] != document['text_sha256']:
            raise ValueError('Imported source differs from frozen document')
        stored = connection.execute('SELECT source_id, text FROM chunks WHERE id=?', (cid,)).fetchall()
        if len(stored) != 1 or stored[0][0] != sid or stored[0][1] != hit['text']:
            raise ValueError('Selected passage differs from stored source chunk')
        chunk = dict(source_id=sid, text=hit['text'])
        if cid in snapshot['chunks'] and snapshot['chunks'][cid] != chunk:
            raise ValueError('Repeated chunk has contradictory content')
        snapshot['chunks'][cid] = chunk
        snapshot['documents'][did] = dict(source_id=sid, text_sha256=document['text_sha256'])
        snapshot['ranked'].append(dict(rank=rank, chunk_id=cid, document_id=did,
                                       **{k: hit[k] for k in ('score', 'relevance') if k in hit}))
    return snapshot


@contextmanager
def capture_chunk_ranking(repo, notebook, question, documents, mapping):
    """Wrap one isolated Ask instance and retain exactly one observed stage.

    Copy producer values synchronously; verify their SQLite/source ownership
    after Ask returns. Observation errors cannot erase a generated answer.
    Missing/failed/repeated stages are never interpreted as empty retrieval.
    """
    saved = dict(**identity(), case_id=question['case_id'], question=question['original_question'],
                 request_question=question['question'], mode='chunk',
                 status='pending', reason='selection_stage_not_observed', call_count=0)
    service = getattr(getattr(repo, '_runtime', None), 'ask_component', None)
    original = getattr(service, BOUNDARY, None)
    if not callable(original):
        saved['reason'] = 'product_capture_boundary_unavailable'
        yield saved
        return
    calls = []
    sig = signature(original)
    had_local = BOUNDARY in vars(service)

    @wraps(original)
    def observed(*args, **kwargs):
        call = dict(complete=False)
        calls.append(call)
        result = original(*args, **kwargs)
        try:
            bound = sig.bind(*args, **kwargs)
            if bound.arguments.get('notebook_id') != notebook:
                raise ValueError('Selection belongs to another notebook')
            if not isinstance(result, tuple) or len(result) != 2 or not isinstance(result[0], (list, tuple)):
                raise ValueError('Native selected-passages boundary changed')
            hits = []
            for chunk in result[0]:
                hit = {k: getattr(chunk, k) for k in ('chunk_id', 'source_id', 'text')}
                if (any(not isinstance(value, str) for value in hit.values())
                        or not hit['chunk_id'] or not hit['source_id']):
                    raise ValueError('Invalid native selected passage')
                for key in ('score', 'relevance'):
                    value = getattr(chunk, key, None)
                    if value is not None:
                        if type(value) not in (int, float) or not math.isfinite(value):
                            raise ValueError('Invalid native score observation')
                        hit[key] = value
                hits.append(hit)
            call.update(complete=True, hits=hits)
        except Exception as exc:
            call['error_type'] = type(exc).__name__
        return result

    setattr(service, BOUNDARY, observed)
    try:
        yield saved
    finally:
        if had_local:
            setattr(service, BOUNDARY, original)
        else:
            delattr(service, BOUNDARY)
        saved['call_count'] = len(calls)
        if len(calls) > 1:
            saved.update(status='error', reason='ambiguous_multiple_selection_stages')
        elif calls:
            if not calls[0]['complete']:
                saved.update(status='error', reason='selection_stage_failed_or_unreadable')
            else:
                try:
                    snapshot = _snapshot(repo, calls[0]['hits'], documents, mapping)
                    saved.update(status='complete', reason=None, snapshot=snapshot, snapshot_id=fingerprint(snapshot))
                    validate_capture(saved, question, documents, identity())
                except Exception as exc:
                    saved.pop('snapshot', None)
                    saved.pop('snapshot_id', None)
                    saved.update(status='error', reason='selection_snapshot_failed', error_type=type(exc).__name__)
                finally:
                    if hasattr(repo, 'close_local'):
                        try:
                            repo.close_local()
                        except Exception as exc:
                            saved.pop('snapshot', None)
                            saved.pop('snapshot_id', None)
                            saved.update(status='error', reason='selection_snapshot_cleanup_failed', error_type=type(exc).__name__)


def validate_capture(saved, question, documents, policy):
    """Replay an observed ranking using saved texts; no live product or labels.

    Return None for an explicitly unavailable observation, [] for an observed
    empty selection, or the complete native ordered passages otherwise.
    """
    if policy != identity() or not isinstance(saved, dict) or any(saved.get(k) != v for k, v in policy.items()):
        raise ValueError('SN retrieval observation differs from declared capture policy')
    if (saved.get('mode') != 'chunk' or saved.get('case_id') != question['case_id']
            or saved.get('question') != question['original_question']
            or saved.get('request_question') != question['question']
            or type(saved.get('call_count')) is not int or saved['call_count'] < 0):
        raise ValueError('SN retrieval observation has different mode or question identity')
    if saved.get('status') in {'pending', 'error'}:
        if 'snapshot' in saved or 'snapshot_id' in saved or not isinstance(saved.get('reason'), str) or not saved['reason']:
            raise ValueError('Unavailable SN retrieval cannot carry a partial ranking snapshot')
        return None
    snapshot = saved.get('snapshot')
    if (saved.get('status') != 'complete' or saved['call_count'] != 1 or saved.get('reason') is not None
            or not isinstance(snapshot, dict) or saved.get('snapshot_id') != fingerprint(snapshot)):
        raise ValueError('SN retrieval snapshot hash or completeness changed')
    ranked, chunks, bindings = snapshot.get('ranked'), snapshot.get('chunks'), snapshot.get('documents')
    if not isinstance(ranked, list) or not isinstance(chunks, dict) or not isinstance(bindings, dict):
        raise ValueError('Invalid SN retrieval snapshot')
    public = {d['id']: d for d in documents}
    sources = set()
    for did, binding in bindings.items():
        if (did not in public or not isinstance(binding, dict)
                or binding.get('text_sha256') != public[did]['text_sha256']
                or not isinstance(binding.get('source_id'), str) or not binding['source_id']
                or binding['source_id'] in sources):
            raise ValueError('SN retrieval source snapshot differs from frozen public documents')
        sources.add(binding['source_id'])
    passages, used_chunks, used_documents = [], set(), set()
    for rank, hit in enumerate(ranked, 1):
        if not isinstance(hit, dict) or type(hit.get('rank')) is not int or hit['rank'] != rank:
            raise ValueError('SN retrieval producer order changed')
        cid, did = hit.get('chunk_id'), hit.get('document_id')
        if not isinstance(cid, str) or not isinstance(did, str) or cid not in chunks or did not in bindings:
            raise ValueError('SN retrieval passage has no source snapshot')
        chunk = chunks[cid]
        if (not isinstance(chunk, dict) or chunk.get('source_id') != bindings[did]['source_id']
                or not isinstance(chunk.get('text'), str)):
            raise ValueError('SN retrieval passage snapshot is inconsistent')
        used_chunks.add(cid)
        used_documents.add(did)
        passages.append(dict(text=chunk['text']))
    if used_chunks != set(chunks) or used_documents != set(bindings):
        raise ValueError('SN retrieval snapshot contains unobserved chunks or documents')
    return passages
