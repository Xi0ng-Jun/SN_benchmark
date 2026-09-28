"""Lazy server runtime for the pinned LAB LongChat citation method.

Calls author collation/generation/parser directly. Does not use LAB's scoring
callback or feed evaluation labels to model objects.
"""
from __future__ import annotations

from copy import deepcopy
import importlib.metadata
import json
import os
from pathlib import Path
import random
import sys
import time

from .artifacts import digest, save_json
from .benchmark_submission import build_submission
from .lab_qasper import REVISION, instance_json, prediction_row
from .starter_protocol import fingerprint


def generate_rows(questions, documents, evidence_maps, instance_factory, model, output):
    output = Path(output)
    rows = []
    with (output / 'events.jsonl').open('x', encoding='utf-8') as events:
        for index, question in enumerate(questions):
            started = time.monotonic()
            qid, pid = question['question_id'], question['paper_id']
            row = dict(case_id=question['case_id'], status='error', prediction='', record={})
            stage = 'input'
            try:
                instance = instance_factory(instance_json(documents[pid], qid, question['question']))
                batch = model.validation_collate_fn([instance])
                token_ids = batch['tokenized_input']['input_ids'].tolist()[0]
                request_path = output / 'requests' / f'{index:05d}.json'
                save_json(request_path, dict(question_id=qid, prompt=batch['input_texts'][0],
                                            effective_token_ids=token_ids,
                                            effective_input=model.tokenizer.decode(token_ids)))
                row['record']['request_sha256'] = digest(request_path)
                stage = 'generation'
                predictions = model.validation_step(batch, index)
                if len(predictions) != 1:
                    raise ValueError('LAB must return exactly one prediction per case')
                native = predictions[0].to_json_dict()
                row['record']['lab_prediction'] = native
                stage = 'projection'
                converted = prediction_row(native, qid, question['case_id'], evidence_maps[pid])
                converted['record'].update(row['record'])
                row = converted
            except Exception as exc:
                row['record']['error'] = {'type': type(exc).__name__, 'stage': stage}
            row['record']['elapsed_seconds'] = time.monotonic() - started
            events.write(json.dumps(row, ensure_ascii=False) + '\n')
            events.flush()
            rows.append(row)
    save_json(output / 'predictions.json', rows)
    return rows


