"""C02 adapter checks with synthetic scores, real canonical bundles and snapshots.

No model/scorer runs: the hand-written scored fixtures deliberately separate
official batch estimates from per-case means.
"""
import hashlib
import importlib
import json
import shutil
import sys
from contextlib import contextmanager

import pytest

from test_benchmark_comparison import examples, rehash, save
from rag_eval.benchmark_official import prepare_inputs, scorer_identity
from rag_eval.benchmark_submission import build_submission
from rag_eval.identity import fingerprint
from rag_eval.notebook_bundle import load_bundle
from rag_eval.workbench.ingest import import_result, json_bytes, read_snapshot
from rag_eval.workbench.repository import WorkbenchRepository


def compare(*args, **kwargs):
    return importlib.import_module('rag_eval.workbench.result_comparison').compare_task_results(
        *args, **kwargs)


def fixture(tmp_path, *, change=None):
    bundle, entries, submissions, scores = examples(tmp_path, two_groups=True)
    if change:
        change(bundle, submissions, scores)
    for entry, sub, score in zip(entries, submissions, scores):
        save(entry['submission'], sub)
        save(entry['scores'], score)
    repo = WorkbenchRepository(tmp_path / 'independent-state')
    workspace = repo.register_workspace('Synthetic C02', tmp_path)
    task = repo.create_task(workspace['id'], 'Compare synthetic methods', '', request_key='task')
    ids = [import_result(tmp_path / 'bundle', **entry, state_root=repo.root)['id'] for entry in entries]
    for result_id in ids:
        repo.attach_result(task['id'], result_id)
    return repo, task['id'], ids, entries


def refresh(bundle, submissions, scores, i, *, case_ids=None):
    old = submissions[i]
    ids = case_ids if case_ids is not None else old['case_ids']
    sub = build_submission(bundle, method=old['method'],
                           predictions=[r for r in old['predictions'] if r['case_id'] in ids],
                           case_ids=ids)
    submissions[i] = sub
    score = scores[i]
    prepared = prepare_inputs(bundle, sub)
    score.update(submission_id=fingerprint(sub), prepared_id=fingerprint(prepared),
                 **{k: sub[k] for k in ('scope', 'case_ids', 'coverage', 'method', 'method_id')})
    score['per_case'] = [dict(row, status=prediction['status'])
                         for row, prediction in zip(
                             [r for r in score['per_case'] if r['case_id'] in ids], sub['predictions'])]
    rehash(score)


def file_hashes(root):
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in root.rglob('*') if p.is_file()}


def test_full_pair_preserves_official_estimates_and_has_real_frozen_sources_without_writes(tmp_path):
    repo, task_id, ids, entries = fixture(tmp_path)
    before = file_hashes(tmp_path)
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert (result['task_id'], result['left_result_id'], result['right_result_id']) == (task_id, *ids)
    assert result['status'] == 'comparable'
    assert result['reason'] is None
    report = result['report']
    assert report['scope'] == 'full'
    assert report['source'] == json.loads((tmp_path / 'source.json').read_text())
    assert report['methods'][0]['metrics'] == {'rougeL': .4, 'mauve': .2}
    assert report['methods'][1]['metrics'] == {'rougeL': .5, 'mauve': pytest.approx(.3)}
    assert report['paired_metrics'] == ['rougeL']
    assert report['comparisons'][0]['paired_mean_differences']['rougeL'] == pytest.approx(.1)
    assert report['comparisons'][0]['uncertainty']['rougeL']['computed'] is True
    for side, result_id, entry, method in zip(('left', 'right'), ids, entries, report['methods']):
        view = result[side]
        assert view['status'] == 'readable'
        assert view['read_reference'] == {'kind': 'result', 'result_id': result_id}
        assert view['summary']['case_count'] == view['summary']['bundle_case_count'] == 2
        assert view['summary']['pending_metrics'] == ['citation_support']
        snapshot = read_snapshot(repo.root, result_id)
        assert view['summary']['source'] == report['source'] == snapshot['bundle_manifest']['source']
        assert report['bundle_id'] == snapshot['submission']['bundle_id'] == fingerprint(snapshot['bundle_manifest'])
        assert view['summary']['metrics']['mauve']['kind'] == 'batch-only'
        assert method['files'] == {'snapshot_id': result_id, 'source_hashes': {
            name: hashlib.sha256(path.read_bytes()).hexdigest() for name, path in entry.items()}}
    assert file_hashes(tmp_path) == before  # Includes SQLite, frozen and original artifacts.


