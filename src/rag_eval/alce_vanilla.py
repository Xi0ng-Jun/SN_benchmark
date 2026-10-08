"""Pinned ALCE VANILLA prompts and native HF generation, with observed citations.

No gold labels enter requests. This controlled rerun is not a recovered paper
run: model snapshot, dependencies, RNG and loading policy are recorded afresh.
"""
from __future__ import annotations

import ast
from copy import deepcopy
import importlib.metadata
import json
import logging
import os
from pathlib import Path
import random
import shutil
import time
from types import SimpleNamespace

from .artifacts import digest, save_json
from .benchmark_submission import build_submission
from .notebook_alce import OFFICIAL_REVISION
from .notebook_bundle import load_bundle
from .identity import fingerprint


def author_symbols(upstream):
    """Load unchanged definitions, excluding unused API/search/Torch imports.

    The selected source files must match the pinned lock before executing any
    definition. LLM.__init__ is not called; only its original HF generate branch.
    """
    upstream = Path(upstream)
    lock = json.loads((Path(__file__).resolve().parents[2] / 'configs/alce-vanilla-source-lock.json').read_text())
    if lock['revision'] != OFFICIAL_REVISION:
        raise ValueError('Unexpected ALCE revision')
    for name, checksum in lock['files'].items():
        if digest(upstream / name) != checksum:
            raise ValueError('ALCE source hash mismatch: ' + name)
    namespace = {'logger': logging.getLogger(__name__)}
    for name, wanted in [('utils.py', {'make_doc_prompt', 'get_shorter_text', 'make_demo'}),
                         ('run.py', {'LLM'})]:
        tree = ast.parse((upstream / name).read_text())
        nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in wanted]
        if {n.name for n in nodes} != wanted:
            raise ValueError('Missing pinned ALCE definitions')
        exec(compile(ast.Module(body=nodes, type_ignores=[]), str(upstream / name), 'exec'), namespace)
    return namespace, lock


def check_demo_overlap(prompt_data, cases):
    normalize = lambda text: ' '.join(text.casefold().split())
    questions = {normalize(c['question']) for c in cases}
    if any(normalize(d['question']) in questions for d in prompt_data['demos']):
        raise ValueError('ALCE demonstrations overlap evaluation questions')


def make_request(case, prompt_data, head, make_demo):
    docs = [{'title': d['title'], 'text': d['text']} for d in case['candidate_documents'][:5]]
    item = dict(question=case['question'], docs=docs)
    prompt = head + make_demo(item, prompt=prompt_data.get('demo_prompt'), ndoc=5,
                              doc_prompt=prompt_data.get('doc_prompt'),
                              instruction=prompt_data.get('instruction'), use_shorter=None, test=True)
    return dict(case_id=case['case_id'], question=case['question'], prompt=prompt, docs=docs,
                citation_index_to_document_id={str(i): d['id'] for i, d in
                                                enumerate(case['candidate_documents'][:5], 1)})


