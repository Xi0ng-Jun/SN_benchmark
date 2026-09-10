from __future__ import annotations

import argparse
import json
from pathlib import Path

from .deepeval_runner import evaluate_records, load_result_records


def main() -> None:
    parser = argparse.ArgumentParser(description="Run DeepEval native RAG metrics")
    parser.add_argument("results", type=Path, help="adapter result JSONL")
    parser.add_argument("--output", type=Path, help="optional JSON summary path")
    args = parser.parse_args()
    result = evaluate_records(load_result_records(args.results))
    summary = {
        "result": str(result),
        "results_file": str(args.results),
    }
    rendered = json.dumps(summary, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
