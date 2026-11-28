# How this scenario's operations behave

From the researcher. Facts about the operations only; nothing here is about how to solve the tasks.

## What a run is

- The solving agent is Claude Code 2.1.302 with the solver model, started by Harbor inside
  the task's container. Its working directory is `/alb-workspace`. It is given the task's
  `instruction.md` as its prompt, exactly as the benchmark does.
- The harness folder is mounted read-only at `/opt/hb-harness`. Its `start` is run in place
  of Claude Code: it receives the command-line arguments Harbor would give Claude Code and
  the instruction on stdin, and its stdout is stored as the agent's log. `start` launches
  Claude Code with `"$HB_CLAUDE" "$@"`. Its environment also has `HB_AGENT_SECONDS` (the
  time it has) and `HB_HARNESS` (`/opt/hb-harness`).
- Whatever `start` does counts against the agent's time. When the time is up, `start` and
  everything it started are stopped. Then the whole workspace is copied out, and
  `/alb-workspace/submission/` is what gets scored.
- The agent's time is the task's own limit (`agent.timeout_sec` in `task.toml`). With
  `hb run TASK --seconds N` it is `N` minus 900 seconds, which are kept for setting up the
  task environment and collecting the output; `N` must be above 960.
- Solver settings fixed by the researcher (`hb status` shows them) are applied when Claude
  Code is launched through `$HB_CLAUDE`, whatever `start` passes.

## What a run leaves in `runs/rNNNN/out`

- `submission/`: the copy of `/alb-workspace/submission` that was scored.
- `harbor/<job>/<trial>/agent/`: `claude-code.txt` (the agent's raw stream, with
  timestamps), `trajectory.json` (the same as steps), and `hb/workspace/` (the whole
  workspace as it was when the agent ended), `hb/run.json` (how `start` ended).
- `meta.json`: times and how the run ended.

`runs/rNNNN/score/verify/` is the complete output of the benchmark's own verifier;
`verify/verifier/evaluation/metrics.json` holds the official score (`score`) and the
verdict of each stage.

## `hb score`

The product folder must contain `submission/` (with `layout.gds`), as a run's `out` does.

## `hb enter`

Runs your command in the task's container without an agent and without network. The
exchange folder is the current directory, `/alb-workspace/hb`; the task's files are one
level up (`../task`, `../GUIDE.md`, `../DEVICE_API.md`, `../eda_env.sh`), as the solving
agent sees them.

## The practice task folders

Each holds what the solving agent is given: `instruction.md`, `task.toml`, `environment/`.
