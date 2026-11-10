"""Fixed task-and-metric notes for the analyst, selected by the iteration's input.

Definitions only: what the benchmark asks and what the primary metric counts.
Never a question, a gold answer, a case id or a score. The notes exist because an
analyst that does not know the metric drafts changes that work against it (e.g.
"give one short answer" on a task scored by covering every reading).
"""
from __future__ import annotations

ALCE_ASQA = """## Task and metric (fixed definitions; no cases)
- Task: ASQA-style long-form factoid questions. Many questions are ambiguous: one surface question has several
  plausible readings (different people, editions, dates, places or senses of a term). The reference answer is
  a short passage that resolves the ambiguity by answering each reading in turn.
- Primary metric str_em: for every reading, the reference lists acceptable short answers; a reading counts as
  covered when one of its short answers appears verbatim (case- and punctuation-insensitive) in the response.
  The score is the covered fraction of readings. It does not reward brevity and does not penalise extra
  correct readings; it does penalise answering only one reading, paraphrasing the short answer away (e.g.
  "the late 1990s" instead of the exact year), and omitting the asked value.
- Implications for product changes: recognising that a question is ambiguous and answering each plausible
  reading with its exact name/number/date tends to help; collapsing to a single reading, shortening answers
  to one fact, or moving the asked value into vague prose tends to hurt. Improvements must be general product
  behaviour grounded in the notebook's evidence, never padding with unsupported guesses.

"""

GENERIC = ""


def task_note(protocol_input: dict | None) -> str:
    """The note for this input (empty when none applies)."""
    data = protocol_input or {}
    name = ' '.join(str(data.get(k) or '') for k in ('input_id', 'suite', 'dataset', 'title')).casefold()
    if 'asqa' in name:
        return ALCE_ASQA
    return GENERIC
