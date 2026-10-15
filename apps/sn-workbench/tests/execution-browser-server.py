"""Actual workbench with isolated source trees, canonical input and no-model execution."""
import argparse
import json
from pathlib import Path
import subprocess
import sys
from tempfile import TemporaryDirectory

import uvicorn

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / 'tests'))
from test_workbench_evaluation_plans import setup
from test_sn_source_capture import _repo
from rag_eval.bundle_index import install_bundle
from rag_eval.workbench.api import create_app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18109)
    parser.add_argument('--static', type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='sn-execution-browser-') as directory:
        root = Path(directory)
        repository, workspace, task, plans, _, command = setup(root)
        plans.save(**command)
        project = _repo(root)
        registrations = []
        for name, title in [('first', 'Baseline source'), ('second', 'Candidate source')]:
            worktree = root / name
            subprocess.run(['git', '-C', str(project), 'worktree', 'add', '-qb', name,
                            str(worktree), 'HEAD'], check=True)
            registrations.append(dict(id=name, title=title, path=str(worktree)))
        refs = install_bundle(root / 'data/bundle', root / 'artifacts')
        config = {
            'workspace': {'title': workspace['title'], 'location': str(root / 'workspace')},
            'inputs': [{'id': 'registered', 'title': 'Synthetic QASPER',
                        'bundle': str(root / 'data/bundle')}],
            'task_worktrees': {'project_root': str(project), 'artifact_root': str(root / 'artifacts'),
                              'registrations': registrations},
            'job_preparation': {'artifact_root': str(root / 'artifacts'),
                                'inputs': {'registered': {'bundle_ref': refs['bundle'], 'index_ref': refs['index']}}},
            'job_execution': {'mode': 'smoke', 'output_root': str(root / 'executions'),
                              'argv': [sys.executable, '-c',
                                       'import json,sys; from pathlib import Path; '
                                       'p=Path(sys.argv[1]); p.mkdir(); '
                                       '(p/"state.json").write_text(json.dumps(dict(phase="finished",terminal=True)))',
                                       '{run_dir}']},
        }
        app = create_app(repository.root, config, actor='execution-browser',
                         access_token='execution-browser-fixture', static_directory=args.static.resolve())
        print(json.dumps({'task_id': task['id'], 'port': args.port}), flush=True)
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
