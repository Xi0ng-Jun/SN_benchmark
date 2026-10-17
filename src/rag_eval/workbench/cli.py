"""Local workbench CLI; imports and serves without SN or model execution."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .ingest import import_result
from .repository import WorkbenchRepository
from .configuration import input_catalog, load_config


_JOB_COMMANDS = {'job-prepare', 'job-cancel', 'job-list', 'job-show', 'job-receipt',
                 'job-run', 'job-status', 'job-execution-status', 'job-execution-cancel'}


def _original_job_key(parser):
    # Early child-parser errors have no completed namespace. Reuse argparse's
    # own option matcher, including accepted abbreviations and equals syntax;
    # repeated string options use the final value just as normal parsing does.
    key = None
    for index, argument in enumerate(sys.argv[1:], start=1):
        if not argument.startswith('--'):
            continue
        matches = parser._get_option_tuples(argument)
        if len(matches) != 1 or matches[0][0].dest != 'request_key':
            continue
        _, equals, explicit = argument.partition('=')
        if equals:
            key = explicit
        elif index + 1 < len(sys.argv):
            try:
                is_value = parser._parse_optional(sys.argv[index + 1]) is None
            except argparse.ArgumentError:
                is_value = False
            if is_value:
                key = sys.argv[index + 1]
    return key


class _JobParser(argparse.ArgumentParser):
    """Job parsing omits rejected argv values; existing commands retain behavior."""
    def parse_args(self, args=None, namespace=None):
        parsed, unknown = self.parse_known_args(args, namespace)
        if unknown:
            self._selected_job = getattr(parsed, 'command', None)
            self._parsed_job_key = getattr(parsed, 'request_key', None)
            self.error('unrecognized arguments: ' + ' '.join(unknown))
        return parsed

    def error(self, message):
        command = getattr(self, '_selected_job', None) or self.prog.rsplit(' ', 1)[-1]
        if command in _JOB_COMMANDS:
            print(json.dumps({'error': 'Invalid Job command', 'code': 'JOB_COMMAND_INVALID',
                              'operation': command.removeprefix('job-'),
                              'request_key': self._parsed_job_key if hasattr(self, '_parsed_job_key')
                              else _original_job_key(self)}), file=sys.stderr)
            raise SystemExit(2) from None
        super().error(message)


def main():
    parser = _JobParser(description=__doc__)
    parser.add_argument('--state', type=Path, default=Path('var/sn-workbench'))
    commands = parser.add_subparsers(dest='command', required=True)
    serve = commands.add_parser('serve')
    serve.add_argument('--config', type=Path, required=True)
    serve.add_argument('--port', type=int, default=8091)
    serve.add_argument('--static', type=Path, default=Path('apps/sn-workbench/dist'))
    serve.add_argument('--actor', default='local-reviewer')
    ingest = commands.add_parser('import')
    ingest.add_argument('--bundle', type=Path, required=True)
    ingest.add_argument('--submission', type=Path, required=True)
    ingest.add_argument('--scores', type=Path, required=True)
    ingest.add_argument('--task')
    export = commands.add_parser('export')
    export.add_argument('--task', required=True)
    review = commands.add_parser('review')
    review.add_argument('--input', type=Path, required=True, help='JSON command with task/result/case, base_revision, judgment, reason, evidence and request_key')
    review.add_argument('--actor', required=True, help='Explicit local operator identity')
    plan_save = commands.add_parser('plan-save', help='Save a draft; does not execute or freeze runtime')
    plan_save.add_argument('--config', type=Path, required=True)
    plan_save.add_argument('--task', required=True)
    plan_save.add_argument('--actor', required=True)
    plan_save.add_argument('--input', type=Path, required=True, help='JSON original selection command')
    for name in ('plan-list', 'plan-show', 'plan-receipt'):
        command = commands.add_parser(name)
        command.add_argument('--task', required=True)
        if name == 'plan-show':
            command.add_argument('--plan', required=True)
            command.add_argument('--revision')
        if name == 'plan-receipt':
            command.add_argument('--actor', required=True)
            command.add_argument('--request-key', required=True)
    for name in ('job-prepare', 'job-cancel', 'job-list', 'job-show', 'job-receipt',
                 'job-run', 'job-status', 'job-execution-status', 'job-execution-cancel'):
        command = commands.add_parser(name, help='Register, execute or read a Job')
        command.add_argument('--task', required=True)
        if name in ('job-prepare', 'job-cancel', 'job-receipt', 'job-run', 'job-execution-cancel'):
            command.add_argument('--actor', required=True)
            command.add_argument('--request-key', required=True)
        if name == 'job-prepare':
            command.add_argument('--plan', required=True)
            command.add_argument('--revision', required=True)
            command.add_argument('--content-hash', required=True)
        if name in ('job-cancel', 'job-show'):
            command.add_argument('--job', required=True)
        if name == 'job-cancel':
            command.add_argument('--state-version', type=int, required=True)
        if name == 'job-receipt':
            command.add_argument('--operation', choices=('prepare', 'cancel'), required=True)
        if name == 'job-run':
            command.add_argument('--config', type=Path, required=True)
            command.add_argument('--job', required=True)
            command.add_argument('--state-version', type=int, required=True)
            command.add_argument('--preparation-id', required=True)
            command.add_argument('--budget', type=Path, required=True,
                                 help='JSON execution options: timeout_seconds; optional limit_cny requires confirmed=true')
        if name in ('job-status', 'job-execution-status'):
            command.add_argument('--job', required=True)
            command.add_argument('--execution', required=True)
        if name == 'job-execution-cancel':
            command.add_argument('--job', required=True)
            command.add_argument('--execution', required=True)
    for name in ('worktree-bind', 'worktree-read', 'worktree-run-argv'):
        command = commands.add_parser(name, help='Bind/read a trusted worktree or capture argv; never execute')
        command.add_argument('--config', type=Path, required=True)
        command.add_argument('--task', required=True)
        if name == 'worktree-bind':
            command.add_argument('--worktree', required=True, help='Trusted registered ID')
    for name in ('iterations-migrate', 'iteration-show', 'iteration-legacy-node', 'iteration-legacy-parent'):
        command = commands.add_parser(name, help='Read iterations or map legacy Tasks with evidenced fields only')
        command.add_argument('--config', type=Path, required=True)
        if name != 'iteration-show':
            command.add_argument('--actor', required=True)
        if name == 'iterations-migrate':
            command.add_argument('--apply', action='store_true', help='Write; the default is a dry-run report')
        if name in ('iteration-show', 'iteration-legacy-node'):
            command.add_argument('--iteration', required=True)
        if name in ('iteration-legacy-node', 'iteration-legacy-parent'):
            command.add_argument('--basis', required=True, help='Evidence for this manual association')
        if name == 'iteration-legacy-node':
            command.add_argument('--label', required=True)
            command.add_argument('--parent')
            command.add_argument('--execution', action='append', default=[])
        if name == 'iteration-legacy-parent':
            command.add_argument('--candidate', required=True)
            command.add_argument('--parent', required=True)
    args = parser.parse_args()
    if args.command.startswith('iteration'):
        from .configuration import task_worktree_services
        from .iterations import IterationService
        from .job_execution import JobExecutionStore
        repository = WorkbenchRepository(args.state)
        service, _ = task_worktree_services(repository, load_config(args.config))
        iterations = IterationService(repository, service, executions=JobExecutionStore(repository))
        if args.command == 'iterations-migrate':
            result = iterations.migrate_legacy(actor=args.actor, dry_run=not args.apply)
        elif args.command == 'iteration-show':
            result = iterations.iteration(args.iteration)
        elif args.command == 'iteration-legacy-node':
            result = iterations.record_legacy_candidate(args.iteration, label=args.label, basis=args.basis,
                                                        actor=args.actor, execution_ids=args.execution,
                                                        parent_candidate_id=args.parent)
        else:
            result = iterations.assign_legacy_parent(args.candidate, args.parent, basis=args.basis, actor=args.actor)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command.startswith('worktree-'):
        from .configuration import task_worktree_services
        config = load_config(args.config)
        service, registrations = task_worktree_services(WorkbenchRepository(args.state), config)
        try:
            if args.command == 'worktree-bind':
                entry = registrations[args.worktree]
                result = service.bind(args.task, entry['path'], paseo_workspace_id=entry.get('paseo_workspace_id'))
            elif args.command == 'worktree-read':
                result = service.read(args.task)
            else:
                model_config = config.get('task_worktrees', {}).get('model_config')
                if model_config is None:
                    raise ValueError('Registered experimental model config required')
                result = {'executable': False, 'argv': service.run_argv(args.task, model_config=Path(model_config))}
        except (KeyError, ValueError, OSError):
            print(json.dumps({'error': 'Worktree command rejected', 'code': 'WORKTREE_COMMAND_REJECTED'}), file=sys.stderr)
            raise SystemExit(2) from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == 'import':
        result = import_result(args.bundle, args.submission, args.scores, args.state)
        if args.task:
            WorkbenchRepository(args.state).attach_result(args.task, result['id'])
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command == 'export':
        print(json.dumps(dict(format='sn-workbench-task-export-v1', **WorkbenchRepository(args.state).task(args.task)),
                         ensure_ascii=False, indent=2))
    elif args.command == 'review':
        command = json.loads(args.input.read_text())
        if 'reviewer' in command:
            raise ValueError('Reviewer comes from --actor, not input content')
        result = WorkbenchRepository(args.state).save_review(reviewer=args.actor, **command)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command.startswith('job-'):
        from .evaluation_jobs import EvaluationJobStore, JobRejected
        from .job_execution import ExecutionRejected, JobExecutionStore
        from .repository import Conflict
        repository = WorkbenchRepository(args.state)
        store = EvaluationJobStore(repository)
        operation = args.command.removeprefix('job-')
        if operation == 'prepare':
            call = lambda: store.prepare(args.task, args.plan, args.revision, args.content_hash,
                                         actor=args.actor, request_key=args.request_key)
        elif operation == 'cancel':
            call = lambda: store.cancel(args.task, args.job, args.state_version,
                                        actor=args.actor, request_key=args.request_key)
        elif operation == 'list':
            call = lambda: store.list(args.task)
        elif operation == 'show':
            call = lambda: store.get(args.task, args.job)
        elif operation == 'run':
            from .jobs_api import build_execution_spec
            config = load_config(args.config)
            budget = json.loads(args.budget.read_text())
            execution = JobExecutionStore(repository)
            def call():
                spec = build_execution_spec(store, args.task, args.job, args.preparation_id,
                                            config.get('job_execution'), request_key=args.request_key, budget=budget)
                spec['budget'] = budget
                return execution.run(args.task, args.job, args.state_version,
                                     execution_spec=spec, actor=args.actor,
                                     request_key=args.request_key)
        elif operation in ('status', 'execution-status'):
            call = lambda: JobExecutionStore(repository).get(args.task, args.execution)
        elif operation == 'execution-cancel':
            call = lambda: JobExecutionStore(repository).cancel(args.task, args.execution,
                                                                actor=args.actor, request_key=args.request_key)
        else:
            call = lambda: store.receipt(args.task, actor=args.actor, request_key=args.request_key,
                                         operation=args.operation)
        try:
            result = call()
        except (Conflict, JobRejected, ExecutionRejected):
            print(json.dumps({'error': 'Job command rejected; check the original receipt and current record',
                              'code': 'JOB_COMMAND_REJECTED',
                              'operation': args.operation if operation == 'receipt' else operation,
                              'request_key': getattr(args, 'request_key', None)}), file=sys.stderr)
            raise SystemExit(2) from None
        if operation in ('prepare', 'cancel', 'receipt', 'run', 'execution-cancel'):
            result = {'operation': args.operation if operation == 'receipt' else operation,
                      'request_key': args.request_key, 'record': result}
        print(json.dumps(result, ensure_ascii=False, indent=2))
    elif args.command.startswith('plan-'):
        from .evaluation_plans import PlanDraftStore
        from .plan_inputs import NotebookPlanInputs
        locators = {}
        if args.command == 'plan-save':
            config = load_config(args.config)
            locators = {key: entry['bundle'] for key, entry in input_catalog(config).items()}
        store = PlanDraftStore(WorkbenchRepository(args.state), NotebookPlanInputs(locators))
        if args.command == 'plan-save':
            try:
                command = json.loads(args.input.read_text())
            except (OSError, UnicodeError, json.JSONDecodeError):
                parser.error('Plan command must be a readable UTF-8 JSON object')
            required = {'title', 'input_id', 'expected_bundle_id', 'case_ids', 'mode', 'request_key'}
            allowed = required | {'plan_id', 'base_revision'}
            if not isinstance(command, dict) or not required <= set(command) or set(command) - allowed:
                parser.error('Plan command requires title, input_id, expected_bundle_id, case_ids, mode and request_key; identities come from CLI arguments')
            operation = lambda: store.save(task_id=args.task, actor=args.actor, **command)
        elif args.command == 'plan-list':
            operation = lambda: store.list(args.task)
        elif args.command == 'plan-show':
            operation = lambda: store.get(args.task, args.plan, args.revision)
        else:
            operation = lambda: store.receipt(args.task, actor=args.actor, request_key=args.request_key)
        # Domain refusals are local command diagnostics. Do not print exception
        # text or imply that an earlier request never committed. Programming
        # exceptions stay outside this explicit boundary.
        from .repository import Conflict
        try:
            result = operation()
        except (Conflict, ValueError, FileNotFoundError) as error:
            if isinstance(error, Conflict):
                message = 'Plan draft conflicts with saved state; check the receipt and current revision'
            elif isinstance(error, FileNotFoundError):
                message = 'Required task, plan or notebook input is unavailable'
            else:
                message = 'Invalid plan draft command or saved record'
            key = command.get('request_key') if args.command == 'plan-save' else getattr(args, 'request_key', None)
            print(json.dumps({'error': message,
                              'code': 'PLAN_SAVE_REJECTED' if args.command == 'plan-save' else 'PLAN_READ_REJECTED',
                              'request_key': key if isinstance(key, str) else None}), file=sys.stderr)
            raise SystemExit(2) from None
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        from .api import create_app
        import uvicorn
        config = load_config(args.config)
        app = create_app(args.state, config, actor=args.actor, static_directory=args.static)
        print(f'Open http://127.0.0.1:{args.port}/ and enter the local workbench access code', flush=True)
        uvicorn.run(app, host='127.0.0.1', port=args.port)


if __name__ == '__main__':
    main()
