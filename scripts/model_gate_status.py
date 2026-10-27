#!/usr/bin/env python3
"""Show how full the server's model gate is right now (no model calls, never creates a gate).

The directory is ``<job_execution.output_root>/model-gate``. ``in_flight`` is the
number of requests holding a slot, ``waiting`` the requests queued for one.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))


def main():
    from rag_eval.model_gate import gate_status
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory', type=Path)
    args = parser.parse_args()
    print(json.dumps(gate_status(args.directory), indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
