#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.deepeval_runner import evaluate_records, load_result_records
from rag_eval.project import open_repository
from rag_eval.project_judge import ProjectJudge


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--project-root', default=str(ROOT / 'project'))
    p.add_argument('--results', type=Path, required=True)
    args = p.parse_args()
    # The project adapter changes cwd while opening its repository; resolve
    # CLI paths before that side effect so relative paths remain usable.
    results_path = args.results.resolve()
    repo = open_repository(args.project_root)
    try:
        result = evaluate_records(load_result_records(results_path), model=ProjectJudge(repo))
        print(result)
    finally:
        repo.close()


if __name__ == '__main__':
    main()
