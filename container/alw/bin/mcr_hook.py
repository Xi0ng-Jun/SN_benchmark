#!/usr/bin/env python3
"""Claude Code hook (``PostToolUse`` on Bash|Write|Edit, and ``Stop``).

1. Direct-overwrite rescue: if the submission differs from the certified best, the written
   content is saved to ``work/draft/_direct_snapshot/<seq>_<sha8>/``, evaluated, promoted if
   ratchet-better, else the submission is restored; the agent is told either way.
2. ``T_warn`` reminder (once; skipped when ``MCR_PHASE=B``).

Never blocks a tool call and always exits 0. Output: Claude Code hook JSON
(``hookSpecificOutput.additionalContext`` for PostToolUse, ``systemMessage`` for Stop), or
nothing when there is nothing to say.
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def main() -> int:
    try:
        event = json.loads(sys.stdin.read() or "{}")
    except ValueError:
        event = {}
    name = event.get("hook_event_name") or "PostToolUse"
    messages: list[str] = []
    try:
        import mcr_common as mc
        from ssap.kernel.promote import handle_direct_submission_write

        ws = mc.workspace()
        if ws.mcr_dir.is_dir():
            suite = mc.suite()
            rescue = handle_direct_submission_write(ws, suite.evaluate, suite.tier_spec,
                                                    validate=suite.validate)
            if rescue and rescue.get("additionalContext"):
                messages.append(rescue["additionalContext"])
            if os.environ.get("MCR_PHASE") != "B":
                wm = mc.watermarks()
                elapsed = mc.elapsed_sec()
                marker = ws.mcr_dir / "t_warn_notified"
                if elapsed is not None and wm.warn_due(elapsed) and not marker.exists():
                    marker.write_text(f"{elapsed:.1f}\n")
                    left = max(0.0, wm.t_soft_sec - elapsed)
                    messages.append(
                        f"[MCR] Time warning: {elapsed / 60:.0f} min elapsed; the session stops in "
                        f"about {left / 60:.0f} min. Converge now: finish the current edit, run "
                        "mcr_promote on work/draft/, and update /alb-workspace/.mcr/handoff.json "
                        "(status, known_blockers, next_steps).")
    except Exception as exc:  # noqa: BLE001 - a hook must never break the agent
        messages.append(f"[MCR] hook error ({type(exc).__name__}); submission unchanged by the hook.")
    if messages:
        text = "\n".join(messages)
        if name == "Stop":
            out = {"systemMessage": text}
        else:
            out = {"hookSpecificOutput": {"hookEventName": name, "additionalContext": text}}
        sys.stdout.write(json.dumps(out))
    return 0


if __name__ == "__main__":
    sys.exit(main())
