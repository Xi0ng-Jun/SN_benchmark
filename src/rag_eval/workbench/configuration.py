"""Trusted local registrations; paths never come from workbench commands."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import re


def input_catalog(config):
    """Validate registrations without requiring their artifacts to be online."""
    entries = config.get('inputs', [])
    if not isinstance(entries, list):
        raise ValueError('Invalid input catalog')
    result = {}
    for entry in entries:
        if (not isinstance(entry, dict) or
                set(entry) - {'id', 'title', 'description', 'bundle', 'bundle_id'} or
                not isinstance(entry.get('id'), str) or
                not re.fullmatch(r'\S+', entry['id']) or
                not isinstance(entry.get('bundle'), (str, Path)) or not str(entry['bundle']).strip()):
            raise ValueError('Invalid input registration')
        if entry['id'] in result:
            raise ValueError('Duplicate input ID')
        if entry.get('bundle_id') is not None and (not isinstance(entry['bundle_id'], str) or
                                                  not re.fullmatch(r'[0-9a-f]{64}', entry['bundle_id'])):
            raise ValueError('Invalid registered bundle identity')
        if any(key in entry and not isinstance(entry[key], str) for key in ('title', 'description')):
            raise ValueError('Invalid input catalog description')
        result[entry['id']] = deepcopy(entry)
    return result


def load_config(path):
    """Resolve configured paths relative to the containing file, never cwd."""
    path = Path(path).resolve()
    config = json.loads(path.read_text())
    input_catalog(config)
    for source in config.get('sources', []):
        for key in ('bundle', 'submission', 'scores'):
            source[key] = str((path.parent / source[key]).resolve())
    for entry in config.get('inputs', []):
        entry['bundle'] = str((path.parent / entry['bundle']).resolve())
    config['workspace']['location'] = str((path.parent / config['workspace']['location']).resolve())
    views = config.get('run_views', {})
    for entry in views.get('runs', []):
        for key in ('run_dir', 'scores'):
            if entry.get(key):
                entry[key] = str((path.parent / entry[key]).resolve())
    for entry in views.get('worktrees', []):
        entry['path'] = str((path.parent / entry['path']).resolve())
    if views.get('ledger'):
        views['ledger'] = str((path.parent / views['ledger']).resolve())
    for key, value in views.get('batch', {}).items():
        if key not in {'mode', 'artifact_index_id'} and value and not value.startswith('$'):
            views['batch'][key] = str((path.parent / value).resolve())
    execution = config.get('job_execution')
    if isinstance(execution, dict) and execution.get('output_root'):
        execution['output_root'] = str((path.parent / execution['output_root']).resolve())
        for key in ('python', 'artifact_root', 'project_root', 'model_config',
                    'dependency_requirements', 'runtime_expectation', 'scorer_sources'):
            if execution.get(key):
                target = path.parent / execution[key]
                # The symlink invocation selects the virtual environment.
                execution[key] = os.path.abspath(target) if key == 'python' else str(target.resolve())
    preparation = config.get('job_preparation')
    if isinstance(preparation, dict) and preparation.get('artifact_root'):
        preparation['artifact_root'] = str((path.parent / preparation['artifact_root']).resolve())
    trees = config.get('task_worktrees', {})
    for key in ('project_root', 'artifact_root', 'model_config'):
        if trees.get(key):
            trees[key] = str((path.parent / trees[key]).resolve())
    for entry in trees.get('registrations', []):
        entry['path'] = str((path.parent / entry['path']).resolve())
    return config


def task_worktree_services(repository, config):
    from ..artifact_store import ArtifactStore
    from .task_worktree import initialize_task_worktrees, TaskWorktreeService, SN_PROJECT_ROOT
    section = config.get('task_worktrees', {})
    initialize_task_worktrees(repository)
    service = TaskWorktreeService(repository, ArtifactStore(section.get('artifact_root', repository.root / 'artifacts')),
                                  project_root=section.get('project_root', SN_PROJECT_ROOT))
    registrations = {}
    for entry in section.get('registrations', []):
        identity = entry.get('id')
        if not isinstance(identity, str) or not identity.strip() or identity in registrations:
            raise ValueError('Invalid or duplicate worktree registration')
        registrations[identity] = entry
    return service, registrations
