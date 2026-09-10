"""Observe provider log events inside one isolated, sequential runtime."""
from contextlib import contextmanager


@contextmanager
def capture_usage(logger_class):
    original = logger_class.log
    result = {'calls': [], 'coverage': 'provider_logged_events', 'cost': None}

    def observed(logger, record):
        result['calls'].append({k: record[k] for k in
            ('kind', 'model', 'status', 'latency_ms', 'usage', 'support_id') if k in record})
        return original(logger, record)

    logger_class.log = observed
    try:
        yield result
    finally:
        logger_class.log = original
        calls = result['calls']
        result['call_count'] = len(calls)
        result['calls_with_usage'] = sum(bool(c.get('usage')) for c in calls)
        for field in ('prompt_tokens', 'completion_tokens', 'total_tokens'):
            values = [c['usage'][field] for c in calls
                      if isinstance(c.get('usage'), dict) and isinstance(c['usage'].get(field), (int, float))]
            result[field] = sum(values) if values else None
