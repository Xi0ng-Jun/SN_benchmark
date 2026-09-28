"""Import the two complete, fixed Multi-Meta-RAG answer files without inference.

Queries join the answer, retrieval and frozen case files independently of order.
The stored prompt must reconstruct exactly from the author's first six passages.
Source gold is checked as an observation, never supplied as a prediction.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil

from .artifacts import digest, save_json
from .external_submission_import import import_external_predictions
from .multihop_official import PUBLISHED_RANKING_CONTRACT
from .notebook_bundle import load_bundle
from .starter_protocol import fingerprint


REVISION = 'e77e4638cbae16fa7a63f291e73230d5bb356081'
REPOSITORY = 'https://github.com/mxpoliakov/Multi-Meta-RAG'
RETRIEVAL_PATH = 'output/voyage-02_256_32_with_filtering.json'
RETRIEVAL_SHA256 = '3ebd57ddf6e2ea29f61d45a8e25514bbf91ae74f8c95bf020bd539b9559774eb'
MODELS = {
    'gpt4': dict(path='qa_output/gpt-4-voyage-02-filtering.json',
                 sha256='f4ad29c607867e4ef73a3f9d5016559ee9bbcdb0331c3a05cb2948d0cf82812c',
                 model='gpt-4-0613', provider='OpenAI', script='qa_gpt.py',
                 max_output_tokens=None,
                 prefix='Below is a question followed by some context from different sources. '),
    'palm': dict(path='qa_output/google-palm-voyage-02-filtering.json',
                 sha256='732232e2b5fcb2d4af163b3cd9de14ee3f2815d6000e1034ff5702a6882dcea0',
                 model='text-bison@001', provider='Google Vertex AI', script='qa_google.py',
                 max_output_tokens=12,
                 prefix='You will be provided with questions followed by some context from different sources. '),
}
INSTRUCTION = ("Please answer the question based on the context. The answer to the question is a word or entity. "
               "If the provided information is insufficient to answer the question, respond 'Insufficient Information'. "
               "Answer directly without explanation.")


def _index(rows, field, label):
    indexed = {}
    for i, row in enumerate(rows):
        key = row.get(field)
        if not isinstance(key, str) or not key or key in indexed:
            raise ValueError(f'Missing or duplicate {label} query at row {i}')
        indexed[key] = (i, row)
    return indexed


def prepare_rows(bundle, answers, rankings, *, model, answer_sha256, retrieval_sha256):
    """Validate complete native files and return generic rows, explicit map and audit."""
    spec = MODELS[model]
    cases = _index(bundle['cases'], 'question', 'bundle')
    by_answer = _index(answers, 'query', 'answer')
    by_ranking = _index(rankings, 'query', 'retrieval')
    if not cases or set(cases) != set(by_answer) or set(cases) != set(by_ranking):
        raise ValueError('Multi-Meta complete answer/retrieval query scope differs from frozen bundle')
    predictions, mappings = [], []
    for source_row, answer in enumerate(answers):
        query = answer['query']
        case = cases[query][1]
        ranking_row, ranking = by_ranking[query]
        gold = case['gold']
        if (answer.get('gold_answer') != gold['answer'] or ranking.get('answer') != gold['answer']
                or answer.get('question_type') != gold['question_type']
                or ranking.get('question_type') != gold['question_type']
                or [x['fact'] for x in ranking['gold_list']] != [x['fact'] for x in gold['evidence']]):
            raise ValueError(f'Source scoring observations differ from frozen gold: {query}')
        hits = ranking['retrieval_list']
        if not isinstance(hits, list) or any(not isinstance(x.get('text'), str) for x in hits):
            raise ValueError(f'Invalid author ranking: {query}')
        context = '--------------'.join(x['text'] for x in hits[:6])
        expected = f"{spec['prefix']}{INSTRUCTION}\n\nQuestion:{query}\n\nContext:\n\n{context}"
        if answer.get('prompt') != expected:
            raise ValueError(f'Observed prompt differs from first-six author passages: {query}')
        if not isinstance(answer.get('model_answer'), str):
            raise ValueError(f'Author model_answer is not text: {query}')
        external_id = f'multimeta-{model}:{source_row}'
        predictions.append(dict(
            external_id=external_id, prediction=answer['model_answer'],
            status='success' if answer['model_answer'].strip() else 'no_answer',
            query=query, prompt=answer['prompt'],
            record={'native_source': dict(answer_sha256=answer_sha256, answer_row=source_row,
                                         retrieval_sha256=retrieval_sha256, retrieval_row=ranking_row)},
            retrieval=dict(stage=PUBLISHED_RANKING_CONTRACT, source_sha256=retrieval_sha256,
                           source_row=ranking_row, query=query, ranked=deepcopy(hits))))
        mappings.append(dict(external_id=external_id, case_id=case['case_id'], source_row=source_row,
                             decision='exact-unique-query; source gold and first-six prompt verified'))
    audit = dict(case_count=len(cases), answer_rows=len(answers), retrieval_rows=len(rankings),
                 empty_answers=sum(not row['prediction'].strip() for row in predictions),
                 prompt_checks=len(predictions), source_gold_checks=len(predictions),
                 corpus_identity='not proven by published answer/retrieval files',
                 empty_answer_policy='observed empty string becomes no_answer; retained in denominator; cause unknown',
                 scorer_policy='fixed upstream MultiHop scripts, not author evaluate_qa.py')
    return predictions, mappings, audit


def import_multimeta(bundle_dir: Path, source_dir: Path, output_dir: Path, *, model: str):
    bundle_dir, source_dir, output_dir = (Path(p).resolve() for p in (bundle_dir, source_dir, output_dir))
    for source in (bundle_dir, source_dir):
        if output_dir.is_relative_to(source) or source.is_relative_to(output_dir):
            raise ValueError('Import output must be outside bundle and source')
    spec = MODELS[model]
    answer_file, retrieval_file = source_dir / spec['path'], source_dir / RETRIEVAL_PATH
    for path, expected in ((answer_file, spec['sha256']), (retrieval_file, RETRIEVAL_SHA256)):
        if digest(path) != expected:
            raise ValueError(f'Fixed Multi-Meta source SHA256 mismatch: {path}')
    bundle = load_bundle(bundle_dir)
    if (bundle['manifest']['suite'] != 'multihop_rag'
            or bundle['manifest'].get('adaptation_revision') != 'notebook-data-v3'):
        raise ValueError('Multi-Meta import requires a MultiHop notebook-data-v3 bundle')
    rows, mappings, audit = prepare_rows(
        bundle, json.loads(answer_file.read_text()), json.loads(retrieval_file.read_text()), model=model,
        answer_sha256=spec['sha256'], retrieval_sha256=RETRIEVAL_SHA256)
    method = dict(
        name=f'multimeta-{model}-voyage02-full', kind='reference', citation_style='none',
        model_identity=dict(provider=spec['provider'], model=spec['model'], source_revision=REVISION),
        input_policy='author-retrieval-output-first-six',
        configuration=dict(
            comparison_category='recomputed-subset', source_scope='complete frozen question scope',
            source_repository=REPOSITORY, source_revision=REVISION, answer_source_sha256=spec['sha256'],
            retrieval_contract=PUBLISHED_RANKING_CONTRACT, retrieval_source_sha256=RETRIEVAL_SHA256,
            retriever='voyage-2 + BAAI/bge-reranker-large + metadata filtering',
            corpus_identity='not proven; no same-corpus claim',
            generation_script=spec['script'], temperature=0.1, max_retrieved_for_generation=6,
            max_output_tokens=spec['max_output_tokens'],
            output_budget_status='explicit' if model == 'palm' else 'not set explicitly by author script',
            generation_prompt_prefix=spec['prefix'] + INSTRUCTION))
    output_dir.mkdir(parents=True, exist_ok=False)
    normalized = output_dir / 'normalized'
    normalized.mkdir()
    raw = output_dir / 'raw'
    raw.mkdir()
    for path in (answer_file, retrieval_file):
        shutil.copyfile(path, raw / path.name)
    save_json(normalized / 'predictions.json', rows)
    save_json(normalized / 'case-map.json', {'format': 'external-case-map-v1', 'mappings': mappings})
    save_json(normalized / 'method.json', method)
    save_json(normalized / 'source.json', dict(
        repository=REPOSITORY, revision=REVISION, answer_path=spec['path'], retrieval_path=RETRIEVAL_PATH,
        answer_sha256=spec['sha256'], retrieval_sha256=RETRIEVAL_SHA256,
        adapter_sha256=digest(Path(__file__)), bundle_id=fingerprint(bundle['manifest']), audit=audit))
    return import_external_predictions(bundle_dir, normalized, method,
                                       case_map=normalized / 'case-map.json', output_dir=output_dir / 'submission')
