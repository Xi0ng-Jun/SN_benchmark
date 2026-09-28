#!/usr/bin/env python3
"""Prepare or run pinned ALCE VANILLA with observed top-five citation mappings."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.alce_vanilla import prepare_run, execute


def main():
    p = argparse.ArgumentParser(description=__doc__)
    for name in ('bundle', 'upstream', 'output'):
        p.add_argument('--' + name, type=Path, required=True)
    p.add_argument('--case-id-file', type=Path)
    p.add_argument('--prepare-only', action='store_true')
    p.add_argument('--model-path', type=Path, help='Local Llama model/tokenizer snapshot; execution only')
    p.add_argument('--method', type=Path, help='Explicit controlled-rerun method JSON; execution only')
    args = p.parse_args()
    if not args.prepare_only and (not args.model_path or not args.method):
        p.error('--model-path and --method required to execute')
    bundle, requests, prepared = prepare_run(args.bundle, args.upstream, args.output,
                                            case_ids=args.case_id_file.read_text().splitlines()
                                            if args.case_id_file else None)
    if args.prepare_only:
        print(json.dumps(dict(state='prepared', cases=len(requests), output=str(args.output))))
    else:
        print(execute(bundle, requests, prepared, args.output, args.model_path,
                      json.loads(args.method.read_text())))


if __name__ == '__main__':
    main()
