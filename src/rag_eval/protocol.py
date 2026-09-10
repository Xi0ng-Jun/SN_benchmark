import re


def validate_result(row):
    if not isinstance(row, dict):
        raise ValueError('result must be an object')
    for field in ('question', 'answer'):
        if not isinstance(row.get(field), str) or not row[field].strip():
            raise ValueError(f'{field} must be nonempty text')
    context = row.get('retrieval_context')
    if not isinstance(context, list) or not all(isinstance(x, str) for x in context):
        raise ValueError('retrieval_context must be a string list')
    return row


def capture_context(sink):
    block, mapping = sink['context_block'], sink['id_map']
    # Only split at core-issued, line-leading handles, retaining delivered text.
    matches = [m for m in re.finditer(r'(?m)^(k\d+): ', block) if m[1] in mapping]
    contexts, ids, sources = [], [], []
    if matches and matches[0].start():
        contexts.append(block[:matches[0].start()])
    for n, match in enumerate(matches):
        stop = matches[n + 1].start() - 1 if n + 1 < len(matches) else len(block)
        contexts.append(block[match.start():stop])
        ids.append(mapping[match[1]].get('object_id', ''))
        sources.append(mapping[match[1]].get('source_id', ''))
    if not matches and block:
        contexts = [block]
    return dict(context_block=block, retrieval_context=contexts, retrieved_ids=ids,
                source_ids=sources, handles=[m[1] for m in matches])
