"""Single-flight, version-keyed reads for the workbench's expensive GETs.

Browsers poll every few seconds, and rebuilding an iteration tree, its ledger
or a candidate's comparisons takes long at R1 scale. Run in request threads,
every poll started another rebuild: the server's thread pool filled up (static
files share it) and dozens of CPU-bound threads fought over the GIL.

Here one key (route and identity) has at most one computation at a time; other
requests for that key await the same task on the event loop without holding a
thread, and a request that disconnects does not cancel it. A result is kept
with the version it was computed at and served while the version is
unchanged. All computations together hold at most ``limit`` threads.

Only *when* and *how often* a response is computed changes; what it contains
does not.
"""
from __future__ import annotations

import asyncio


class HeavyReads:
    def __init__(self, limit=2):
        if type(limit) is not int or limit < 1:
            raise ValueError('HeavyReads needs a positive limit')
        self.limit = limit
        self._slots = None  # created on the serving event loop
        self._inflight = {}
        self._cache = {}
        self.computations = 0
        self.running = self.peak = 0

    async def get(self, key, compute, *, prepare=None, version_of=None):
        """The value of ``compute()`` for ``key``.

        ``prepare()`` runs first in a thread (scope checks, then the current
        version, or None to always recompute); a cached value of that version is
        returned without computing. Both are synchronous callables. A computation
        that itself records state (comparison records) names the version it
        leaves behind with ``version_of(value)``; otherwise the value is kept at
        the version ``prepare`` saw.
        """
        running = self._inflight.get(key)
        if running is None:
            version = await asyncio.to_thread(prepare) if prepare is not None else None
            cached = self._cache.get(key)
            if version is not None and cached is not None and cached[0] == version:
                return cached[1]
            running = self._inflight.get(key)  # another request may have started it meanwhile
            if running is None:
                running = self._inflight[key] = asyncio.ensure_future(self._run(key, version, compute, version_of))
                # Retrieved here too, so a failure nobody awaited any more is not reported as lost.
                running.add_done_callback(lambda task: task.cancelled() or task.exception())
        return await asyncio.shield(running)

    async def _run(self, key, version, compute, version_of):
        if self._slots is None:
            self._slots = asyncio.Semaphore(self.limit)
        try:
            async with self._slots:
                self.computations += 1
                self.running += 1
                self.peak = max(self.peak, self.running)
                try:
                    value = await asyncio.to_thread(compute)
                finally:
                    self.running -= 1
            if version is not None:
                self._cache[key] = (version_of(value) if version_of is not None else version, value)
            return value
        finally:
            self._inflight.pop(key, None)
