"""Explicit, offline-only execution of ALCE's fixed full batch evaluator.

Model snapshots and NLTK resources must be provisioned first. This module never
substitutes a smaller NLI model, downloads weights, or averages per-case MAUVE.
"""
from __future__ import annotations

import hashlib
import ast
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys

from .artifacts import save_json

AUTOAIS = 'google/t5_xxl_true_nli_mixture'
QA = 'gaotianyu1350/roberta-large-squad'
MAUVE = 'gpt2-large'


def alce_arguments(task):
    if task not in {'asqa', 'qampari', 'eli5'}:
        raise ValueError('Unknown ALCE task')
    # Upstream checks the *entire --f string* for qampari. Use a local basename.
    args = ['--f', task + '.json', '--citations', '--at_most_citations', '3']
    if task == 'asqa':
        args += ['--qa', '--mauve']
    elif task == 'eli5':
        args += ['--claims_nli', '--mauve']
    return args


def parse_metrics(task, values):
    alce_arguments(task)
    required = {'citation_rec', 'citation_prec'}
    diagnostics = {'length'}
    if task == 'qampari':
        required |= {'qampari_prec', 'qampari_rec', 'qampari_rec_top5', 'qampari_f1', 'qampari_f1_top5'}
        diagnostics |= {'num_preds', 'str_em', 'str_hit'}
    else:
        required |= {'rougeLsum', 'mauve'}
        if task == 'asqa':
            required |= {'str_em', 'str_hit', 'QA-EM', 'QA-F1', 'QA-Hit'}
        else:
            required |= {'claims_nli'}
            diagnostics |= {'str_em', 'str_hit'}
    if not isinstance(values, dict) or set(values) != required | diagnostics:
        raise ValueError('Official ALCE output has missing or unexpected metrics')
    if any(type(v) not in (float, int) or not math.isfinite(v) or v < 0 for v in values.values()):
        raise ValueError('Official ALCE output must contain finite nonnegative numbers')
    if any(values[k] > 100 for k in required):
        raise ValueError('Official ALCE percent metric is outside 0..100')
    return ({k: values[k] / 100 for k in sorted(required)},
            {k: values[k] for k in sorted(diagnostics)})


