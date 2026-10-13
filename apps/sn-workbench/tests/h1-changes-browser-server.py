"""Actual App over the H1 task binding receipt and its trusted config. Read-only use.

Uses the receipt's independent /tmp state (never var/sn-workbench) and a token
passed via WORKBENCH_TOKEN; prints neither. Task binding, hypotheses and run
association come from the M2-C service and the trusted config, not from HEAD.
"""
import argparse
import json
import os
from pathlib import Path

from fastapi.staticfiles import StaticFiles
import uvicorn

from rag_eval.workbench.api import create_app
from rag_eval.workbench.configuration import load_config

H1 = Path(__file__).resolve().parents[1] / 'test-results/r1-launch-20261013/h1'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, required=True)
    parser.add_argument('--static', type=Path, required=True)
    args = parser.parse_args()
    receipt = json.loads((H1 / 'task-binding.json').read_text())
    state = Path(receipt['state'])
    if not str(state).startswith('/tmp/') or 'var/sn-workbench' in str(state):
        raise SystemExit('refusing non-isolated state')
    config = load_config(H1 / 'workbench-config.json')
    app = create_app(state, config, actor='h1-ui-reviewer', access_token=os.environ['WORKBENCH_TOKEN'])
    app.mount('/', StaticFiles(directory=args.static.resolve(), html=True), name='workbench')
    uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
