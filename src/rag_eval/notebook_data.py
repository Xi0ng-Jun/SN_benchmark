"""Local official-file adapters. Gold is never serialized into SN materials."""
from __future__ import annotations

from copy import deepcopy

from .starter_protocol import fingerprint, require_text

VERSION = 'sn-notebook-benchmarks-v1'
SUITES = {name: {'product': True} for name in ('qasper', 'multihop_rag', 'alce', 'qmsum')}


def _text(value, name):
    require_text(value, name)
    return value


def _list(value, name, *, nonempty=True):
    if not isinstance(value, list) or (nonempty and not value):
        raise ValueError(name + ' must be a list' + (' with members' if nonempty else ''))
    return value


def _document(suite, key, title, text):
    _text(text, 'document text')
    doc = {'id': suite + '-doc-' + fingerprint(key), 'title': title, 'text': text}
    from hashlib import sha256
    return {**doc, 'text_sha256': sha256(text.encode()).hexdigest(), 'document_sha256': fingerprint(doc)}


def _case(suite, sample, task, question, docs, references, gold, gold_ids, group):
    _text(question, 'question')
    for reference in references:
        _text(reference, 'reference answer')
    if not references and not (suite == 'alce' and task == 'eli5'):
        raise ValueError('Reference answers required')
    return dict(protocol_version=VERSION, product_protocol=VERSION, suite=suite, task=task,
                sample_id=str(sample), case_id=suite + ':' + str(sample), question=question,
                references=list(dict.fromkeys(references)), gold=gold,
                material_document_ids=list(dict.fromkeys(docs)), gold_document_ids=list(dict.fromkeys(gold_ids)),
                group_id=suite + '-group-' + fingerprint(group), material_role='source_documents',
                product_review={'status': 'applicable', 'reason': 'official text data adaptation; no manual approval gate'})


def _qasper(raw):
    if not isinstance(raw, dict) or not raw:
        raise ValueError('QASPER requires the official paper-ID JSON object')
    cases, documents, decisions = [], [], []
    for paper_id, paper in raw.items():
        paragraphs, pieces = [], [_text(paper['title'], 'paper title')]
        abstract = paper.get('abstract', '')
        if abstract:
            _text(abstract, 'abstract')
            pieces += ['Abstract', abstract]
            paragraphs.append({'id': 'abstract', 'text': abstract})
        for section_no, section in enumerate(_list(paper['full_text'], 'full_text')):
            heading = section.get('section_name') or ''
            if heading:
                pieces.append(_text(heading, 'section_name'))
            for number, paragraph in enumerate(_list(section['paragraphs'], 'paragraphs', nonempty=False)):
                if not isinstance(paragraph, str):
                    raise ValueError('paragraph must be text')
                if paragraph.strip():
                    paragraphs.append({'id': f'{section_no}:{number}', 'text': paragraph})
                    pieces.append(paragraph)
        if not paragraphs:
            raise ValueError('Paper has no textual paragraphs')
        doc = _document('qasper', paper_id, paper['title'], '\n\n'.join(pieces))
        documents.append(doc)
        for qa in _list(paper['qas'], 'qas'):
            sample = _text(qa['question_id'], 'question_id')
            question = _text(qa['question'], 'question')
            annotations, exclusion = [], None
            for entry in _list(qa['answers'], 'answers'):
                answer = entry['answer']
                evidence = _list(answer.get('evidence', []), 'evidence', nonempty=False)
                if any(not isinstance(e, str) for e in evidence):
                    raise ValueError('evidence must contain text')
                if any('FLOAT SELECTED' in e for e in evidence):
                    exclusion = 'figure_or_table_evidence_outside_text_scope'
                if type(answer['unanswerable']) is not bool:
                    raise ValueError('unanswerable must be boolean')
                if answer['unanswerable']:
                    value, kind, evidence = 'Unanswerable', 'none', []
                elif answer.get('extractive_spans'):
                    spans = _list(answer['extractive_spans'], 'extractive_spans')
                    value, kind = ', '.join(_text(s, 'span') for s in spans), 'extractive'
                elif answer.get('free_form_answer'):
                    value, kind = _text(answer['free_form_answer'], 'free_form_answer'), 'abstractive'
                elif type(answer.get('yes_no')) is bool:
                    value, kind = ('Yes' if answer['yes_no'] else 'No'), 'boolean'
                else:
                    raise ValueError('QASPER annotation has no answer')
                if any(e not in {p['text'] for p in paragraphs} for e in evidence):
                    exclusion = exclusion or 'evidence_not_mappable_to_imported_text'
                annotations.append(dict(answer=value, type=kind, evidence=evidence))
            decision = dict(case_id='qasper:' + sample, sample_id=sample,
                            status='excluded' if exclusion else 'selected', reason=exclusion or 'text evidence available')
            decisions.append(decision)
            if exclusion:
                continue
            kinds = {a['type'] for a in annotations}
            cases.append(_case('qasper', sample, next(iter(kinds)) if len(kinds) == 1 else 'mixed',
                               question, [doc['id']], [a['answer'] for a in annotations],
                               dict(annotations=annotations, paragraphs=paragraphs),
                               [doc['id']] if any(a['evidence'] for a in annotations) else [], paper_id))
    return cases, documents, decisions


