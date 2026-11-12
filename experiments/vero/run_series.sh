#!/usr/bin/env bash
# Runs of one method on one task, one after another; stops the series at the first failure it cannot recover.
#   experiments/vero/run_series.sh v41  simpleqa|retail     run1 [run2 ...]
#   experiments/vero/run_series.sh gepa simpleqa|tau-bench  run1 [run2 ...]
# v41: optimisation run, then the test endpoint. A process_failure halt (service outage, agent failure) is
# resumed after RESUME_WAIT seconds, at most MAX_RESUMES times; a crash or a persistent failure ends the
# series (later runs are not started with a broken service). Finished runs are skipped.
# Detached:  setsid nohup experiments/vero/run_series.sh v41 retail run1 run2 run3 > $VERO_STATE/v41-retail.log 2>&1 &
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh" || exit 2
cd "$HERE/../.." || exit 2
method=${1:?method: v41|gepa}; task=${2:?task}; shift 2
[ $# -gt 0 ] || { echo "no run label given"; exit 2; }
MAX_RESUMES=${MAX_RESUMES:-3}; RESUME_WAIT=${RESUME_WAIT:-600}
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
cli() { PYTHONPATH=src "$VERO_PYTHON" -m rag_eval.vero.cli "$@"; }
halt_reason() { "$VERO_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("halt_reason") or "")' "$1/state.json" 2>/dev/null; }
"$HERE/services.sh" status >/dev/null || { echo "services are down: experiments/vero/services.sh start"; exit 1; }

case "$method" in
v41)
  root=$VERO_STATE/$task; config=${V41_CONFIG:-$HERE/v41-$task.json}
  [ -f "$root/setup.json" ] || { echo "$root is not initialised (rag_eval.vero.cli init)"; exit 2; }
  for r in "$@"; do
    dir=$root/runs/$r
    if [ -f "$dir/endpoint.json" ]; then echo "[$(stamp)] $task $r: already finished"; continue; fi
    resumes=0
    while :; do
      echo "[$(stamp)] $task $r: run (resume $resumes)"
      cli run --state-root "$root" --run-dir "$dir" --v4-config "$config"; rc=$?
      reason=$(halt_reason "$dir")
      if [ $rc = 0 ] || [ "$reason" = budget_exhausted ]; then break; fi
      if [ $rc != 3 ] || [ "$reason" != process_failure ]; then
        echo "[$(stamp)] $task $r: run ended with exit $rc (halt reason: ${reason:-none}); series stopped"; exit 1
      fi
      resumes=$((resumes + 1))
      if [ $resumes -gt "$MAX_RESUMES" ]; then echo "[$(stamp)] $task $r: still failing after $MAX_RESUMES resumes; series stopped"; exit 1; fi
      echo "[$(stamp)] $task $r: process_failure; resuming in $RESUME_WAIT s"; sleep "$RESUME_WAIT"
    done
    echo "[$(stamp)] $task $r: halted ($reason); test endpoint"
    cli test --state-root "$root" --run-dir "$dir" --k "${TEST_K:-1}" || { echo "[$(stamp)] $task $r: test failed; series stopped"; exit 1; }
  done ;;
gepa)
  for r in "$@"; do
    tries=0
    until "$VERO_ROOT/vero-benchmarking/.venv/bin/python" "$HERE/run_gepa.py" --task "$task" --run "$r"; do
      tries=$((tries + 1))
      if [ $tries -gt "$MAX_RESUMES" ]; then echo "[$(stamp)] gepa $task $r: failed $tries times; series stopped"; exit 1; fi
      echo "[$(stamp)] gepa $task $r: failed; restarting from scratch in $RESUME_WAIT s"; sleep "$RESUME_WAIT"
    done
  done ;;
*) echo "unknown method $method"; exit 2 ;;
esac
echo "[$(stamp)] $method $task: series finished"
