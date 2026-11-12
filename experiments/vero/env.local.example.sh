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

# export VERO_SAMPLE_CONCURRENCY=6                  # one series sends at most 3 x this many requests at once
# export UPSTREAM_PROXY=                            # "" = reach Wikipedia directly (default: $https_proxy)
