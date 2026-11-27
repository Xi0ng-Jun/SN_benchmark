#!/usr/bin/env python3
"""Read-only text views of agent trajectories (Harbor ``agent/trajectory.json``), for analysing
where a run spent its time and where it got stuck. Standard library only.

    traj_digest.py list     STATE                 one line per trajectory under STATE/jobs
    traj_digest.py overview TRAJ                  outcome, 10-minute windows, one line per step
    traj_digest.py steps    TRAJ A-B [A-B ...]    command, tool output and thinking of steps A..B
    traj_digest.py grep     TRAJ REGEX            steps whose command / output / thinking match

TRAJ is a path to ``trajectory.json`` or any unique substring of a path printed by ``list``
(then ``--state STATE`` or $SSAP_STATE is needed). Options: ``--obs N`` / ``--think N`` /
``--cmd N`` character limits for ``steps``; ``--where cmd,obs,think,msg`` for ``grep``;
``--no-steps`` for ``overview``.

The activity label of a step is a keyword guess from its command (table ``ACTIVITY`` below,
first match wins). It is an aid for navigation, not a measurement: check the step itself
before relying on it.
"""
from __future__ import annotations

import argparse
import collections
import glob
import json
import os
import re
import sys
from datetime import datetime
from pathlib import Path

# (label, regex on the tool-call arguments); first match wins. Scenario words are confined to this
# table. File tools without a matching argument are labelled write / read by tool name.
ACTIVITY = [
    ("deliver", r"mcr_promote|mcr_check|submission/"),
    ("lvs", r"\blvs\b|sky130\.lvs|netgen"),
    ("drc", r"\bdrc\b|\.drc|lyrdb"),
    ("extract", r"\bpex\b|magic\b|ext2spice"),
    ("simulate", r"ngspice|\.spi\b|testbench|tb_"),
    ("write", r"cat\s*>+\s*\S+\s*<<|\btee\b|sed -i"),
    ("run", r"python[\d.]*\s|\bbash\s|\bsh\s|klayout"),
    ("read", r"\b(cat|sed -n|head|tail|grep|ls|find|strings|wc|which|file|awk)\b"),
]
LIMIT_MSG = "Output token limit hit"
API_ERR = "API Error"


def leaves(o):
    if isinstance(o, str):
        yield o
    elif isinstance(o, dict):
        for v in o.values():
            yield from leaves(v)
    elif isinstance(o, list):
        for v in o:
            yield from leaves(v)


def one_line(s, n: int) -> str:
    return re.sub(r"\s+", " ", str(s or "")).strip()[:n]