def test_two_subsets_use_complete_canonical_not_snapshot_sublist(tmp_path):
    def subset(bundle, subs, scores):
        for i in range(2):
            refresh(bundle, subs, scores, i, case_ids=subs[i]['case_ids'][:1])
    repo, task_id, ids, _ = fixture(tmp_path, change=subset)
    assert len(read_snapshot(repo.root, ids[0])['cases']) == 1
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'comparable'
    assert result['report']['scope'] == 'subset'
    assert result['report']['case_count'] == 1
    assert result['left']['summary']['bundle_case_count'] == 2
    assert result['left']['summary']['source'] == result['right']['summary']['source'] == result['report']['source']
    # Snapshot directory is never accepted as a full canonical bundle.
    refused = compare(repo, task_id, *ids, canonical_bundle_directory=repo.root / 'imports' / ids[0])
    assert refused['status'] == 'unavailable'
    assert refused['report'] is None


def test_missing_trusted_canonical_does_not_follow_commit_source(tmp_path):
    repo, task_id, ids, _ = fixture(tmp_path)
    result = compare(repo, task_id, *ids)
    assert result['status'] == 'unavailable'
    assert result['reason']['code'] == 'canonical_bundle_required'
    assert result['report'] is None
    assert result['left']['status'] == result['right']['status'] == 'readable'


@pytest.mark.parametrize('source_action', ['change', 'delete'])
def test_frozen_comparison_survives_original_submission_and_scores_changes(tmp_path, source_action):
    repo, task_id, ids, entries = fixture(tmp_path)
    first = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    for entry in entries:
        for path in entry.values():
            if source_action == 'change':
                path.write_text('{}')
            else:
                path.unlink()
    before = file_hashes(tmp_path)
    assert compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle') == first
    assert file_hashes(tmp_path) == before


@pytest.mark.parametrize('damage', ['missing', 'changed', 'invalid_json'])
def test_bad_snapshot_does_not_erase_readable_other_side_or_leak_private_path(tmp_path, damage):
    repo, task_id, ids, _ = fixture(tmp_path)
    target = repo.root / 'imports' / ids[1] / 'snapshot.json'
    if damage == 'missing':
        target.unlink()
    else:
        target.write_text('{}' if damage == 'changed' else '{')
    before = file_hashes(tmp_path)
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'unavailable'
    assert result['report'] is None
    assert result['left']['status'] == 'readable'
    assert result['left']['summary']['metrics']['rougeL']['value'] == .4
    assert result['right']['status'] == 'unavailable'
    assert result['right']['summary'] is None
    assert result['right']['reason']['detail']
    assert str(tmp_path) not in json.dumps(result)
    assert file_hashes(tmp_path) == before


@pytest.mark.parametrize('damage', ['missing', 'hash', 'json', 'object_shape', 'missing_field'])
def test_missing_or_corrupt_canonical_is_unavailable_with_both_summaries(tmp_path, damage):
    repo, task_id, ids, _ = fixture(tmp_path)
    if damage == 'missing':
        shutil.rmtree(tmp_path / 'bundle')
    elif damage == 'hash':
        (tmp_path / 'bundle' / 'cases.jsonl').write_text('{}\n')
    elif damage == 'json':
        (tmp_path / 'bundle' / 'manifest.json').write_text('{')
    elif damage == 'object_shape':
        (tmp_path / 'bundle' / 'manifest.json').write_text('[]')
    else:
        path = tmp_path / 'bundle' / 'manifest.json'
        manifest = json.loads(path.read_text())
        manifest.pop('max_documents')
        save(path, manifest)
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'unavailable'
    assert result['reason']['code'] == 'canonical_bundle_unavailable'
    assert result['reason']['detail']
    assert result['left']['status'] == result['right']['status'] == 'readable'
    assert str(tmp_path) not in json.dumps(result)


def test_both_snapshot_failures_are_preserved(tmp_path):
    repo, task_id, ids, _ = fixture(tmp_path)
    for result_id in ids:
        (repo.root / 'imports' / result_id / 'COMMIT.json').unlink()
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'unavailable'
    assert result['left']['reason']['code'] == result['right']['reason']['code'] == 'snapshot_unavailable'
    assert result['left']['summary'] is result['right']['summary'] is None
    assert result['report'] is None


def test_invalid_caller_path_type_is_not_hidden_as_artifact_unavailability(tmp_path):
    repo, task_id, ids, _ = fixture(tmp_path)
    with pytest.raises(TypeError):
        compare(repo, task_id, *ids, canonical_bundle_directory=object())