def prepare_run(bundle_dir, upstream, output, *, case_ids=None):
    import numpy as np
    import yaml
    output, upstream = Path(output).resolve(), Path(upstream).resolve()
    for path in (Path(bundle_dir).resolve(), upstream):
        if output.is_relative_to(path) or path.is_relative_to(output):
            raise ValueError('ALCE output must be outside inputs')
    symbols, lock = author_symbols(upstream)
    bundle = load_bundle(bundle_dir)
    manifest = bundle['manifest']
    source = manifest['source']
    task, retriever = source.get('task'), source.get('retriever')
    if (manifest['suite'] != 'alce' or manifest['adaptation_revision'] != 'notebook-data-v3'
            or source.get('variant') != 'ordinary'
            or (task, retriever) not in {('asqa', 'gtr'), ('eli5', 'bm25'), ('qampari', 'gtr')}):
        raise ValueError('ALCE VANILLA requires ordinary v3 ASQA/GTR, ELI5/BM25 or QAMPARI/GTR')
    config_name = f'configs/{task}_llama2_shot2_ndoc5_{retriever}_default.yaml'
    config = yaml.safe_load((upstream / config_name).read_text())
    prompt_data = json.loads((upstream / config['prompt_file']).read_text())
    check_demo_overlap(prompt_data, bundle['cases'])
    all_ids = [c['case_id'] for c in bundle['cases']]
    if case_ids is not None and (not case_ids or len(set(case_ids)) != len(case_ids)
                                or set(case_ids) - set(all_ids)):
        raise ValueError('Invalid ALCE case scope')
    demo_ids = np.random.RandomState(42).choice(len(prompt_data['demos']), config['shot'], replace=False).tolist()
    head = ''.join(symbols['make_demo'](prompt_data['demos'][i], prompt=prompt_data['demo_prompt'],
                                       ndoc=config['ndoc'], doc_prompt=prompt_data['doc_prompt'],
                                       instruction=prompt_data['instruction'], use_shorter=None)
                   + prompt_data['demo_sep'] for i in demo_ids)
    requests = [make_request(c, prompt_data, head, symbols['make_demo']) for c in bundle['cases']
                if case_ids is None or c['case_id'] in set(case_ids)]
    output.mkdir(parents=True, exist_ok=False)
    for name in lock['files']:
        target = output / 'author-source' / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(upstream / name, target)
    save_json(output / 'requests.json', requests)
    prepared = dict(format='alce-vanilla-controlled-v1', state='prepared', task=task,
                    bundle_id=fingerprint(manifest), source_lock=lock, author_config=config,
                    implementation_sha256=digest(Path(__file__)), demo_ids=demo_ids,
                    case_ids=[r['case_id'] for r in requests], requests_sha256=digest(output / 'requests.json'),
                    differences=['controlled local HF snapshot; no paper-score reproduction claim',
                                 'author VANILLA prompt, first five passages, two demos; no test labels',
                                 'explicit torch seed 42 (author seeds only numpy)',
                                 'local-only fp16 auto-device loading; no author GPU reserve heuristic',
                                 'generation budget counts effective input tokens including BOS; author tokenize estimate omits it',
                                 'context exhaustion and exceptions are errors, not blank successful generations',
                                 'no chat template; original author plain prompt and LLM.generate HF branch'])
    save_json(output / 'manifest.json', prepared)
    return bundle, requests, prepared


def generate_rows(requests, llm, output):
    output = Path(output)
    rows, native = [], []
    with (output / 'events.jsonl').open('x', encoding='utf-8') as events:
        for index, request in enumerate(requests):
            started = time.monotonic()
            record = dict(citation_index_to_document_id=request['citation_index_to_document_id'],
                          shown_documents=request['docs'], prompt_sha256=fingerprint(request['prompt']))
            row = dict(case_id=request['case_id'], prediction='', status='error', record=record)
            stage = 'tokenization'
            try:
                prompt = request['prompt']
                token_ids = llm.tokenizer(prompt)['input_ids']
                budget = min(300, 4096 - len(token_ids))
                record.update(input_tokens=len(token_ids), max_new_tokens=budget)
                save_json(output / 'effective-inputs' / f'{index:05d}.json',
                          dict(case_id=request['case_id'], input_ids=token_ids))
                stage = 'context_budget'
                if budget <= 0:
                    raise ValueError('Context budget exhausted; prompt is not truncated')
                stage = 'generation'
                raw = llm.generate(prompt, budget)
                record['raw_generation'] = raw
                text = raw.replace('<|im_end|>', '').rstrip()
                if text.endswith('End.'):
                    text = text[:-len('End.')]
                row.update(prediction=text, status='success' if text.strip() else 'no_answer')
            except Exception as exc:
                # Public artifacts keep error type/stage; exception strings can
                # expose local paths or credentials from external libraries.
                record['error'] = dict(stage=stage, type=type(exc).__name__)
            record['elapsed_seconds'] = time.monotonic() - started
            rows.append(row)
            native.append(dict(request, output=row['prediction'], status=row['status']))
            events.write(json.dumps(row, ensure_ascii=False) + '\n')
            events.flush()
    save_json(output / 'native-output.json', dict(data=native))
    return rows


