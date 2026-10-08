"""Canonical-bundle installation and bounded partition capsules.

Derived capsule hashes are separate from the original official bundle identity.
Generation receives only the product request; labelled cases remain evaluator-side.
"""
from __future__ import annotations

from collections import defaultdict
import hashlib
import json
from pathlib import Path

from .artifact_store import ArtifactStore, atomic_pointer
from .artifacts import digest
from .identity import fingerprint
from .notebook_bundle import REQUEST_REVISIONS, partition_bundle

FORMAT = 'sn-bundle-index-v1'
CAPSULE = 'sn-partition-capsule-v1'


def _bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')) + '\n').encode()


class BundleLookup:
    """Build shared ID indexes once, preserve canonical ordering in partitions."""
    def __init__(self, bundle):
        self.bundle = bundle
        self.cases = {c['case_id']: c for c in bundle['cases']}
        self.documents = {d['id']: d for d in bundle['documents']}
        self.partitions = {p['partition_id']: p for p in bundle['partitions']}
        self.decisions = defaultdict(list)
        for ordinal, decision in enumerate(bundle['decisions']):
            self.decisions[decision['case_id']].append((ordinal, decision))

    def partition(self, partition_id, request_revision):
        if partition_id not in self.partitions:
            raise ValueError('Unknown notebook partition')
        part = self.partitions[partition_id]
        cases = [self.cases[cid] for cid in part['case_ids']]
        documents = [self.documents[did] for did in part['document_ids']]
        decisions = [d for _, d in sorted(item for cid in part['case_ids'] for item in self.decisions[cid])]
        subset = dict(self.bundle, cases=cases, documents=documents, decisions=decisions, partitions=[part])
        product = partition_bundle(subset, partition_id, request_revision=request_revision)
        return dict(format=CAPSULE, manifest=self.bundle['manifest'], partition=part, cases=cases,
                    documents=documents, decisions=decisions, product=product,
                    qasper_evidence_catalogues={did: self.bundle['qasper_evidence_catalogues'][did]
                        for did in part['document_ids']} if 'qasper_evidence_catalogues' in self.bundle else {})


def install_bundle(directory, artifact_root):
    from .notebook_bundle import load_bundle
    directory = Path(directory).resolve()
    artifact_path = Path(artifact_root).resolve()
    if artifact_path.is_relative_to(directory) or directory.is_relative_to(artifact_path):
        raise ValueError('Shared store must be separate from original frozen bundle')
    bundle = load_bundle(directory)
    store = ArtifactStore(artifact_root)
    lookup = BundleLookup(bundle)
    files, entries = {}, {}
    for partition_id in lookup.partitions:
        revisions = {}
        for request_revision in REQUEST_REVISIONS:
            name = 'partitions/' + fingerprint([partition_id, request_revision]) + '.json'
            content = _bytes(lookup.partition(partition_id, request_revision))
            files[name] = content
            revisions[request_revision] = name
        entries[partition_id] = revisions
    paths = {name: directory / name for name in ('manifest.json', *bundle['manifest']['files'])}
    capsule_hashes = {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}
    ref = store.install_files('bundle', paths, identity={'bundle_id': fingerprint(bundle['manifest']),
                                                       'partition_capsules': capsule_hashes})
    copied = store.resolve(ref)
    if (json.loads((copied / 'manifest.json').read_text()) != bundle['manifest']
            or any(digest(copied / name) != expected for name, expected in bundle['manifest']['files'].items())):
        raise ValueError('Bundle source changed while installing')
    files['index.json'] = _bytes(dict(format=FORMAT, bundle=ref, source_manifest=bundle['manifest'],
                                    partitions=entries))
    index = store.install_bytes('index', files, identity=dict(format=FORMAT, bundle=ref))
    refs = {'bundle': ref, 'index': index}
    atomic_pointer(store.root / 'bundle-indexes' / (fingerprint(bundle['manifest']) + '.json'), refs)
    return refs


def load_partition(artifact_root, refs, partition_id, request_revision, *, verify_all=False):
    if request_revision not in REQUEST_REVISIONS:
        raise ValueError('Unknown notebook request revision')
    store = ArtifactStore(artifact_root)
    directory = store.resolve(refs['index'], files=None if verify_all else ['index.json'])
    index = json.loads((directory / 'index.json').read_text(encoding='utf-8'))
    if index.get('format') != FORMAT or index.get('bundle') != refs['bundle']:
        raise ValueError('Partition index refers to a different bundle')
    try:
        name = index['partitions'][partition_id][request_revision]
    except KeyError as exc:
        raise ValueError('Unknown notebook partition') from exc
    bundle_dir = store.resolve(refs['bundle'], files=['manifest.json'])
    bundle_object = json.loads((bundle_dir / 'object.json').read_text())
    index_object = json.loads((directory / 'object.json').read_text())
    if (json.loads((bundle_dir / 'manifest.json').read_text()) != index['source_manifest']
            or bundle_object['identity'].get('partition_capsules', {}).get(name) != index_object['files'].get(name)):
        raise ValueError('Partition capsule canonical bundle binding changed')
    store.resolve(refs['index'], files=[name])
    capsule = json.loads((directory / name).read_text(encoding='utf-8'))
    if (capsule.get('format') != CAPSULE or capsule.get('manifest') != index['source_manifest']
            or capsule.get('partition', {}).get('partition_id') != partition_id
            or capsule.get('product', {}).get('manifest', {}).get('partition_id') != partition_id):
        raise ValueError('Partition capsule identity changed')
    # Hash verification authenticates the installed object; reconstruct the
    # public request from its evaluator-side subset at this consumer boundary.
    # This retains bounded work and checks all material and gold separation.
    subset = dict(capsule, partitions=[capsule['partition']])
    if not capsule.get('qasper_evidence_catalogues'):
        subset.pop('qasper_evidence_catalogues', None)
    expected = partition_bundle(subset, partition_id, request_revision=request_revision)
    if capsule['product'] != expected:
        raise ValueError('Partition capsule public request/product changed')
    return capsule


def find_installed_bundle(directory, artifact_root, *, expected_index_id):
    """Find an explicitly installed object from the small original manifest.

    This checks manifest identity only; actual shared bytes are checked at their
    consumer boundary. Changes to an unused original raw path do not mutate the
    explicitly frozen store and cannot change the run's recorded source.
    """
    directory = Path(directory).resolve()
    manifest = json.loads((directory / 'manifest.json').read_text())
    store = ArtifactStore(artifact_root)
    pointer = store.root / 'bundle-indexes' / (fingerprint(manifest) + '.json')
    if not pointer.is_file() or pointer.is_symlink():
        raise ValueError('Bundle not installed; prepare with --artifact-root before running')
    refs = json.loads(pointer.read_text())
    if not isinstance(expected_index_id, str) or refs.get('index', {}).get('id') != expected_index_id:
        raise ValueError('Installed partition index differs from pinned canonical installation identity')
    location = store.resolve(refs['bundle'], files=['manifest.json'])
    obj = json.loads((location / 'object.json').read_text())
    if (json.loads((location / 'manifest.json').read_text()) != manifest
            or obj['identity'].get('bundle_id') != fingerprint(manifest)
            or obj['files'] != dict(manifest['files'], **{'manifest.json': digest(directory / 'manifest.json')})):
        raise ValueError('Installed bundle manifest differs from supplied bundle')
    store.resolve(refs['index'], files=['index.json'])
    return refs
