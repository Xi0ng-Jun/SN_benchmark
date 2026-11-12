# Copy to experiments/vero/env.local.sh (not tracked) and fill in. Sourced by env.sh.
export VERO_ROOT=/path/to/vero                      # git clone --depth 1 --branch paper-v1 <VeRO repository>
# export VERO_PYTHON=/path/to/python                # default: this repository's .venv

# Upstream models (LiteLLM model strings). For an OpenAI-compatible endpoint: openai/<model name>.
export VERO_TARGET_MODEL=openai/<target-model>      # the agent being optimised (and the tau-bench simulated user)
export VERO_TARGET_API_BASE=https://<endpoint>/v1
export VERO_TARGET_API_KEY=<key>                    # or: $(some-command-that-prints-the-key)
# export VERO_TARGET_MAX_PARALLEL=80                # stay below the API group's concurrency limit

# Optional; each defaults to the target's value.
# export VERO_GRADER_MODEL=openai/<grader-model>    # SimpleQA grader (the paper uses GPT-4.1)
# export VERO_GRADER_API_BASE=... VERO_GRADER_API_KEY=... VERO_GRADER_MAX_PARALLEL=...
# export VERO_OPTIMIZER_MODEL=openai/<model>        # GEPA reflection model = the analyst/implementer's model
# export VERO_OPTIMIZER_API_BASE=... VERO_OPTIMIZER_API_KEY=... VERO_OPTIMIZER_MAX_PARALLEL=...

# export UPSTREAM_PROXY=                            # "" = reach Wikipedia directly (default: $https_proxy)
