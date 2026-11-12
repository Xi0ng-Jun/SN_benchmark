#!/usr/bin/env bash
# Create the two state roots (SimpleQA, Retail) for the models of the current environment.
#   experiments/vero/init_tasks.sh "<analyst / implementer command>"
# The command gets the prompt as its last argument; {case_dir} is replaced by the directory of the analysis
# cases, e.g.  "claude -p --add-dir {case_dir} --permission-mode acceptEdits"  (do not end the command with
# an option that takes several values, or the prompt becomes one of them).
set -eu
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh"
cd "$HERE/../.."
cmd=${1:?the analyst / implementer command}
for t in simpleqa:simple_qa:web_search_agent retail:retail:tau-bench; do
  IFS=: read -r name preset agent <<< "$t"
  PYTHONPATH=src "$VERO_PYTHON" -m rag_eval.vero.cli init --state-root "$VERO_STATE/$name" --preset "$preset" \
    --vero-root "$VERO_ROOT" --agent-python "$VERO_ROOT/vero-agents/agents/$agent/.venv/bin/python" \
    --analyst-cmd "$cmd" --implementer-cmd "$cmd" --sample-timeout 180 --sample-concurrency "$VERO_SAMPLE_CONCURRENCY"
done
