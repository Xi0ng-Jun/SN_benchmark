#!/usr/bin/env bash
# One-time setup on a machine; safe to run again. Needs git, uv, curl and VERO_ROOT (from env.local.sh or
# the environment). It prepares:
#   - the VeRO checkout at tag paper-v1 with experiments/vero/patches/gepa-paper-v1.patch
#   - the environments of the two agents and of vero-benchmarking (GEPA)
#   - the two datasets (copied from experiments/vero/datasets: the splits every machine must share)
#   - the LiteLLM proxy and, when this repository has no Python environment yet, .venv
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(cd "$HERE/../.." && pwd)"
LOCAL=${VERO_ENV_LOCAL:-$HERE/env.local.sh}
if [ -f "$LOCAL" ]; then source "$LOCAL"; fi
: "${VERO_ROOT:?set VERO_ROOT in experiments/vero/env.local.sh}"
PAPER_COMMIT=4ae9c1df1e5d303b875629fa53b686a211897c4e
for tool in git uv curl; do
  command -v "$tool" >/dev/null || { echo "missing: $tool"; exit 2; }
done

if [ ! -d "$VERO_ROOT/.git" ]; then
  git clone --depth 1 --branch paper-v1 "${VERO_REPO_URL:-https://github.com/scaleapi/vero.git}" "$VERO_ROOT"
fi
head=$(git -C "$VERO_ROOT" rev-parse HEAD)
[ "$head" = "$PAPER_COMMIT" ] || { echo "VeRO checkout is at $head, expected paper-v1 ($PAPER_COMMIT)"; exit 1; }
# gepa.py is the only VeRO file this experiment changes: reset it, then apply the current patch.
git -C "$VERO_ROOT" checkout -- vero-benchmarking/src/vero_benchmarking/gepa.py
git -C "$VERO_ROOT" apply "$HERE/patches/gepa-paper-v1.patch"
echo "VeRO: paper-v1 with the GEPA patch"

(cd "$VERO_ROOT/vero-agents/agents/web_search_agent" && uv sync)
(cd "$VERO_ROOT/vero-agents/agents/tau-bench" && uv sync)
(cd "$VERO_ROOT/vero-benchmarking" && uv sync --extra gepa)

mkdir -p "$VERO_ROOT/vero-benchmarking/datasets"
cp -r "$HERE/datasets/." "$VERO_ROOT/vero-benchmarking/datasets/"
echo "datasets: $(ls "$VERO_ROOT/vero-benchmarking/datasets" | tr '\n' ' ')"

command -v litellm >/dev/null || uv tool install 'litellm[proxy]'
command -v litellm >/dev/null || { echo "litellm is installed but not on PATH (add ~/.local/bin, or run: uv tool update-shell)"; exit 1; }

if [ -z "${VERO_PYTHON:-}" ] && [ ! -x "$REPO/.venv/bin/python" ] && [ ! -x "$REPO/../../.venv/bin/python" ]; then
  uv venv --python 3.13 "$REPO/.venv"
  uv pip install --python "$REPO/.venv/bin/python" 'httpx>=0.27,<1' 'pytest>=8'
fi
echo "setup finished"
