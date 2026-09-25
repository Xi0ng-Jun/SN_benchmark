#!/usr/bin/env python3
"""Freeze submissions, acquire pinned scorers, and score benchmark answers."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    fetch = commands.add_parser('fetch-sources', help='Download and SHA256-check fixed public scorer scripts')
    fetch.add_argument('--output', type=Path, required=True)
    export = commands.add_parser('export-sn', help='Validate and combine isolated SN runs, without inference')
    export.add_argument('--bundle', type=Path, required=True)
    export.add_argument('--runs', type=Path, nargs='+', required=True)
    export.add_argument('--case-ids', nargs='+')
    export.add_argument('--qasper-evidence', action='store_true',
                        help='Explicitly recover old QASPER final-citation evidence into the new submission, read-only')
    export.add_argument('--output', type=Path, required=True)
    ingest = commands.add_parser('import-predictions', help='Import explicit method metadata and prediction JSON list')
    ingest.add_argument('--bundle', type=Path, required=True)
    ingest.add_argument('--method', type=Path, required=True)
    ingest.add_argument('--predictions', type=Path, required=True)
    ingest.add_argument('--case-ids', nargs='+')
    ingest.add_argument('--output', type=Path, required=True)
    score = commands.add_parser('score', help='Score frozen submission; full ALCE needs explicit offline model runtime')
    score.add_argument('--bundle', type=Path, required=True)
    score.add_argument('--submission', type=Path, required=True)
    score.add_argument('--sources', type=Path, required=True)
    score.add_argument('--output', type=Path, required=True)
    score.add_argument('--rouge-home', type=Path)
    score.add_argument('--alce-full', action='store_true')
    score.add_argument('--alce-python', type=Path, default=Path(sys.executable))
    score.add_argument('--alce-hf-cache', type=Path)
    score.add_argument('--alce-nltk-data', type=Path)
    score.add_argument('--alce-timeout', type=float, default=3600)
    args = parser.parse_args(argv)
    from rag_eval.benchmark_official import fetch_sources, prepare_inputs, score_prepared
    from rag_eval.benchmark_submission import export_sn_runs, write_submission
    if args.command == 'fetch-sources':
        print(fetch_sources(args.output))
    elif args.command == 'export-sn':
        print(export_sn_runs(args.bundle, args.runs, args.output, case_ids=args.case_ids,
                             qasper_evidence=args.qasper_evidence))
    elif args.command == 'import-predictions':
        print(write_submission(args.bundle, args.output, method=json.loads(args.method.read_text()),
                               predictions=json.loads(args.predictions.read_text()), case_ids=args.case_ids))
    else:
        from rag_eval.notebook_bundle import load_bundle
        from rag_eval.benchmark_submission import validate_submission
        for source in [args.bundle, args.submission, args.sources, args.rouge_home,
                       args.alce_hf_cache, args.alce_nltk_data]:
            if source and (args.output.resolve().is_relative_to(source.resolve()) or
                           source.resolve().is_relative_to(args.output.resolve())):
                parser.error('Score output must be outside all inputs and dependencies')
        runtime = None
        if args.alce_full:
            if not args.alce_hf_cache or not args.alce_nltk_data:
                parser.error('--alce-full requires --alce-hf-cache and --alce-nltk-data')
            runtime = dict(python=str(args.alce_python.resolve()), hf_cache=args.alce_hf_cache,
                           nltk_data=args.alce_nltk_data, timeout=args.alce_timeout)
        elif args.alce_hf_cache or args.alce_nltk_data:
            parser.error('ALCE model resources require explicit --alce-full')
        bundle = load_bundle(args.bundle)
        submission = validate_submission(bundle, json.loads(args.submission.read_text()))
        result = score_prepared(bundle, submission, prepare_inputs(bundle, submission),
                                source_directory=args.sources, output_dir=args.output,
                                rouge_home=args.rouge_home, alce_runtime=runtime)
        print(json.dumps({'output': str(args.output), 'scope': result['scope'],
                          'coverage': result['coverage'], 'metrics': result['metrics'],
                          'pending_metrics': result['pending_metrics']}, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
