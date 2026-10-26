"""Task-scoped reads, drafts and recorded actions for workbench Agents.

Execution requests may run automatically under a frozen iteration budget and
trusted execution configuration; otherwise a person confirms in the workbench.
Implementers receive their own cluster projection and ordinal-only run results.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

REQUEST_COMMANDS = ('node derive', 'node freeze', 'node run', 'node fresh-run', 'node cluster-run', 'node adopt', 'node reject', 'iteration create', 'iteration sealed-run')
ANALYST_COMMANDS = ('task', 'runs', 'cases', 'case', 'execution-cases', 'flips', 'hypotheses', 'hypothesis-draft', 'docs', 'leak-check',
                    'iteration show', 'iteration ledger', 'node list', 'node show', 'compare', 'proposal draft', 'requests',
                    'request withdraw', 'cluster propose', 'cluster list', 'cluster show', 'cluster result', *REQUEST_COMMANDS)
IMPLEMENTER_COMMANDS = ('card', 'leak-check', 'node freeze', 'node run', 'node cluster-run', 'requests', 'request withdraw', 'cluster list', 'cluster show', 'cluster result')
GROUPS = ('iteration', 'node', 'proposal', 'request', 'cluster')


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
    views._command_config = config
    return repository, views


def iteration_service(repository, views, *, initialize=True):
    """The CLI's IterationService; it must see executions to know which candidates are evaluated."""
    from .comparisons import Admission
    from .iterations import IterationService
    from .job_execution import JobExecutionStore
    config = getattr(views, '_command_config', {})
    if initialize and config.get('task_worktrees', {}).get('create_candidates') is True:
        from .configuration import input_catalog
        from .evaluation_plans import PlanDraftStore
        from .plan_inputs import NotebookPlanInputs
        from .evaluation_jobs import EvaluationJobStore
        from .jobs_api import build_execution_spec
        plans = PlanDraftStore(repository, NotebookPlanInputs({k: e['bundle'] for k, e in input_catalog(config).items()}))
        return IterationService(repository, views._worktree_service, plans=plans, jobs=EvaluationJobStore(repository),
            executions=JobExecutionStore(repository), admission=Admission(views),
            preparation_config=config.get('job_preparation'), execution_config=config.get('job_execution'),
            build_spec=build_execution_spec)
    return IterationService(repository, views._worktree_service, executions=JobExecutionStore(repository),
                            execution_config=config.get('job_execution'),
                            admission=Admission(views), initialize=initialize)


