#!/usr/bin/env python3
"""Prepare/execute the pinned LAB LongChat citation method on frozen QASPER."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.lab_qasper import prepare_lab


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for flag in ('bundle', 'upstream', 'test-itg', 'train-itg', 'output'):
        p.add_argument('--' + flag, type=Path, required=True)
    p.add_argument('--case-id-file', type=Path)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--model-path', type=Path, help='Local LongChat model/tokenizer snapshot; required to execute')
    p.add_argument('--method', type=Path, help='Explicit controlled-rerun method JSON; required to execute')
    p.add_argument('--nltk-data', type=Path, help='Local NLTK tokenizer resources; required to execute')
    p.add_argument('--device', default='cuda:0')
    args = p.parse_args()
    if not args.prepare_only and (not args.model_path or not args.method or not args.nltk_data):
        p.error('--model-path, --method and --nltk-data are required to execute')
    bundle, manifest = prepare_lab(args.bundle, args.upstream, args.test_itg, args.train_itg, args.output,
                                  case_ids=args.case_id_file.read_text().splitlines() if args.case_id_file else None)
    if args.prepare_only:
        print(json.dumps({'state': 'prepared', 'cases': len(manifest['case_ids']), 'output': str(args.output)}))
        return
    from rag_eval.lab_qasper_runtime import execute
    print(execute(bundle, manifest, args.output, args.model_path, args.device,
                  json.loads(args.method.read_text()), args.nltk_data))


if __name__ == '__main__':
    main()