def execute(bundle, prepared_manifest, output, model_path, device, method, nltk_data):
    output, model_path = Path(output).resolve(), Path(model_path).resolve()
    if not model_path.is_dir():
        raise ValueError('Use a local LongChat model/tokenizer snapshot')
    method = deepcopy(method)
    if method.get('configuration', {}).get('comparison_category') != 'controlled-rerun':
        raise ValueError('LAB method must declare controlled-rerun')
    if method.get('kind') != 'reference' or method.get('citation_style') != 'none':
        raise ValueError('LAB produces parsed reference answers with explicit evidence')
    model_files = {str(p.relative_to(model_path)): digest(p) for p in sorted(model_path.rglob('*'))
                   if p.is_file() and '.cache' not in p.relative_to(model_path).parts}
    if not model_files:
        raise ValueError('Empty model snapshot')
    packages = {}
    for name in ('torch', 'transformers', 'hydra-core', 'omegaconf', 'intertext-graph',
                 'langchain', 'peft', 'nltk', 'numpy'):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = 'missing'
    config_identity = dict(profile='lab-longchat-citation-hf-v1', author_revision=REVISION,
                           source_lock=prepared_manifest['source_lock'],
                           preparation_sha256=prepared_manifest['implementation_sha256'],
                           runtime_sha256=digest(Path(__file__)), model_files=model_files,
                           model_snapshot_id=fingerprint(model_files), package_versions=packages,
                           training_examples=prepared_manifest['training_examples'],
                           source_data_sha256=prepared_manifest['source_data_sha256'],
                           differences=prepared_manifest['differences'], device=device,
                           random_seed=635191, max_input_tokens=16000, max_new_tokens=100,
                           precision='bf16', generation='greedy', batch_size=1, use_vllm=False,
                           evidence_projection='original-ordered-paragraph-text; structural nodes become invalid evidence; unknown raw IDs follow author parser dropping')
    method['configuration'].update(config_identity)
    build_submission(bundle, method=method, predictions=[], case_ids=prepared_manifest['case_ids'])
    save_json(output / 'method.json', method)
    manifest = dict(prepared_manifest, state='running')
    save_json(output / 'manifest.json', manifest)
    import_path = sys.path[:]
    original_nltk_path = None
    offline_env = {name: os.environ.get(name) for name in ('HF_HUB_OFFLINE', 'TRANSFORMERS_OFFLINE')}
    try:
        os.environ.update(HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1')
        # Author parsing uses sentence tokenization after generation. Fail once
        # before loading weights instead of spending an entire run on this error.
        import nltk
        nltk_data = Path(nltk_data).resolve()
        original_nltk_path = nltk.data.path[:]
        nltk.data.path[:] = [str(nltk_data)]
        nltk.sent_tokenize('First sentence. Second sentence.')
        method['configuration']['nltk_files'] = {
            str(p.relative_to(nltk_data)): digest(p) for p in sorted(nltk_data.rglob('*')) if p.is_file()}
        save_json(output / 'method.json', method)
        source = output / 'author-source'
        sys.path.insert(0, str(source))
        import torch
        import numpy as np
        from hydra import compose, initialize_config_dir
        import config_lib.base_config as author_config
        from config_lib.config_container import ConfigContainer
        from evaluation.common import BaseInstance, CustomDataset, Statistics
        from models.causal_lm import CausalLMForExtractionModel

        with initialize_config_dir(config_dir=str(source / 'config'), version_base=None):
            config = compose(config_name='config', overrides=[
                'task=qasper', 'model=longchat-7b-v1.5-32k', 'description=sn-controlled-citation',
                'do_train=False', 'load_model=False', 'use_dev_as_test_data=False',
                'use_first_n_test_instances=-1', 'required_aspects=answer_and_segments',
                'extraction_mode=node_id', 'answer_format=text', 'do_retrieve_then_read=False',
                'do_post_hoc_extract=False', 'do_prune_to_extraction_nodes=False', 'n_examples=3'])
        # Author init normally reads cwd Git metadata; generation must identify
        # the pinned author sources rather than this adapter's checkout.
        original_commit_setter = author_config._set_commit_hash
        author_config._set_commit_hash = lambda cfg: setattr(cfg, 'commit_hash', REVISION)
        try:
            author_config.init_config(config)
        finally:
            author_config._set_commit_hash = original_commit_setter
        config.use_vllm = False
        config.model.batch_size = 1
        config.model.max_input_length = 16000
        config.task.max_new_tokens = 100
        config.model.hf_model_id = str(model_path)
        config.task.example_ids = [0, 1, 2]
        config.location.prompts = str(source / 'config/prompts.yaml')
        config.dataloader_num_workers = 0
        random.seed(config.random_seed)
        np.random.seed(config.random_seed)
        torch.manual_seed(config.random_seed)
        examples = json.loads((output / 'training-examples.json').read_text())
        train = CustomDataset([BaseInstance.from_json_dict(row) for row in examples])
        stats = Statistics(model_name=config.model.model_name, task_name='qasper',
                           description=config.description, config=config)
        model = CausalLMForExtractionModel(config, stats, train)
        model.to(device)
        model.eval()
        # Record resolved config after author's prompt setup mutates it. Paths
        # stay in this private run artifact; method identity uses content hashes.
        save_json(output / 'author-resolved-config.json', ConfigContainer.dict_config_to_json_dict(config))
        questions = json.loads((output / 'questions.json').read_text())
        documents = json.loads((output / 'public-documents.json').read_text())
        evidence_maps = json.loads((output / 'evidence-map.json').read_text())
        with torch.inference_mode():
            rows = generate_rows(questions, documents, evidence_maps, BaseInstance.from_json_dict, model, output)
        submission = build_submission(bundle, method=method, predictions=rows,
                                      case_ids=prepared_manifest['case_ids'])
        save_json(output / 'submission.json', submission)
        manifest.update(state='complete' if submission['coverage']['generation_complete'] else 'incomplete',
                        coverage=submission['coverage'])
    except BaseException as exc:
        manifest.update(state='interrupted' if isinstance(exc, KeyboardInterrupt) else 'failed',
                        error_type=type(exc).__name__)
        raise
    finally:
        sys.path[:] = import_path
        if original_nltk_path is not None:
            nltk.data.path[:] = original_nltk_path
        for name, value in offline_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        save_json(output / 'manifest.json', manifest)
    return output / 'submission.json'
