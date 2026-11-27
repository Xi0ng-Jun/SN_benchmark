#!/usr/bin/env python3
"""Run the trajectory analysis of ``docs/trajectory-analysis-brief.md`` over many runs, unattended.

One headless agent session per trajectory (task A); the session may only run
``scripts/traj_digest.py`` and read files, and its final reply is saved as the report. Nothing
under ``STATE/jobs`` is written. Standard library only. Run from the repository root.

    analyze_batch.py list   [--batch GLOB]          what would be analysed, and what is already done
    analyze_batch.py check                          one tiny session: does the agent command work here?
    analyze_batch.py run    [--batch GLOB] [--jobs N] [--summary NAME]
                                                    analyse every trajectory without a report, then
                                                    (with --summary) write the task B summary
    analyze_batch.py bundle [--batch GLOB]          concatenate the reports into one text file

STATE comes from ``--state`` or $SSAP_STATE. Reports go to ``STATE/analysis/auto/``, session logs
to ``STATE/analysis/auto/logs/``. A report that already exists is kept (``--force`` redoes it).
The agent command defaults to ``claude`` with the model of the calling shell; ``--model`` passes
``--model``.
"""
from __future__ import annotations

import argparse
import concurrent.futures
import getpass
import glob
import os
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = ["Bash(python3 scripts/traj_digest.py:*)", "Read", "Grep", "Glob"]

TASK_A = """阅读 docs/trajectory-analysis-brief.md，按“任务 A”分析这一条轨迹：
{traj}

与说明不同的地方，以这里为准：
1. 不要写任何文件。把报告全文作为最终回复输出，回复里只有报告本身，不要加开场白或结束语。
2. 你只能执行以 `python3 scripts/traj_digest.py` 开头的单条命令，TRAJ 参数直接用上面的完整路径。
   不要用 cd、export、管道、重定向或其他命令；当前目录已经是仓库根目录。
3. 需要看这次运行的其他文件（轨迹所在目录的上一级里的 result.json、exception.txt、
   verifier/evaluation/metrics.json 等）时，用 Read 或 Grep 工具，不要读 trajectory.json 本身。
4. 报告里用运行名指代这次运行，不要出现绝对路径。
5. 报告第一行写：`# 单次运行报告：{label}`。
"""

TASK_B = """阅读 docs/trajectory-analysis-brief.md，按“任务 B”汇总目录 {reports} 下所有 run_*.md 报告
（用 Glob 列出，用 Read 逐份读完）。

与说明不同的地方，以这里为准：
1. 不要写任何文件。把汇总全文作为最终回复输出，回复里只有汇总本身。
2. 需要回到轨迹核对时，只能执行以 `python3 scripts/traj_digest.py` 开头的单条命令。
3. 第 1 节的表按运行逐行列出，并另给一张“各类卡点出现在几次运行里”的计数表；
   数不出来的写“未统计”。
4. 汇总里不要出现绝对路径。第一行写：`# 汇总报告：{name}`。
"""

CHECK = ("执行这一条命令：python3 scripts/traj_digest.py list {state}\n"
         "然后只回复一个整数：这条命令输出了多少行。不要回复别的内容。")


def trajectories(state: str, batch: str) -> list[tuple[str, Path]]:
    """(label, path) per trajectory; the label is unique and free of the state path."""
    hits = sorted(glob.glob(f"{state}/jobs/{batch}/harbor/*/*/agent/trajectory.json"))
    seen: dict[str, int] = {}
    out = []
    for h in hits:
        run = h.split("/jobs/")[1].split("/")[2]
        seen[run] = seen.get(run, 0) + 1
    for h in hits:
        parts = h.split("/jobs/")[1].split("/")
        run, trial = parts[2], parts[3]
        label = run if seen[run] == 1 else f"{run}__{trial.rsplit('__', 1)[-1]}"
        out.append((label, Path(h)))
    return out


def session(args, prompt: str, out: Path, log: Path) -> tuple[bool, str]:
    """Run one headless session; keep its final reply in ``out`` only when it succeeded."""
    cmd = [args.claude, "-p", "--max-turns", str(args.max_turns), "--allowedTools", *ALLOWED]
    if args.model:
        cmd += ["--model", args.model]
    log.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    try:
        with open(log, "w") as lf:
            p = subprocess.run(cmd, input=prompt, stdout=subprocess.PIPE, stderr=lf, text=True,
                               cwd=ROOT, timeout=args.timeout)
    except subprocess.TimeoutExpired:
        return False, f"timeout after {args.timeout}s"
    except OSError as e:
        return False, f"cannot start {args.claude!r}: {e}"
    text = (p.stdout or "").strip()
    took = f"{(time.time() - t0) / 60:.1f} min"
    if p.returncode != 0 or len(text) < 200:
        with open(log, "a") as lf:
            lf.write(f"\n--- exit {p.returncode}, stdout {len(text)} chars ---\n{text}\n")
        return False, f"exit {p.returncode}, reply {len(text)} chars, {took} (see {log.name})"
    note = ""
    if getpass.getuser() in text:
        note = " WARNING: contains the user name"
    out.write_text(text + "\n")
    return True, f"{len(text.encode()) / 1024:.1f} KB, {took}{note}"