@pytest.mark.parametrize('selection', ['same', 'unknown_task', 'unattached', 'wrong_task'])
def test_invalid_selection_is_input_error_and_does_not_write(tmp_path, selection):
    repo, task_id, ids, _ = fixture(tmp_path)
    if selection == 'same':
        ids[1] = ids[0]
    elif selection == 'unknown_task':
        task_id = 'unknown'
    elif selection == 'unattached':
        ids[1] = '0' * 64
    else:
        workspace = repo.workspaces()[0]
        task_id = repo.create_task(workspace['id'], 'Other', '', request_key='other')['id']
        repo.attach_result(task_id, ids[0])
    before = file_hashes(tmp_path)
    with pytest.raises(ValueError):
        compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert file_hashes(tmp_path) == before


def test_direct_association_and_fresh_snapshot_override_cached_summary(tmp_path):
    repo, task_id, ids, _ = fixture(tmp_path)
    with repo.connect(write=True) as conn:
        conn.execute('UPDATE task_results SET summary=? WHERE task_id=?',
                     (json.dumps({'id': 'fake', 'metrics': {'rougeL': 999}}), task_id))
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'comparable'
    assert result['left']['summary']['metrics']['rougeL']['value'] == .4
    with pytest.raises(ValueError, match='attached'):
        compare(repo, task_id, ids[0], 'fake', canonical_bundle_directory=tmp_path / 'bundle')


def rejection_change(name):
    def change(bundle, subs, scores):
        if name in {'missing', 'error'}:
            subs[1]['predictions'][0].update(status=name, prediction='')
            refresh(bundle, subs, scores, 1)
        elif name in {'category', 'published-reference', 'duplicate_method'}:
            if name == 'category':
                subs[1]['method']['configuration'].pop('comparison_category')
            elif name == 'published-reference':
                subs[1]['method']['configuration']['comparison_category'] = name
            else:
                subs[1]['method'] = subs[0]['method']
                subs[1]['predictions'][0]['prediction'] = 'Different saved answer.'
            refresh(bundle, subs, scores, 1)
        elif name in {'scope', 'case_ids'}:
            if name == 'case_ids':
                refresh(bundle, subs, scores, 0, case_ids=subs[0]['case_ids'][:1])
            refresh(bundle, subs, scores, 1, case_ids=subs[1]['case_ids'][1:])
        elif name == 'scorer':
            scores[1]['dependencies']['python'] = 'different-synthetic-version'
            identity = scorer_identity(scores[1]['profile'], scores[1]['dependencies'])
            scores[1].update(scorer_identity=identity, scorer_id=fingerprint(identity))
        elif name == 'no_common_metrics':
            scores[1].update(metrics={'another': .5}, batch_only_metrics=['another'])
            for row in scores[1]['per_case']:
                row['metrics'] = {}
        else:
            for i, score in enumerate(scores):
                ids = subs[i]['case_ids']
                eligible = ids[:1] if i == 0 or name == 'eligible_ids' else ids
                if name == 'eligible_ids' and i == 1:
                    eligible = ids[1:]
                score.update(metric_denominators={'rougeL': 2, 'mauve': len(eligible)},
                             metric_case_ids={'rougeL': ids, 'mauve': eligible})
        for score in scores:
            rehash(score)
    return change


@pytest.mark.parametrize('mutation,detail', [
    ('missing', 'incomplete'), ('error', 'incomplete'), ('category', 'comparison_category'),
    ('published-reference', 'published-reference'), ('duplicate_method', 'Duplicate'),
    ('scope', 'scope'), ('case_ids', 'case_ids'), ('scorer', 'scorer identity'),
    ('no_common_metrics', 'No common'), ('denominators', 'denominators'), ('eligible_ids', 'IDs')])
def test_core_rejections_remain_not_comparable_not_unavailable(tmp_path, mutation, detail):
    repo, task_id, ids, _ = fixture(tmp_path, change=rejection_change(mutation))
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'not_comparable'
    assert result['report'] is None
    assert result['reason']['code'] == 'comparison_rejected'
    assert detail in result['reason']['detail']
    assert result['reason']['message']
    assert result['left']['status'] == result['right']['status'] == 'readable'


