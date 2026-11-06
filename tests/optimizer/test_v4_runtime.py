"""Runtime contracts: observe actual coordinator ordering with synthetic, bounded work."""
import threading
import time

import pytest

from rag_eval.optimizer.adapter import EvaluationUnavailable
from rag_eval.optimizer.v4.config import V4Config
from rag_eval.optimizer.v4.loop import V4Run
from .v4_mock import ScriptedRun, V4Mock


def config(**kw):
    kw.setdefault("time_budget_hours", 1)
    kw.setdefault("b", 2)
    kw.setdefault("merge", False)
    kw.setdefault("ablation", False)
    return V4Config(**kw)


def execute(mock, cfg=None, path=None):
    run = ScriptedRun(mock, mock.seed, cfg or config(), search=mock.search, validation=mock.validation,
                      state_dir=path)
    return run, run.run()


def test_diagnosis_sample_is_reused_from_calibration_and_scores_reach_analyst():
    class Mock(V4Mock):
        def evaluate_search(self, version, task_ids, k):
            self.diagnoses.append((version.content_hash, tuple(task_ids), k))
            return super().evaluate_search(version, task_ids, k)

        def analyse(self, trajectories, n_cards, **kwargs):
            self.seen_scores = kwargs.get('case_scores')
            return super().analyse(trajectories, n_cards, **kwargs)

    mock = Mock([[], []], n_search=100)
    mock.diagnoses = []
    run, _ = execute(mock, config(diagnose_size=7))
    assert len(mock.diagnoses) == 1
    assert len(mock.diagnoses[0][1]) == 7 and mock.diagnoses[0][2] == 1
    assert mock.seen_scores and set(mock.seen_scores) <= set(mock.diagnoses[0][1])
    assert set(mock.diagnoses[0][1]) != set(sorted(mock.search)[:7])


def test_candidate_validation_failure_isolated_and_successful_sibling_adopted():
    mock = V4Mock([[dict(name='bad', delta=.2), dict(name='good', delta=.1)]])
    mock.fail_once['rest:bad'] = EvaluationUnavailable('candidate crashes')
    run, reason = execute(mock)
    assert reason == 'no_adoption'
    assert run.lineage[-1]['version'] == 'seed+r1-good'
    bad = next(r for r in run.rounds[0]['records'].values() if r['title'] == 'bad')
    assert bad['label'] == 'run_failed'


def test_consecutive_failures_halt_and_retry_resumes_unfinished_stage(tmp_path):
    mock = V4Mock([[dict(name='bad', delta=.2)]])
    mock.fail_once['rest:bad'] = EvaluationUnavailable('unavailable')
    cfg = config(b=1, max_consecutive_run_failures=1)
    run, reason = execute(mock, cfg, tmp_path)
    assert reason == 'process_failure' and run.run_failures == 1
    built = mock.calls['implement']
    resumed, reason = execute(mock, cfg, tmp_path)
    assert reason == 'no_adoption' and len(resumed.lineage) == 2
    assert mock.calls['implement'] == built


def test_waiting_minibatch_does_not_occupy_implementation_slot():
    second_built = threading.Event()
    class Mock(V4Mock):
        def implement(self, card, workspace):
            if card.failure_mode_desc == 'second':
                second_built.set()
            return super().implement(card, workspace)

        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if subset == 'mb' and version.content_hash == 'seed+r1-first' and not hasattr(self, 'overlapped'):
                self.overlapped = second_built.wait(.5)
            return super().evaluate_subset(version, reference, subset, task_ids, k)

    mock = Mock([[dict(name='first', delta=.1), dict(name='second', delta=.1)]])
    execute(mock, config(b=1))
    assert mock.overlapped


def test_reviewer_not_started_when_even_confirmed_mechanism_cannot_qualify():
    mock = V4Mock([[dict(name='tiny', delta=.00001)]])
    run, _ = execute(mock)
    assert mock.calls.get('review', 0) == 0
    rec = run.rounds[0]['records']['0']
    assert rec['label'] == 'below_threshold'


def test_early_merge_and_diagnosis_overlap_slow_candidate():
    merged = threading.Event()
    class Mock(V4Mock):
        def implement(self, card, workspace):
            if card.failure_mode_desc == 'slow':
                self.merged_before_slow = merged.wait(1)
            return super().implement(card, workspace)

        def rebase(self, candidate, new_parent):
            merged.set()
            return super().rebase(candidate, new_parent)

        def evaluate_search(self, version, task_ids, k):
            if version.content_hash != 'seed':
                self.prefetched.append(version.content_hash)
            return super().evaluate_search(version, task_ids, k)

    mock = Mock([[dict(name='A', delta=.1), dict(name='B', delta=.06), dict(name='slow', delta=-.2)]])
    mock.prefetched = []
    run, _ = execute(mock, config(b=3, merge=True))
    assert mock.merged_before_slow and mock.prefetched
    assert run.rounds[0]['decision']['merge']['adopt']


def test_history_survives_parent_adoption_without_numeric_validation_feedback():
    mock = V4Mock([[dict(name='A', delta=.1)], []])
    execute(mock)
    assert any(e.get('earlier_parent') and e['label'] == 'adopted' for e in mock.last_history)
    assert all('delta_val' not in e and 'se_val' not in e for e in mock.last_history)


