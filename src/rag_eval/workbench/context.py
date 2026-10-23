"""Resolve explicitly selected context; never read or send a whole result bundle."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from .ingest import read_case, read_result


MAX_CONTEXT_BYTES = 65536
# These local runtime/build directories do not contain selectable source context.
_EXCLUDED_PARTS = {'var', 'runtime', 'node_modules', 'dist', 'build', 'test-results', '__pycache__', 'access-token'}


def _snapshot(reference, title, text, source_refs):
    raw = text.encode('utf-8')
    if len(raw) > MAX_CONTEXT_BYTES:
        raise ValueError('Context exceeds the selectable size; choose a smaller source')
    return dict(reference=reference, title=title, text=text,
                sha256=hashlib.sha256(raw).hexdigest(), source_refs=source_refs)


def resolve_context(repository, task_id, reference, *, run_views=None):
    """Return a content snapshot for preview and later explicit message submission.

    Imported-result case context contains saved question/answer/status only. A
    run case (registered run view) is analysis material and includes gold,
    citations and ranked retrieval; it is for the analysis Agent, never for SN
    generation. File selection is relative to the task workspace. This is a
    local selection boundary, not a sandbox for a coding Agent.
    """
    fields = {'goal': {'kind'}, 'file': {'kind', 'path'},
              'result': {'kind', 'result_id'}, 'case': {'kind', 'result_id', 'case_id'},
              'run_case': {'kind', 'run_id', 'case_id'}, 'task_runs': {'kind'}, 'iteration_tree': {'kind'}, 'brief': {'kind', 'session_id'}}
    if (not isinstance(reference, dict) or not isinstance(reference.get('kind'), str)
            or reference['kind'] not in fields or set(reference) != fields[reference['kind']]
            or any(not isinstance(v, str) or not v.strip() for v in reference.values())):
        raise ValueError('Invalid context reference')
    from .splits import Policy
    policy = Policy(repository, task_id)
    if 'case_id' in reference:
        policy.require_search(reference['case_id'])
    reference = dict(reference)
    task = repository.task(task_id)
    kind = reference['kind']
    if kind == 'goal':
        return _snapshot(reference, '任务目标 · ' + task['title'], task['goal'], {'task_id': task_id})
    if kind == 'brief':
        from .briefs import resolve_brief
        workspace = next(w for w in repository.workspaces() if w['id'] == task['workspace_id'])
        tool = f'{Path(workspace["location"]) / "scripts/sn-eval"} --state {repository.root}'
        title, text, source_refs = resolve_brief(repository, task_id, reference['session_id'], tool=tool,
                                                  run_views=run_views)
        return _snapshot(reference, title, policy.project(text), source_refs)
    if kind == 'task_runs':
        if run_views is None:
            raise ValueError('Run context is not configured')
        title, text, source_refs = run_views.task_runs_context(task_id)
        text = json.dumps(policy.project(json.loads(text)), ensure_ascii=False)
        return _snapshot(reference, title, text, source_refs)
    if kind == 'iteration_tree':
        from .briefs import iteration_tree_context
        title, text, source_refs = iteration_tree_context(repository, run_views, task_id)
        text = policy.project(text)
        return _snapshot(reference, title, text, source_refs)
    if kind == 'run_case':
        if run_views is None:
            raise ValueError('Run case context is not configured')
        try:
            title, text, source_refs = run_views.case_context(reference['run_id'], reference['case_id'])
        except LookupError:
            raise ValueError('Run case is not registered') from None
        return _snapshot(reference, title, text, source_refs)
    if kind == 'file':
        path = Path(reference['path'])
        if (path.is_absolute() or '..' in path.parts or not path.parts
                or any(part.startswith('.') or part in _EXCLUDED_PARTS for part in path.parts)):
            raise ValueError('File is outside selectable workspace sources')
        workspace = next(w for w in repository.workspaces() if w['id'] == task['workspace_id'])
        root = Path(workspace['location']).resolve()
        target = root
        for part in path.parts:
            target = target / part
            if target.is_symlink():
                raise ValueError('Symlink context is not selectable')
        if not target.resolve().is_relative_to(root) or not target.is_file():
            raise ValueError('File is not an available workspace source')
        # Bounded read also handles growth after the metadata check.
        with target.open('rb') as stream:
            raw = stream.read(MAX_CONTEXT_BYTES + 1)
        if len(raw) > MAX_CONTEXT_BYTES:
            raise ValueError('File exceeds the selectable size')
        if any((byte < 32 and byte not in {9, 10, 13}) or byte == 127 for byte in raw):
            raise ValueError('File is not text; binary control bytes are not selectable')
        try:
            text = raw.decode('utf-8')
        except UnicodeDecodeError:
            raise ValueError('File is not UTF-8 text') from None
        reference['path'] = path.as_posix()
        return _snapshot(reference, reference['path'], text, {'workspace_id': task['workspace_id']})
    result_id = reference['result_id']
    if not any(r['id'] == result_id for r in task['results']):
        raise ValueError('Result is not attached to this task')
    if kind == 'result':
        result = read_result(repository.root, result_id, include_cases=False)
        summary = {k: result[k] for k in ('id', 'suite', 'scope', 'case_count', 'bundle_case_count',
                                         'metrics', 'pending_metrics', 'coverage')}
        summary['method'] = {k: result['method'][k] for k in ('name', 'kind')}
        return _snapshot(reference, result['suite'].upper() + ' · 成绩摘要',
                         json.dumps(summary, ensure_ascii=False, indent=2),
                         {k: result[k] for k in ('submission_id', 'scores_id', 'scorer_id')})
    case = read_case(repository.root, result_id, reference['case_id'])
    content = {k: case[k] for k in ('case_id', 'question', 'status', 'answer', 'answer_source')}
    return _snapshot(reference, '案例 · ' + case['case_id'],
                     json.dumps(content, ensure_ascii=False, indent=2), case['source_refs'])
