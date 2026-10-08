#!/usr/bin/env python3
"""Package allowlisted campaign results without modifying saved runs."""
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rag_eval.result_package import main


if __name__ == '__main__':
    raise SystemExit(main())
