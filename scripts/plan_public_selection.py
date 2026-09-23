#!/usr/bin/env python3
"""Plan fixed notebook partitions from a verified local public selection."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.selection_bundle import load_selection_bundle
from rag_eval.selection_partitions import build_partition_plan


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True, help="Existing public-selection-v1 directory")
    parser.add_argument("--reviews", type=Path, help="Real suitability reviews keyed by sample ID; old three suites only")
    parser.add_argument("--max-documents", type=int, default=40, help="Per-partition document cap, 1..40; never a question quota")
    parser.add_argument("--output", type=Path, required=True, help="New immutable JSON file; existing paths rejected")
    args = parser.parse_args()
    selection = load_selection_bundle(args.bundle)
    reviews = json.loads(args.reviews.read_text(encoding="utf-8")) if args.reviews else None
    plan = build_partition_plan(selection["cases"], selection["native_source"], reviews, args.max_documents)
    serialized = json.dumps(plan, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as handle:
        handle.write(serialized)


if __name__ == "__main__":
    main()