def _multihop(raw, corpus):
    docs, urls, titles = [], {}, {}
    for row in _list(corpus, 'corpus'):
        url, title = _text(row['url'], 'url'), _text(row['title'], 'title')
        if url in urls:
            raise ValueError('Duplicate corpus URL')
        doc = _document('multihop_rag', url, title, _text(row['body'], 'body'))
        docs.append(doc)
        urls[url] = doc
        titles.setdefault(title, []).append(doc)
    cases = []
    for index, row in enumerate(_list(raw, 'queries')):
        kind = row['question_type']
        if kind not in {'inference_query', 'comparison_query', 'temporal_query', 'null_query'}:
            raise ValueError('Unsupported MultiHop question_type')
        evidence = []
        for entry in _list(row['evidence_list'], 'evidence_list', nonempty=False):
            if entry.get('url'):
                matches = [urls[entry['url']]] if entry['url'] in urls else []
            else:
                matches = titles.get(entry.get('title'), [])
            if len(matches) != 1:
                raise ValueError('MultiHop evidence must resolve to exactly one corpus document')
            doc = matches[0]
            if entry.get('title') and doc['title'] != entry['title']:
                raise ValueError('MultiHop evidence URL/title mismatch')
            fact = _text(entry['fact'], 'evidence fact')
            if ''.join(fact.split()) not in ''.join(doc['text'].split()):
                raise ValueError('MultiHop evidence fact is absent from its corpus document')
            evidence.append(dict(document_id=doc['id'], fact=fact))
        if kind != 'null_query' and not evidence:
            raise ValueError('Answerable MultiHop query has no evidence')
        answer = _text(row['answer'], 'answer')
        cases.append(_case('multihop_rag', str(index), kind, row['query'], [d['id'] for d in docs],
                           [answer], dict(answer=answer, evidence=evidence, question_type=kind),
                           [e['document_id'] for e in evidence], 'full-corpus'))
    return cases, docs, []


