"""VeRO ``.veroaccess`` rules: which repo paths the optimizer's agents may not see, only read, or write.

Format (VeRO paper-v1): sections ``[exclude]`` / ``[read]`` / ``[write]`` of gitignore-like
globs; the last matching rule wins. Sections may repeat. Unmatched paths default to ``read``.
A pattern without a slash matches at any depth; a pattern also matches everything below it.
"""
from __future__ import annotations

import re
from functools import lru_cache

LEVELS = ("exclude", "read", "write")

# web_search_agent ships no .veroaccess. Ours: the agent code is writable, the task/grader module
# (vero_tasks: inference entry and the LLM grader) and packaging stay read-only.
WEB_SEARCH_AGENT_RULES = """
[exclude]
.git
**/__pycache__
[read]
**
[write]
src/web_search_agent/**
[read]
src/web_search_agent/vero_tasks/**
"""


@lru_cache(maxsize=512)
def _regex(pattern: str) -> re.Pattern:
    p = pattern.strip().strip("/")
    if "/" not in p:
        p = "**/" + p
    out, i = "", 0
    while i < len(p):
        if p.startswith("**/", i):
            out += "(?:.*/)?"
            i += 3
        elif p.startswith("**", i):
            out += ".*"
            i += 2
        elif p[i] == "*":
            out += "[^/]*"
            i += 1
        elif p[i] == "?":
            out += "[^/]"
            i += 1
        else:
            out += re.escape(p[i])
            i += 1
    return re.compile(f"^{out}(?:/.*)?$")


class AccessRules:
    def __init__(self, text: str):
        self.rules: list[tuple[str, str]] = []
        level = None
        for raw in text.splitlines():
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            m = re.fullmatch(r"\[(\w+)\]", line)
            if m:
                if m.group(1) not in LEVELS:
                    raise ValueError(f"unknown .veroaccess section [{m.group(1)}]")
                level = m.group(1)
                continue
            if level is None:
                raise ValueError(f".veroaccess rule {line!r} outside a section")
            self.rules.append((level, line))

    def level(self, path: str) -> str:
        path = path.strip("/")
        found = "read"
        for level, pattern in self.rules:
            if _regex(pattern).match(path):
                found = level
        return found

    def writable(self, path: str) -> bool:
        return self.level(path) == "write"

    def excluded(self, path: str) -> bool:
        return self.level(path) == "exclude"

    def describe(self) -> str:
        """The rules as shown to the agents."""
        out, last = [], None
        for level, pattern in self.rules:
            if level != last:
                out.append(f"[{level}]")
                last = level
            out.append(pattern)
        return "\n".join(out)
