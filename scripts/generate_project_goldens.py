#!/usr/bin/env python3
import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from rag_eval.project import open_repository, load_chunks
from rag_eval.synthetic import generate_goldens


class ProjectModel:
    model = 'project-chat-workload'
    def __init__(self, repo):
        self.repo = repo
    def json(self, prompt):
        raw = self.repo.chat('chunk_question_generation').chat_json(
            [{'role': 'user', 'content': prompt}], {'items': 'array'}
        )
        return json.loads(raw)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--project-root', default=str(ROOT / 'project'))
    p.add_argument('--notebook-id', required=True)
    p.add_argument('--output', type=Path, required=True)
    p.add_argument('--chunks', type=int, default=20)
    p.add_argument('--count', type=int, default=10)
    args = p.parse_args()
    repo = open_repository(args.project_root)
    try:
        chunks = load_chunks(repo, args.notebook_id, args.chunks)
        rows, rejected = generate_goldens(chunks, ProjectModel(repo), args.output, args.count)
    finally:
        repo.close()
    args.output.with_suffix('.rejected.json').write_text(json.dumps(rejected, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({'accepted': len(rows), 'rejected': len(rejected), 'output': str(args.output)}, ensure_ascii=False))


if __name__ == '__main__':
    main()
