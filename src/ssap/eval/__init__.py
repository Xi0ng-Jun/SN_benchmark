"""Fork-aligned evaluation and the pure ``C1`` holdout protocol.

* ``infra_ledger``  -- ``infra_error`` versus scored task failure, paired reruns, ``delta_worst``;
* ``event_ledger``  -- ``Rescued / Neutral / Damaged`` events and ``K_idle`` zero-rescue pruning;
* ``fork_eval``     -- dev diagnostics and the three fork-point evaluation paths;
* ``c1_protocol``   -- pure ``C1`` validation selection and the sealed multi-seed test report.

No statistical veto gate exists anywhere in this package: promotion and selection use only the
sign of a net difference on the native primary metric ``Y*``.
"""
