#!/usr/bin/env python3
"""Prepare or execute a disclosed KG2RAG adaptation on frozen HotpotQA cases."""
import argparse
import json
from pathlib import Path
import sys
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.kg2rag_runner import prepare_run, execute_prepared


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument('--bundle', type=Path, required=True)
    p.add_argument('--upstream', type=Path, required=True, help='Pinned KG2RAG checkout root')
    p.add_argument('--output', type=Path, required=True, help='New run directory')
    p.add_argument('--demonstrations', type=Path, default=Path(__file__).resolve().parents[1]
                   / 'configs/hotpot-kg2rag-train-demonstrations.json')
    p.add_argument('--case-id-file', type=Path)
    p.add_argument('--prepare-only', action='store_true', help='Write inputs and adapted code without model imports')
    p.add_argument('--method', type=Path, help='Explicit public method and actual model identities; required to execute')
    p.add_argument('--model-name', default='llama3:8b')
    p.add_argument('--embed-model-name', default='mxbai-embed-large')
    p.add_argument('--reranker', help='Local reranker model; required to execute')
    p.add_argument('--reranker-device', default='cuda:0')
    p.add_argument('--top-k', type=int, default=10)
    p.add_argument('--use-tpt', action='store_true')
    args = p.parse_args()
    if not args.prepare_only and (not args.method or not args.reranker):
        p.error('--method and --reranker are required to execute')
    if args.top_k < 1:
        p.error('--top-k must be positive')
    case_ids = args.case_id_file.read_text().splitlines() if args.case_id_file else None
    bundle, samples, manifest = prepare_run(args.bundle, args.upstream, args.demonstrations,
                                             args.output, case_ids=case_ids)
    if args.prepare_only:
        print(json.dumps({'state': 'prepared', 'cases': len(samples), 'output': str(args.output)}))
        return
    runtime = SimpleNamespace(dataset='hotpotqa', model_name=args.model_name,
                              embed_model_name=args.embed_model_name, reranker=args.reranker,
                              reranker_device=args.reranker_device, top_k=args.top_k, use_tpt=args.use_tpt)
    print(execute_prepared(bundle, samples, manifest, args.output, runtime,
                          json.loads(args.method.read_text())))


if __name__ == '__main__':
    main()
