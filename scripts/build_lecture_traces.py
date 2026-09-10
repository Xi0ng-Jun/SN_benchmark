"""Generate CS336-style edtrace JSON files for the executable lectures."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("modules", nargs="*", default=["lecture_01", "lecture_02", "lecture_03", "lecture_04"])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    lectures = root / "lectures"
    output = root / "var" / "traces"
    output.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["PYTHONPATH"] = str(lectures) + os.pathsep + str(root / "src")
    for module in args.modules:
        subprocess.run(
            [sys.executable, "-m", "edtrace.execute", "-m", module, "-o", str(output)],
            cwd=lectures,
            env=env,
            check=True,
        )
        print(f"wrote {output / (module + '.json')}")


if __name__ == "__main__":
    main()
