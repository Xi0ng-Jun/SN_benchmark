"""Reachable public JSON category types must reject through the shared core."""
import pytest

from rag_eval.benchmark_comparison import compare_frozen_submissions, compare_submissions
from rag_eval.workbench.ingest import read_snapshot
from test_workbench_result_comparison import compare, file_hashes, fixture, refresh


@pytest.mark.parametrize('category', [['controlled-rerun'], {}])
def test_importable_nonstring_category_rejects_without_losing_readable_sides(tmp_path, category):
    def change(bundle, submissions, scores):
        submissions[1]['method']['configuration']['comparison_category'] = category
        refresh(bundle, submissions, scores, 1)

    repo, task_id, ids, entries = fixture(tmp_path, change=change)
    snapshots = [read_snapshot(repo.root, result_id) for result_id in ids]
    assert snapshots[1]['submission']['method']['configuration']['comparison_category'] == category
    before = file_hashes(tmp_path)
    result = compare(repo, task_id, *ids, canonical_bundle_directory=tmp_path / 'bundle')
    assert result['status'] == 'not_comparable'
    assert result['report'] is None
    assert result['reason']['code'] == 'comparison_rejected'
    assert 'comparison_category must be a string' in result['reason']['detail']
    for side, result_id in zip(('left', 'right'), ids):
        assert result[side]['status'] == 'readable'
        assert result[side]['summary']['id'] == result_id
        assert result[side]['read_reference'] == {'kind': 'result', 'result_id': result_id}

    frozen = [{'submission': snapshot['submission'], 'scores': snapshot['scores'],
               'source': {'snapshot_id': result_id, 'source_hashes': snapshot['source_hashes']}}
              for result_id, snapshot in zip(ids, snapshots)]
    with pytest.raises(ValueError, match='comparison_category must be a string'):
        compare_submissions(tmp_path / 'bundle', entries)
    with pytest.raises(ValueError, match='comparison_category must be a string'):
        compare_frozen_submissions(tmp_path / 'bundle', frozen)
    assert file_hashes(tmp_path) == before
