"""Read tools (and the hypothesis-draft write) for Agents working on a Task.

Run through `scripts/sn-eval`. Every command prints JSON. None of them starts
SN, calls a model or changes a run; `hypothesis-draft` only stores a draft that
a person must approve. `--role implementer` limits the commands to the approved
card and the leak check, and strips case ids from leak findings.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ANALYST_COMMANDS = ('task', 'runs', 'cases', 'case', 'flips', 'hypotheses', 'hypothesis-draft', 'docs', 'leak-check')
IMPLEMENTER_COMMANDS = ('card', 'leak-check')


def build(state, config_path):
    from .configuration import load_config, task_worktree_services
    from .job_execution import JobExecutionStore
    from .repository import WorkbenchRepository
    from .run_views import RunViews
    # Same loader as `serve`: relative paths resolve against the config file.
    config = load_config(Path(config_path))
    repository = WorkbenchRepository(state)
    worktree_service, _ = task_worktree_services(repository, config)
    workspace = repository.register_workspace(config['workspace']['title'], config['workspace']['location'])
    views = RunViews.from_config(config.get('run_views'), base=Path.cwd(), worktree_service=worktree_service,
                                 execution_enabled=isinstance(config.get('job_execution'), dict),
                                 execution_config=config.get('job_execution'),
                                 execution_records=lambda: JobExecutionStore(repository).records(workspace['id']))
    return repository, views


def iteration_service(repository, views):
    """The CLI's IterationService; it must see executions to know which candidates are evaluated."""
    from .iterations import IterationService
    from .job_execution import JobExecutionStore
    return IterationService(repository, views._worktree_service, executions=JobExecutionStore(repository))


def task_run_ids(views, task_id):
    content = json.loads(views.task_runs_context(task_id)[1])
    return [run['id'] for run in content['runs']]


def main(argv=None):
    parser = argparse.ArgumentParser(prog='sn-eval', description=__doc__)
    local = Path('configs/sn-workbench-local.json').is_file()
    parser.add_argument('--state', default=os.environ.get('SN_WORKBENCH_STATE',
                        'var/sn-workbench' if local else 'var/sn-workbench-server/state'))
    parser.add_argument('--config', default=os.environ.get('SN_WORKBENCH_CONFIG',
                        'configs/sn-workbench-local.json' if local else 'var/sn-workbench-server/config.json'))
    parser.add_argument('--task', required=True)
    parser.add_argument('--role', choices=('analyst', 'implementer'), default='analyst')
    parser.add_argument('--hypothesis')
    parser.add_argument('--candidate', help='iteration candidate (implementer leak-check)')
    parser.add_argument('--parent', help='evaluated candidate a drafted proposal hangs under')
    parser.add_argument('command')
    parser.add_argument('args', nargs='*')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--needs-read', action='store_true')
    parser.add_argument('--status')
    parser.add_argument('--file')
    options = parser.parse_intermixed_args(argv)
    allowed = IMPLEMENTER_COMMANDS if options.role == 'implementer' else ANALYST_COMMANDS
    if options.command not in allowed:
        parser.error(f'{options.role} commands: ' + ', '.join(allowed))
    from .hypotheses import HypothesisStore, leak_check
    repository, views = build(options.state, options.config)
    store = HypothesisStore(repository)
    task_id, a = options.task, options.args
    command = options.command
    if command == 'task':
        task = repository.task(task_id)
        runs = json.loads(views.task_runs_context(task_id)[1])
        out = dict(task={k: task[k] for k in ('id', 'title', 'goal')}, worktree=runs['worktree'], runs=runs['runs'],
                   hypotheses=[_card_summary(c) for c in store.list(task_id)])
        iteration = _iteration_summary(repository, views, task_id, [run['id'] for run in runs['runs']])
        if iteration is not None:
            out['iteration'] = iteration
    elif command == 'runs':
        out = views.list_runs() if options.all else json.loads(views.task_runs_context(task_id)[1])
    elif command == 'cases':
        out = views.cases(a[0], status=options.status, needs_read=True if options.needs_read else None)
    elif command == 'case':
        out = views.case(a[0], a[1])
    elif command == 'flips':
        out = views.flips(a[0], a[1])
    elif command == 'hypotheses':
        out = store.list(task_id)
    elif command == 'docs':
        from .briefs import ANALYST_DOCS
        root = Path.cwd()
        out = [dict(path=str(root / path), purpose=note) for path, note in ANALYST_DOCS]
    elif command == 'hypothesis-draft':
        card = json.loads(Path(options.file).read_text(encoding='utf-8'))
        leak = leak_check(views, task_id, task_run_ids(views, task_id),
                          extra_texts=[('implementer_brief', str(card.get('implementer_brief', ''))),
                                       ('mechanism', str(card.get('mechanism', ''))),
                                       ('change_scope', '\n'.join(map(str, card.get('change_scope', []))))])
        leak['findings'] = [f for f in leak['findings'] if f['source'] in ('implementer_brief', 'mechanism', 'change_scope')]
        if leak['findings']:
            out = dict(saved=False, reason='implementer-facing fields overlap benchmark text; rewrite them',
                       findings=leak['findings'])
        elif options.parent:
            saved = iteration_service(repository, views).draft_proposal(options.parent, card, author='agent:analyst')
            if saved['task_id'] != task_id:
                raise SystemExit('the parent candidate belongs to another iteration Task')
            out = dict(saved=True, card=saved, next='a person approves or rejects this proposal')
        else:
            out = dict(saved=True, card=store.draft(task_id, card, author='agent:analyst'),
                       next='a person approves or rejects it on the Changes page')
    elif command == 'card':
        card = store.get(task_id, options.hypothesis)
        if card['status'] not in ('approved', 'implementing', 'verified', 'concluded'):
            raise SystemExit('card is not approved')
        out = {k: card['card'].get(k) for k in ('title', 'component', 'mechanism', 'implementer_brief',
                                                'possible_harm', 'change_scope')}
    else:  # leak-check
        out = leak_check(views, task_id, task_run_ids(views, task_id), hypothesis_id=options.hypothesis,
                         candidate_id=options.candidate)
        if options.role == 'implementer':
            out['findings'] = [dict(kind=f['kind'], source=f['source']) for f in out['findings']]
            out.pop('runs', None)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write('\n')


def _iteration_summary(repository, views, task_id, run_ids):
    """Candidates of the iteration this Task backs, with the run ids of their attempts."""
    service = iteration_service(repository, views)
    found = [i for i in service.iterations() if i['task_id'] == task_id]
    if not found:
        return None
    tree = service.iteration(found[0]['id'])
    owned = lambda executions: [r for r in run_ids if r.rsplit('-', 1)[0] in executions]
    return dict(id=tree['id'], origin=tree['origin'], start_commit=tree['start_commit'],
                unknown=tree['unknown'], candidates=[dict(
                    id=c['id'], parent=c['parent_candidate_id'], depth=c['depth'], state=c['state'],
                    frozen_commit=c['frozen_commit'], proposal_id=c['proposal_id'],
                    runs=owned({a['execution_id'] for a in c['attempts']} | {l['execution_id'] for l in c['links']}))
                    for c in tree['candidates']],
                unassigned_runs=owned(set(tree['unassigned_executions'])))


def _card_summary(record):
    verification = record.get('verification') or {}
    return dict(id=record['id'], status=record['status'], title=record['card']['title'],
                predictions=len(record['card']['predictions']),
                held=verification.get('held'), decided=verification.get('decided'),
                conclusion=record.get('conclusion'))


if __name__ == '__main__':
    main()