def task_run_ids(views, task_id):
    """Every run of a Task; a summarized whole-benchmark execution expands to its partition runs."""
    ids = []
    for run in json.loads(views.task_runs_context(task_id)[1])['runs']:
        if run.get('kind') == 'execution':
            ids.extend(entry.id for entry in views.execution_runs(run['id']))
        else:
            ids.append(run['id'])
    return ids


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
    parser.add_argument('--candidate', help="the implementer's own iteration candidate")
    parser.add_argument('--node', help="the analyst's own node: drafted proposals hang under it")
    parser.add_argument('--parent', help='evaluated candidate a drafted proposal hangs under')
    parser.add_argument('--scope', choices=('iteration',), help="an iteration analyst: drafts name --parent; requests derive only")
    parser.add_argument('--integrate', help='node derive: comma-separated candidates folded into the child (merge derivation)')
    parser.add_argument('--method', choices=('merge', 'cherry-pick', 'rewrite'), default='merge',
                        help='node derive --integrate: how the folded candidates are brought in')
    parser.add_argument('--reason', help='why a person should confirm this request')
    parser.add_argument('--proposal', help='approved proposal a derive request starts from')
    parser.add_argument('--timeout', type=int, help='run request: timeout_seconds (default 3600)')
    parser.add_argument('--attempts', type=int, help='run request: number of attempts (1-4)')
    parser.add_argument('--limit-cny', type=float, help='run request: optional cost guard')
    parser.add_argument('--rank', choices=('root',), help='node list: flat list ranked by Δ根')
    parser.add_argument('command')
    parser.add_argument('args', nargs='*')
    parser.add_argument('--all', action='store_true')
    parser.add_argument('--needs-read', action='store_true')
    parser.add_argument('--status')
    parser.add_argument('--file')
    options = parser.parse_intermixed_args(argv)
    if options.command in GROUPS and options.args:
        options.command = options.command + ' ' + options.args.pop(0)
    allowed = IMPLEMENTER_COMMANDS if options.role == 'implementer' else ANALYST_COMMANDS
    if options.command not in allowed:
        parser.error(f'{options.role} commands: ' + ', '.join(allowed))
    if options.scope == 'iteration' and (options.node or options.role != 'analyst'):
        parser.error('an iteration analyst is not bound to a node')
    if options.scope == 'iteration' and options.command in REQUEST_COMMANDS and options.command != 'node derive':
        parser.error('an iteration analyst requests derivations only; freeze, run and decisions belong to a candidate')
    if options.integrate and options.command != 'node derive':
        parser.error('--integrate only applies to node derive')
    if options.command == 'hypothesis-draft' and options.parent:
        parser.error('a proposal under a candidate is `proposal draft --parent <candidate>`')
    from .hypotheses import HypothesisStore, leak_check
    repository, views = build(options.state, options.config)
    store = HypothesisStore(repository)
    task_id, a = options.task, options.args
    command = options.command
    from .splits import Policy
    policy = Policy(repository, task_id)
    if command == 'case':
        policy.require_search(a[1])
    if command.startswith('cluster '):
        from .clusters import ClusterStore
        service = iteration_service(repository, views)
        clusters = ClusterStore(repository, service, views)
        own = options.candidate if options.role == 'implementer' else None
        if options.role == 'implementer' and not own:
            raise SystemExit('cluster evidence needs the implementer own --candidate')
        if command == 'cluster result':
            from .comparisons import ComparisonService
            source = own or options.node
            if not source or service.iteration(service.candidate(source)['iteration_id'])['task_id'] != task_id:
                raise ValueError('Cluster result needs a candidate of this task')
            out = ComparisonService(repository, service, views).for_cluster(source, a[0], actor='agent:' + options.role,
                                                                          implementer=own is not None)
        elif command == 'cluster list':
            out = clusters.list(task_id, implementer_candidate=own)
        elif command == 'cluster show':
            out = clusters.show(task_id, a[0], implementer_candidate=own)
        else:
            if not options.file:
                raise SystemExit('cluster propose needs --file')
            card = json.loads(Path(options.file).read_text(encoding='utf-8'))
            source = card.pop('candidate_id', None) or options.node or options.parent
            if not source or (options.node and source != options.node):
                raise SystemExit('cluster propose needs its source candidate (--node or file candidate_id)')
            if service.iteration(service.candidate(source)['iteration_id'])['task_id'] != task_id:
                raise SystemExit('Cluster source belongs to another task')
            out = clusters.propose(source, card, actor='agent:analyst')
    elif command == 'task':
        task = repository.task(task_id)
        runs = json.loads(views.task_runs_context(task_id)[1])
        out = dict(task={k: task[k] for k in ('id', 'title', 'goal')}, worktree=runs['worktree'], runs=runs['runs'],
                   hypotheses=[_card_summary(c) for c in store.list(task_id)])
        iteration = _iteration_summary(repository, views, task_id, task_run_ids(views, task_id))
        if iteration is not None:
            out['iteration'] = iteration
    elif command == 'runs':
        out = views.list_runs() if options.all else json.loads(views.task_runs_context(task_id)[1])
    elif command == 'cases':
        out = views.cases(a[0], status=options.status, needs_read=True if options.needs_read else None)
    elif command == 'case':
        out = views.case(a[0], a[1])
    elif command == 'execution-cases':
        out = views.execution_cases(a[0])
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
        out = _draft_leak(views, task_id, card) or dict(
            saved=True, card=store.draft(task_id, card, author='agent:analyst'),
            next='a person approves or rejects it on the Changes page')
    elif command == 'proposal draft':
        if options.node and options.parent and options.parent != options.node:
            raise SystemExit(f'this analyst is bound to node {options.node}; propose under another node '
                             'from an analyst bound to that node')
        parent = options.parent or options.node
        if not parent or not options.file:
            raise SystemExit('proposal draft needs --parent <candidate> and --file card.json')
        tools = IterationTools(repository, views, task_id, options.role)
        tools.own(parent)
        card = json.loads(Path(options.file).read_text(encoding='utf-8'))
        out = _draft_leak(views, task_id, card) or dict(
            saved=True, card=tools.service.draft_proposal(parent, card, author='agent:analyst'),
            next='a person approves or rejects this proposal')
    elif command in ('iteration show', 'iteration ledger', 'node list', 'node show', 'compare', 'requests', 'request withdraw',
                     *REQUEST_COMMANDS):
        out = IterationTools(repository, views, task_id, options.role).run(command, a, options)
    elif command == 'card':
        card = store.get(task_id, options.hypothesis)
        if card['status'] not in ('approved', 'implementing', 'verified', 'concluded'):
            raise SystemExit('card is not approved')
        out = {k: card['card'].get(k) for k in ('title', 'component', 'mechanism', 'implementer_brief',
                                                'possible_harm', 'change_scope')}
        from .clusters import referenced_cluster
        if referenced_cluster(card['card']):
            out['cluster_id'] = referenced_cluster(card['card'])
    else:  # leak-check
        out = leak_check(views, task_id, task_run_ids(views, task_id), hypothesis_id=options.hypothesis,
                         candidate_id=options.candidate)
        if options.role == 'implementer':
            out['findings'] = [dict(kind=f['kind'], source=f['source']) for f in out['findings']]
            out.pop('runs', None)
    if options.role == 'analyst' and command != 'iteration ledger':
        out = policy.project(out)
    json.dump(out, sys.stdout, ensure_ascii=False, indent=1, default=str)
    sys.stdout.write('\n')