def cmd_list(args, items, outdir) -> int:
    for label, path in items:
        rep = outdir / f"run_{label}.md"
        print(f"{'done' if rep.is_file() else 'todo'}\t{label}\t{os.path.getsize(path) // 1024}KB")
    print(f"# {len(items)} trajectories, {sum((outdir / f'run_{l}.md').is_file() for l, _ in items)} reports "
          f"in {outdir}")
    return 0


def cmd_check(args, outdir) -> int:
    expect = len(glob.glob(f"{args.state}/jobs/*/harbor/*/*/agent/trajectory.json"))
    out = outdir / "logs" / "check.reply.txt"
    cmd = [args.claude, "-p", "--max-turns", "6", "--allowedTools", *ALLOWED]
    if args.model:
        cmd += ["--model", args.model]
    try:
        v = subprocess.run([args.claude, "--version"], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, timeout=60).stdout.strip()
        p = subprocess.run(cmd, input=CHECK.format(state=args.state), stdout=subprocess.PIPE,
                           stderr=subprocess.PIPE, text=True, cwd=ROOT, timeout=600)
    except (OSError, subprocess.TimeoutExpired) as e:
        print(f"FAIL: {e}")
        return 1
    reply = (p.stdout or "").strip()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(reply + "\n--- stderr ---\n" + (p.stderr or ""))
    ok = p.returncode == 0 and str(expect) in reply
    print(f"version: {v}\nexit: {p.returncode}\nreply: {reply[:200]!r}\nexpected line count: {expect}")
    print("PASS" if ok else f"FAIL (details in {out})")
    return 0 if ok else 1


def cmd_run(args, items, outdir) -> int:
    todo = [(l, p) for l, p in items if args.force or not (outdir / f"run_{l}.md").is_file()]
    print(f"# {len(items)} trajectories, {len(todo)} to analyse, {args.jobs} at a time", flush=True)

    def one(item):
        label, path = item
        ok, msg = session(args, TASK_A.format(traj=path, label=label), outdir / f"run_{label}.md",
                          outdir / "logs" / f"run_{label}.log")
        print(f"{'ok  ' if ok else 'FAIL'}\t{label}\t{msg}", flush=True)
        return ok

    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, args.jobs)) as ex:
        results = list(ex.map(one, todo))
    failed = results.count(False)
    print(f"# finished: {results.count(True)} ok, {failed} failed", flush=True)
    if args.summary and not failed:
        ok, msg = session(args, TASK_B.format(reports=outdir, name=args.summary),
                          outdir / f"summary_{args.summary}.md", outdir / "logs" / f"summary_{args.summary}.log")
        print(f"{'ok  ' if ok else 'FAIL'}\tsummary_{args.summary}\t{msg}", flush=True)
        failed += not ok
    elif args.summary:
        print("# summary skipped: rerun after the failed sessions are fixed", flush=True)
    return 1 if failed else 0


def cmd_bundle(args, items, outdir) -> int:
    files = [outdir / f"run_{l}.md" for l, _ in items if (outdir / f"run_{l}.md").is_file()]
    files += sorted(outdir.glob("summary_*.md"))
    dest = outdir / "bundle.txt"
    user = getpass.getuser()
    with open(dest, "w") as fh:
        for f in files:
            fh.write(f"\n<<<<< {f.name} >>>>>\n" + f.read_text().replace(user, "<USER>"))
    print(f"{len(files)} files, {os.path.getsize(dest) // 1024} KB -> {dest}")
    return 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("action", choices=["list", "check", "run", "bundle"])
    ap.add_argument("--state", default=os.environ.get("SSAP_STATE"))
    ap.add_argument("--batch", default="test-H_raw*", help="glob on the batch directory under STATE/jobs")
    ap.add_argument("--jobs", type=int, default=3)
    ap.add_argument("--summary", default=None, help="name of the task B summary to write after the runs")
    ap.add_argument("--claude", default="claude")
    ap.add_argument("--model", default=None)
    ap.add_argument("--max-turns", type=int, default=200)
    ap.add_argument("--timeout", type=int, default=5400, help="seconds per session")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args(argv)
    if not args.state:
        sys.exit("give --state STATE or set SSAP_STATE")
    outdir = Path(args.state) / "analysis" / "auto"
    outdir.mkdir(parents=True, exist_ok=True)
    if args.action == "check":
        return cmd_check(args, outdir)
    items = trajectories(args.state, args.batch)
    return {"list": cmd_list, "run": cmd_run, "bundle": cmd_bundle}[args.action](args, items, outdir)


if __name__ == "__main__":
    sys.exit(main())
