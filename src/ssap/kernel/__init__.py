"""Benchmark-agnostic MCR kernel ``H_base``: ratchet order, atomic write-through promotion,
handoff record, fork-point snapshots, wall-clock watermarks and the per-trial artifact summary.

Benchmark knowledge (artifact names, mirror checkers, integrity validators) is injected by the
caller; nothing in this package names a benchmark, a process kit or an EDA tool.
"""
from .ratchet import Cert, Decision, TierSpec, certify, decide, is_strictly_better
from .promote import (PromoteResult, Workspace, draft_needs_evaluation,
                      handle_direct_submission_write, load_best, promote,
                      restore_submission_from_best)
from .watermarks import Watermarks, compute_gate

__all__ = ["Cert", "Decision", "TierSpec", "certify", "decide", "is_strictly_better",
           "PromoteResult", "Workspace", "draft_needs_evaluation", "handle_direct_submission_write",
           "load_best", "promote", "restore_submission_from_best", "Watermarks", "compute_gate"]
