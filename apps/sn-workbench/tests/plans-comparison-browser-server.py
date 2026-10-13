"""Actual-App fixture with isolated canonical input and SQLite, no execution."""
import argparse
import copy
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

import uvicorn

sys.path.insert(0, str(Path('tests').resolve()))
from test_benchmark_comparison import examples
from rag_eval.identity import fingerprint
from rag_eval.benchmark_official import scorer_identity
from rag_eval.workbench.api import create_app
from rag_eval.workbench.ingest import import_result
from rag_eval.workbench.repository import WorkbenchRepository


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=18101)
    parser.add_argument('--static', type=Path, required=True)
    args = parser.parse_args()
    with TemporaryDirectory(prefix='sn-bc-browser-') as directory:
        root = Path(directory)
        bundle, entries, _, _ = examples(root)
        state = root / 'independent-state'
        repository = WorkbenchRepository(state)
        workspace = repository.register_workspace('Independent B/C fixture', root / 'dirty-workspace')
        (root / 'dirty-workspace').mkdir()
        (root / 'dirty-workspace' / 'unsaved-change.txt').write_text('Synthetic local edit')
        for name in ('Plan A', 'Plan B'):
            repository.create_task(workspace['id'], name, 'Drafts do not execute', request_key=name)
        task = repository.create_task(workspace['id'], 'Compare fixtures', 'Read two frozen results', request_key='compare')
        for entry in entries:
            imported = import_result(root / 'bundle', **entry, state_root=state)
            repository.attach_result(task['id'], imported['id'])
        # An independently valid score identity, intentionally incompatible
        # with the other side. No scoring function or model runs here.
        score = copy.deepcopy(json.loads(entries[1]['scores'].read_text()))
        score['dependencies']['python'] = 'synthetic-different-dependency'
        score['scorer_identity'] = scorer_identity(score['profile'], score['dependencies'])
        score['scorer_id'] = fingerprint(score['scorer_identity'])
        score.pop('scores_id')
        score['scores_id'] = fingerprint(score)
        rejected_score = root / 'different-scorer.json'
        rejected_score.write_text(json.dumps(score))
        rejected = repository.create_task(workspace['id'], 'Compare rejected', 'Different scorer identities', request_key='rejected')
        for entry in (entries[0], {'submission': entries[1]['submission'], 'scores': rejected_score}):
            imported = import_result(root / 'bundle', **entry, state_root=state)
            repository.attach_result(rejected['id'], imported['id'])
        config = {'workspace': {'title': workspace['title'], 'location': str(root / 'dirty-workspace')},
                  'inputs': [{'id': 'canonical', 'title': 'QMSum synthetic canonical',
                              'description': 'Two-case full canonical fixture, no scores executed',
                              'bundle': str(root / 'bundle'), 'bundle_id': fingerprint(bundle['manifest'])}]}
        app = create_app(state, config, actor='fixture-reviewer', access_token='bc-browser-fixture',
                         static_directory=args.static.resolve())
        uvicorn.run(app, host='127.0.0.1', port=args.port, log_level='warning', access_log=False)


if __name__ == '__main__':
    main()
