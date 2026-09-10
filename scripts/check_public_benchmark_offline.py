#!/usr/bin/env python3
"""Run public benchmark checks that require neither product nor judge model calls."""
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    commands = [
        [str(ROOT / ".venv/bin/python"), "-m", "pytest", "-q"],
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/prepare_public_benchmark.py"), "--help"],
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/report_public_benchmark.py"), "--help"],
        [str(ROOT / ".venv/bin/python"), str(ROOT / "scripts/audit_public_benchmark.py"), "--help"],
    ]
    for command in commands:
        result = subprocess.run(command, cwd=ROOT)
        if result.returncode:
            return result.returncode
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
