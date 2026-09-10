"""Observe native synthesis boundaries without changing model inputs."""
from contextlib import contextmanager
from copy import deepcopy
from functools import wraps
from inspect import signature

from .protocol import capture_context


@contextmanager
def capture_synthesis(service):
    calls, originals = [], {}

    def wrapper(original, name):
        sig = signature(original)

        @wraps(original)
        def observed(*args, **kwargs):
            bound = sig.bind_partial(*args, **kwargs)
            sink = bound.arguments.get('baseline_sink')
            if sink is None:
                sink = {}
                bound.arguments['baseline_sink'] = sink
            record = {'method': name, 'succeeded': False,
                      'sectioned': bool(bound.arguments.get('sectioned', False)),
                      'section_index': bound.arguments.get('section_index', 0),
                      'section_title': bound.arguments.get('section_title', '')}
            try:
                result = original(*bound.args, **bound.kwargs)
                record.update(succeeded=True, answer=str(result[0] or ''))
                return result
            finally:
                if 'context_block' in sink:
                    record.update(deepcopy(sink))
                    calls.append(record)
        return observed

    for name in ('_answer_chunks', '_answer_mix', '_answer_reasoning'):
        if hasattr(service, name):
            original = getattr(service, name)
            originals[name] = original
            setattr(service, name, wrapper(original, name))
    try:
        yield calls
    finally:
        for name, original in originals.items():
            setattr(service, name, original)


def final_context(calls):
    successful = [c for c in calls if c['succeeded'] and c.get('answer', '').strip()]
    if not successful or successful[-1].get('sectioned'):
        return dict(context_supported=False, retrieval_context=[], source_ids=[],
                    retrieved_ids=[], context_block='', handles=[],
                    context_unavailable_reason='sectioned_synthesis' if successful else 'no_successful_synthesis')
    result = capture_context(successful[-1])
    return dict(result, context_supported=True, context_unavailable_reason=None)
