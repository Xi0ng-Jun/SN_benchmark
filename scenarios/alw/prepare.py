#!/usr/bin/env python3
"""Turn ``facts.json`` and this machine's facts into a usable scenario directory.

Writes ``site.json`` (where things are on this machine), ``scenario.json`` and
``tasks/<practice task>/`` (what the solving agent is given for that task: the
instruction, ``task.toml`` and ``environment/``; with ``--with-tests`` also the
verifier's own inputs under ``tests/``). Exam tasks get no folder here: their files
stay in the benchmark checkout, which no sandbox mounts. Run it again after editing
``facts.json``; limits can also be changed later with ``hb-host limits``.
"""
import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
SLOTS = ("ANTHROPIC_MODEL ANTHROPIC_SMALL_FAST_MODEL ANTHROPIC_DEFAULT_HAIKU_MODEL "
         "ANTHROPIC_DEFAULT_SONNET_MODEL ANTHROPIC_DEFAULT_OPUS_MODEL CLAUDE_CODE_SUBAGENT_MODEL "
         "CLAUDE_CODE_AUTO_MODE_MODEL CLAUDE_CODE_BG_CLASSIFIER_MODEL")
TOOLS = "/opt/analog-layout/python/bin:/opt/analog-layout/tools/bin:/opt/analog-layout/agent/bin"
PYTHONS = ("/opt/analog-layout/python/bin/python3.12", "python3", "/usr/local/bin/python3",
           "/usr/bin/python3")
# One development session: Claude Code with the developer model, the fixed brief on stdin,
# its stream kept in the workspace. Nothing here is about the tasks.
DEVELOPER = r'''
mkdir -p "$HB_WORKSPACE/.home" "$HB_WORKSPACE/sessions" || exit 1
export HOME="$HB_WORKSPACE/.home" CLAUDE_CONFIG_DIR="$HB_WORKSPACE/.home/.claude"
export ANTHROPIC_BASE_URL="$HB_MODEL_URL" ANTHROPIC_API_KEY="$HB_MODEL_KEY"
for v in %(slots)s; do export "$v=$HB_MODEL"; done
export DISABLE_NON_ESSENTIAL_MODEL_CALLS=1 CLAUDE_CODE_TWO_STAGE_CLASSIFIER=false
export API_TIMEOUT_MS=3000000 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1
export NO_PROXY=127.0.0.1,localhost no_proxy=127.0.0.1,localhost
export PATH="$PATH:%(tools)s" PYTHONPATH="/opt/analog-layout/pdks/sky130/python"
cd "$HB_WORKSPACE" || exit 1
exec /opt/claude-code/bin/claude --model "$HB_MODEL" --verbose --output-format=stream-json \
  --permission-mode=bypassPermissions %(extra)s -p < "$HB_BRIEF" \
  > "$HB_WORKSPACE/sessions/$(date -u +%%Y%%m%%dT%%H%%M%%SZ).jsonl"
'''


def find_python(image: str) -> str:
    for cand in PYTHONS:
        try:
            p = subprocess.run(["docker", "run", "--rm", "--network", "none", "--entrypoint", "sh",
                                image, "-c", f"command -v {cand}"], capture_output=True, text=True,
                               timeout=120)
        except (OSError, subprocess.TimeoutExpired):
            break
        if p.returncode == 0 and p.stdout.strip():
            return p.stdout.strip().splitlines()[-1]
    sys.exit(f"prepare: no python3 found in {image}; give --developer-python")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--alw-repo", default=os.environ.get("ALW_REPO"))
    ap.add_argument("--solver-model", default=os.environ.get("SSAP_MODEL"))
    ap.add_argument("--developer-model", default=None, help="default: the solver model")
    ap.add_argument("--upstream-url", default="${SSAP_GATEWAY}",
                    help="model service; ${VAR} is read from the environment at run time")
    ap.add_argument("--upstream-key-env", default="SSAP_UPSTREAM_KEY")
    ap.add_argument("--developer-python", default=None)
    ap.add_argument("--with-tests", action="store_true",
                    help="also show the developer agent the practice tasks' tests/ folders")
    a = ap.parse_args()
    if not a.alw_repo or not a.solver_model:
        ap.error("--alw-repo and --solver-model (or ALW_REPO and SSAP_MODEL) are needed")
    facts = json.loads((HERE / "facts.json").read_text())
    repo = Path(a.alw_repo).resolve()
    used = facts["practice"] + facts["exam"]
    missing = [t for t in used if not (repo / "tasks" / t / "task.toml").is_file()]
    if missing:
        sys.exit(f"prepare: not in {repo}/tasks: {missing}")
    if set(facts["practice"]) & set(facts["exam"]):
        sys.exit("prepare: a task is both practice and exam")

    site = dict(facts["site"], alw_repo=str(repo))
    for prog in ("harbor", "alw"):
        found = shutil.which(site[prog])
        if not found:
            sys.exit(f"prepare: {site[prog]} is not on PATH")
        site[prog] = found
    (HERE / "site.json").write_text(json.dumps(site, indent=1))

    tasks_dir = HERE / "tasks"
    if tasks_dir.exists():
        shutil.rmtree(tasks_dir)
    for t in facts["practice"]:
        src, dst = repo / "tasks" / t, tasks_dir / t
        dst.mkdir(parents=True)
        for name in ("instruction.md", "instruction.zh-CN.md", "task.toml"):
            if (src / name).is_file():
                shutil.copy2(src / name, dst / name)
        shutil.copytree(src / "environment", dst / "environment", symlinks=True)
        if a.with_tests:
            shutil.copytree(src / "tests", dst / "tests", symlinks=True)

    dev = dict(facts.get("developer") or {})
    extra = f"--effort {dev.pop('effort')}" if dev.get("effort") else ""
    dev.pop("effort", None)
    scenario = {
        "tasks": [{"id": t, "group": facts["groups"][t], "files": f"tasks/{t}"}
                  for t in facts["practice"]]
                 + [{"id": t, "group": facts["groups"][t]} for t in facts["exam"]],
        "split": {"practice": facts["practice"], "exam": facts["exam"]},
        # -B: the commands must not leave bytecode files in the scenario directory (it is hashed)
        "commands": {k: ["python3", "-B", f"{k}.py"] for k in ("run", "score", "enter", "invalid")},
        "score": {"file": "verify/verifier/evaluation/metrics.json", "key": "score"},
        "initial_harness": "harness",
        "limits": facts["limits"],
        "models": {"solver": a.solver_model, "developer": a.developer_model or a.solver_model,
                   "upstream_url": a.upstream_url, "upstream_key_env": a.upstream_key_env,
                   "listen_host": "0.0.0.0", "url_host": "host.docker.internal"},
        "developer": {"command": ["sh", "-c", DEVELOPER % {"slots": SLOTS, "tools": TOOLS,
                                                           "extra": extra}],
                      "image": site["agent_image"],
                      "python": a.developer_python or find_python(site["agent_image"]), **dev},
        "solver_settings": facts.get("solver_settings") or {},
        "exam": facts["exam_settings"],
    }
    (HERE / "scenario.json").write_text(json.dumps(scenario, indent=1))
    print(f"prepare: {len(facts['practice'])} practice tasks, {len(facts['exam'])} exam tasks; "
          f"wrote site.json, scenario.json, tasks/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
