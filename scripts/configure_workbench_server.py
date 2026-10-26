#!/usr/bin/env python3
"""Initialize private server configuration and check local inputs, without models."""
import argparse
import json
import os
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))

from rag_eval.artifact_store import ArtifactStore
from rag_eval.bundle_index import install_bundle
from rag_eval.identity import fingerprint
from rag_eval.notebook_bundle import load_bundle
from rag_eval.workbench.configuration import load_config


def require(condition, message):
    if not condition:
        raise ValueError(message)


def initialize(args):
    path = args.config.absolute()
    require(not path.exists() and not path.is_symlink(), 'Config already exists; refusing overwrite')
    require(not args.workspace_id or args.sn_root, '--workspace-id requires --sn-root')
    entries = {}
    for value in args.bundle:
        name, sep, directory = value.partition('=')
        require(sep and re.fullmatch(r'[a-zA-Z0-9_.-]+', name) and directory,
                '--bundle must be ID=/absolute/or/relative/bundle')
        require(name not in entries, 'Duplicate input ID')
        entries[name] = Path(directory).resolve()
    sn = args.sn_root.resolve() if args.sn_root else path.parent / 'sn-not-configured'
    if args.sn_root:
        result = subprocess.run(['git', '-C', str(sn), 'rev-parse', '--show-toplevel'],
                                capture_output=True, text=True)
        require(result.returncode == 0 and Path(result.stdout.strip()).resolve() == sn,
                '--sn-root must be the root of a Git checkout')
        require((sn / 'backend').is_dir(), '--sn-root must contain SN backend/')
        require(not path.resolve().is_relative_to(sn), 'Config must be outside the SN checkout')
    # Reuse an existing campaign store so finished runs, seeds and new runs share inputs.
    store = args.artifact_root.resolve() if args.artifact_root else path.parent / 'artifacts'
    config = {
        'workspace': {'title': 'SN 服务器评测', 'location': str(ROOT)},
        'sources': [], 'inputs': [],
        'task_worktrees': {'project_root': str(sn), 'artifact_root': str(store),
                          'registrations': [], 'create_from_hypothesis': bool(args.workspace_id),
                          'create_candidates': bool(args.workspace_id)},
        'job_preparation': {'artifact_root': str(store), 'inputs': {}},
    }
    if args.workspace_id:
        config['agent_bridge'] = {'url': args.paseo_url}
        # Server operator discovers installed pi profiles; do not copy local IDs/models.
        config['agent_creation'] = {'workspaceId': args.workspace_id, 'profiles': {}}
    for name, directory in entries.items():
        bundle = load_bundle(directory)
        require(bundle['manifest']['adaptation_revision'] == 'notebook-data-v3',
                'New server inputs require notebook-data-v3')
        refs = install_bundle(directory, store)
        canonical = ArtifactStore(store).resolve(refs['bundle'])
        config['inputs'].append(dict(id=name, title=name, bundle=str(canonical),
                                     bundle_id=fingerprint(bundle['manifest'])))
        config['job_preparation']['inputs'][name] = dict(bundle_ref=refs['bundle'], index_ref=refs['index'])
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        json.dump(config, stream, ensure_ascii=False, indent=2)
        stream.write('\n')
    print('Created private configuration; model execution is disabled.')


