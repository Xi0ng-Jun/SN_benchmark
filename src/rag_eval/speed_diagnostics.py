"""Where a partition's wall-clock time goes: phases, model gate waits, request latencies.

Diagnostics only: requests, responses, scores and comparison identity are
untouched. A partition process installs one observer around every outgoing
model request (the transports the model gate wraps, see ``model_gate``). The
runner marks phase boundaries with ``phase(name)``; each request is attributed
to the phase in which it started and keeps its start time, gate wait and
service time. Start times are wall-clock offsets, so the partitions of one
execution (or of several executions on one server) merge exactly and show how
many requests were waiting or holding a gate slot at any moment.

Only the last path segments of a request URL are kept (``chat/completions``);
hosts, queries, headers and bodies are never recorded.
"""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import json
import math
import os
from pathlib import Path
import re
import threading
import time

FORMAT = 'sn-speed-diagnostics-v1'
FILENAME = 'speed-diagnostics.json'
COLUMNS = ('phase', 'endpoint', 'start', 'gate_wait', 'service', 'outcome')
_SEGMENT = re.compile(r'[A-Za-z][A-Za-z0-9_.-]{0,39}')
_observer = None


def phase(name):
    """Mark a phase boundary of this process; a no-op when nothing observes it."""
    observer = _observer
    if observer is not None:
        observer.enter(name)


def endpoint(path):
    """The last two path segments that look like names, e.g. ``chat/completions``."""
    segments = [s for s in str(path or '').split('?', 1)[0].split('/') if s]
    kept = [s if _SEGMENT.fullmatch(s) else '*' for s in segments[-2:]]
    return '/'.join(kept) or '/'


