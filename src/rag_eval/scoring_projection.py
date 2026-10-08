"""Suite-specific official scoring records, separate from full run observations."""
from __future__ import annotations

from copy import deepcopy
import json

PROTOCOL = 'sn-official-scoring-projection-v1'
GOLD_FIELDS = {'gold', 'gold_document_ids', 'expected_answer', 'references'}
CREDENTIAL_FIELDS = {'api_key', 'apikey', 'authorization', 'access_token', 'password', 'base_url'}
COMMON_FIELDS = {'status', 'answer', 'reason', 'error', 'error_phase', 'error_type', 'error_support_id'}
SUITE_FIELDS = {
    'qasper': {'response', 'captures', 'qasper_evidence', 'predicted_evidence'},
    'hotpotqa': {'response', 'captures', 'hotpot_evidence', 'predicted_supporting_facts'},
    'multihop_rag': {'retrieval'},
    'alce': {'source_to_document', 'anchor_documents', 'mapping_errors', 'mapping_status',
             'anchor_mapping_errors', 'anchor_mapping_status'},
    'qmsum': set(),
}


def validate_public_json(value):
    try:
        json.dumps(value, allow_nan=False)
    except (TypeError, ValueError) as exc:
        raise ValueError('Submission must contain finite JSON values') from exc
    pending = [value]
    while pending:
        current = pending.pop()
        if isinstance(current, dict):
            for key, item in current.items():
                if not isinstance(key, str):
                    raise ValueError('Submission JSON object keys must be strings')
                if key.lower() in CREDENTIAL_FIELDS:
                    raise ValueError('Submission must not contain credentials or endpoint addresses')
                pending.append(item)
        elif isinstance(current, (list, tuple)):
            pending.extend(current)


def validate_record_labels(record):
    if not isinstance(record, dict):
        raise ValueError('Generation record must be a JSON object')
    if GOLD_FIELDS & record.keys():
        raise ValueError('Generation record contains gold scoring labels; use request-v3')


def project_record(suite, record):
    if suite not in SUITE_FIELDS:
        raise ValueError('Unsupported scoring projection suite: ' + str(suite))
    validate_public_json(record)
    validate_record_labels(record)
    fields = COMMON_FIELDS | SUITE_FIELDS[suite]
    selected = {key: item for key, item in record.items() if key in fields}
    if suite == 'alce' and 'response' in record:
        response = record['response']
        if not isinstance(response, dict):
            raise ValueError('Observed ALCE response must be a JSON object')
        selected['response'] = {key: item for key, item in response.items() if key == 'anchors'}
    return deepcopy(selected)
