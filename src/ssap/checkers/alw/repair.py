"""Stateless Phase B repair: argument rewrite and repair prompt.

Shared by the live container wrapper (``mcr_entrypoint.py``) and the offline Path 1 replay
(``ssap.targets.alw.agent_wrapper.build_stateless_repair_invocation``), so both start Phase B
from the same arguments, prompt and budget inputs (prefix-invariant semantics).
"""
from __future__ import annotations

import json
from typing import Iterable

DROP_WITH_VALUE = {"--resume", "-r", "--session-id"}
DROP_FLAGS = {"--print", "-p", "--continue", "-c", "--fork-session"}
WORKSPACE = "/alb-workspace"


def phase_b_args(argv: Iterable[str], turns: int) -> list[str]:
    """Phase A arguments without session-resume flags, plus the stateless Phase B flags."""
    out, skip = [], False
    for a in argv:
        if skip:
            skip = False
            continue
        key = a.split("=", 1)[0]
        if key in DROP_WITH_VALUE:
            skip = "=" not in a
            continue
        if a in DROP_FLAGS or a == "--no-session-persistence":
            continue
        if key == "--max-turns":
            skip = "=" not in a
            continue
        out.append(a)
    return out + ["-p", "--no-session-persistence", "--max-turns", str(int(turns))]


def repair_prompt(gate: dict, fork: dict, handoff_record: dict | None,
                  workspace: str = WORKSPACE) -> str:
    w = workspace
    return f"""You are a stateless repair session for the layout task in {w}.
Read the original task statement in {w}/.mcr/task_prompt.txt first; it still applies.
A previous session ended. Its certified best layout is {w}/submission/layout.gds
(copy in {w}/.mcr/best/), its draft area is {w}/work/draft/.

Gate report (why this repair runs):
{json.dumps(gate, indent=1, default=str)}

Handoff record of the previous session:
{json.dumps(handoff_record, indent=1, default=str) if handoff_record else "(none)"}

Budget: about {int(fork['granted_repair_budget_sec'])} seconds and {fork['granted_repair_turns']} turns.
Rules: edit only work/draft/ (layout.gds, src/); certify with
`mcr_promote --gds work/draft/layout.gds --src work/draft/src` (JSON result; only a
ratchet-better candidate replaces submission/). `mcr_check` previews a certificate.
Never write submission/ directly. Fix the failing certificate components first
(m = m_gds, m_lvs, m_shape, m_fill, m_morph, m_drc), then stop.
"""
