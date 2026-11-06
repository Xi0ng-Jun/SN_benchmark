"""Deferred service + offline adapter integration; no services or models."""
import dataclasses

import pytest

from rag_eval.optimizer.adapter import EvalResult, Version
from rag_eval.optimizer.v4 import calibrate, decide
from rag_eval.optimizer.v4.config import V4Config
from rag_eval.optimizer.v4.loop import V4Run
from rag_eval.workbench import execution_scopes
from test_workbench_iterations import env  # noqa: F401
from test_workbench_deferred_split import deferred, P as D, D as P  # two-case probe, one-case diagnosis
from .v4_mock import V4Mock


def test_smallest_deferred_scope_and_imported_baseline_reuse(env, tmp_path):
    service = env['service']
    tree = deferred(env)
    tree = service.register_calibration_samples(tree['id'], P, D, actor='alice')
    before = {kind: execution_scopes.subset_plan(service, tree, kind, ids)
              for kind, ids in [('probe', P), ('diag', D)]}
    mock = V4Mock([])
    probe = mock.evaluate_subset(mock.seed, None, 'probe', P, 3)[0]
    diagnosis = mock.evaluate_search(mock.seed, D, 1)
    tree = service.freeze_split(tree['id'], D, P, actor='alice')
    assert before == {kind: execution_scopes.subset_plan(service, tree, kind, ids)
                      for kind, ids in [('probe', P), ('diag', D)]}
    measured = set(mock.measured)
    bundle = dict(identity=dict(seed=mock.seed.content_hash, calibration_k=3),
                  probe=P, diagnosis=D, results=dict(probe=dataclasses.asdict(probe),
                                                     diagnosis=dataclasses.asdict(diagnosis)),
                  summary=dict(rerun_var=0, safeguard_var={}), calibration=dict(rerun_var=0))
    run = V4Run(mock, mock.seed, V4Config(time_budget_hours=1), search=D, validation=P,
                state_dir=tmp_path / 'run', calibration_bundle=bundle)
    try:
        run._calibrate()
        assert set(run.parent_val(mock.seed).per_task_scores) == set(P)
        assert run._diagnose(mock.seed) == diagnosis
        assert mock.measured == measured
        assert {len(v) for v in decide.attempts_by_task(run.parent_val(mock.seed)).values()} == {3}
    finally:
        for pool in [*run._pools.values(), run._prefetch_pool, run._analysis_pool]:
            pool.shutdown(wait=True)
