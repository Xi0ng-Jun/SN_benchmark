"""Controlled HotpotQA runner around hash-pinned KG2RAG author code.

Generation sees public contexts only. Prompt/cache/error adaptations are
explicit; this is not a reproduction of the paper's unmodified runtime.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import importlib.util
import importlib.metadata
import json
from pathlib import Path
import sys
import time

from .artifacts import digest, save_json
from .benchmark_submission import build_submission
from .notebook_bundle import load_bundle
from .starter_protocol import fingerprint

REVISION = '7d626c77b7af30b55aa3f960cde755b9549a0616'
SOURCE_HASHES = {
    'code/kg_rag_distractor.py': 'd5ec81942afdf741f36a98435756ff3cfc76815a518d57fc4bb79165bd9da197',
    'code/preprocess/hotpot_extraction.py': '24a63fb8ee2be4d3f92b51d0c345375f15bebb13b0b9c0ce75abb61dd7910fcb',
    'code/util/kg_post_processor.py': 'dd61721a49770ba8dbe3334e478cb2036b0440c5e8256906cc0f41fcbac1e8f2',
    'code/util/kg_response_synthesizer.py': 'a16494c24348b0cbebeb0a775cd0e41d063bffe60daf1688deeb58f08593568c',
    'code/util/prompt_helper.py': 'be877999de0f4a45a548a5199d7747d10ed2697e4a2efd55aaf40102a5c42853',
}
ADAPTATIONS = [
    'hotpotqa-distractor only; public question/context input, no gold fields',
    'QA examples replaced by frozen train-only demonstrations',
    'KG extraction uses same instruction/parser without validation-derived demonstrations',
    'KG cache keyed by exact title and complete sentence array, scoped to this run',
    'author query errors propagate to explicit per-case error records',
    'reranker device is an explicit runtime option instead of hard-coded device 3',
]


def public_samples(bundle):
    documents = {doc['id']: doc for doc in bundle['documents']}
    return [{'_id': case['sample_id'], 'question': case['question'],
             'context': [[documents[did]['title'],
                          [unit['text'] for unit in documents[did]['source_units']]]
                         for did in case['material_document_ids']]}
            for case in bundle['cases']]


def qa_prompt(demonstrations, evaluated):
    examples = demonstrations.get('examples')
    if demonstrations.get('split') != 'train' or not isinstance(examples, list) or not examples:
        raise ValueError('Explicit nonempty training demonstrations required')
    ids = {s['_id'] for s in evaluated}
    questions = {' '.join(s['question'].lower().split()) for s in evaluated}
    for ex in examples:
        if not all(isinstance(ex.get(k), str) and ex[k].strip() for k in ('id', 'question', 'answer')):
            raise ValueError('Training demonstration requires id/question/answer')
        if ex['id'] in ids or ' '.join(ex['question'].lower().split()) in questions:
            raise ValueError('Training demonstrations overlap evaluated questions')
    # Literal braces in an example must not become PromptTemplate placeholders.
    def literal(text):
        return text.replace('{', '{{').replace('}', '}}')
    shots = ''.join(f"Q: {literal(e['question'])}\nA: {literal(e['answer'])}\n" for e in examples)
    return ('Context information is below.\n{context_str}\n'
            'Think step by step but give a short factoid answer (as few words as possible) '
            'based on the context and your own knowledge.\n' + shots
            + '---------------------\nQ: {query_str}\nA: ')


def _replace_once(source, old, new):
    if source.count(old) != 1:
        raise ValueError('Pinned KG2RAG adaptation target changed')
    return source.replace(old, new, 1)


def staged_sources(upstream, prompt):
    upstream = Path(upstream)
    for name, expected in SOURCE_HASHES.items():
        if digest(upstream / name) != expected:
            raise ValueError('KG2RAG source hash mismatch: ' + name)
    source = (upstream / 'code/kg_rag_distractor.py').read_text()
    source = _replace_once(source, "    sample_answer = sample['answer']\n", '')
    assignment = next(line for line in source.splitlines()
                      if line.startswith('    qa_rag_template_str = '))
    source = _replace_once(source, assignment, '    qa_rag_template_str = ' + repr(prompt))
    source = _replace_once(source, 'model_name_or_path=args.reranker,device=3',
                           'model_name_or_path=args.reranker,devices=args.reranker_device')
    source = _replace_once(source, "        prediction = ''\n        sps = []", '        raise')
    extraction = (upstream / 'code/preprocess/hotpot_extraction.py').read_text()
    function = next(n for n in ast.parse(extraction).body
                    if isinstance(n, ast.FunctionDef) and n.name == 'extract_triplets')
    extraction = ast.get_source_segment(extraction, function)
    assignment = next(line for line in extraction.splitlines() if line.startswith('    query = '))
    instruction = ('Extract triplets informative from the text. Make sure the triplet texts '
                   'are only directly from the given text! Complete directly and strictly '
                   'following the instructions without any additional words, line break nor space!\n'
                   'Format: <head##relation##tail>$$\n--------------------\nText: ')
    extraction = _replace_once(extraction, assignment,
                               '    query = ' + repr(instruction) + " + ctx + '\\nTriplets:'")
    result = {'kg_rag_distractor.py': source, 'extract_triplets.py': extraction + '\n'}
    result.update({name.removeprefix('code/'): (upstream / name).read_text()
                   for name in SOURCE_HASHES if name.startswith('code/util/')})
    for name, text in result.items():
        compile(text, name, 'exec')
    return result


def prepare_run(bundle_dir, upstream, demonstrations_file, output, *, case_ids=None):
    output = Path(output).resolve()
    for path in (Path(bundle_dir).resolve(), Path(upstream).resolve()):
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('KG2RAG output must be outside source inputs')
    bundle = load_bundle(bundle_dir)
    if (bundle['manifest']['suite'] != 'hotpotqa'
            or bundle['manifest'].get('adaptation_revision') != 'notebook-data-v3'
            or bundle['manifest']['source'].get('setting') != 'distractor'):
        raise ValueError('KG2RAG requires a HotpotQA distractor v3 bundle')
    samples = public_samples(bundle)
    demos = json.loads(Path(demonstrations_file).read_text())
    # Check overlap against the full evaluation split, even for a smoke subset.
    prompt = qa_prompt(demos, samples)
    if case_ids is not None:
        known = {c['case_id']: c['sample_id'] for c in bundle['cases']}
        if not case_ids or len(set(case_ids)) != len(case_ids) or set(case_ids) - set(known):
            raise ValueError('Invalid explicit KG2RAG case scope')
        selected = {known[cid] for cid in case_ids}
        samples = [s for s in samples if s['_id'] in selected]
    sources = staged_sources(upstream, prompt)
    output.mkdir(parents=True, exist_ok=False)
    for name, text in sources.items():
        target = output / 'adapted-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text)
    save_json(output / 'public-input.json', samples)
    save_json(output / 'demonstrations.json', demos)
    manifest = dict(format='kg2rag-controlled-run-v1', state='prepared', upstream_revision=REVISION,
                    source_hashes=SOURCE_HASHES, adaptations=ADAPTATIONS,
                    implementation_sha256=digest(Path(__file__)),
                    bundle_id=fingerprint(bundle['manifest']),
                    case_ids=[c['case_id'] for c in bundle['cases']
                              if case_ids is None or c['case_id'] in set(case_ids)],
                    public_input_sha256=digest(output / 'public-input.json'),
                    demonstrations_sha256=digest(output / 'demonstrations.json'),
                    adapted_source_hashes={name: digest(output / 'adapted-source' / name) for name in sources})
    save_json(output / 'manifest.json', manifest)
    return bundle, samples, manifest


def context_kg(title, sentences, cache_dir, extract, llm):
    identity = {'title': title, 'sentences': sentences}
    path = Path(cache_dir) / (fingerprint(identity) + '.json')
    if path.exists():
        cached = json.loads(path.read_text())
        if cached['context'] != identity:
            raise ValueError('KG cache does not match public context')
        return cached['kg']
    kg = {}
    for i, sentence in enumerate(sentences):
        triples = extract(llm, sentence if i == 0 else f'{title}: {sentence}')
        if triples:
            kg[str(i)] = triples
    save_json(path, {'context': identity, 'kg': kg})
    return kg


def run_samples(samples, author, args, extract, llm, output):
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    rows = []
    with (output / 'events.jsonl').open('x', encoding='utf-8') as events:
        for sample in samples:
            started = time.monotonic()
            row = dict(external_id=sample['_id'], status='error', prediction='', record={})
            try:
                kg = {title: context_kg(title, sentences, output / 'kg-cache', extract, llm)
                      for title, sentences in sample['context']}
                qid, answer, support = author.process_sample(args, sample, kg)
                if qid != sample['_id'] or not isinstance(answer, str):
                    raise ValueError('KG2RAG returned a different question or invalid answer')
                if (not isinstance(support, list) or any(
                        not isinstance(f, list) or len(f) != 2 or not isinstance(f[0], str)
                        or type(f[1]) is not int for f in support)):
                    raise ValueError('KG2RAG returned invalid supporting facts')
                row.update(status='success' if answer.strip() else 'no_answer', prediction=answer)
                row['record']['predicted_supporting_facts'] = support
            except Exception as exc:
                # Avoid leaking service URLs or credentials from arbitrary SDK error text.
                row['record']['error'] = {'type': type(exc).__name__}
            row['record']['elapsed_seconds'] = time.monotonic() - started
            events.write(json.dumps(row, ensure_ascii=False) + '\n')
            events.flush()
            rows.append(row)
    save_json(output / 'predictions.json', rows)
    return rows


def execute_prepared(bundle, samples, manifest, output, args, method):
    """Called only by explicit execution; imports model dependencies lazily."""
    output = Path(output)
    method = deepcopy(method)
    if method.get('configuration', {}).get('comparison_category') != 'controlled-rerun':
        raise ValueError('Method must explicitly declare controlled-rerun')
    reranker = Path(args.reranker)
    if not reranker.is_dir():
        raise ValueError('Reranker must be an existing local model directory')
    reranker_files = {str(p.relative_to(reranker)): digest(p)
                      for p in sorted(reranker.rglob('*'))
                      if p.is_file() and '.cache' not in p.relative_to(reranker).parts}
    if not reranker_files:
        raise ValueError('Reranker model directory is empty')
    package_versions = {}
    for package in ('FlagEmbedding', 'llama-index-core', 'llama-index-llms-ollama',
                    'llama-index-embeddings-ollama', 'networkx', 'torch', 'transformers', 'ollama'):
        try:
            package_versions[package] = importlib.metadata.version(package)
        except importlib.metadata.PackageNotFoundError:
            package_versions[package] = 'missing'
    method['configuration'].update(
        benchmark_setting='distractor', upstream_revision=REVISION,
        adaptations=ADAPTATIONS, implementation_sha256=manifest['implementation_sha256'],
        adapted_source_hashes=manifest['adapted_source_hashes'],
        demonstrations_sha256=manifest['demonstrations_sha256'],
        reranker_files=reranker_files, package_versions=package_versions,
        ollama_identity_status='configured tags; server-resolved model digests not observed by this adapter',
        runtime=dict(model_name=args.model_name, embed_model_name=args.embed_model_name,
                     top_k=args.top_k, use_tpt=args.use_tpt, reranker_device=args.reranker_device))
    build_submission(bundle, method=method, predictions=[], case_ids=manifest['case_ids'])
    save_json(output / 'method.json', method)
    manifest = dict(manifest, state='running')
    save_json(output / 'manifest.json', manifest)
    original_import_path = sys.path[:]
    try:
        sys.path.insert(0, str(output / 'adapted-source'))
        def load(name, filename):
            spec = importlib.util.spec_from_file_location(name, output / 'adapted-source' / filename)
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            return module
        author = load('kg2rag_author_adapter', 'kg_rag_distractor.py')
        extractor = load('kg2rag_extraction_adapter', 'extract_triplets.py')
        author.init_model(args)
        extraction_llm = author.Ollama(model=args.model_name, request_timeout=120)
        rows = run_samples(samples, author, args, extractor.extract_triplets, extraction_llm, output)
        mapping = {c['sample_id']: c['case_id'] for c in bundle['cases']}
        predictions = [dict(row, case_id=mapping[row['external_id']]) for row in rows]
        submission = build_submission(bundle, method=method, predictions=predictions,
                                      case_ids=manifest['case_ids'])
        save_json(output / 'submission.json', submission)
        manifest.update(state='complete' if submission['coverage']['generation_complete'] else 'incomplete',
                        coverage=submission['coverage'])
    except BaseException as exc:
        manifest.update(state='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                        error_type=type(exc).__name__)
        raise
    finally:
        sys.path[:] = original_import_path
        save_json(output / 'manifest.json', manifest)
    return output / 'submission.json'
