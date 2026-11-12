"""Analyst / implementer agents as CLI coding agents (pi, claude, codex, ...) run in a worktree.

The command is a shell-style string; the prompt is appended as the last argument, e.g.
``pi -p --no-session --model deepseek-flash`` or ``claude -p --permission-mode acceptEdits``.
stdout+stderr go to a log file; the final answer is read from stdout.
"""
from __future__ import annotations

import json
import logging
import os
import re
import shlex
import signal
import subprocess
import threading
from pathlib import Path
from typing import Any, Optional

log = logging.getLogger(__name__)


class AgentCommandFailed(RuntimeError):
    """The agent command exited non-zero (model service, credentials, CLI): infrastructure, not an answer."""


class AgentRunner:
    def __init__(self, retries: int = 2, retry_wait: float = 30.0):
        self._lock = threading.Lock()
        self._procs: set = set()
        self._cancelled = threading.Event()
        self.retries, self.retry_wait = retries, retry_wait

    def run(self, command: str, prompt: str, *, cwd: Path, timeout: float, log_path: Path,
            env: Optional[dict] = None) -> str:
        """The agent's stdout. A non-zero exit is retried ``retries`` times, then AgentCommandFailed."""
        log_path.parent.mkdir(parents=True, exist_ok=True)
        (log_path.with_suffix(".prompt.md")).write_text(prompt)
        for attempt in range(1 + self.retries):
            try:
                return self._once(command, prompt, cwd=cwd, timeout=timeout, log_path=log_path, env=env,
                                  append=attempt > 0)
            except AgentCommandFailed as exc:
                if attempt == self.retries or self._cancelled.is_set():
                    raise
                wait = self.retry_wait * (attempt + 1)
                log.warning("%s; retry %d of %d in %.0f s", exc, attempt + 1, self.retries, wait)
                if self._cancelled.wait(wait):
                    raise
        raise AssertionError("unreachable")

    def _once(self, command: str, prompt: str, *, cwd: Path, timeout: float, log_path: Path,
              env: Optional[dict], append: bool) -> str:
        argv = shlex.split(command) + [prompt]
        with open(log_path, "a" if append else "w") as fh:
            proc = subprocess.Popen(argv, cwd=cwd, stdout=subprocess.PIPE, stderr=fh, stdin=subprocess.DEVNULL,
                                    text=True, env=env, start_new_session=True)
            with self._lock:
                self._procs.add(proc)
            try:
                out, _ = proc.communicate(timeout=timeout)
            except subprocess.TimeoutExpired:
                self._kill(proc)
                proc.communicate()
                raise TimeoutError(f"agent timeout after {timeout:.0f} s ({argv[0]})") from None
            finally:
                with self._lock:
                    self._procs.discard(proc)
        with open(log_path, "a") as fh:
            fh.write("\n--- stdout ---\n" + (out or "") + "\n")
        if proc.returncode != 0:
            tail = " ".join(log_path.read_text(errors="replace").split())[-300:]
            raise AgentCommandFailed(f"agent command {argv[0]} exited {proc.returncode} ({tail}); see {log_path}")
        return out or ""

    @staticmethod
    def _kill(proc) -> None:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass

    def cancel(self) -> None:
        self._cancelled.set()
        with self._lock:
            procs = list(self._procs)
        for proc in procs:
            self._kill(proc)


def last_json(text: str) -> Any:
    """The last JSON value of an agent answer: a fenced ```json block, else the last [...] / {...}."""
    blocks = re.findall(r"```(?:json)?\s*\n(.*?)```", text, flags=re.S)
    for block in reversed(blocks):
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            continue
    for opener, closer in (("[", "]"), ("{", "}")):
        end = text.rfind(closer)
        start = text.rfind(opener, 0, end)
        while start != -1 and end != -1:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                start = text.rfind(opener, 0, start)
    raise ValueError("no JSON found in the agent's answer")


def shorten(obj: Any, limit: int) -> Any:
    """Recursively cut long strings (tool outputs, page contents) to *limit* characters."""
    if isinstance(obj, str):
        if len(obj) > limit:
            return obj[:limit] + f" …[{len(obj) - limit} chars cut]"
        return obj
    if isinstance(obj, list):
        return [shorten(x, limit) for x in obj]
    if isinstance(obj, dict):
        return {k: shorten(v, limit) for k, v in obj.items()}
    return obj


def parse_json_strings(obj: Any) -> Any:
    """tau-bench stores each trial's trajectory as a JSON string: decode those for display."""
    if isinstance(obj, list):
        out = []
        for x in obj:
            if isinstance(x, str) and x[:1] in "[{":
                try:
                    x = json.loads(x)
                except json.JSONDecodeError:
                    pass
            out.append(x)
        return out
    return obj
