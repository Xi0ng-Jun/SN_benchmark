"""ALW mirror checkers ``m_base = (m_gds, m_lvs, m_shape, m_fill, m_morph, m_drc)``.

Re-implemented from the public semantics of the benchmark verifier (geometry, KLayout / Magic +
Netgen LVS, extracted device area, morphology screen, DRC). All task inputs are read from the
T=0 frozen copy ``.mcr/trusted_task/`` and the captured task prompt; no private verifier input,
no per-task constant and no benchmark package import is used.
"""
from .registry import ALW_TIERS, AlwContext, AlwSuite, Registry, alw_registry, alw_workspace

__all__ = ["ALW_TIERS", "AlwContext", "AlwSuite", "Registry", "alw_registry", "alw_workspace"]