def test_different_bundle_is_not_comparable(tmp_path):
    repo, task_id, ids, _ = fixture(tmp_path)
    other = tmp_path / 'other'
    other.mkdir()
    _, entries, _, _ = examples(other, two_groups=False)
    result_id = import_result(other / 'bundle', **entries[1], state_root=repo.root)['id']
    repo.attach_result(task_id, result_id)
    result = compare(repo, task_id, ids[0], result_id, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'not_comparable'
    assert 'different frozen bundle' in result['reason']['detail']
    assert result['right']['summary']['scope'] == 'full'


def test_zero_unanswered_pending_and_batch_only_are_distinct(tmp_path):
    def unanswered(bundle, subs, scores):
        for i, status in enumerate(('no_answer', 'clarification')):
            subs[i]['predictions'][0].update(status=status, prediction='')
            scores[i]['metrics']['rougeL'] = 0
            scores[i]['per_case'][0]['metrics']['rougeL'] = 0
            refresh(bundle, subs, scores, i)
    repo, task_id, ids, _ = fixture(tmp_path, change=unanswered)
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'comparable'
    assert result['left']['summary']['metrics']['rougeL']['value'] == 0
    assert result['left']['summary']['coverage']['no_answer'] == 1
    assert result['right']['summary']['coverage']['clarification'] == 1
    assert result['report']['methods'][0]['pending_metrics'] == ['citation_support']
    assert result['report']['methods'][0]['batch_only_metrics'] == ['mauve']
    paired = result['report']['comparisons'][0]['paired'][0]
    assert (paired['left_status'], paired['right_status']) == ('no_answer', 'clarification')
    assert paired['left']['rougeL'] == paired['right']['rougeL'] == 0


def refreeze(repo, task_id, result_id, mutate):
    """Create a NEW, checksum-valid persisted object; do not tamper the old ID."""
    snapshot = read_snapshot(repo.root, result_id)
    mutate(snapshot)
    new_id = fingerprint(snapshot)
    assert new_id != result_id
    target = repo.root / 'imports' / new_id
    target.mkdir()
    payload = json_bytes(snapshot)
    commit = json.loads((repo.root / 'imports' / result_id / 'COMMIT.json').read_text())
    commit.update(id=new_id, sha256=hashlib.sha256(payload).hexdigest())
    (target / 'snapshot.json').write_bytes(payload)
    (target / 'COMMIT.json').write_bytes(json_bytes(commit))
    assert read_snapshot(repo.root, new_id) == snapshot
    repo.attach_result(task_id, new_id)
    return new_id


def test_legal_null_batch_only_metrics_follow_core_normalization_without_changing_scores(tmp_path):
    def no_batch_metric(bundle, subs, scores):
        for score in scores:
            score['metrics'].pop('mauve')
            score['batch_only_metrics'] = None
            rehash(score)
    repo, task_id, ids, entries = fixture(tmp_path, change=no_batch_metric)
    core = importlib.import_module('rag_eval.benchmark_comparison')
    expected = core.compare_submissions(tmp_path / 'bundle', entries)
    frozen = [read_snapshot(repo.root, result_id) for result_id in ids]
    assert core.compare_frozen_submissions(tmp_path / 'bundle', [
        {'submission': s['submission'], 'scores': s['scores'],
         'source': {'snapshot_id': result_id, 'source_hashes': s['source_hashes']}}
        for s, result_id in zip(frozen, ids)])['common_metrics'] == ['rougeL']
    before = file_hashes(tmp_path)
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'comparable'
    assert result['left']['summary']['metrics'] == {
        'rougeL': {'value': .4, 'denominator': 2, 'kind': 'per-case'}}
    assert result['report']['methods'][0]['metrics'] == expected['methods'][0]['metrics']
    assert result['report']['methods'][0]['batch_only_metrics'] == []
    assert read_snapshot(repo.root, ids[0])['scores']['batch_only_metrics'] is None
    assert file_hashes(tmp_path) == before


@pytest.mark.parametrize('mutation', ['manifest', 'question', 'references', 'case_count',
                                    'documents', 'denominators', 'eligible', 'per_metrics', 'batch_only'])
def test_new_hash_snapshot_must_bind_all_projections_to_complete_canonical(tmp_path, mutation):
    repo, task_id, ids, _ = fixture(tmp_path)
    def mutate(s):
        if mutation == 'manifest':
            s['bundle_manifest']['source']['revision'] = 'unrelated-new-revision'
        elif mutation == 'question':
            s['cases'][0]['question'] = 'Unrelated saved question?'
        elif mutation == 'references':
            s['cases'][0]['references'] = ['Unrelated answer.']
        elif mutation == 'case_count':
            s['bundle_case_count'] = 100
        elif mutation == 'documents':
            s['documents'][0]['text'] = 'Unrelated saved source material.'
        elif mutation == 'denominators':
            s['denominators']['rougeL'] = 1
        elif mutation == 'eligible':
            s['eligible']['rougeL'] = s['eligible']['rougeL'][:1]
        elif mutation == 'per_metrics':
            s['per_metrics'] = []
        else:
            s['batch_only'] = []
    new_id = refreeze(repo, task_id, ids[1], mutate)
    before = file_hashes(tmp_path)
    result = compare(repo, task_id, ids[0], new_id, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'unavailable'
    assert result['report'] is None
    assert result['right']['status'] == 'unavailable'
    assert result['right']['reason']['code'] == 'snapshot_unavailable'
    assert result['right']['reason']['detail']
    assert result['left']['summary']['metrics']['rougeL']['value'] == .4
    assert str(tmp_path) not in json.dumps(result)
    assert file_hashes(tmp_path) == before


@pytest.mark.parametrize('canonical', [True, False])
def test_checksum_valid_malformed_score_structure_preserves_the_other_side(tmp_path, canonical):
    repo, task_id, ids, _ = fixture(tmp_path)
    def bad_denominators(snapshot):
        snapshot['scores']['metric_denominators'] = {}
        rehash(snapshot['scores'])
    new_id = refreeze(repo, task_id, ids[1], bad_denominators)
    before = file_hashes(tmp_path)
    result = compare(repo, task_id, ids[0], new_id,
                     canonical_bundle_directory=tmp_path / 'bundle' if canonical else None)
    assert result['status'] == 'unavailable'
    assert result['right']['summary'] is None
    assert result['right']['reason']['code'] == 'snapshot_unavailable'
    assert 'denominator' in result['right']['reason']['detail'].lower()
    assert result['left']['status'] == 'readable'
    assert result['left']['summary']['metrics']['rougeL']['value'] == .4
    assert result['report'] is None
    assert file_hashes(tmp_path) == before


@contextmanager
def change_manifest_between_real_reads(canonical, damage, *, restore_for_diagnosis):
    """Schedule real file changes; never replace the loader or comparison core."""
    manifest_path = canonical / 'manifest.json'
    original = manifest_path.read_bytes()
    malformed = json.loads(original)
    if damage == 'array':
        malformed = []
    elif damage == 'missing_key':
        malformed.pop('max_documents')
    else:
        malformed['files'] = None
    reads = []
    previous_trace = sys.gettrace()
    def trace(frame, event, arg):
        if event == 'call' and frame.f_code is load_bundle.__code__:
            reads.append(frame.f_locals['directory'])
            if len(reads) == 2:
                manifest_path.write_bytes(json_bytes(malformed))
            elif len(reads) == 3 and restore_for_diagnosis:
                manifest_path.write_bytes(original)
        return trace
    try:
        sys.settrace(trace)
        yield reads
    finally:
        sys.settrace(previous_trace)


@pytest.mark.parametrize('damage', ['array', 'missing_key', 'invalid_files_type'])
def test_canonical_damaged_after_first_load_preserves_both_readable_sides(tmp_path, damage):
    repo, task_id, ids, _ = fixture(tmp_path)
    original_hashes = file_hashes(tmp_path)
    canonical = tmp_path / 'trusted-copy'
    shutil.copytree(tmp_path / 'bundle', canonical)
    with change_manifest_between_real_reads(canonical, damage, restore_for_diagnosis=False) as reads:
        result = compare(repo, task_id, *ids, canonical_bundle_directory=canonical)
    assert len(reads) == 3
    assert result['status'] == 'unavailable'
    assert result['reason']['code'] == 'canonical_bundle_unavailable'
    assert result['reason']['detail']
    assert result['report'] is None
    assert result['left']['status'] == result['right']['status'] == 'readable'
    assert result['left']['summary']['metrics']['rougeL']['value'] == .4
    assert result['right']['summary']['metrics']['rougeL']['value'] == .5
    assert str(tmp_path) not in json.dumps(result)
    assert {p: value for p, value in file_hashes(tmp_path).items()
            if not p.startswith('trusted-copy/')} == original_hashes


@pytest.mark.parametrize('damage,error_type', [('array', AttributeError), ('missing_key', KeyError),
                                             ('invalid_files_type', TypeError)])
def test_healthy_canonical_diagnostic_reread_propagates_original_structure_error(tmp_path, damage, error_type):
    repo, task_id, ids, _ = fixture(tmp_path)
    canonical = tmp_path / 'trusted-copy'
    shutil.copytree(tmp_path / 'bundle', canonical)
    before = file_hashes(tmp_path)
    with change_manifest_between_real_reads(canonical, damage, restore_for_diagnosis=True) as reads:
        with pytest.raises(error_type) as caught:
            compare(repo, task_id, *ids, canonical_bundle_directory=canonical)
    assert len(reads) == 3
    if damage == 'missing_key':
        assert caught.value.args == ('max_documents',)
    assert file_hashes(tmp_path) == before
