from __future__ import annotations

import argparse
import json
from pathlib import Path

from .datasets import load_dataset
from .metrics import evidence_recall_at_k, hit_at_k, mean_reciprocal_rank, ndcg_at_k


def main() -> None:
    parser = argparse.ArgumentParser(description="Run deterministic RAG benchmark metrics")
    parser.add_argument("dataset", type=Path, help="dataset full directory")
    parser.add_argument("--k", type=int, action="append", default=[1, 5, 10])
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rows = load_dataset(args.dataset)
    if args.limit:
        rows = rows[: args.limit]
    report = {"dataset": rows[0]["dataset"] if rows else args.dataset.parent.name, "samples": len(rows), "metrics": {}}
    for k in sorted(set(args.k)):
        # Dataset-only mode has no system ranking; this output is a schema check.
        report["metrics"][f"k={k}"] = {
            "hit_at_k": None,
            "evidence_recall_at_k": None,
            "mrr": None,
            "ndcg": None,
            "note": "Provide adapter results with retrieved_ids to calculate retrieval scores.",
        }
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    if args.output:
        args.output.write_text(rendered + "\n", encoding="utf-8")
    else:
        print(rendered)


if __name__ == "__main__":
    main()
