"""Frozen QASPER inputs and output conversion for the author's LAB citation method."""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json
from .notebook_bundle import load_bundle
from .starter_protocol import fingerprint

REVISION = '6c663ed61521dd0032ca41af3dc503e7c2e13dac'
TEST_SHA256 = '0b5d84987791e9da68aa96605407cc887d777407d25a6a06ebd464600471ab66'
TRAIN_SHA256 = '94cbc6cae8996ee7cda609a8ede286c5234610d7bb7b88b40dc75e358b03fab8'
EXAMPLE_INDICES = (11, 0, 17)


def public_document(document):
    result = deepcopy(document)
    result['meta'] = {k: document['meta'][k] for k in ('arxiv_id', 'ix_counter')}
    for node in result['nodes'] + result['span_nodes']:
        node['meta'] = {}
    for edge in result['edges']:
        edge['meta'] = None
    return result


def evidence_text_map(document, official_paper):
    """Use full ordered equality, not ambiguous text lookup, to recover whitespace."""
    texts = [official_paper['abstract']] + [p for s in official_paper['full_text'] for p in s['paragraphs']]
    paragraphs = [n for n in document['nodes'] if n['ntype'] == 'p']
    captions = [n for n in document['nodes'] if n['ntype'] == 'figure-or-table']
    caption_texts = [f['caption'] for f in official_paper['figures_and_tables']]
    if ([p['content'] for p in paragraphs] != [t.strip() for t in texts]
            or [p['content'] for p in captions] != [t.strip() for t in caption_texts]):
        raise ValueError('LAB nodes differ from ordered official QASPER paragraphs')
    mapping = {n['ix']: '\x00lab-nonparagraph:' + n['ix'] for n in document['nodes']}
    if len(mapping) != len(document['nodes']):
        raise ValueError('Duplicate LAB node identity')
    mapping.update({n['ix']: text for n, text in zip(paragraphs, texts)})
    mapping.update({n['ix']: text for n, text in zip(captions, caption_texts)})
    return mapping


def instance_json(document, qid, question):
    return dict(task_name='qasper', example_id=qid, document=json.dumps(document, ensure_ascii=False),
                prompt=question, question=question, statement='', extraction_level='paragraph',
                extraction_candidates=[n['ix'] for n in document['nodes'] if n['ntype'] == 'p'],
                free_text_answer=[], answer_type=[], extraction_nodes=[], answer_has_multiple_statements=False)


def _training_examples(documents, test_question_ids):
    flattened = [(d, q, a) for d in documents for q in d['meta']['qas'] for a in q['answers']]
    examples, identities = [], []
    for index in EXAMPLE_INDICES:
        doc, question, annotation = flattened[index]
        if question['question_id'] in test_question_ids:
            raise ValueError('LAB training example overlaps test question')
        answer = annotation['answer']
        if answer['unanswerable']:
            text, kind = 'Unanswerable', 'unanswerable'
        elif answer['extractive_spans']:
            text, kind = ', '.join(answer['extractive_spans']), 'extractive'
        elif answer['free_form_answer']:
            text, kind = answer['free_form_answer'], 'abstractive'
        elif answer['yes_no'] is not None:
            text, kind = ('Yes' if answer['yes_no'] else 'No'), 'boolean'
        else:
            raise ValueError('Invalid training annotation')
        evidence = [n['ix'] for n in doc['nodes'] if n['ntype'] == 'p' and any(
            e['annotation_id'] == annotation['annotation_id'] for e in n['meta']['is_evidence_for'])]
        instance = instance_json(public_document(doc), question['question_id'], question['question'])
        instance.update(free_text_answer=[text], answer_type=[kind], extraction_nodes=[evidence])
        examples.append(instance)
        identities.append(dict(original_train_index=index, question_id=question['question_id'],
                               annotation_id=annotation['annotation_id']))
    return examples, identities


def prediction_row(prediction, qid, case_id, node_texts):
    if prediction.get('task_name') != 'qasper' or prediction.get('example_id') != qid:
        raise ValueError('LAB prediction question identity mismatch')
    answer, nodes = prediction.get('free_text_answer'), prediction.get('extraction_nodes')
    if not isinstance(answer, str) or not isinstance(prediction.get('raw_generation'), str):
        raise ValueError('LAB prediction text is invalid')
    if not isinstance(nodes, list) or any(not isinstance(n, str) or n not in node_texts for n in nodes):
        raise ValueError('LAB prediction has an unknown evidence node')
    return dict(case_id=case_id, status='success' if answer.strip() else 'no_answer', prediction=answer,
                record=dict(raw_generation=prediction['raw_generation'], lab_node_ids=nodes,
                            parsed_abstention=answer.strip().lower() == 'unanswerable',
                            predicted_evidence=[node_texts[n] for n in nodes]))


