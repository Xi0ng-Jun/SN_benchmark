#!/usr/bin/env bash
# Seed baseline for candidate target models: the unoptimised agent of both tasks, once on the test split.
#   experiments/vero/baseline.sh <LiteLLM model string> [...]     e.g. openai/model-a openai/model-b
# Each model replaces VERO_TARGET_MODEL for this script only (the LiteLLM proxy is restarted with it).
# State: $VERO_STATE/baseline/<model>/<task>; finished evaluations are reused. LIMIT=3 for a smoke test.
# Afterwards: put the chosen model into env.local.sh and run services.sh start (it applies the mapping).
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE/../.." || exit 2
[ $# -gt 0 ] || { sed -n 2,6p "$0"; exit 2; }
TASKS=${TASKS:-"simpleqa:simple_qa:web_search_agent retail:retail:tau-bench"}
for model in "$@"; do
  (
    export VERO_TARGET_MODEL_OVERRIDE=$model
    source "$HERE/env.sh" || exit 2
    "$HERE/services.sh" start | tail -n 2
    "$HERE/services.sh" status >/dev/null || { echo "$model: services are down"; exit 1; }
    cli() { PYTHONPATH=src "$VERO_PYTHON" -m rag_eval.vero.cli "$@"; }
    slug=$(printf %s "$model" | tr -c 'A-Za-z0-9._-' '_')
    for t in $TASKS; do
      IFS=: read -r name preset agent <<< "$t"
      root=$VERO_STATE/baseline/$slug/$name
      mkdir -p "$root"
      if [ ! -f "$root/setup.json" ]; then
        cli init --state-root "$root" --preset "$preset" --vero-root "$VERO_ROOT" \
          --agent-python "$VERO_ROOT/vero-agents/agents/$agent/.venv/bin/python" \
          --sample-timeout 180 --sample-concurrency "$VERO_SAMPLE_CONCURRENCY" > "$root/init.log" 2>&1 \
          || { echo "$model $name: init failed, see $root/init.log"; continue; }
      fi
      echo "[$(date -u +%H:%M:%SZ)] $model $name: evaluating the seed on the test split"
      if cli eval --state-root "$root" --split test ${LIMIT:+--limit "$LIMIT"} > "$root/test.json.new" 2> "$root/test.log"; then
        mv "$root/test.json.new" "$root/test.json"
      else
        rm -f "$root/test.json.new"; echo "$model $name: evaluation failed, see $root/test.log"
      fi
    done
  )
done
source "$HERE/env.sh" >/dev/null 2>&1
"$VERO_PYTHON" - "$VERO_STATE/baseline" <<'PY'
import json, pathlib, sys
base = pathlib.Path(sys.argv[1])
print("\n| target model | task | scored / tasks | seed test score | answered rate |\n|---|---|---|---|---|")
for f in sorted(base.glob("*/*/test.json")):
    d, setup = json.loads(f.read_text()), json.loads((f.parent / "setup.json").read_text())
    fmt = lambda v: "-" if v is None else f"{v:.3f}"
    print(f"| {(setup.get('models') or {}).get('target')} | {f.parent.name} | {d['scored']} / {d['tasks']} | "
          f"{fmt(d['mean_score'])} | {fmt(d['answered_rate'])} |")
PY