# v4.1 removed round_timebox_hours: work overlaps naturally without artificial timeboxes


@pytest.mark.parametrize("failure_type", [EvaluationUnavailable, ValueError])
def test_ablation_measurements_overlap_and_failure_only_skips_one_unit(failure_type):
    barrier = threading.Barrier(2)
    class Mock(V4Mock):
        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if '*' in version.content_hash and subset == 'mb':
                try:
                    barrier.wait(.5)
                    self.overlap = True
                except threading.BrokenBarrierError:
                    self.overlap = False
                if version.content_hash.startswith('seed*r2-B'):
                    raise failure_type('one ablation failed')
            return super().evaluate_subset(version, reference, subset, task_ids, k)
    mock = Mock([[dict(name='A', delta=.1)], [dict(name='B', delta=.1)], [dict(name='C', delta=.1)], []])
    run, reason = execute(mock, config(ablation=True, ablation_parallel=3))
    assert reason == 'no_adoption' and mock.overlap
    assert run.ablation['status'] == 'done'
    assert any(row['status'] == 'run_failed' for row in run.ablation['units'].values())


def test_slow_review_does_not_block_next_candidate_measurement():
    second_measured = threading.Event()
    class Mock(V4Mock):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.overlap = False

        def review(self, card, diff, pairs, *, candidate, key):
            if card.failure_mode_desc == 'first':
                self.overlap = second_measured.wait(.5)
            return super().review(card, diff, pairs, candidate=candidate, key=key)

        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if subset == 'rest' and version.content_hash == 'seed+r1-second':
                second_measured.set()
            return super().evaluate_subset(version, reference, subset, task_ids, k)

    mock = Mock([[dict(name='first', delta=.1), dict(name='second', delta=.1)]])
    execute(mock, config(b=1))
    assert mock.overlap


def test_speculative_analysis_reused_only_on_unchanged_parent():
    measured = threading.Event()
    class Mock(V4Mock):
        def analyse(self, trajectories, n_cards, **kwargs):
            self.analyses.append((kwargs['round_id'], kwargs['parent'].content_hash))
            if kwargs['round_id'] == 2:
                measured.set()
            return super().analyse(trajectories, n_cards, **kwargs)

        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if version.content_hash == 'seed+r1-X' and subset == 'rest':
                assert measured.wait(.5)
            return super().evaluate_subset(version, reference, subset, task_ids, k)

    mock = Mock([[dict(name='X', delta=-.001)], []])
    mock.analyses = []
    run, _ = execute(mock, config())
    assert mock.analyses == [(1, 'seed'), (2, 'seed')]
    assert run.rounds[1]['analysis_prefetched']


def test_budget_cancels_blocked_work_and_never_merges_after_expiry():
    from rag_eval.optimizer.adapter import EvaluationCancelled
    class Mock(V4Mock):
        def configure_runtime(self, *, remaining, max_concurrent_executions):
            self.remaining = remaining
            self.cancelled = threading.Event()

        def check_runtime(self):
            if self.remaining() <= 0 or self.cancelled.is_set():
                raise EvaluationCancelled('deadline')

        def cancel_pending(self):
            self.cancelled.set()

        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if version.content_hash != 'seed' and subset == 'rest':
                while not self.cancelled.wait(.01):
                    self.check_runtime()
                self.check_runtime()
            return super().evaluate_subset(version, reference, subset, task_ids, k)

    mock = Mock([[dict(name='A', delta=.1), dict(name='B', delta=.1)]])
    start = time.monotonic()
    run, reason = execute(mock, config(time_budget_hours=.15/3600, merge=True))
    assert reason == 'time_budget' and time.monotonic() - start < 1
    assert mock.calls.get('rebase', 0) == 0 and len(run.lineage) == 1
    assert mock.cancelled.is_set()


def test_sibling_frozen_during_failure_shutdown_is_persisted_before_resume(tmp_path):
    building = threading.Event()
    class Mock(V4Mock):
        def implement(self, card, workspace):
            if card.failure_mode_desc == 'slow':
                building.set()
                time.sleep(.15)
            return super().implement(card, workspace)

        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if version.content_hash == 'seed+r1-bad' and subset == 'rest':
                assert building.wait(.5)
            return super().evaluate_subset(version, reference, subset, task_ids, k)

    mock = Mock([[dict(name='bad', delta=.1), dict(name='slow', delta=.2)]])
    mock.fail_once['rest:bad'] = EvaluationUnavailable('unavailable')
    cfg = config(max_consecutive_run_failures=1, speculative_analysis=False)
    run, reason = execute(mock, cfg, tmp_path)
    assert reason == 'process_failure'
    assert run.current['records']['1']['candidate'] == 'seed+r1-slow'
    assert run.current['records']['1']['stage'] == 'built'
    before = mock.calls['implement']
    execute(mock, cfg, tmp_path)
    assert mock.calls['implement'] == before


def test_candidate_exception_does_not_abort_sibling():
    mock = V4Mock([[dict(name='bad', delta=.1), dict(name='good', delta=.2)]])
    mock.fail_once['smoke:bad'] = ValueError('candidate returned malformed response')
    run, reason = execute(mock)
    assert reason == 'no_adoption' and run.lineage[-1]['version'] == 'seed+r1-good'
