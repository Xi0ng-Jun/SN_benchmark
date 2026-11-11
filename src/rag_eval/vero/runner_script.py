"""Run one VeRO (paper-v1) task on given dataset samples of one checkout.

Executed by the VeRO agent project's own interpreter (``python -I runner_script.py ...``), never
imported by rag_eval: it must stay standalone (stdlib + the agent venv's packages only).

    python -I runner_script.py describe <dataset_path> [sys_path ...]
    python -I runner_script.py run <request.json> <out.json>

The request names the checkout (``import_root`` is put first on sys.path, so the candidate's
code shadows any installed copy of the agent), the task module and name, the dataset path and
the samples ``[{task_id, split, index}]``. Inference and evaluation are VeRO's own
``VeroTask.run_batch_inference`` / ``run_batch_evaluation``; VeRO's session store is bypassed.

The output holds one record per sample (score, errors, feedback, output, execution trace, input)
or ``fatal`` with the phase (import / run) and traceback when the whole batch failed.
"""
import asyncio
import importlib
import json
import sys
import time
import traceback


def _jsonable(x):
    try:
        return json.loads(json.dumps(x, default=str))
    except Exception:  # noqa: BLE001
        return str(x)


def describe(dataset_path, extra_paths=()):
    for path in reversed(list(extra_paths)):
        sys.path.insert(0, path)
    from datasets import load_from_disk
    dd = load_from_disk(dataset_path)
    print(json.dumps({split: len(dd[split]) for split in dd}))


async def run(req):
    out = dict(samples=[], fatal=None)
    try:
        importlib.import_module(req["module"])
        from vero.core.task import VeroTask
        task = VeroTask.get_task(req["task"])
    except BaseException:  # noqa: BLE001
        out["fatal"] = dict(phase="import", traceback=traceback.format_exc())
        return out

    from datasets import load_from_disk
    from vero.core.db.candidate import Candidate
    from vero.core.db.dataset import DatasetSubset
    from vero.core.db.run import ExperimentRun
    from vero.core.evaluation import EvaluationParameters

    dd = load_from_disk(req["dataset_path"])
    by_split = {}
    for s in req["samples"]:
        by_split.setdefault(s["split"], []).append(s)
    for split, samples in by_split.items():
        rows = [dict(dd[split][s["index"]]) for s in samples]
        params = EvaluationParameters(
            run=ExperimentRun(
                candidate=Candidate(commit=req["commit"], repo_name=req.get("repo_name", "target")),
                dataset_subset=DatasetSubset(dataset_id=req.get("dataset_id", "dataset"), split=split,
                                             sample_ids=[s["index"] for s in samples])),
            session_id="rag-eval", task=req["task"], task_params=req.get("task_params") or {},
            max_concurrency=req.get("max_concurrency", 8), sample_timeout=req.get("sample_timeout", 300))
        start = time.monotonic()
        try:
            outputs = await task.run_batch_inference(rows, params)
            results = await task.run_batch_evaluation(rows, outputs, params)
        except BaseException:  # noqa: BLE001
            out["fatal"] = dict(phase="run", split=split, traceback=traceback.format_exc())
            return out
        wall = (time.monotonic() - start) / max(1, len(samples))
        for s, row, res in zip(samples, rows, results):
            rec = dict(task_id=s["task_id"], wall_s=wall, input=_jsonable(row))
            if isinstance(res, BaseException):
                rec["exception"] = "".join(traceback.format_exception(type(res), res, res.__traceback__))
            else:
                rec.update(score=res.score, error=res.error, eval_error=res.eval_error, feedback=res.feedback,
                           output=_jsonable(res.output), execution_trace=_jsonable(res.execution_trace),
                           error_traceback=res.error_traceback)
            out["samples"].append(rec)
    return out


def main(argv):
    if len(argv) >= 2 and argv[0] == "describe":
        describe(argv[1], argv[2:])
        return 0
    if len(argv) >= 3 and argv[0] == "run":
        with open(argv[1], encoding="utf-8") as fh:
            req = json.load(fh)
        for path in reversed(req.get("sys_path") or []):
            sys.path.insert(0, path)
        result = asyncio.run(run(req))
        with open(argv[2], "w", encoding="utf-8") as fh:
            json.dump(result, fh, ensure_ascii=False)
        return 0
    print(__doc__, file=sys.stderr)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
