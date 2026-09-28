#!/usr/bin/env python3
"""Validate the frozen external-comparison campaign before any model run."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def _load(path: Path):
    try:
        return json.loads(path.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot load JSON {path}: {exc}') from exc


def _ids(path: Path) -> list[str]:
    try:
        rows = [line.strip() for line in path.read_text().splitlines()
                if line.strip() and not line.lstrip().startswith('#')]
    except OSError as exc:
        raise ValueError(f'Cannot read case-id file {path}: {exc}') from exc
    if any(any(ch.isspace() for ch in value) for value in rows):
        raise ValueError(f'Case IDs contain whitespace: {path}')
    if len(rows) != len(set(rows)):
        raise ValueError(f'Case IDs contain duplicates: {path}')
    if not rows:
        raise ValueError(f'Case-id file is empty: {path}')
    return rows


def _bundle_ids(path: Path) -> set[str]:
    cases = path / 'cases.jsonl' if path.is_dir() else path
    try:
        return {json.loads(line)['case_id'] for line in cases.read_text().splitlines() if line.strip()}
    except (OSError, json.JSONDecodeError, KeyError) as exc:
        raise ValueError(f'Cannot load bundle case IDs {cases}: {exc}') from exc


def validate(registry_path: Path, campaign_path: Path, plan_path: Path,
             *, root: Path, bundles: dict[str, Path]) -> dict:
    registry = _load(registry_path)
    campaign = _load(campaign_path)
    try:
        plan = [json.loads(line) for line in plan_path.read_text().splitlines() if line.strip()]
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f'Cannot load JSONL {plan_path}: {exc}') from exc
    errors: list[str] = []
    methods = registry.get('methods')
    scopes = registry.get('scopes')
    candidates = campaign.get('candidates')
    if not isinstance(methods, list) or not isinstance(scopes, dict) or not isinstance(candidates, list):
        errors.append('registry/campaign has invalid methods, scopes or candidates shape')
        return {'valid': False, 'errors': errors, 'summary': {}}
    method_ids = [item.get('method_id') for item in methods]
    if any(not isinstance(value, str) or not value for value in method_ids):
        errors.append('registry methods must have nonempty method_id')
    if len(method_ids) != len(set(method_ids)):
        errors.append('registry contains duplicate method_id')
    registry_ids = set(method_ids)
    candidate_ids = [item.get('method_id') for item in candidates]
    if len(candidate_ids) != len(set(candidate_ids)):
        errors.append('campaign contains duplicate candidate method_id')
    for item in candidates:
        if item.get('method_id') not in registry_ids:
            errors.append(f'campaign candidate is not registered: {item.get("method_id")!r}')
    job_ids = [item.get('job_id') for item in plan]
    if len(job_ids) != len(set(job_ids)):
        errors.append('execution plan contains duplicate job_id')
    candidate_set = set(candidate_ids)
    for item in plan:
        if item.get('method_id') not in candidate_set:
            errors.append(f'execution job references unlisted candidate: {item.get("method_id")!r}')
        if item.get('phase') not in {'smoke', 'full'}:
            errors.append(f'execution job has invalid phase: {item.get("job_id")!r}')
    scope_ids = set(scopes)
    for item in methods:
        if item.get('scope_id') not in scope_ids:
            errors.append(f'method references unknown scope: {item.get("method_id")!r}')
    smoke_files: dict[str, dict] = {}
    for suite, scope in campaign.get('scopes', {}).items():
        case_file = scope.get('smoke_case_file')
        if not isinstance(case_file, str):
            errors.append(f'{suite}: smoke_case_file is missing')
            continue
        path = (root / case_file).resolve()
        try:
            values = _ids(path)
            if suite == 'alce':
                bundle_keys = [key for key in ('alce.asqa', 'alce.qampari', 'alce.eli5') if key in bundles]
                if bundle_keys and len(bundle_keys) != 3:
                    errors.append('alce: provide all three task bundles (alce.asqa, alce.qampari, alce.eli5)')
            else:
                bundle_keys = [suite] if suite in bundles else []
            missing_by_bundle = {}
            for key in bundle_keys:
                bundle_ids = _bundle_ids(bundles[key])
                missing = sorted(set(values) - bundle_ids)
                if missing:
                    missing_by_bundle[key] = missing
                if suite == 'alce':
                    task = key.split('.', 1)[1]
                    expected_count = scope.get('expected_cases', {}).get(task)
                else:
                    expected_count = scope.get('expected_cases')
                if isinstance(expected_count, int) and len(bundle_ids) != expected_count:
                    errors.append(f'{suite}: {key} has {len(bundle_ids)} unique cases; expected {expected_count}')
            for key, missing in missing_by_bundle.items():
                errors.append(f'{suite}: smoke IDs absent from {key} bundle: {missing}')
            smoke_files[suite] = {'path': str(path), 'count': len(values),
                                  'bundle_checked': bundle_keys,
                                  'missing_by_bundle': missing_by_bundle}
        except ValueError as exc:
            errors.append(str(exc))
    return {
        'valid': not errors,
        'errors': errors,
        'summary': {
            'registry_methods': len(methods),
            'registry_scopes': len(scopes),
            'campaign_candidates': len(candidates),
            'execution_jobs': len(plan),
            'smoke_files': smoke_files,
        },
    }


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--registry', type=Path, required=True)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--execution-plan', type=Path, required=True)
    parser.add_argument('--root', type=Path, default=Path('.'),
                        help='Root used to resolve smoke_case_file paths')
    parser.add_argument('--bundle', action='append', default=[], metavar='SUITE=PATH',
                        help='Optional frozen bundle/cases.jsonl to verify smoke IDs; repeat per suite')
    parser.add_argument('--output', type=Path,
                        help='Optional JSON report path; stdout is always written')
    args = parser.parse_args(argv)
    bundles = {}
    for value in args.bundle:
        if '=' not in value or not value.split('=', 1)[0]:
            parser.error('--bundle must use SUITE=PATH')
        suite, path = value.split('=', 1)
        if suite in bundles:
            parser.error(f'duplicate --bundle suite: {suite}')
        bundles[suite] = Path(path)
    try:
        report = validate(args.registry, args.campaign, args.execution_plan,
                          root=args.root, bundles=bundles)
    except ValueError as exc:
        report = {'valid': False, 'errors': [str(exc)], 'summary': {}}
    rendered = json.dumps(report, ensure_ascii=False, indent=2)
    print(rendered)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + '\n')
    return 0 if report['valid'] else 1


if __name__ == '__main__':
    sys.exit(main())