def _file_hash(path):
    h = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(4 * 1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def _tree_hashes(root):
    return {str(p.relative_to(root)): _file_hash(p) for p in sorted(root.rglob('*')) if p.is_file()}


def inspect_cache(cache, task):
    """Record the resolved offline main ref and every public snapshot byte."""
    alce_arguments(task)
    cache = Path(cache).resolve()
    models = [AUTOAIS] + ([QA, MAUVE] if task == 'asqa' else [MAUVE] if task == 'eli5' else [])
    result = {}
    for model in models:
        root = cache / ('models--' + model.replace('/', '--'))
        ref = root / 'refs/main'
        if not ref.is_file():
            raise ValueError('Missing offline model cache reference: ' + model)
        revision = ref.read_text().strip()
        if not re.fullmatch('[a-f0-9]{40}', revision):
            raise ValueError('Model cache requires a resolved commit revision: ' + model)
        snapshot = root / 'snapshots' / revision
        if not snapshot.is_dir() or not (snapshot / 'config.json').is_file():
            raise ValueError('Incomplete offline model cache snapshot: ' + model)
        files = list(snapshot.rglob('*'))
        if not any(p.is_file() and (p.suffix == '.safetensors' or p.name.startswith('pytorch_model')) for p in files):
            raise ValueError('Missing offline model weights: ' + model)
        result[model] = {'revision': revision, 'files': _tree_hashes(snapshot)}
    return result


def citation_eligibility_program(source):
    """Extract the exact CLI preprocessing and AutoAIS sentence eligibility."""
    tree = ast.parse(source)
    functions = {node.name: node for node in tree.body if isinstance(node, ast.FunctionDef)}
    preprocessing = [node for node in ast.walk(functions['main']) if isinstance(node, ast.Assign)
                     and ('.strip().split(' in ast.unparse(node.value) or '<|im_end|>' in ast.unparse(node.value))]
    segmentation = [node for node in ast.walk(functions['compute_autoais']) if isinstance(node, ast.If)
                    and isinstance(node.test, ast.Name) and node.test.id == 'qampari']
    if len(preprocessing) != 2 or len(segmentation) != 1:
        raise ValueError('Official ALCE citation eligibility contract changed')
    body = '\n'.join('    ' + line for node in [*preprocessing, segmentation[0]]
                     for line in ast.unparse(node).splitlines())
    return ('eligible_indices = []\nfor i, item in enumerate(data):\n' + body +
            '\n    if len(sents):\n        eligible_indices.append(i)\n')


def score_alce_batch(prepared, *, source_directory, output_dir, hf_cache, nltk_data,
                     python=sys.executable, timeout=3600):
    from .benchmark_official import _source, SOURCES
    if prepared.get('suite') != 'alce':
        raise ValueError('ALCE input required')
    task = prepared['task']
    arguments = alce_arguments(task)
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError('Positive ALCE batch timeout required')
    sources = {local: _source(source_directory, public) for local, public in
               [('eval.py', 'alce_eval.py'), ('utils.py', 'alce_utils.py')]}
    models = inspect_cache(hf_cache, task)
    nltk_data = Path(nltk_data).resolve()
    if not (nltk_data / 'tokenizers').is_dir():
        raise ValueError('Provision NLTK tokenizer resources before ALCE scoring')
    nltk_hashes = _tree_hashes(nltk_data)
    destination = Path(output_dir).resolve()
    destination.mkdir(parents=True, exist_ok=False)
    for name, source in sources.items():
        (destination / name).write_text(source, encoding='utf-8')
    save_json(destination / (task + '.json'), {'data': prepared['data']})
    # Seed the original evaluator's bootstrap without modifying its source.
    eligibility = citation_eligibility_program(sources['eval.py'])
    launcher = ('import runpy, sys, numpy, torch, json, os, nltk\n'
                'nltk.data.path = [os.environ["NLTK_DATA"]]\nfrom nltk import sent_tokenize\n'
                'sent_tokenize("Tokenizer resource preflight.")\n'
                'numpy.random.seed(0)\ntorch.manual_seed(0)\n'
                'data = json.load(open(' + repr(task + '.json') + '))["data"]\n'
                'qampari = ' + repr(task == 'qampari') + '\n' + eligibility +
                'json.dump(eligible_indices, open("citation-eligible-indices.json", "w"))\n'
                'sys.argv = ["eval.py"] + sys.argv[1:]\n'
                'runpy.run_path("eval.py", run_name="__main__")\n')
    (destination / 'launch.py').write_text(launcher, encoding='utf-8')
    environment = dict(os.environ, HF_HUB_CACHE=str(Path(hf_cache).resolve()),
                       HUGGINGFACE_HUB_CACHE=str(Path(hf_cache).resolve()),
                       TRANSFORMERS_CACHE=str(Path(hf_cache).resolve()),
                       HF_HUB_OFFLINE='1', TRANSFORMERS_OFFLINE='1', HF_DATASETS_OFFLINE='1',
                       NLTK_DATA=str(nltk_data), PYTHONHASHSEED='0', TOKENIZERS_PARALLELISM='false')
    packages_code = ('import importlib.metadata as m,json,sys; '
                     'print(json.dumps({"python":sys.version,"packages":'
                     '{d.metadata["Name"]:d.version for d in m.distributions()}}))')
    runtime = subprocess.run([str(python), '-c', packages_code], env=environment,
                             text=True, capture_output=True, check=True, timeout=60)
    dependencies = {'models': models, 'nltk_files': nltk_hashes,
                    'runtime': json.loads(runtime.stdout), 'numpy_seed': 0, 'torch_seed': 0,
                    'at_most_citations': 3, 'arguments': arguments,
                    'sources': {k: SOURCES[k] for k in ('alce_eval.py', 'alce_utils.py')},
                    'launcher_sha256': hashlib.sha256(launcher.encode()).hexdigest(),
                    'bridge_sha256': _file_hash(Path(__file__))}
    command = [str(python), 'launch.py', *arguments]
    save_json(destination / 'invocation.json', {'command': command, 'dependencies': dependencies,
              'hf_cache': str(Path(hf_cache).resolve()), 'nltk_data': str(nltk_data), 'timeout': timeout,
              'input_sha256': _file_hash(destination / (task + '.json'))})
    try:
        with (destination / 'stdout.txt').open('w') as stdout, (destination / 'stderr.txt').open('w') as stderr:
            completed = subprocess.run(command, cwd=destination, env=environment,
                                       stdout=stdout, stderr=stderr, timeout=timeout)
        if completed.returncode:
            raise RuntimeError('Official ALCE CLI failed; inspect saved stdout/stderr')
        raw = json.loads((destination / (task + '.json.score')).read_text())
        metrics, diagnostics = parse_metrics(task, raw)
        eligible = json.loads((destination / 'citation-eligible-indices.json').read_text())
        if (not isinstance(eligible, list) or not eligible or
                any(type(i) is not int or not 0 <= i < len(prepared['case_ids']) for i in eligible)
                or eligible != sorted(set(eligible))):
            raise ValueError('Invalid or empty ALCE citation metric denominator')
    except Exception as exc:
        save_json(destination / 'failure.json', {'status': 'error', 'error_type': type(exc).__name__})
        raise
    result = {'metrics': metrics, 'diagnostics': diagnostics, 'dependencies': dependencies,
              'metric_case_ids': {name: [prepared['case_ids'][i] for i in eligible]
                                  if name in {'citation_rec', 'citation_prec'} else prepared['case_ids']
                                  for name in metrics},
              'raw_metrics': raw, 'metric_scope': 'official-batch; no inferred per-case model scores'}
    save_json(destination / 'result.json', result)
    return result
