"""Cross-process cap on concurrent model requests.

Partition processes run in parallel and SN schedules its own concurrency
inside each one, so the product of the two can exceed what the model service
accepts and turn into a 429 storm.  This gate wraps the same synchronous
transports as the budget guard: every outgoing request first takes one of N
slot files (an exclusive ``flock``), held until the response body is read.
Locks vanish with their process, so a killed partition cannot leak a slot.
The gate changes only timing; requests, retries and responses are untouched.
"""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import os
from pathlib import Path
import threading
import time


class ModelGate:
    def __init__(self, directory, slots, *, poll=0.05):
        if type(slots) is not int or slots < 1:
            raise ValueError('Model gate needs a positive slot count')
        self.directory = Path(directory).resolve()
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.slots, self.poll = slots, poll
        self.waited_seconds, self.requests = 0.0, 0
        self._stats = threading.Lock()

    def acquire(self):
        started = time.monotonic()
        while True:
            for index in range(self.slots):
                fd = os.open(self.directory / f'slot-{index}', os.O_RDWR | os.O_CREAT, 0o600)
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    os.close(fd)
                    continue
                with self._stats:
                    self.waited_seconds += time.monotonic() - started
                    self.requests += 1
                return fd
            time.sleep(self.poll)

    @staticmethod
    def release(fd):
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


@contextmanager
def install_model_gate(directory, slots):
    import httpx
    import requests
    gate = ModelGate(directory, slots)
    original_http = httpx.Client.send
    original_requests = requests.Session.send

    def http_send(client, request, **kwargs):
        fd = gate.acquire()
        try:
            response = original_http(client, request, **kwargs)
        except BaseException:
            gate.release(fd)
            raise
        if not kwargs.get('stream', False):
            gate.release(fd)
            return response
        # A streamed body is still being produced by the service: hold the
        # slot until the caller closes the response.
        released = []
        close = response.close

        def close_and_release():
            try:
                close()
            finally:
                if not released:
                    released.append(True)
                    gate.release(fd)
        response.close = close_and_release
        return response

    def request_send(session, request, **kwargs):
        fd = gate.acquire()
        try:
            return original_requests(session, request, **kwargs)
        finally:
            gate.release(fd)

    httpx.Client.send, requests.Session.send = http_send, request_send
    try:
        yield gate
    finally:
        httpx.Client.send, requests.Session.send = original_http, original_requests