class SpeedObserver:
    def __init__(self, *, gate=None):
        self.gate = gate
        self.started_epoch = time.time()
        self._origin = time.monotonic()
        self._lock = threading.Lock()
        self._phases = [['setup', 0.0]]
        self._rows = []

    def now(self):
        return time.monotonic() - self._origin

    def enter(self, name):
        with self._lock:
            if self._phases[-1][0] != name:
                self._phases.append([name, round(self.now(), 3)])

    def current(self):
        return self._phases[-1][0]

    def record(self, phase_name, path, start, gate_wait, service, outcome):
        row = [phase_name, endpoint(path), round(start, 3), round(gate_wait, 4), round(service, 4), outcome]
        with self._lock:
            self._rows.append(row)

    def snapshot(self, **extra):
        with self._lock:
            phases, rows = [list(p) for p in self._phases], [list(r) for r in self._rows]
        wall = round(self.now(), 3)
        bounds = [p[1] for p in phases[1:]] + [wall]
        return dict(format=FORMAT,
                    started_at=datetime.fromtimestamp(self.started_epoch, timezone.utc).isoformat(),
                    started_epoch=self.started_epoch, wall_seconds=wall,
                    phases=[dict(name=name, start=start, seconds=round(end - start, 3))
                            for (name, start), end in zip(phases, bounds)],
                    gate_slots=self.gate.slots if self.gate is not None else None,
                    gate_requested_slots=self.gate.requested_slots if self.gate is not None else None,
                    requests=dict(columns=list(COLUMNS), rows=rows), **extra)

    def save(self, run, **extra):
        """Atomic, compact (one line per request would be needlessly large)."""
        path = Path(run) / FILENAME
        temporary = path.with_suffix('.json.tmp')
        with temporary.open('w', encoding='utf-8') as stream:
            os.chmod(temporary, 0o600)
            json.dump(self.snapshot(**extra), stream, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
            stream.write('\n')
        temporary.replace(path)
        return path


@contextmanager
def observing(observer):
    """Make ``observer`` receive this process's ``phase`` marks."""
    global _observer
    previous, _observer = _observer, observer
    try:
        yield observer
    finally:
        _observer = previous


def load(run):
    """A partition's saved observation, or None when it was not written (older runs)."""
    path = Path(run) / FILENAME
    try:
        saved = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    return saved if isinstance(saved, dict) and saved.get('format') == FORMAT else None


def _quantile(ordered, q):
    # Nearest rank: a reported value is always an observed one.
    return ordered[max(0, math.ceil(q * len(ordered)) - 1)]


def distribution(values):
    ordered = sorted(values)
    if not ordered:
        return dict(count=0)
    return dict(count=len(ordered), sum=round(sum(ordered), 3), p50=_quantile(ordered, .5),
                p90=_quantile(ordered, .9), p99=_quantile(ordered, .99), max=ordered[-1])


def _concurrency(requests, slots):
    """Peak waiting/holding requests and how long the gate was full, from merged timelines."""
    events = []
    for start, wait, service in requests:
        # At one instant ends come first, so a slot freed and retaken is not counted twice.
        events += [(start + wait + service, 0, 0, -1), (start, 1, 1, 0), (start + wait, 2, -1, 1)]
    events.sort()
    waiting = holding = peak_waiting = peak_holding = 0
    saturated = queued = 0.0
    last = events[0][0] if events else 0.0
    for at, _, dwait, dhold in events:
        if slots is not None and holding >= slots:
            saturated += at - last
        if waiting > 0:
            queued += at - last
        last = at
        waiting += dwait
        holding += dhold
        peak_waiting, peak_holding = max(peak_waiting, waiting), max(peak_holding, holding)
    return dict(peak_in_flight=peak_holding, peak_waiting=peak_waiting,
                gate_full_seconds=round(saturated, 3) if slots is not None else None,
                seconds_with_waiters=round(queued, 3))


def summarize(observations):
    """Merge saved observations {name: snapshot} into one execution-level summary.

    Phase walls are per partition (max and sum); request distributions and
    concurrency are over all requests of the given partitions only. A server
    gate is shared with any other execution running at the same time.
    """
    observations = {name: item for name, item in observations.items() if item}
    phases, walls, timeline, slots = {}, [], [], set()
    groups = {}
    for name, item in sorted(observations.items()):
        walls.append((item.get('wall_seconds') or 0, name))
        slots.add(item.get('gate_slots'))
        for entry in item.get('phases') or []:
            phases.setdefault(entry['name'], []).append((entry['seconds'], name))
        index = {column: i for i, column in enumerate((item.get('requests') or {}).get('columns') or COLUMNS)}
        for row in (item.get('requests') or {}).get('rows') or []:
            value = {column: row[i] for column, i in index.items()}
            group = groups.setdefault((value['phase'], value['endpoint']),
                                      dict(service=[], gate_wait=[], outcomes={}))
            group['service'].append(value['service'])
            group['gate_wait'].append(value['gate_wait'])
            outcome = str(value['outcome'])
            group['outcomes'][outcome] = group['outcomes'].get(outcome, 0) + 1
            timeline.append((item['started_epoch'] + value['start'], value['gate_wait'], value['service']))
    requests = {}
    for (phase_name, path), group in sorted(groups.items()):
        requests.setdefault(phase_name, {})[path] = dict(
            count=len(group['service']), outcomes=dict(sorted(group['outcomes'].items())),
            service_seconds=distribution(group['service']), gate_wait_seconds=distribution(group['gate_wait']))
    waits = [w for g in groups.values() for w in g['gate_wait']]
    service = [s for g in groups.values() for s in g['service']]
    gate_slots = slots.pop() if len(slots) == 1 else None
    by_partition = {name: {e['name']: e['seconds'] for e in item.get('phases') or []}
                    for name, item in observations.items()}
    return dict(
        format=FORMAT + '-summary', partitions=len(observations),
        wall_seconds=dict(max=max(walls)[0] if walls else None, sum=round(sum(w for w, _ in walls), 3)),
        phases={name: dict(max=max(values)[0], sum=round(sum(v for v, _ in values), 3),
                           slowest_partition=max(values)[1])
                for name, values in phases.items()},
        slowest_partitions=[dict(name=name, wall_seconds=wall, phases=by_partition[name])
                            for wall, name in sorted(walls, reverse=True)[:3]],
        gate=dict(slots=gate_slots, acquisitions=len(waits),
                  waited_seconds_sum=round(sum(waits), 3), waited_seconds_max=max(waits, default=None),
                  # Share of request time spent queueing for a slot rather than being served.
                  waited_share=round(sum(waits) / (sum(waits) + sum(service)), 4) if waits and sum(waits) + sum(service) else None),
        concurrency=_concurrency(timeline, gate_slots),
        requests=requests)
