"""Read-only inventory and role-based private benchmark result packages."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path, PurePosixPath
import shutil
import stat
import tarfile
import tempfile
import time

from .artifacts import digest
from .experiment_aggregation import _SECRET
from .run_reader import RunReadContext


CATEGORIES = ('input', 'source', 'runtime', 'outputs', 'agent', 'official', 'report', 'other')
MAX_FILES = 1_000_000
MAX_ENTRIES = 2_000_000
MAX_DEPTH = 64
RESULT_NAMES = frozenset({
    'submission.json', 'scores.json', 'prepared.json', 'bundle-manifest.json',
    'import-audit.json', 'scorer-audit.json', 'scorer-identity.json', 'sources.json',
    'retrieval.json', 'normalized-text.json', 'asqa.json', 'qampari.json', 'eli5.json',
    'scope.json', 'coverage.json', 'provenance.json', 'comparison.json', 'report.json',
})
ROOT_NAMES = frozenset({
    'campaign.json', 'campaign-manifest.json', 'scope.json', 'coverage.json',
    'provenance.json', 'comparison.json', 'report.json',
})
RUN_NAMES = frozenset({
    'manifest.json', 'state.json', 'source-identity.json', 'runtime-identity.json',
    'scope.json', 'coverage.json', 'provenance.json', 'preparation-usage.json',
})
REVIEW_NAMES = frozenset({
    'planned.jsonl', 'outputs.jsonl', 'scores.jsonl', 'product-bundle.json',
    'base-manifest.json', 'base-planned.jsonl', 'base-scores.jsonl',
    'document-map.json', 'source-to-document.json', 'scoring-invocation.json',
    'scoring-identity.json', 'components.jsonl', 'planned-component-scores.jsonl',
    'component-scores.jsonl', 'component-score-summary.json', 'component-batch.json',
    'judge-client-source.json',
})
AGENT_NAMES = frozenset({
    'components.jsonl', 'native-manifest.json', 'native-traces.jsonl',
    'native-scores.jsonl', 'native-outputs.jsonl', 'native-summary.json',
    'native-errors.jsonl', 'native-diagnostics.jsonl', 'native-metric-events.jsonl',
    'planned-component-scores.jsonl', 'component-scores.jsonl',
    'component-score-summary.json', 'component-batch.json',
})
BLOCKED_PARTS = frozenset({'runtime', 'storage', 'logs', 'configs', '.git', '.local', '__pycache__'})


class _PackageContext:
    def __init__(self):
        self.reader = RunReadContext()
        self.object_manifests = {}
        self.copied_objects = set()

    def manifest(self, directory):
        if directory not in self.object_manifests:
            self.object_manifests[directory] = json.loads((directory / 'object.json').read_text())
        return self.object_manifests[directory]


def _root(path):
    path = Path(path).absolute()
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('Symlink roots are not permitted')
    if not path.is_dir():
        raise ValueError('Campaign/package root must be an existing directory')
    return path.resolve()


def _walk(root):
    count, entry_count = 0, 0
    stack = [root]
    while stack:
        directory = stack.pop()
        if len(directory.relative_to(root).parts) > MAX_DEPTH:
            raise ValueError('Inventory directory depth limit exceeded')
        with os.scandir(directory) as entries:
            children = []
            for entry in entries:
                entry_count += 1
                if entry_count > MAX_ENTRIES:
                    raise ValueError('Inventory entry limit exceeded')
                children.append(entry)
            children.sort(key=lambda entry: entry.name)
        for entry in children:
            path = Path(entry.path)
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise ValueError(f'Symlink is not permitted: {path.relative_to(root)}')
            if stat.S_ISDIR(info.st_mode):
                stack.append(path)
            elif stat.S_ISREG(info.st_mode):
                count += 1
                if count > MAX_FILES:
                    raise ValueError('Inventory file limit exceeded')
                yield path, info
            else:
                raise ValueError(f'Non-regular campaign file: {path.relative_to(root)}')


def _category(relative):
    parts = relative.parts
    if any(part in BLOCKED_PARTS for part in parts):
        return 'runtime'
    if 'agent' in parts:
        return 'agent'
    if parts[0] in {'official-scores', 'prepared', 'scorer-audit'}:
        return 'official'
    if parts[0] in {'reports', 'report', 'comparisons'}:
        return 'report'
    if 'input' in parts or 'bundles' in parts or 'indexes' in parts:
        return 'input'
    if parts[:2] == ('artifacts', 'objects') and len(parts) > 2:
        return 'input' if parts[2] in {'bundle', 'index'} else 'source'
    if 'source' in parts or 'evaluator' in parts or 'sn' in parts or relative.name == 'product-source.tar':
        return 'source'
    if parts[0] in {'runs', 'submissions'}:
        return 'outputs'
    return 'other'


def _scan(root, *, mode=None):
    started = time.monotonic()
    root = _root(root)
    categories = {name: {'logical_bytes': 0, 'file_count': 0, 'allocated_bytes': 0}
                  for name in CATEGORIES}
    selected = []
    for path, info in _walk(root):
        relative = path.relative_to(root)
        if mode is not None and _selected(relative, mode):
            selected.append(path)
        category = categories[_category(relative)]
        category['logical_bytes'] += info.st_size
        category['file_count'] += 1
        if hasattr(info, 'st_blocks') and category['allocated_bytes'] is not None:
            category['allocated_bytes'] += info.st_blocks * 512
        else:
            category['allocated_bytes'] = None
    totals = {key: (None if any(value[key] is None for value in categories.values())
                    else sum(value[key] for value in categories.values()))
              for key in ('logical_bytes', 'file_count', 'allocated_bytes')}
    return ({'format': 'sn-campaign-inventory-v1', 'categories': categories,
             'totals': totals, 'elapsed_seconds': time.monotonic() - started}, selected)


def inventory(root: Path) -> dict:
    """Count all regular files without following links or reading file contents."""
    return _scan(root)[0]


def _selected(relative, mode):
    parts = relative.parts
    if any(part in BLOCKED_PARTS for part in parts):
        return False
    if len(parts) == 1:
        return relative.name in ROOT_NAMES
    if parts[0] in {'submissions', 'official-scores', 'prepared', 'scorer-audit'}:
        return relative.name in RESULT_NAMES
    if parts[0] in {'reports', 'comparisons'}:
        return relative.suffix in {'.json', '.jsonl', '.md', '.html', '.css', '.js', '.csv'}
    if parts[0] == 'runs' and len(parts) == 3:
        return relative.name in RUN_NAMES or (mode == 'review' and relative.name in REVIEW_NAMES)
    if mode == 'review' and parts[0] == 'runs' and len(parts) == 4 and parts[2] == 'agent':
        return relative.name in AGENT_NAMES
    if parts[0] == 'runs' and len(parts) >= 4 and parts[2] == 'official-scoring':
        if len(parts) == 4:
            return relative.name in {'input.json', 'invocation.json', 'execution.json', 'scores.json'}
        return mode == 'review' and len(parts) == 5 and parts[3] == 'official-source' and relative.suffix == '.py'
    return False


def _check_credentials(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if _SECRET.search(key):
                raise ValueError('Package contains a structured credential or endpoint field')
            _check_credentials(child)
    elif isinstance(value, list):
        for child in value:
            _check_credentials(child)


def _check_json(path):
    if path.suffix == '.json':
        _check_credentials(json.loads(path.read_text(encoding='utf-8')))
    elif path.suffix == '.jsonl':
        with path.open(encoding='utf-8') as handle:
            for line in handle:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    if not line.endswith('\n') and not handle.read(1):
                        break
                    raise ValueError(f'Corrupt journal: {path.name}') from None
                _check_credentials(row)


def _relative(root, value):
    if not isinstance(value, str) or not value or '\\' in value:
        raise ValueError('Invalid relative artifact path')
    relative = PurePosixPath(value)
    if relative.is_absolute() or '..' in relative.parts or str(relative) != value:
        raise ValueError('Artifact path escapes its root or is not canonical')
    path = root.joinpath(*relative.parts)
    if any(parent.is_symlink() for parent in (path, *path.parents)):
        raise ValueError('Symlink artifact is not permitted')
    if not path.is_file():
        raise ValueError(f'Missing declared artifact: {value}')
    return path


def _validate_runs(root, context):
    runs = root / 'runs'
    if not runs.exists():
        return []
    found, identities = [], set()
    for run in sorted(runs.iterdir()):
        if not run.is_dir():
            raise ValueError('runs/ must contain run directories')
        manifest_path = run / 'manifest.json'
        if not manifest_path.is_file():
            raise ValueError(f'Missing run manifest: {run.name}')
        manifest = json.loads(manifest_path.read_text())
        identity = manifest.get('run_id')
        if not isinstance(identity, str) or identity in identities or identity != run.name:
            raise ValueError('Duplicate or mismatched run/attempt identity')
        identities.add(identity)
        shared = _resolve_shared(root, run, context) if (run / 'artifact-refs.json').exists() else None
        if shared is None and (run / 'input').exists():
            context.reader.bundle(run / 'input')
        if 'planned_sha256' in manifest:
            if digest(_relative(run, 'planned.jsonl')) != manifest['planned_sha256']:
                raise ValueError('Run planned artifact hash differs from manifest')
        attachment = manifest.get('scoring_attachment', {})
        for relative, expected in attachment.get('files', {}).items():
            if digest(_relative(run, relative)) != expected:
                raise ValueError('Declared scoring attachment artifact hash differs')
        source_identity = run / 'source-identity.json'
        if source_identity.exists():
            source = json.loads(source_identity.read_text())
            recorded_code = manifest.get('identity', {}).get('code')
            if recorded_code is not None and source != recorded_code:
                raise ValueError('Saved source identity differs from run code identity')
            source_root = shared[1].get('evaluator') if shared else run / 'source'
            if shared and source_root is not None and 'benchmark_source_hashes' in source:
                files = context.manifest(source_root)['files']
                if source['benchmark_source_hashes'] != files:
                    raise ValueError('Saved source identity differs from evaluator object file set')
            for relative, expected in source.get('benchmark_source_hashes', {}).items():
                if source_root is None or not source_root.exists():
                    raise ValueError('Missing declared evaluator source artifact')
                actual = context.manifest(source_root)['files'].get(relative) if shared else digest(_relative(source_root, relative))
                if actual != expected:
                    raise ValueError('Declared source artifact hash differs')
            if 'product_archive_sha256' in source:
                source_root = shared[1].get('sn') if shared else run
                if source_root is None:
                    raise ValueError('Missing declared product source artifact')
                actual = (context.manifest(source_root)['files'].get('product-source.tar') if shared
                          else digest(_relative(source_root, 'product-source.tar')))
                if actual != source['product_archive_sha256']:
                    raise ValueError('Declared product source hash differs')
                if shared:
                    snapshot_identity = context.manifest(source_root)['identity']
                    if ('product_revision' in snapshot_identity
                            and source.get('product_revision') != snapshot_identity['product_revision']):
                        raise ValueError('Declared product source revision differs from shared object')
        if manifest.get('format') == 'public-starter-run-v1':
            from .run_reader import load_run
            load_run(run, context=context.reader, include_model_events=False)
        found.append(run)
    return found


def _private_json(path, value):
    descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')


def _private_parent(path, root):
    missing = []
    parent = path.parent
    while parent != root and not parent.exists():
        missing.append(parent)
        parent = parent.parent
    for directory in reversed(missing):
        directory.mkdir(mode=0o700)


def _copy(source, destination, root, *, expected=None):
    _private_parent(destination, root)
    descriptor = os.open(destination, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    expected = digest(source) if expected is None else expected
    with source.open('rb') as origin, os.fdopen(descriptor, 'wb') as target:
        shutil.copyfileobj(origin, target)
    if digest(destination) != expected:
        raise ValueError('Source changed during packaging; copied hash differs')


def _resolve_shared(root, run, context):
    from .artifact_store import ArtifactStore
    refs_path = run / 'artifact-refs.json'
    refs = json.loads(refs_path.read_text())
    if (not isinstance(refs, dict) or refs.get('format') != 'sn-run-artifact-refs-v1'
            or set(refs) - {'format', 'root', 'bundle', 'index', 'sources'}
            or not {'root', 'bundle', 'index'} <= refs.keys()):
        raise ValueError('Invalid run artifact references')
    store_path = refs['root']
    if (not isinstance(store_path, str) or not store_path or Path(store_path).is_absolute()
            or '\\' in store_path):
        raise ValueError('Artifact store reference must be relative')
    store_root = (run / store_path).resolve()
    if not store_root.is_relative_to(root):
        raise ValueError('Artifact store reference escapes campaign')
    _root(run / store_path)
    store = ArtifactStore(store_root)
    sources = refs.get('sources', {})
    if not isinstance(sources, dict) or set(sources) - {'evaluator', 'sn'}:
        raise ValueError('Invalid shared source references')
    directories = {}
    for kind, reference in [('bundle', refs['bundle']), ('index', refs['index']), *sources.items()]:
        if not isinstance(reference, dict) or reference.get('kind') != kind:
            raise ValueError('Shared artifact role differs from declared kind')
        directories[kind] = context.reader.object(store, reference)
    return refs, directories


def _shared_dependencies(root, run, staging, context):
    refs, directories = _resolve_shared(root, run, context)
    for reference in [refs['bundle'], refs['index'], *refs.get('sources', {}).values()]:
        directory = directories[reference['kind']]
        destination = staging / 'artifacts/objects' / reference['kind'] / reference['id']
        identity = (reference['kind'], reference['id'], reference['manifest_sha256'])
        if identity in context.copied_objects:
            continue
        manifest = context.manifest(directory)
        for path, _ in _walk(directory):
            relative = path.relative_to(directory)
            expected = (reference['manifest_sha256'] if relative.as_posix() == 'object.json'
                        else manifest['files'][relative.as_posix()])
            _copy(path, destination / relative, staging, expected=expected)
        context.copied_objects.add(identity)
    packed_run = staging / run.relative_to(root)
    _private_json(packed_run / 'artifact-refs.json', {**refs, 'root': '../../artifacts'})


def _legacy_dependencies(root, run, staging):
    directory = run / 'input'
    if directory.exists():
        manifest = json.loads((directory / 'manifest.json').read_text())
        for relative in ['manifest.json', *manifest['files']]:
            source = _relative(directory, relative)
            _copy(source, staging / source.relative_to(root), staging)
    identity_path = run / 'source-identity.json'
    if identity_path.exists():
        identity = json.loads(identity_path.read_text())
        for relative in identity.get('benchmark_source_hashes', {}):
            source = _relative(run / 'source', relative)
            _copy(source, staging / source.relative_to(root), staging)
        if 'product_archive_sha256' in identity:
            source = _relative(run, 'product-source.tar')
            _copy(source, staging / source.relative_to(root), staging)


def _review_declared_dependencies(root, run, staging):
    manifest = json.loads((run / 'manifest.json').read_text())
    attachment = manifest.get('scoring_attachment', {})
    packed_run = staging / run.relative_to(root)
    changed = {}
    for relative, expected in attachment.get('files', {}).items():
        source = _relative(run, relative)
        destination = packed_run / relative
        if not destination.exists():
            if relative != 'model-events.jsonl':
                raise ValueError(f'Review depends on an excluded artifact: {relative}')
            _check_json(source)
            _copy(source, destination, staging)
        actual = digest(destination)
        if actual != expected:
            if relative != 'artifact-refs.json':
                raise ValueError('Review attachment hash changed while packaging')
            changed[relative] = {'original_sha256': expected, 'packed_sha256': actual}
    if changed:
        _copy(run / 'manifest.json', packed_run / 'package-origin-manifest.json', staging)
        patched = {**attachment, 'files': {**attachment['files'],
                   **{relative: row['packed_sha256'] for relative, row in changed.items()}}}
        (packed_run / 'manifest.json').unlink()
        _private_json(packed_run / 'manifest.json', {**manifest, 'scoring_attachment': patched})
        _private_json(packed_run / 'package-provenance.json', {
            'format': 'sn-package-reference-rewrite-v1',
            'original_manifest_sha256': digest(run / 'manifest.json'), 'reference_rewrites': changed,
        })
def _validate_package(root, context):
    root = _root(root)
    files = {path.relative_to(root).as_posix() for path, _ in _walk(root)}
    manifest = json.loads((root / 'package-manifest.json').read_text())
    if manifest.get('format') != 'sn-result-package-v1' or manifest.get('mode') not in {'results', 'review'}:
        raise ValueError('Unknown package format or mode')
    expected_files = manifest['files']
    if files != set(expected_files) | {'package-manifest.json'}:
        raise ValueError('Package file inventory differs from manifest')
    for relative, expected in expected_files.items():
        if digest(_relative(root, relative)) != expected:
            raise ValueError(f'Package artifact hash differs: {relative}')
    if manifest['mode'] == 'review':
        _validate_runs(root, context)
    return {'valid': True, 'mode': manifest['mode'], 'file_count': len(files)}


def validate_package(root: Path) -> dict:
    """Verify every declared packed byte and reference, including shared objects."""
    return _validate_package(root, _PackageContext())


def package_campaign(campaign: Path, output: Path, *, mode: str) -> dict:
    """Write a new archive, or a new directory plus adjacent archive and receipt."""
    started = time.monotonic()
    if mode not in {'results', 'review'}:
        raise ValueError('Package mode must be results or review')
    campaign = _root(campaign)
    output = Path(output).absolute()
    if any(path.is_symlink() for path in (output, *output.parents)):
        raise ValueError('Symlink output path is not permitted')
    output = output.resolve()
    if output.is_relative_to(campaign):
        raise ValueError('Package output must be outside the campaign')
    archive_only = output.name.endswith('.tar.gz')
    archive = output if archive_only else output.with_name(output.name + '.tar.gz')
    receipt = archive.with_name(archive.name + '.receipt.json')
    from .artifact_store import reject_shared_output
    for publication in (output, archive, receipt):
        reject_shared_output(publication)
    for path in {output, archive, receipt}:
        if path.exists():
            raise FileExistsError(f'Package output already exists: {path}')
    context = _PackageContext()
    source_inventory, selected = _scan(campaign, mode=mode)
    runs = _validate_runs(campaign, context)
    if not selected:
        raise ValueError('Campaign contains no allowlisted result artifacts')
    for path in selected:
        _check_json(path)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix='.result-package-', dir=output.parent))
    archive_created, receipt_created = False, False
    try:
        for source in selected:
            _copy(source, staging / source.relative_to(campaign), staging)
        if mode == 'results':
            for run in runs:
                if (run / 'artifact-refs.json').exists():
                    refs, _ = _resolve_shared(campaign, run, context)
                    packed_run = staging / run.relative_to(campaign)
                    _private_json(packed_run / 'artifact-identities.json', {
                        'format': 'sn-packaged-artifact-identities-v1',
                        **{key: value for key, value in refs.items() if key not in {'format', 'root'}},
                    })
        if mode == 'review':
            for run in runs:
                if (run / 'artifact-refs.json').exists():
                    _shared_dependencies(campaign, run, staging, context)
                else:
                    _legacy_dependencies(campaign, run, staging)
                _review_declared_dependencies(campaign, run, staging)
        _private_json(staging / 'inventory.json', {
            'format': 'sn-result-package-inventory-v1', 'mode': mode,
            'campaign': source_inventory, 'packed': inventory(staging),
            'preparation_seconds': time.monotonic() - started,
        })
        hashes = {path.relative_to(staging).as_posix(): digest(path) for path, _ in _walk(staging)}
        _private_json(staging / 'package-manifest.json', {
            'format': 'sn-result-package-v1', 'mode': mode, 'files': hashes,
        })
        _validate_package(staging, context)
        descriptor = os.open(archive, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        archive_created = True
        with os.fdopen(descriptor, 'wb') as handle, tarfile.open(fileobj=handle, mode='w:gz') as tar:
            packed_files = list(_walk(staging))
            directories = {parent.relative_to(staging).as_posix()
                           for path, _ in packed_files for parent in path.parents
                           if parent != staging and parent.is_relative_to(staging)}
            for name in sorted(directories):
                member = tarfile.TarInfo(name)
                member.type, member.mode = tarfile.DIRTYPE, 0o700
                tar.addfile(member)
            for path, info in packed_files:
                member = tarfile.TarInfo(path.relative_to(staging).as_posix())
                member.size, member.mode = info.st_size, 0o600
                with path.open('rb') as source:
                    tar.addfile(member, source)
        report = {'format': 'sn-result-package-receipt-v1', 'mode': mode,
                  'archive': archive.name, 'package_bytes': archive.stat().st_size,
                  'archive_sha256': digest(archive), 'elapsed_seconds': time.monotonic() - started,
                  'inventory': source_inventory, 'packed': inventory(staging),
                  'validation': dict(context.reader.stats),
                  'shared_objects_copied': len(context.copied_objects)}
        _private_json(receipt, report)
        receipt_created = True
        if not archive_only:
            if output.exists():
                raise FileExistsError('Package output appeared during packaging')
            staging.rename(output)
        return report
    except BaseException:
        if archive_created:
            archive.unlink(missing_ok=True)
        if receipt_created:
            receipt.unlink(missing_ok=True)
        raise
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--campaign', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--mode', choices=('results', 'review'), required=True)
    args = parser.parse_args(argv)
    try:
        report = package_campaign(args.campaign, args.output, mode=args.mode)
    except (ValueError, OSError, KeyError) as exc:
        parser.exit(1, f'Result packaging stopped: {exc}\n')
    print(json.dumps(report, ensure_ascii=False, allow_nan=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