def _alce(raw, task):
    if task not in {'asqa', 'qampari', 'eli5'}:
        raise ValueError('ALCE requires explicit asqa/qampari/eli5 task')
    rows = raw.get('data') if isinstance(raw, dict) else raw
    cases, documents = [], {}
    for index, row in enumerate(_list(rows, 'ALCE rows')):
        candidates = []
        for entry in _list(row['docs'], 'candidate docs'):
            title = entry.get('title', '')
            if not isinstance(title, str):
                raise ValueError('candidate title must be text')
            text = _text(entry['text'], 'candidate text')
            doc = _document('alce', [title, text], title, text)
            documents.setdefault(doc['id'], doc)
            candidates.append(doc)
        gold = {key: deepcopy(row[key]) for key in ('answer', 'annotations', 'qa_pairs', 'answers', 'claims') if key in row}
        if task == 'asqa':
            refs = [a for pair in _list(gold.get('qa_pairs'), 'qa_pairs')
                    for a in _list(pair.get('short_answers'), 'short_answers')]
        elif task == 'qampari':
            refs = [a for aliases in _list(gold.get('answers'), 'answers') for a in _list(aliases, 'aliases')]
        else:
            refs = [_text(c, 'claim') for c in _list(gold.get('claims'), 'claims')]
        ids = [d['id'] for d in candidates]
        case = _case('alce', str(index), task, row['question'], ids, refs, gold, [], [task, ids])
        case['candidate_documents'] = candidates
        cases.append(case)
    return cases, list(documents.values()), []


def _qmsum(raw):
    cases, docs = [], []
    for meeting_no, meeting in enumerate(_list(raw, 'meetings')):
        turns, lines = [], []
        for index, turn in enumerate(_list(meeting['meeting_transcripts'], 'meeting_transcripts')):
            speaker = _text(turn['speaker'], 'speaker')
            content = _text(turn['content'], 'turn content')
            turns.append(dict(id=index, speaker=speaker, content=content))
            lines.append(f'[turn {index}] {speaker}: {content}')
        doc = _document('qmsum', str(meeting_no), f'Meeting {meeting_no}', '\n\n'.join(lines))
        docs.append(doc)
        for kind in ('general', 'specific'):
            for index, query in enumerate(_list(meeting[kind + '_query_list'], kind + ' queries', nonempty=False)):
                spans = []
                for span in _list(query.get('relevant_text_span', []), 'spans', nonempty=False):
                    if not isinstance(span, list) or len(span) != 2 or any(type(v) not in (str, int) for v in span):
                        raise ValueError('Invalid meeting span')
                    try:
                        start, end = map(int, span)
                    except ValueError:
                        raise ValueError('Invalid meeting span') from None
                    if not 0 <= start <= end < len(turns):
                        raise ValueError('Meeting span outside transcript')
                    spans.append([start, end])
                if kind == 'specific' and not spans:
                    raise ValueError('Specific query requires relevant_text_span')
                answer = _text(query['answer'], 'summary answer')
                cases.append(_case('qmsum', f'{meeting_no}:{kind}:{index}', kind, query['query'], [doc['id']],
                                   [answer], dict(answer=answer, relevant_text_span=spans if kind == 'specific' else [], turns=turns),
                                   [doc['id']] if kind == 'specific' else [], str(meeting_no)))
    return cases, docs, []


def adapt(suite, raw, *, corpus=None, task=None):
    if suite not in SUITES:
        raise ValueError('Unknown notebook benchmark')
    if suite != 'multihop_rag' and corpus is not None:
        raise ValueError('Separate corpus applies only to MultiHop-RAG')
    if suite != 'alce' and task is not None:
        raise ValueError('Explicit task applies only to ALCE')
    if suite == 'qasper':
        cases, docs, decisions = _qasper(raw)
    elif suite == 'multihop_rag':
        cases, docs, decisions = _multihop(raw, corpus)
    elif suite == 'alce':
        cases, docs, decisions = _alce(raw, task)
    else:
        cases, docs, decisions = _qmsum(raw)
    if not decisions:
        decisions = [dict(case_id=c['case_id'], sample_id=c['sample_id'], status='selected',
                          reason='official text data adaptation') for c in cases]
    ids = [d['case_id'] for d in decisions]
    if len(ids) != len(set(ids)):
        raise ValueError('Duplicate question ID')
    return dict(cases=cases, documents=docs, decisions=decisions)
