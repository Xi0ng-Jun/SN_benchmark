"""Actual App + read-only run views over the real E5 run, in an isolated state.

Never touches var/sn-workbench, the preview, SN or any model. The temporary
SQLite state holds only two tasks. The run, its official scores, the bundle
object it consumed (as plan input) and the SN worktree are read in place.
"""
import argparse
from pathlib import Path
from tempfile import TemporaryDirectory

from fastapi.staticfiles import StaticFiles
import uvicorn

from rag_eval.workbench.api import create_app
from rag_eval.workbench.repository import WorkbenchRepository
from rag_eval.workbench.run_views import RunEntry, RunViews, WorktreeEntry

ROOT = Path(__file__).resolve().parents[3]
EVIDENCE = ROOT / 'apps/sn-workbench/test-results/source-execution-20261012'
RUN = EVIDENCE / 'e5-s3-real-run-20261013'
BUNDLE = EVIDENCE / 'e5-preparation/store/objects/bundle/b83a31aca12c311d92e5cccb1e29a6637bed6fa2321b289e364748c12d3c0f81'
WORKTREE = Path('/home/wabiwabi/.paseo/worktrees/31u90a1x/sn-eval-exp-1')
BASE = '0680aa12313a5d3e67f4e0a4be94144a5b6af28c'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18111)
    parser.add_argument('--static', type=Path, required=True)
    parser.add_argument('--token', default='run-views-browser-fixture')
    args = parser.parse_args()
    with TemporaryDirectory(prefix='sn-run-views-browser-') as directory:
        state = Path(directory) / 'independent-state'
        repository = WorkbenchRepository(state)
        workspace = repository.register_workspace('SN 评测研发（独立 fixture）', str(ROOT))
        bound = repository.create_task(workspace['id'], '核查 QASPER 单题链路', '读 E5 单题 run 的错题证据，决定下一条假设',
                                       request_key='bound')
        repository.create_task(workspace['id'], '未绑定 worktree 的任务', '用于对照侧栏状态', request_key='unbound')
        config = {'workspace': {'title': workspace['title'], 'location': str(ROOT)},
                  'inputs': [{'id': 'qasper-input', 'title': 'QASPER 完整输入（E5 run 使用的 bundle）',
                              'description': '1451 题 / 416 分区；保存计划不会运行评测', 'bundle': str(BUNDLE)}]}
        config['run_views'] = {
            'runs': [{'id': 'e5-attempt-1', 'run_dir': str(RUN / 'attempt-1/cli-run'),
                      'scores': str(RUN / 'official/scores/scores.json'), 'title': 'E5 单题 attempt-1', 'task_id': bound['id']}],
            'worktrees': [{'id': 'sn-eval-exp-1', 'path': str(WORKTREE), 'branch': 'sn-eval-exp-1',
                           'base': BASE, 'task_id': bound['id'], 'paseo_workspace_id': 'wks_fc0534ddd56f1261'}],
            'ledger': str(ROOT / 'var/sn-eval/runs.jsonl')}
        app = create_app(state, config, actor='fixture-reviewer', access_token=args.token)
        app.mount('/', StaticFiles(directory=args.static.resolve(), html=True), name='workbench')
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
