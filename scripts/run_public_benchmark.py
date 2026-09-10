#!/usr/bin/env python3
"""Run frozen public inputs through isolated native Silicon Notebook Ask."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from rag_eval.artifacts import digest, save_json, save_jsonl
from rag_eval.benchmark_runtime import (append_jsonl, open_runtime, prepare_notebook,
    read_json, run_questions, snapshot_invocation, utc_now)
from rag_eval.datasets import iter_jsonl
from rag_eval.scoring_identity import JUDGE_SETTINGS, build_scoring_identity


def choose(questions, scope, limit=None):
    if scope == 'all':
        selected = questions
    elif scope in ('debug', 'regression'):
        selected = [q for q in questions if q['split'] == scope]
    else:
        selected = [q for q in questions if q.get(scope)]
    return selected if limit is None else selected[:limit]


def judge_outputs(repo, cell, selected, repeat):
    from app.core.llm_logging import LLMInteractionLogger
    from rag_eval.usage_capture import capture_usage
    from rag_eval.benchmark_judge import BenchmarkJudge
    from rag_eval.cases import to_test_case
    from rag_eval.quality_metrics import build_quality_metrics, metric_availability, expected_answer
    outputs = list(iter_jsonl(cell / 'outputs.jsonl'))
    ids = {q['id'] for q in selected}
    path = cell / 'scores.jsonl'
    existing = list(iter_jsonl(path)) if path.exists() else []
    done = {(r['id'], r['metric'], r.get('repeat', 0)) for r in existing}
    judge = BenchmarkJudge(repo)
    for record in outputs:
        if record['id'] not in ids:
            continue
        diagnostic = bool(record.get('diagnostic')) and repeat == 0
        availability = metric_availability(record, diagnostic)
        eligible = record.get('status') == 'success' and bool(record.get('answer', '').strip())
        metrics = {getattr(m, 'name', m.__name__): m for m in build_quality_metrics(record, judge, diagnostic)} if eligible else {}
        case = to_test_case({**record, 'expected_answer': expected_answer(record)}) if eligible else None
        for name, condition in availability.items():
            key = record['id'], name, repeat
            if key in done:
                continue
            result = {k: record[k] for k in ('id', 'dataset', 'mode', 'split', 'group')}
            result.update(metric=name, repeat=repeat, score=None, reason=None, status='skipped', seconds=0)
            if not eligible:
                result['reason'] = 'product_error_or_empty_answer'
            elif not condition['available']:
                result['reason'] = condition['reason']
            else:
                start = time.monotonic()
                usage = {'calls': [], 'call_count': 0, 'coverage': 'not_started', 'total_tokens': None}
                try:
                    metric = metrics[name]
                    with capture_usage(LLMInteractionLogger) as usage:
                        metric.measure(case)
                    if metric.error or metric.score is None:
                        raise ValueError('Metric returned no valid score')
                    result.update(status='valid', score=float(metric.score), reason=metric.reason)
                except Exception as exc:
                    result.update(status='error', error_type=type(exc).__name__, reason='judge_invocation_or_validation_error')
                result['seconds'] = time.monotonic() - start
                result['usage'] = usage
            append_jsonl(path, result)
            print(record['dataset'], record['mode'], record['id'], name, result['status'],
                  result['score'], round(result['seconds'], 1), 'seconds', flush=True)


def run_cell(args, dataset, mode):
    cell = args.run_dir / 'cells' / dataset / mode
    cell.mkdir(parents=True, exist_ok=True)
    with (cell / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        repo, settings, overrides = open_runtime(args.project_root, cell)
        try:
            snapshot_invocation(ROOT, args.project_root, cell, args.bundle, mode, settings, overrides)
            questions = list(iter_jsonl(args.bundle / dataset / 'questions.jsonl'))
            selected = choose(questions, args.scope, args.limit)
            documents = list(iter_jsonl(args.bundle / dataset / 'documents.jsonl'))
            if args.stage in ('prepare', 'all'):
                from app.core.llm_logging import LLMInteractionLogger
                from rag_eval.usage_capture import capture_usage
                started = time.monotonic()
                with capture_usage(LLMInteractionLogger) as usage:
                    prepare_notebook(repo, cell, documents, dataset)
                append_jsonl(cell / 'preparation-usage.jsonl', {'usage': usage,
                    'seconds': time.monotonic() - started, 'at': utc_now()})
            if args.stage in ('ask', 'all'):
                state = read_json(cell / 'state.json', {})
                if not state.get('prepared'):
                    raise ValueError('Prepare must complete before Ask')
                mapping = read_json(cell / 'document-map.json')
                run_questions(repo, cell, state['notebook_id'], mapping, selected, mode, args.product_repeat,
                              {d['id']: d for d in documents})
            if args.stage in ('judge', 'all'):
                judge_outputs(repo, cell, selected, args.judge_repeat)
        finally:
            repo.close()


def aggregate(run, bundle):
    for name in ('outputs.jsonl', 'scores.jsonl', 'attempts.jsonl'):
        rows = []
        for path in sorted((run / 'cells').glob('*/*/' + name)):
            rows.extend(iter_jsonl(path))
        save_jsonl(run / name, rows)
    mapping = {str(p.parent.relative_to(run / 'cells')): read_json(p)
               for p in sorted((run / 'cells').glob('*/*/document-map.json'))}
    save_json(run / 'document-map.json', mapping)
    questions = [q for dataset in ('squad', 'drop') for q in iter_jsonl(bundle / dataset / 'questions.jsonl')]
    save_jsonl(run / 'questions.jsonl', questions)
    repeats = [r for p in sorted((run / 'cells').glob('*/*/product-repeat-*.jsonl')) for r in iter_jsonl(p)]
    save_jsonl(run / 'product-repeats.jsonl', repeats)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--run-dir', type=Path, required=True)
    parser.add_argument('--bundle', type=Path, default=ROOT / 'data/public-benchmark-v1')
    parser.add_argument('--project-root', type=Path, default=ROOT.parent / 'project')
    parser.add_argument('--stage', choices=('prepare', 'ask', 'judge', 'all', 'aggregate'), default='all')
    parser.add_argument('--scope', choices=('all', 'debug', 'regression', 'calibration', 'smoke'), default='all')
    parser.add_argument('--dataset', choices=('squad', 'drop'))
    parser.add_argument('--mode', choices=('chunk', 'reasoning'))
    parser.add_argument('--limit', type=int)
    parser.add_argument('--jobs', type=int, choices=(1, 2), default=1)
    parser.add_argument('--judge-repeat', type=int, default=0)
    parser.add_argument('--product-repeat', type=int, default=0)
    parser.add_argument('--worker', action='store_true', help=argparse.SUPPRESS)
    args = parser.parse_args()
    for name in ('run_dir', 'bundle', 'project_root'):
        setattr(args, name, getattr(args, name).resolve())
    if args.run_dir.is_relative_to(args.project_root) or args.run_dir.is_relative_to(args.bundle):
        parser.error('Run directory must be outside product and frozen inputs')
    if args.limit is not None and args.limit < 1:
        parser.error('limit must be positive')
    if args.judge_repeat < 0:
        parser.error('judge-repeat must be nonnegative')
    if args.product_repeat < 0 or (args.product_repeat and args.stage != 'ask'):
        parser.error('product-repeat must be nonnegative and used with stage ask')
    if args.worker:
        run_cell(args, args.dataset, args.mode)
        return
    args.run_dir.mkdir(parents=True, exist_ok=True)
    with (args.run_dir / '.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        source_manifest = read_json(args.bundle / 'manifest.json')
        if source_manifest is None:
            raise ValueError('Frozen input bundle missing')
        hashes = {str(p.relative_to(args.bundle)): digest(p) for dataset in ('squad', 'drop')
                  for p in sorted((args.bundle / dataset).glob('*.jsonl'))}
        bundle_hash = hashlib.sha256(json.dumps(hashes, sort_keys=True).encode()).hexdigest()
        scoring_hash = digest(ROOT / 'src/rag_eval/quality_metrics.py')
        import tomllib
        model_config = tomllib.loads((args.project_root / '.local/model-services.toml').read_text())
        judge_service = model_config['services'][model_config['bindings']['ask_answer']]
        judge_hash = hashlib.sha256(json.dumps(judge_service, sort_keys=True).encode()).hexdigest()
        from dotenv import dotenv_values
        env_file = dotenv_values(args.project_root / '.env')
        judge_settings = {name: os.environ.get(name, env_file.get(name)) for name in JUDGE_SETTINGS}
        judge_settings['LLM_CACHE_ENABLED'] = 'false'
        identity = {'bundle_sha256': bundle_hash, 'scoring_sha256': scoring_hash,
                    'judge_service_sha256': judge_hash,
                    'protocol': 'public-notebook-v1', 'modes': ['chunk', 'reasoning'],
                    **build_scoring_identity(ROOT, args.project_root, model_config,
                                             settings=judge_settings)}
        manifest_path = args.run_dir / 'manifest.json'
        manifest = read_json(manifest_path, {})
        if manifest and manifest['comparison_identity'] != identity:
            raise ValueError('Data or scoring protocol changed; use a new run directory')
        if not manifest:
            planned_datasets = [args.dataset] if args.dataset else ['squad', 'drop']
            planned_modes = [args.mode] if args.mode else ['chunk', 'reasoning']
            planned_keys = [dict(dataset=dataset, id=question['id'], mode=mode)
                            for dataset in planned_datasets
                            for question in choose(list(iter_jsonl(args.bundle / dataset / 'questions.jsonl')),
                                                   args.scope, args.limit)
                            for mode in planned_modes]
            manifest = {'started_at': utc_now(), 'comparison_identity': identity,
                        'source_manifest': source_manifest, 'human_calibration_status': 'pending',
                        'quality_gate': 'advisory', 'planned_scope': args.scope,
                        'planned_output_keys': planned_keys, 'planned_output_count': len(planned_keys),
                        'response_cache_enabled': False, 'outer_timeout': None}
            save_json(manifest_path, manifest)
        if args.stage == 'aggregate':
            aggregate(args.run_dir, args.bundle)
            return
        cells = [(d, m) for d in ([args.dataset] if args.dataset else ['squad', 'drop'])
                 for m in ([args.mode] if args.mode else ['chunk', 'reasoning'])]
        def launch(cell):
            dataset, mode = cell
            folder = args.run_dir / 'cells' / dataset / mode
            folder.mkdir(parents=True, exist_ok=True)
            cmd = [sys.executable, str(Path(__file__).resolve()), '--worker', '--run-dir', str(args.run_dir),
                   '--bundle', str(args.bundle), '--project-root', str(args.project_root),
                   '--dataset', dataset, '--mode', mode, '--stage', args.stage, '--scope', args.scope,
                   '--judge-repeat', str(args.judge_repeat), '--product-repeat', str(args.product_repeat)]
            if args.limit is not None:
                cmd += ['--limit', str(args.limit)]
            print('Starting', dataset, mode, args.stage, args.scope, flush=True)
            with (folder / 'execution.log').open('a') as log:
                result = subprocess.run(cmd, stdout=log, stderr=subprocess.STDOUT)
            print('Finished', dataset, mode, 'exit', result.returncode, flush=True)
            return result.returncode
        with ThreadPoolExecutor(max_workers=args.jobs) as pool:
            statuses = list(pool.map(launch, cells))
        aggregate(args.run_dir, args.bundle)
        append_jsonl(args.run_dir / 'executions.jsonl', {'finished_at': utc_now(), 'stage': args.stage,
                      'scope': args.scope, 'limit': args.limit, 'jobs': args.jobs,
                      'judge_repeat': args.judge_repeat, 'product_repeat': args.product_repeat,
                      'planned_selected_outputs': sum(len(choose(list(iter_jsonl(args.bundle / d / 'questions.jsonl')),
                          args.scope, args.limit)) for d, m in cells), 'cell_exit_codes': statuses})
        if any(statuses):
            raise SystemExit(2)


if __name__ == '__main__':
    main()