def check(args):
    config = load_config(args.config)
    preparation = config.get('job_preparation', {})
    trees = config.get('task_worktrees', {})
    require(config['workspace']['location'] == str(ROOT), 'Workspace must name this evaluator checkout')
    for entry in config.get('inputs', []):
        bundle = load_bundle(entry['bundle'])
        require(fingerprint(bundle['manifest']) == entry.get('bundle_id'), 'Input identity changed')
        refs = preparation.get('inputs', {}).get(entry['id'], {})
        store = ArtifactStore(preparation['artifact_root'])
        canonical = store.resolve(refs['bundle_ref'])
        index = store.resolve(refs['index_ref'])
        require(fingerprint(load_bundle(canonical)['manifest']) == entry['bundle_id'],
                'Preparation bundle differs from selected input')
        require(json.loads((index / 'index.json').read_text())['bundle'] == refs['bundle_ref'],
                'Preparation index differs from bundle')
    if args.execution:
        execution = config.get('job_execution', {})
        require(execution.get('mode') == 'notebook', 'job_execution notebook configuration is missing')
        require(bool(config.get('inputs')), 'At least one frozen input is required')
        for name in ('python', 'model_config', 'dependency_requirements', 'runtime_expectation'):
            require(isinstance(execution.get(name), str) and Path(execution[name]).is_file(),
                    'Missing job_execution file: ' + name)
        for name in ('project_root', 'artifact_root', 'scorer_sources'):
            require(isinstance(execution.get(name), str) and Path(execution[name]).is_dir(),
                    'Missing job_execution directory: ' + name)
        require(execution['artifact_root'] == preparation.get('artifact_root') == trees.get('artifact_root'),
                'Capture, preparation and execution must use the same artifact store')
        require(execution['project_root'] == trees.get('project_root'), 'SN checkout registrations differ')
        require((Path(execution['project_root']) / 'backend').is_dir(), 'SN backend is missing')
        require(bool(execution.get('output_root')), 'Execution output_root is missing')
        output = Path(execution['output_root']).resolve()
        protected = [Path(execution[k]).resolve() for k in ('project_root', 'artifact_root', 'scorer_sources')]
        protected += [ROOT / 'src', ROOT / 'scripts']
        protected += [Path(e['bundle']).resolve() for e in config['inputs']]
        require(all(not output.is_relative_to(p) and not p.is_relative_to(output) for p in protected),
                'Execution output overlaps source or inputs')
    execution = config.get('job_execution', {})
    if execution:
        for name in ('python', 'runtime_expectation'):
            require(isinstance(execution.get(name), str) and Path(execution[name]).is_file(),
                    'Missing job_execution file: ' + name)
        expectation = json.loads(Path(execution['runtime_expectation']).read_text())
        probe = subprocess.run([execution['python'], '-c',
            'import json,sys; print(json.dumps(dict(executable=sys.executable,prefix=sys.prefix)))'],
            capture_output=True, text=True, timeout=15, check=True)
        observed = json.loads(probe.stdout)
        require(type(expectation) is dict and set(expectation) == {'executable', 'prefix'}
                and all(isinstance(v, str) and v for v in expectation.values()),
                'Invalid interpreter expectation')
        # Match the runner contract: normalize .. while preserving venv invocation symlinks.
        require(os.path.abspath(expectation['executable']) == os.path.abspath(observed['executable'])
                and Path(expectation['prefix']).resolve() == Path(observed['prefix']).resolve(),
                'Interpreter expectation differs from selected Python: '
                f"expected executable={expectation['executable']}, prefix={expectation['prefix']}; "
                f"selected executable={observed['executable']}, prefix={observed['prefix']}")
    if args.execution:
        from rag_eval.runtime_dependency_identity import parse_dependency_requirements
        parse_dependency_requirements(Path(execution['dependency_requirements']).read_bytes())
        require(bool(config.get('agent_creation', {}).get('profiles')),
                'Server Agent profiles have not been registered')
    print(json.dumps(dict(status='local_files_checked', inputs=len(config.get('inputs', [])),
                         execution_files_checked=args.execution, model_requests=0,
                         remote_paseo='not_checked', credentials_and_models='not_checked'), indent=2))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    init = commands.add_parser('init')
    init.add_argument('--config', type=Path, default=ROOT / 'var/sn-workbench-server/config.json')
    init.add_argument('--sn-root', type=Path)
    init.add_argument('--workspace-id', help='Paseo workspace ID for THIS evaluator checkout, not SN')
    init.add_argument('--paseo-url', default='ws://127.0.0.1:6767/ws')
    init.add_argument('--bundle', action='append', default=[], help='Repeat ID=PATH for frozen bundles')
    init.add_argument('--artifact-root', type=Path,
                      help='Existing artifact store to reuse (default: a new store beside the config)')
    doctor = commands.add_parser('check')
    doctor.add_argument('--config', type=Path, required=True)
    doctor.add_argument('--execution', action='store_true', help='Also require local execution configuration')
    args = parser.parse_args()
    try:
        (initialize if args.command == 'init' else check)(args)
    except (ValueError, OSError, KeyError, TypeError, subprocess.SubprocessError) as error:
        # Do not dump private configuration or subprocess stdout/stderr.
        print('Setup rejected: ' + (str(error) if type(error) is ValueError else type(error).__name__), file=sys.stderr)
        return 1
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