def prepare_lab(bundle_dir, upstream, test_file, train_file, output, *, case_ids=None):
    output, upstream = Path(output).resolve(), Path(upstream).resolve()
    for path in (Path(bundle_dir).resolve(), upstream, Path(test_file).resolve().parent,
                 Path(train_file).resolve().parent):
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('LAB output must be outside source inputs')
    for path, expected in ((test_file, TEST_SHA256), (train_file, TRAIN_SHA256)):
        if digest(path) != expected:
            raise ValueError('LAB ITG data hash mismatch')
    lock = json.loads((Path(__file__).resolve().parents[2] / 'configs/qasper-lab-source-lock.json').read_text())
    if lock['revision'] != REVISION:
        raise ValueError('Unexpected LAB source revision')
    for name, expected in lock['files'].items():
        if digest(upstream / name) != expected:
            raise ValueError('LAB code/config hash mismatch: ' + name)
    bundle = load_bundle(bundle_dir)
    if bundle['manifest']['suite'] != 'qasper' or bundle['manifest']['adaptation_revision'] != 'notebook-data-v3':
        raise ValueError('LAB citation comparison requires QASPER v3')
    raw = json.loads((Path(bundle_dir) / 'raw-data').read_text())
    test = [json.loads(line) for line in Path(test_file).read_text().splitlines()]
    train = [json.loads(line) for line in Path(train_file).read_text().splitlines()]
    if len(test) != len(raw) or {d['prefix'] for d in test} != set(raw):
        raise ValueError('LAB test papers differ from frozen QASPER')
    cases = {c['sample_id']: c for c in bundle['cases']}
    if case_ids is not None and (not case_ids or len(set(case_ids)) != len(case_ids)
                                or set(case_ids) - {c['case_id'] for c in cases.values()}):
        raise ValueError('Invalid LAB case scope')
    documents, evidence_maps, questions = {}, {}, []
    observed = set()
    for doc in test:
        paper_id = doc['prefix']
        if doc['meta']['qas'] != raw[paper_id]['qas']:
            raise ValueError('LAB questions/annotations differ from frozen QASPER')
        mapping = evidence_text_map(doc, raw[paper_id])
        documents[paper_id] = public_document(doc)
        evidence_maps[paper_id] = mapping
        for question in doc['meta']['qas']:
            qid = question['question_id']
            if qid in observed or qid not in cases or cases[qid]['question'] != question['question']:
                raise ValueError('LAB question mapping is not unique/exact')
            observed.add(qid)
            if case_ids is None or cases[qid]['case_id'] in case_ids:
                questions.append(dict(question_id=qid, question=question['question'],
                                      paper_id=paper_id, case_id=cases[qid]['case_id']))
    if observed != set(cases):
        raise ValueError('LAB test coverage differs from bundle')
    examples, example_ids = _training_examples(train, observed)
    output.mkdir(parents=True, exist_ok=False)
    for name in lock['files']:
        target = output / 'author-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(upstream / name, target)
    payloads = {'public-documents.json': documents, 'questions.json': questions,
                'evidence-map.json': evidence_maps, 'training-examples.json': examples}
    for name, payload in payloads.items():
        save_json(output / name, payload)
    manifest = dict(format='lab-qasper-citation-v1', state='prepared', bundle_id=fingerprint(bundle['manifest']),
                    source_lock=lock, source_data_sha256={'test': TEST_SHA256, 'train': TRAIN_SHA256},
                    implementation_sha256=digest(Path(__file__)), training_examples=example_ids,
                    case_ids=[q['case_id'] for q in questions], public_papers=len(documents),
                    files={name: digest(output / name) for name in payloads},
                    differences=['test labels removed; training examples retain their own labels',
                                 'author LongChat citation config; explicit HF single-case execution',
                                 'original QASPER official scorer, not LAB metrics or published paper totals',
                                 'author prompt includes title/abstract/headings/paragraphs but omits figure captions',
                                 'ordered ITG paragraph map restores original whitespace for official evidence scoring'])
    save_json(output / 'manifest.json', manifest)
    return bundle, manifest
