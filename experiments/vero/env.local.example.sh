# Copy to experiments/vero/env.local.sh (not tracked) and fill in. Sourced by env.sh and setup_vero.sh.
export VERO_ROOT=/path/to/vero                      # VeRO checkout at tag paper-v1 (setup_vero.sh clones it)
# State, logs and the agents' worktrees. Use a directory outside every git repository and outside every
# directory tree with a CLAUDE.md / AGENTS.md: coding-agent CLIs load such files from parent directories.
export VERO_STATE=/path/to/vero-state
# export VERO_PYTHON=/path/to/python                # default: this repository's .venv (setup_vero.sh creates it)

# Upstream models (LiteLLM model strings). For an OpenAI-compatible endpoint: openai/<model name>.
export VERO_TARGET_MODEL=openai/<target-model>      # the agent being optimised (and the tau-bench simulated user)
export VERO_TARGET_API_BASE=https://<endpoint>/v1
export VERO_TARGET_API_KEY=<key>                    # or: $(some-command-that-prints-the-key)

# Each defaults to the target's value.
# export VERO_GRADER_MODEL=openai/<grader-model>    # SimpleQA grader (the paper uses GPT-4.1): a strong model
# export VERO_GRADER_API_BASE=... VERO_GRADER_API_KEY=...
# export VERO_OPTIMIZER_MODEL=openai/<model>        # GEPA reflection model = the analyst/implementer's model
# export VERO_OPTIMIZER_API_BASE=... VERO_OPTIMIZER_API_KEY=...

# Concurrency and gate/retry settings of unattended runs (run_queue.sh, gate.sh). 3 is the stability default:
# one series then sends at most 3 x 3 = 9 requests at once. At 6 (18 requests) a single series already trips
# the upstream's channel limit.
# export VERO_SAMPLE_CONCURRENCY=3     # one series sends at most 3 x this many requests; 6 trips the upstream channel limit
# export GATE_FLOOR=0.25               # the seed probe must score at least this on validation before a run starts
# export GATE_PROBE_N=12               # validation samples in the probe
# export RETRY_WAIT=900                # seconds between retries of an unhealthy or failed series
# export SERIES_DEADLINE_H=12          # give up on one series after this many hours
# export MAX_RESTARTS=3                # restart a run from scratch at most this many times when its measurements are invalid
# export UPSTREAM_PROXY=                            # "" = reach Wikipedia directly (default: $https_proxy)
