"""Preparation concurrency that SN's model scheduler can actually queue.

SN's ServiceScheduler (``app/services/model_scheduler.py``) queues at most
2 x max_concurrency requests per actor; every preparation worker of a
partition acts for the same notebook and holds one chat request at a time
(source summary, then paper metadata). More workers than that make the
scheduler refuse calls with ModelQueueFull, which SN then hides behind
fallback summaries. Only the bindings and the numeric ``max_concurrency``
fields of the services config are read; nothing else from that private file
is used or reported.
"""
from __future__ import annotations

from pathlib import Path
import tomllib

PREPARATION_CHAT_WORKLOADS = ('source_summary', 'paper_metadata', 'notebook_metadata')
SN_ACTOR_QUEUE_FACTOR = 2


def preparation_chat_limit(model_config):
    """(service id, max_concurrency) of the most limited chat service preparation uses, or None."""
    try:
        with Path(model_config).open('rb') as handle:
            parsed = tomllib.load(handle)
    except (OSError, tomllib.TOMLDecodeError):
        return None
    services, bindings = parsed.get('services') or {}, parsed.get('bindings') or {}
    limits = []
    for workload in PREPARATION_CHAT_WORKLOADS:
        service_id = bindings.get(workload)
        service = services.get(service_id) if isinstance(service_id, str) else None
        if not isinstance(service, dict) or service.get('kind') != 'chat':
            continue
        value = service.get('max_concurrency')
        if type(value) is int and value > 0:
            limits.append((value, service_id))
    if not limits:
        return None
    value, service_id = min(limits)
    return service_id, value


def check_preparation_workers(workers, model_config):
    """Refuse preparation_workers that would overflow SN's per-actor queue."""
    if model_config is None or workers == 1:
        return
    limit = preparation_chat_limit(model_config)
    if limit is None:
        return
    service_id, concurrency = limit
    cap = SN_ACTOR_QUEUE_FACTOR * concurrency
    if workers > cap:
        raise ValueError(f'preparation_workers {workers} exceeds {SN_ACTOR_QUEUE_FACTOR} x max_concurrency '
                         f'{concurrency} of chat service {service_id!r} = {cap}, SN\'s per-actor queue cap; '
                         f'more workers make SN refuse summary/metadata calls (ModelQueueFull)')
