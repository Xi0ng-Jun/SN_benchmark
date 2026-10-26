"""Human HTTP commands and service-owned workers for durable LCD rounds."""
from __future__ import annotations

import logging
import threading

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field, StrictInt

from .loop import TERMINAL

_LOG = logging.getLogger(__name__)


class StartRound(BaseModel):
    model_config = ConfigDict(extra='forbid')
    request_key: str = Field(min_length=1, max_length=200)
    rounds: StrictInt = Field(default=1, ge=1)


class StopRound(BaseModel):
    model_config = ConfigDict(extra='forbid')


class RoundManager:
    """Poll durable invocations; one independent worker per active iteration.

    Workers are service-owned, so HTTP disconnects do not cancel work. Shutdown
    stops scheduling; receipts recover any dispatch already in flight. The loop
    service's file lock also serializes ticks across server processes.
    """
    def __init__(self, service, *, allowed=lambda iid: True, poll_seconds=2):
        self.service, self.allowed, self.poll_seconds = service, allowed, poll_seconds
        self.closed = threading.Event()
        self.signal = threading.Event()
        self.workers = {}
        self.thread = None

    def start(self):
        if self.thread is not None and self.thread.is_alive():
            return
        self.service.resume()
        self.closed.clear()
        self.thread = threading.Thread(target=self._run, name='lcd-round-manager', daemon=True)
        self.thread.start()

    def wake(self):
        self.signal.set()

    def close(self):
        self.closed.set()
        self.signal.set()
        if self.thread is not None:
            self.thread.join(timeout=5)

    def _tick(self, rid):
        try:
            self.service.tick(rid)
        except Exception as exc:
            # No exception text: service failures may include private addresses.
            _LOG.error('LCD round %s tick failed (%s)', rid, type(exc).__name__)

    def _run(self):
        while not self.closed.is_set():
            try:
                self.service.heartbeat()
                self.workers = {key: worker for key, worker in self.workers.items() if worker.is_alive()}
                for record in self.service.list():
                    if self.closed.is_set():
                        break
                    rid = record['id']
                    if record['status'] in TERMINAL or rid in self.workers:
                        continue
                    try:
                        permitted = self.allowed(record['iteration_id'])
                    except Exception:
                        permitted = False
                    if not permitted:
                        continue
                    worker = threading.Thread(target=self._tick, args=(rid,), name='lcd-round-' + rid, daemon=True)
                    self.workers[rid] = worker
                    worker.start()
            except Exception as exc:
                _LOG.error('LCD round scheduling failed (%s)', type(exc).__name__)
            self.signal.wait(self.poll_seconds)
            self.signal.clear()


class RoundAPI:
    def __init__(self, iteration_api, service, manager):
        self.iterations, self.service, self.manager = iteration_api, service, manager

    def router(self):
        router = APIRouter()

        @router.post('/api/iterations/{iteration_id}/rounds')
        def start(iteration_id: str, command: StartRound):
            self.iterations._write()
            self.iterations._scope(iteration_id=iteration_id)
            record = self.service.start(iteration_id, actor=self.iterations.actor, **command.model_dump())
            self.manager.wake()
            return record

        @router.get('/api/iterations/{iteration_id}/rounds')
        def listing(iteration_id: str):
            self.iterations._scope(iteration_id=iteration_id)
            return self.service.list(iteration_id)

        @router.get('/api/rounds/{round_id}')
        def show(round_id: str):
            record = self.service.get(round_id)
            self.iterations._scope(iteration_id=record['iteration_id'])
            return record

        @router.post('/api/rounds/{round_id}/stop')
        def stop(round_id: str, command: StopRound):
            self.iterations._write()
            show(round_id)
            return self.service.stop(round_id, actor=self.iterations.actor)

        return router
