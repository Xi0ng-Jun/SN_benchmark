"""A fake VeRO world for offline tests: stand-in ``vero`` / ``datasets`` packages, a toy agent, a dataset.

The toy agent answers a question iff its ``difficulty`` < SKILL (0.3 at the seed). The fake
analyst always proposes raising SKILL; the fake implementer sets it to 0.8 and also edits a read-only
file (which must be reverted) and records whether the excluded file was visible. After the first
adoption every further attempt leaves no change (implementation error) or is deduplicated (empty
analysis): the run must halt on consecutive run failures instead of spinning (v4.1 loop regression).
"""
from __future__ import annotations

import json
import sys
import textwrap
from pathlib import Path

FAKE_LIBS = {
    "vero/__init__.py": "",
    "vero/core/__init__.py": "",
    "vero/core/_bag.py": """
        class Bag:
            def __init__(self, **kw):
                self.__dict__.update(kw)
    """,
    "vero/core/evaluation.py": "from vero.core._bag import Bag as EvaluationParameters\n",
    "vero/core/db/__init__.py": "",
    "vero/core/db/candidate.py": "from vero.core._bag import Bag as Candidate\n",
    "vero/core/db/dataset.py": "from vero.core._bag import Bag as DatasetSubset\n",
    "vero/core/db/run.py": "from vero.core._bag import Bag as ExperimentRun\n",
    "vero/core/db/result.py": """
        from dataclasses import dataclass
        from typing import Any

        @dataclass
        class TaskOutput:
            output: Any = None
            error: Any = None
            execution_trace: Any = None

        class TaskResult:
            def __init__(self, score=None, feedback=None, output=None, error=None, eval_error=None,
                         execution_trace=None, error_traceback=None):
                self.score, self.feedback, self.output, self.error = score, feedback, output, error
                self.eval_error, self.execution_trace, self.error_traceback = eval_error, execution_trace, error_traceback

            @classmethod
            def from_task_output(cls, task_output, **kw):
                err = task_output.error
                return cls(output=task_output.output, execution_trace=task_output.execution_trace,
                           error=str(err) if err is not None else None, **kw)
    """,
    "vero/core/task/__init__.py": """
        from vero.core.db.result import TaskOutput

        class VeroTask:
            _registry = {}

            def __init__(self, name):
                self.name, self.fns = name, {}
                VeroTask._registry[name] = self

            def __call__(self, tag, batch=False):
                def deco(fn):
                    self.fns[(tag, batch)] = fn
                    return fn
                return deco

            @classmethod
            def get_task(cls, name):
                return cls._registry[name]

            async def run_batch_inference(self, tasks, params):
                fn, out = self.fns[("run_inference", False)], []
                for t in tasks:
                    try:
                        out.append(await fn(t, params))
                    except Exception as e:
                        out.append(TaskOutput(error=e))
                return out

            async def run_batch_evaluation(self, tasks, outputs, params):
                fn = self.fns[("run_evaluation", False)]
                return [await fn(t, o, params) for t, o in zip(tasks, outputs)]

        def create_task(name, **kw):
            return VeroTask(name)
    """,
    "datasets/__init__.py": """
        import json, os

        def load_from_disk(path):
            with open(os.path.join(path, "data.json")) as fh:
                return json.load(fh)
    """,
}

AGENT = {
    "fake_agent/__init__.py": "",
    "fake_agent/agent.py": """
        SKILL = 0.3

        def solve(question, difficulty):
            if difficulty < SKILL:
                return "a" + question[1:]
            return "unknown"
    """,
    "fake_agent/vero_tasks/__init__.py": "from .qa import qa_task\n",
    "fake_agent/vero_tasks/qa.py": """
        from vero.core.db.result import TaskOutput, TaskResult
        from vero.core.task import create_task
        from fake_agent.agent import solve

        qa_task = create_task("fake_qa")

        @qa_task("run_inference")
        async def run_inference(task, evaluation_parameters):
            out = solve(task["question"], task["difficulty"])
            trace = [{"role": "user", "content": task["question"]},
                     {"role": "assistant", "content": out, "tool": "lookup" if out != "unknown" else "none"}]
            return TaskOutput(output=out, execution_trace=trace)

        @qa_task("run_evaluation")
        async def run_evaluation(task, output, evaluation_parameters):
            ok = output.output == task["answer"]
            return TaskResult.from_task_output(output, score=1.0 if ok else 0.0,
                                               feedback="CORRECT" if ok else "INCORRECT")
    """,
    "secret_test.txt": "test answers\n",
    ".veroaccess": """
        [read]
        **
        [write]
        fake_agent/agent.py
        [exclude]
        secret_test.txt
    """,
}

ANALYST = """
import json, pathlib, sys
prompt = sys.argv[-1]
assert "case-" in prompt, prompt[:2000]
print("Looking at the cases.")
print("```json")
print(json.dumps([{"title": "Agent gives up on hard questions",
                   "failure_mode": "returns 'unknown' (see case-01)",
                   "change": "raise SKILL in fake_agent/agent.py",
                   "predicate": {"pattern": "lookup", "direction": "increase"}}]))
print("```")
"""

IMPLEMENTER = """
import os, pathlib, sys
p = pathlib.Path("fake_agent/agent.py")
p.write_text(p.read_text().replace("SKILL = 0.3", "SKILL = 0.8"))
q = pathlib.Path("fake_agent/vero_tasks/qa.py")
q.write_text(q.read_text() + "\\n# grader tampering\\n")
pathlib.Path("notes.txt").write_text("untracked scratch")
marker = pathlib.Path(os.environ.get("FAKE_MARKER_DIR", ".")) / "secret_seen"
if pathlib.Path("secret_test.txt").exists():
    marker.write_text("yes")
print("Raised SKILL to 0.8.")
"""


def _write(root: Path, files: dict) -> None:
    for rel, text in files.items():
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(textwrap.dedent(text).lstrip("\n"))


def build(tmp: Path) -> dict:
    libs, agent, data, bin_ = tmp / "libs", tmp / "vero-src" / "agent", tmp / "dataset", tmp / "bin"
    _write(libs, FAKE_LIBS)
    _write(agent, AGENT)
    splits = {}
    for split, n in (("train", 12), ("validation", 20), ("test", 10)):
        offset = {"train": 0, "validation": 100, "test": 200}[split]
        splits[split] = [dict(question=f"q{offset + i}", answer=f"a{offset + i}",
                              difficulty=((offset + i) * 37 % 100) / 100) for i in range(n)]
    data.mkdir(parents=True)
    (data / "data.json").write_text(json.dumps(splits))
    _write(bin_, {"analyst.py": ANALYST, "implementer.py": IMPLEMENTER})
    return dict(libs=libs, agent=agent, dataset=data,
                analyst_cmd=f"{sys.executable} {bin_ / 'analyst.py'}",
                implementer_cmd=f"{sys.executable} {bin_ / 'implementer.py'}")