def execute(bundle, requests, prepared, output, model_path, method):
    output, model_path = Path(output).resolve(), Path(model_path).resolve()
    method = deepcopy(method)
    if (method.get('kind') != 'reference' or method.get('citation_style') != 'numeric'
            or method.get('configuration', {}).get('comparison_category') != 'controlled-rerun'):
        raise ValueError('ALCE method must be numeric reference controlled-rerun')
    if not model_path.is_dir():
        raise ValueError('Local model/tokenizer snapshot required')
    files = {str(p.relative_to(model_path)): digest(p) for p in sorted(model_path.rglob('*'))
             if p.is_file() and '.cache' not in p.relative_to(model_path).parts}
    if not any(name.endswith(('.safetensors', '.bin')) for name in files):
        raise ValueError('Local model weights required')
    if (fingerprint(bundle['manifest']) != prepared['bundle_id']
            or digest(output / 'requests.json') != prepared['requests_sha256']
            or json.loads((output / 'requests.json').read_text()) != requests):
        raise ValueError('Prepared ALCE inputs changed')
    symbols, lock = author_symbols(output / 'author-source')
    packages = {name: importlib.metadata.version(name) for name in ('torch', 'transformers', 'accelerate', 'numpy')}
    method['model_identity'].update(snapshot_id=fingerprint(files), files=files)
    method['configuration'].update(profile='alce-vanilla-llama2-hf-v1', source_lock=lock,
                                    runtime_sha256=digest(Path(__file__)), package_versions=packages,
                                    author_config=prepared['author_config'], demo_ids=prepared['demo_ids'],
                                    max_length=4096, max_new_tokens=300, seed=42, precision='fp16',
                                    citation_mapping_status='observed-top5', differences=prepared['differences'])
    build_submission(bundle, method=method, predictions=[], case_ids=prepared['case_ids'])
    save_json(output / 'method.json', method)
    env = {key: os.environ.get(key) for key in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')}
    save_json(output / 'manifest.json', dict(prepared, state='running'))
    try:
        for key in env:
            os.environ[key] = '1'
        import numpy as np
        import torch
        from transformers import AutoConfig, AutoModelForCausalLM, AutoTokenizer
        config = AutoConfig.from_pretrained(model_path, local_files_only=True, trust_remote_code=False)
        if config.model_type != 'llama':
            raise ValueError('This profile requires a Llama snapshot; other model families need a separate profile')
        random.seed(42)
        np.random.seed(42)
        torch.manual_seed(42)
        llm = symbols['LLM'].__new__(symbols['LLM'])
        llm.args = SimpleNamespace(openai_api=False, model=prepared['author_config']['model'],
                                   temperature=prepared['author_config']['temperature'],
                                   top_p=prepared['author_config']['top_p'])
        llm.prompt_exceed_max_length = llm.fewer_than_50 = 0
        llm.tokenizer = AutoTokenizer.from_pretrained(model_path, use_fast=False, local_files_only=True,
                                                     trust_remote_code=False)
        llm.tokenizer.padding_side = 'left'
        llm.model = AutoModelForCausalLM.from_pretrained(model_path, torch_dtype=torch.float16,
                                                       device_map='auto', local_files_only=True,
                                                       trust_remote_code=False)
        llm.model.eval()
        with torch.inference_mode():
            rows = generate_rows(requests, llm, output)
        submission = build_submission(bundle, method=method, predictions=rows, case_ids=prepared['case_ids'])
        save_json(output / 'submission.json', submission)
        save_json(output / 'manifest.json', dict(prepared, state='finished', coverage=submission['coverage'],
                                               method_id=submission['method_id']))
        return output / 'submission.json'
    except BaseException as exc:
        save_json(output / 'manifest.json', dict(prepared, state='failed', error_type=type(exc).__name__))
        raise
    finally:
        for key, value in env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
