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
    external = commands.add_parser('import-external',
                                   help='Import raw external predictions through an explicit case map')
    external.add_argument('--bundle', type=Path, required=True)
    external.add_argument('--source', type=Path, required=True,
                          help='Directory containing exactly one predictions JSON/JSONL source')
    external.add_argument('--method', type=Path, required=True)
    external.add_argument('--case-map', type=Path, required=True)
    external.add_argument('--case-ids', nargs='+',
                          help='Declare a complete external subset; omit for the full frozen bundle')
    external.add_argument('--output', type=Path, required=True)
    for scoped_command in (export, ingest, external):
        scoped_command.add_argument('--case-id-file', type=Path,
                                    help='One frozen case ID per line; combined with --case-ids if supplied')
    score = commands.add_parser('score', help='Score frozen submission; full ALCE needs explicit offline model runtime')
    score.add_argument('--bundle', type=Path, required=True)
    score.add_argument('--submission', type=Path, required=True)
    score.add_argument('--sources', type=Path, required=True)
    score.add_argument('--output', type=Path, required=True)
    score.add_argument('--rouge-home', type=Path)
    alce_modes = score.add_mutually_exclusive_group()
    alce_modes.add_argument('--alce-full', action='store_true', help='All official answer and citation metrics')
    alce_modes.add_argument('--alce-answer-only', action='store_true',
                            help='Official answer model metrics; citation metrics stay pending')
    score.add_argument('--alce-python', type=Path, default=Path(sys.executable))
    score.add_argument('--alce-hf-cache', type=Path)
    score.add_argument('--alce-nltk-data', type=Path)
    score.add_argument('--alce-timeout', type=float, default=3600)
    args = parser.parse_args(argv)
    if getattr(args, 'case_id_file', None) is not None:
        from rag_eval.notebook_runner import load_case_ids_file
        try:
            args.case_ids = list(args.case_ids or []) + load_case_ids_file(args.case_id_file)
        except ValueError as exc:
            parser.error(str(exc))
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
    elif args.command == 'import-external':
        from rag_eval.external_submission_import import import_external_predictions
        print(import_external_predictions(args.bundle, args.source,
                                          json.loads(args.method.read_text()),
                                          case_map=args.case_map, case_ids=args.case_ids,
                                          output_dir=args.output))
    else:
        from rag_eval.notebook_bundle import load_bundle
        from rag_eval.benchmark_submission import validate_submission
        for source in [args.bundle, args.submission, args.sources, args.rouge_home,
                       args.alce_hf_cache, args.alce_nltk_data]:
            if source and (args.output.resolve().is_relative_to(source.resolve()) or
                           source.resolve().is_relative_to(args.output.resolve())):
                parser.error('Score output must be outside all inputs and dependencies')
        runtime = None
        if args.alce_full or args.alce_answer_only:
            if not args.alce_hf_cache or not args.alce_nltk_data:
                parser.error('ALCE model scoring requires --alce-hf-cache and --alce-nltk-data')
            runtime = dict(python=str(args.alce_python.resolve()), hf_cache=args.alce_hf_cache,
                           nltk_data=args.alce_nltk_data, timeout=args.alce_timeout,
                           scoring_mode='answer-only' if args.alce_answer_only else 'full')
        elif args.alce_hf_cache or args.alce_nltk_data:
            parser.error('ALCE model resources require explicit --alce-full or --alce-answer-only')
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