def _draft_leak(views, task_id, card):
    """A refusal when implementer-facing fields overlap benchmark text, else None."""
    from .hypotheses import leak_check
    leak = leak_check(views, task_id, task_run_ids(views, task_id),
                      extra_texts=[('implementer_brief', str(card.get('implementer_brief', ''))),
                                   ('mechanism', str(card.get('mechanism', ''))),
                                   ('change_scope', '\n'.join(map(str, card.get('change_scope', []))))])
    findings = [f for f in leak['findings'] if f['source'] in ('implementer_brief', 'mechanism', 'change_scope')]
    if findings:
        return dict(saved=False, reason='implementer-facing fields overlap benchmark text; rewrite them',
                    findings=findings)
    return None


class IterationTools:
    """Iteration verbs scoped to the iteration this Task backs (design §8)."""

    def __init__(self, repository, views, task_id, role):
        from .action_requests import ActionRequestStore
        from .comparisons import ComparisonService
        self.service = iteration_service(repository, views)
        self.views = views
        self.comparisons = ComparisonService(repository, self.service, views)
        self.requests = ActionRequestStore(repository, execution_config=self.service.execution_config)
        self.task_id, self.role, self.actor = task_id, role, 'agent:' + role
        found = [i for i in self.service.iterations() if i['task_id'] == task_id]
        self.iteration_id = found[0]['id'] if found else None

    def tree(self):
        if self.iteration_id is None:
            raise SystemExit('this Task backs no iteration')
        from .comparisons import summary
        tree = self.service.iteration(self.iteration_id)
        for candidate in tree['candidates']:
            both = self.comparisons.for_candidate(candidate['id'], actor=self.actor, iteration=tree)
            candidate['comparisons'] = dict(parent=summary(both['parent']), root=summary(both['root']))
        return tree

    def own(self, candidate_id):
        candidate = self.service.candidate(candidate_id)
        if candidate['iteration_id'] != self.iteration_id:
            raise SystemExit("the candidate belongs to another iteration, not this Task's")
        return candidate

    def run(self, command, args, options):
        if command == 'iteration ledger':
            if args and args[0] != self.iteration_id:
                raise SystemExit("only this Task's iteration is visible here")
            from .ledger import LedgerService
            from .sealed import SealedService
            score = SealedService(self.service.repository, self.service, self.views).score(self.iteration_id)
            return LedgerService(self.service.repository, self.service, self.views).read(self.iteration_id, sealed_score=score)
        if command == 'iteration show':
            if args and args[0] != self.iteration_id:
                raise SystemExit("only this Task's iteration is visible here")
            return self.tree()
        if command == 'node list':
            return self.node_list(rank=options.rank)
        if command == 'node show':
            candidate = self.own(args[0])
            return dict(candidate=candidate, comparisons=self.comparisons.for_candidate(args[0], actor=self.actor),
                        requests=[r for r in self.requests.list(task_id=self.task_id) if r['target_id'] == args[0]])
        if command == 'compare':
            return self.compare(*args[:2])
        if command == 'request withdraw':
            record = self.requests.get(args[0]) if args else None
            if record is None or record['task_id'] != self.task_id or (
                    self.role == 'implementer' and record['target_id'] != options.candidate):
                raise SystemExit('only a request of this Task (an implementer: of its own candidate) can be withdrawn')
            return self.requests.withdraw(args[0], actor=self.actor, note=options.reason or '')
        if command == 'requests':
            rows = self.requests.list(task_id=self.task_id, status=options.status)
            if self.role == 'implementer':
                rows = [r for r in rows if r['target_id'] == options.candidate]
            return rows
        return self.request(command, args, options)

    def node_list(self, *, rank=None):
        tree = self.tree()
        rows = [dict(id=c['id'], parent=c['parent_candidate_id'], depth=c['depth'], state=c['state'],
                     frozen_commit=c['frozen_commit'], proposal_id=c['proposal_id'],
                     decision=(c['decision'] or {}).get('decision'), reference=c['reference'],
                     parent_delta=c['comparisons']['parent'], root_delta=c['comparisons']['root'])
                for c in tree['candidates']]
        if rank == 'root':
            def delta(row):
                if row['id'] == tree['root_candidate_id']:
                    return 0.0
                side = row['parent_delta'] if row['root_delta']['status'] == 'same_as_parent' else row['root_delta']
                return side.get('mean_delta')
            rows = sorted(rows, key=lambda row: (delta(row) is None, -(delta(row) or 0.0)))
            for row in rows:
                row['rank_delta'] = delta(row)
        return dict(iteration_id=tree['id'], root_candidate_id=tree['root_candidate_id'],
                    order='rank by Δ根 (flat)' if rank else 'tree', nodes=rows)

    def compare(self, subject_id, reference_id):
        subject = self.own(subject_id)
        self.own(reference_id)
        root_id = self.service.iteration(self.iteration_id)['root_candidate_id']
        if reference_id in (subject['parent_candidate_id'], root_id) and subject_id != root_id:
            both = self.comparisons.for_candidate(subject_id, actor=self.actor)
            side = both['parent'] if reference_id == subject['parent_candidate_id'] else both['root']
            if side.get('status') == 'same_as_parent':
                side = both['parent']
            return side
        return self.comparisons.pinned(subject_id, reference_id)

    def request(self, command, args, options):
        verb = command.replace(' ', '-')
        params, target = {}, None
        if verb == 'iteration-create':
            if not options.file:
                raise SystemExit('iteration create needs --file with ' + ', '.join(
                    ('title', 'start_commit', 'input_id', 'expected_bundle_id', 'case_ids', 'mode')))
            params = json.loads(Path(options.file).read_text(encoding='utf-8'))
        else:
            if not args:
                raise SystemExit(f'{command} needs a candidate id')
            target = self.own(args[0])['id']
            if self.role == 'implementer' and target != options.candidate:
                raise SystemExit('an implementer may only request actions on its own --candidate')
            if verb == 'node-derive' and options.proposal:
                params['proposal_id'] = options.proposal
            if verb == 'node-derive' and options.integrate:
                folded = [self.own(i.strip())['id'] for i in options.integrate.split(',') if i.strip()]
                if target in folded:
                    raise SystemExit('a candidate is not folded into its own child')
                params['integrated_from'] = [dict(candidate_id=i, method=options.method) for i in folded]
            if verb == 'node-cluster-run':
                from .clusters import candidate_cluster
                if len(args) != 2 or candidate_cluster(self.service.repository, self.task_id, target) != args[1]:
                    raise ValueError('cluster-run requires the candidate own cluster')
                params['cluster_id'] = args[1]
            if verb in ('node-run', 'node-fresh-run', 'node-cluster-run', 'iteration-sealed-run'):
                if options.attempts is not None and verb == 'node-run':
                    params['attempts'] = options.attempts
                if options.timeout is not None:
                    params['timeout_seconds'] = options.timeout
                if options.limit_cny is not None:
                    params['limit_cny'] = options.limit_cny
        record = self.requests.request(self.task_id, verb, target, params, reason=options.reason or '',
                                       requested_by=self.actor)
        from .action_requests import auto_execute
        record = auto_execute(self.service, record, workspace_id=self.service.repository.task(self.task_id)['workspace_id'])
        return dict(request=record, next='Request executed under budget policy' if record['status'] == 'executed'
                    else 'nothing has run: a person reviews this request in the workbench' if record['status'] == 'pending'
                    else 'Inspect the recorded execution outcome')


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