def clip(s, n: int) -> str:
    s = str(s or "")
    return s if len(s) <= n else s[: n * 2 // 3] + f"\n...[{len(s) - n} chars omitted]...\n" + s[-n // 3:]


def k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class Step:
    def __init__(self, i: int, raw: dict):
        self.i, self.raw = i, raw
        self.source = raw.get("source") or "?"
        self.message = str(raw.get("message") or "")
        self.think = str(raw.get("reasoning_content") or "")
        calls = raw.get("tool_calls") or []
        self.tools = [str(c.get("function_name") or "?") for c in calls]
        args = [c.get("arguments") for c in calls]
        self.desc = "; ".join(str(a.get("description")) for a in args
                              if isinstance(a, dict) and a.get("description"))
        self.cmd = "\n".join(
            ("\n".join(str(v) for kk, v in a.items() if kk != "description") if isinstance(a, dict)
             else "\n".join(leaves(a))) for a in args)
        self.obs = "\n".join(leaves(raw.get("observation")))
        try:
            self.time = datetime.fromisoformat(str(raw.get("timestamp")).replace("Z", "+00:00"))
        except ValueError:
            self.time = None
        self.limit = self.source == "user" and self.message.startswith(LIMIT_MSG)
        self.api_error = self.message.startswith(API_ERR)

    @property
    def activity(self) -> str:
        if self.limit:
            return "LIMIT"
        if self.api_error:
            return "APIERR"
        if not self.tools:
            return "say" if self.message else "think"
        for label, rx in ACTIVITY:
            if re.search(rx, self.cmd, re.I | re.M):
                return label
        tools = {t.lower() for t in self.tools}
        if tools & {"write", "edit", "multiedit", "notebookedit"}:
            return "write"
        if tools & {"read", "grep", "glob"}:
            return "read"
        return "other"


class Traj:
    def __init__(self, path: Path):
        self.path = path
        doc = json.loads(path.read_text(errors="replace"))
        self.steps = [Step(i, s) for i, s in enumerate(doc.get("steps") or [])]
        self.final_metrics = doc.get("final_metrics") or {}
        self.t0 = next((s.time for s in self.steps if s.time), None)

    def minute(self, s: Step) -> float:
        return (s.time - self.t0).total_seconds() / 60 if s.time and self.t0 else -1.0

    def outcome(self) -> dict:
        trial = self.path.parent.parent
        out: dict = {}
        try:
            m = json.loads((trial / "verifier" / "evaluation" / "metrics.json").read_text())
            out = {kk: m.get(kk) for kk in ("status", "score", "design_passed", "score_components")}
            si = m.get("scoring_inputs") or {}
            out["checks"] = {kk: si.get(kk) for kk in ("submission_present", "geometry_valid", "drc_pass",
                                                       "lvs_pass", "pex_success", "postsim_case_fraction")}
        except (OSError, ValueError):
            out["metrics"] = "not found"
        return out


def find(spec: str, state: str | None) -> Path:
    p = Path(spec)
    if p.is_file():
        return p
    if not state:
        sys.exit("TRAJ is not a file; give --state STATE (or set SSAP_STATE) to search by substring")
    hits = [h for h in sorted(glob.glob(f"{state}/jobs/*/harbor/*/*/agent/trajectory.json")) if spec in h]
    if len(hits) != 1:
        sys.exit(f"{len(hits)} trajectories match {spec!r}:\n" + "\n".join(hits[:20]))
    return Path(hits[0])


def cmd_list(args) -> None:
    for h in sorted(glob.glob(f"{args.target}/jobs/*/harbor/*/*/agent/trajectory.json")):
        parts = h.split("/jobs/")[1].split("/")
        print(f"{parts[0]}\t{parts[2]}\t{os.path.getsize(h) // 1024}KB\t{h}")


def step_line(t: Traj, s: Step) -> str:
    what = one_line(s.desc or s.cmd or s.message, 110)
    return (f"[{s.i:4d}] {t.minute(s):5.0f}m {s.activity:8s} think={k(len(s.think)):>6s} "
            f"out={k(len(s.obs)):>6s} | {what}")


def cmd_overview(args) -> None:
    t = Traj(find(args.target, args.state))
    last = t.steps[-1] if t.steps else None
    print(f"# {t.path}")
    print(f"steps={len(t.steps)} minutes={t.minute(last):.0f} final_metrics={json.dumps(t.final_metrics)[:300]}")
    print(f"outcome={json.dumps(t.outcome(), default=str)}")
    print(f"output-limit prompts={sum(s.limit for s in t.steps)} api errors={sum(s.api_error for s in t.steps)} "
          f"thinking chars={sum(len(s.think) for s in t.steps)}")
    print("\n## 10-minute windows (activity labels are keyword guesses)")
    win: dict[int, list[Step]] = collections.defaultdict(list)
    for s in t.steps:
        win[int(max(t.minute(s), 0) // 10)].append(s)
    for w in sorted(win):
        c = collections.Counter(s.activity for s in win[w])
        acts = " ".join(f"{a}={n}" for a, n in c.most_common())
        print(f"{w * 10:3d}-{w * 10 + 10:3d}m steps={len(win[w]):3d} think={k(sum(len(s.think) for s in win[w])):>7s} | {acts}")
    print("\n## agent messages")
    for s in t.steps:
        if s.source == "agent" and s.message and not s.api_error:
            print(f"[{s.i:4d}] {t.minute(s):5.0f}m {one_line(s.message, 200)}")
    if not args.no_steps:
        print("\n## steps")
        for s in t.steps:
            print(step_line(t, s))


def cmd_steps(args) -> None:
    t = Traj(find(args.target, args.state))
    for rng in args.rest:
        a, _, b = rng.partition("-")
        for s in t.steps[int(a): int(b or a) + 1]:
            print(f"\n===== step {s.i}  {t.minute(s):.1f} min  source={s.source}  {s.activity}")
            if s.think:
                print(f"--- thinking ({len(s.think)} chars)\n{clip(s.think, args.think)}")
            if s.message:
                print(f"--- message\n{clip(s.message, args.think)}")
            if s.tools:
                print(f"--- tool {', '.join(s.tools)}: {s.desc}\n{clip(s.cmd, args.cmd)}")
            if s.obs:
                print(f"--- output ({len(s.obs)} chars)\n{clip(s.obs, args.obs)}")


def cmd_grep(args) -> None:
    t = Traj(find(args.target, args.state))
    rx = re.compile(args.rest[0], re.I)
    fields = args.where.split(",")
    n = 0
    for s in t.steps:
        for f, text in (("cmd", s.cmd), ("obs", s.obs), ("think", s.think), ("msg", s.message)):
            m = rx.search(text) if f in fields else None
            if m:
                n += 1
                ctx = one_line(text[max(0, m.start() - 70): m.end() + 110], 200)
                print(f"[{s.i:4d}] {t.minute(s):5.0f}m {f:5s} {ctx}")
    print(f"# {n} matches")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("mode", choices=("list", "overview", "steps", "grep"))
    ap.add_argument("target", help="STATE for list; trajectory path or unique substring otherwise")
    ap.add_argument("rest", nargs="*")
    ap.add_argument("--state", default=os.environ.get("SSAP_STATE"))
    ap.add_argument("--obs", type=int, default=3000)
    ap.add_argument("--think", type=int, default=3000)
    ap.add_argument("--cmd", type=int, default=3000)
    ap.add_argument("--where", default="cmd,obs,think,msg")
    ap.add_argument("--no-steps", action="store_true")
    args = ap.parse_args(argv)
    if args.mode in ("steps", "grep") and not args.rest:
        ap.error(f"{args.mode} needs " + ("step ranges" if args.mode == "steps" else "a regex"))
    {"list": cmd_list, "overview": cmd_overview, "steps": cmd_steps, "grep": cmd_grep}[args.mode](args)
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except BrokenPipeError:
        sys.exit(0)
