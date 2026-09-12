#!/usr/bin/env python3
"""Execute ONE public starter N or R cell. This command can call online models."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from rag_eval.starter_protocol import SUITES
from rag_eval.public_expansion_protocol import EXPANSION_SUITES

ALL_SUITES = {**SUITES, **EXPANSION_SUITES}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--suite", choices=tuple(ALL_SUITES),
                        help="Optional explicit suite identity check for the frozen bundle")
    parser.add_argument("--run-dir", type=Path, required=True)
    parser.add_argument("--track", choices=("N", "R"), required=True)
    parser.add_argument("--mode", choices=("chunk", "reasoning"))
    parser.add_argument("--models", type=Path, required=True)
    parser.add_argument("--project-root", type=Path, default=ROOT.parent / "project")
    parser.add_argument("--reviews", type=Path)
    parser.add_argument("--instruction-audits", type=Path)
    args = parser.parse_args()
    for name, value in vars(args).items():
        if isinstance(value, Path):
            setattr(args, name, value.resolve())
    if args.run_dir.exists():
        parser.error("Use a new run directory; implicit resume is not supported")
    if args.suite:
        manifest_path = args.bundle / "manifest.json"
        if not manifest_path.exists():
            parser.error("--suite requires a frozen bundle manifest")
        import json
        if json.loads(manifest_path.read_text(encoding="utf-8")).get("suite") != args.suite:
            parser.error("--suite does not match the frozen bundle")
    from rag_eval.starter_runner import execute
    from rag_eval.starter_report import write_report
    code = 0
    try:
        execute(root=ROOT, project=args.project_root, bundle_dir=args.bundle, run=args.run_dir,
                track=args.track, mode=args.mode, models_path=args.models,
                reviews_path=args.reviews, audits_path=args.instruction_audits)
    except KeyboardInterrupt:
        code = 130
        print("Execution interrupted; saved artifacts retained", file=sys.stderr)
    except Exception as exc:
        code = 2
        # Do not print provider exception bodies or private endpoint values.
        print("Execution failed: " + type(exc).__name__ + "; inspect state/artifacts", file=sys.stderr)
    if (args.run_dir / "state.json").exists():
        try:
            report = write_report([args.run_dir], args.run_dir / "report")
            if any(r["state"]["phase"] != "finished" or r["warnings"] for r in report["runs"]):
                code = code or 2
            print(str(args.run_dir / "report/report.md"))
        except Exception as exc:
            print("Report creation failed: " + type(exc).__name__, file=sys.stderr)
            code = code or 2
    return code


if __name__ == "__main__":
    raise SystemExit(main())
