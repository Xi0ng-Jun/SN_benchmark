"""Read normalized public data files only; never import the old benchmark project."""
import json
from pathlib import Path


def iter_jsonl(path):
    with Path(path).open(encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError(f'invalid JSONL at line {number}') from None
            if not isinstance(value, dict):
                raise ValueError(f'expected object at line {number}')
            yield value


def load_jsonl(path):
    return list(iter_jsonl(path))


def unique_map(rows, key):
    result = {}
    for row in rows:
        identity = str(row[key])
        if identity in result:
            raise ValueError(f'duplicate {key}')
        result[identity] = row
    return result


def document_text(row):
    for field in ('text', 'contents'):
        if isinstance(row.get(field), str):
            return row[field]
    abstract = row.get('abstract', [])
    return '\n'.join(filter(None, [row.get('title', ''), *(abstract if isinstance(abstract, list) else [abstract])]))


def load_dataset(root):
    root = Path(root)
    questions = unique_map(iter_jsonl(root / 'questions.jsonl'), 'id')
    annotations = unique_map(iter_jsonl(root / 'annotations.jsonl'), 'question_id')
    if set(questions) != set(annotations):
        raise ValueError('question and annotation IDs differ')
    rows, required = [], set()
    for identity, q in questions.items():
        a = annotations[identity]
        relevance = {str(k): float(v) for k, v in a.get('relevance', {}).items() if float(v) > 0}
        if not relevance:
            relevance = dict.fromkeys(map(str, a.get('metadata', {}).get('evidence_document_ids', [])), 1.0)
        required.update(relevance)
        refs = a.get('references', [])
        if not isinstance(refs, list) or not all(isinstance(x, str) for x in refs):
            raise ValueError('references must be a string list')
        rows.append(dict(id=identity, question=q['question'], language=q.get('language'),
                         group=q.get('group', 'default'), expected_answer='\n'.join(refs), references=refs,
                         answerable=not a.get('unanswerable', False), gold_document_ids=list(relevance),
                         relevance=relevance, dataset=root.parent.name, gold_context=[]))
    texts, seen = {}, set()
    for d in iter_jsonl(root / 'documents.jsonl'):
        identity = str(d['id'])
        if identity in seen:
            raise ValueError('duplicate document id')
        seen.add(identity)
        if identity in required:
            texts[identity] = document_text(d)
    if required - seen:
        raise ValueError('gold document missing from corpus')
    for row in rows:
        row['gold_context'] = [texts[i] for i in row['gold_document_ids']]
    return rows
