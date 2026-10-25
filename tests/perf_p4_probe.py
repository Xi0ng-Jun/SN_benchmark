"""Offline probe for P4: bytes hashed and bundle/partition loads on the per-partition path.

Run: PYTHONPATH=src:tests python tests/perf_p4_probe.py
Builds a synthetic captured SN source (default 1000 files, ~20 MB) and a
multi-partition QASPER bundle in a temporary directory; no SN, no model.
"""
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]


class Counter:
    def __init__(self):
        self.bytes = 0
        self.calls = {}


@contextmanager
def counting():
    counter = Counter()
    original_sha, original_file = hashlib.sha256, hashlib.file_digest

    class Proxy:
        def __init__(self, data=b'', **kwargs):
            self._h = original_sha(**kwargs)
            self.update(data)

        def update(self, data):
            counter.bytes += len(data)
            self._h.update(data)

        def __getattr__(self, name):
            return getattr(self._h, name)

    def file_digest(handle, name):
        result = original_file(handle, name)
        try:
            counter.bytes += handle.tell()
        except (OSError, ValueError):
            pass
        return result
    hashlib.sha256, hashlib.file_digest = Proxy, file_digest
    try:
        yield counter
    finally:
        hashlib.sha256, hashlib.file_digest = original_sha, original_file


@contextmanager
def tally(counter, module, name):
    original = getattr(module, name)

    def wrapped(*args, **kwargs):
        counter.calls[name] = counter.calls.get(name, 0) + 1
        return original(*args, **kwargs)
    setattr(module, name, wrapped)
    try:
        yield
    finally:
        setattr(module, name, original)


def build(directory, *, files=1000, size=20_000, papers=12):
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.bundle_index import install_bundle
    from rag_eval.notebook_bundle import prepare
    from rag_eval.sn_source_capture import capture_sn_source
    raw = {f'paper{p}': dict(title=f'P{p}', abstract='Birds.', full_text=[dict(section_name='R', paragraphs=[f'Fact {p}.'])],
                             qas=[dict(question_id=f'{p}-{q}', question='What?', answers=[dict(answer=dict(
                                 unanswerable=False, extractive_spans=['Fact'], free_form_answer='', yes_no=None,
                                 evidence=[f'Fact {p}.']))]) for q in range(3)]) for p in range(papers)}
    (directory / 'raw.json').write_text(json.dumps(raw))
    (directory / 'source.json').write_text(json.dumps(dict(dataset='qasper', split='test', revision='v0.3',
                                                           source_url='https://example.org', license='CC-BY-4.0')))
    bundle = prepare('qasper', directory / 'raw.json', directory / 'source.json', directory / 'bundle')
    store = ArtifactStore(directory / 'artifacts')
    refs = install_bundle(directory / 'bundle', store.root)
    project = directory / 'sn'
    for index in range(files):
        path = project / 'backend/app' / f'pkg{index % 20}' / f'module_{index}.py'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text((f'# module {index}\n' + 'X = 1\n' * (size // 6))[:size])
    (project / 'backend/app/__init__.py').write_text('')
    subprocess.run(['git', 'init', '-q', str(project)], check=True)
    subprocess.run(['git', '-C', str(project), 'add', '.'], check=True)
    subprocess.run(['git', '-C', str(project), '-c', 'user.name=probe', '-c', 'user.email=probe@example.invalid',
                    'commit', '-qm', 'probe'], check=True)
    source = capture_sn_source(project, store)
    return store, refs, project, source.ref, bundle


def main():
    from rag_eval import bundle_index, notebook_bundle, runtime_environment
    from rag_eval.artifact_store import write_run_refs
    from rag_eval.notebook_batch import plan_batch
    results = {}
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp)
        store, refs, project, sn_ref, bundle = build(directory)
        tar_bytes = (store.resolve(sn_ref, files=[]) / 'product-source.tar').stat().st_size
        results['sn_tar_mb'] = round(tar_bytes / 1e6, 1)
        shared = None
        materialize_once = getattr(runtime_environment, 'materialize_shared_sn_source', None)
        if materialize_once is not None:
            with counting() as counter:
                shared = materialize_once(store, sn_ref, directory / 'execution/invocation/sn-source')
            results['execution_materialize_mb'] = round(counter.bytes / 1e6, 1)
        per_partition = []
        for index in range(3):
            run = directory / f'run{index}'
            run.mkdir()
            write_run_refs(run, store.root, refs)
            kwargs = dict(artifact_root=store.root, captured_source_ref=sn_ref)
            if shared is not None:
                kwargs['shared_source'] = shared
            with counting() as counter:
                runtime_environment.snapshot_sources(ROOT, project, run, **kwargs)
            per_partition.append(round(counter.bytes / 1e6, 1))
        results['source_binding_mb_per_partition'] = per_partition
        # Reading a partition back (child report, parent stage metrics): input objects.
        from rag_eval.run_reader import RunReadContext
        with counting() as counter:
            RunReadContext(partition_only=True).input_directory(directory / 'run0')
        results['partition_read_inputs_mb'] = round(counter.bytes / 1e6, 1)
        # A one-partition retry of a bundle with many partitions.
        ids = directory / 'ids.txt'
        ids.write_text(bundle['partitions'][0]['case_ids'][0] + '\n')
        counter = Counter()
        with counting() as hashed, tally(counter, bundle_index, 'load_partition'):
            plan_batch(bundle=directory / 'bundle', artifact_root=store.root, artifact_index_id=refs['index']['id'],
                       case_id_file=ids, mode='chunk', output_root=directory / 'retry', project_root=project)
        results['plan_batch_partitions_in_bundle'] = len(bundle['partitions'])
        results['plan_batch_load_partition_calls'] = counter.calls.get('load_partition', 0)
        results['plan_batch_mb'] = round(hashed.bytes / 1e6, 2)
        # Republishing an existing evaluator object (once per partition).
        paths = {str(p.relative_to(ROOT)): p for p in sorted((ROOT / 'src/rag_eval').rglob('*.py'))}
        with counting() as counter:
            store.install_files('evaluator', paths, identity={'probe': True})
        with counting() as counter:
            store.install_files('evaluator', paths, identity={'probe': True})
        results['republish_existing_evaluator_mb'] = round(counter.bytes / 1e6, 2)
    print(json.dumps(results, indent=2))


if __name__ == '__main__':
    sys.path.insert(0, str(ROOT / 'src'))
    main()
