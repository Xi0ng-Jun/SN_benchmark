#!/usr/bin/env bash
# Runs of one method on one task, one after another, unattended: a health gate before every start, a
# validity check on every measurement before it becomes a result, and bounded retries with backoff.
#   experiments/vero/run_series.sh v41  simpleqa|retail     run1 [run2 ...]
#   experiments/vero/run_series.sh gepa simpleqa|tau-bench  run1 [run2 ...]
# v41: a process_failure is resumed after RESUME_WAIT (state and evaluation cache survive). A run whose seed
# calibration is implausible — the services were down while the seed was measured — is archived and restarted
# from scratch; its measurements are never turned into a result. A run that ends because the method itself
# is stuck is tested like any other. GEPA has no resume: a failed attempt restarts from scratch, and an
# attempt whose measurements are invalid writes invalid.json (run_gepa.py exits 3) and is archived.
# Finished runs are skipped, so the same command continues a stopped series.
# Detached:  setsid nohup experiments/vero/run_series.sh v41 retail run1 > $VERO_STATE/v41-retail.log 2>&1 &
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh" || exit 2
cd "$HERE/../.." || exit 2
method=${1:?method: v41|gepa}; task=${2:?task}; shift 2
[ $# -gt 0 ] || { echo "no run label given"; exit 2; }
case "$task" in tau-bench) root_task=retail ;; *) root_task=$task ;; esac   # GEPA's task name vs the state root
MAX_RESUMES=${MAX_RESUMES:-6}                 # resumptions of a run that keeps failing (state kept)
MAX_RESTARTS=${MAX_RESTARTS:-3}               # restarts from scratch of a run whose measurements are invalid
MAX_AGENT_FAILURES=${MAX_AGENT_FAILURES:-2}   # a broken agent command is not an outage: stop quickly
MAX_GATE_FAILURES=${MAX_GATE_FAILURES:-8}     # consecutive failed health gates before the series gives up
RESUME_WAIT=${RESUME_WAIT:-600}
RETRY_WAIT=${RETRY_WAIT:-900}
SERIES_DEADLINE_H=${SERIES_DEADLINE_H:-12}
FLOOR=${GATE_FLOOR:-0.25}
GATE=${GATE:-1}
[ -n "${VERO_SKIP_SERVICE_CHECK:-}" ] && GATE=0      # the offline tests run without services
started=$(date +%s)
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
note() { echo "[$(stamp)] $*"; }
cli() { PYTHONPATH=src "$VERO_PYTHON" -m rag_eval.vero.cli "$@"; }
field() { "$VERO_PYTHON" -c 'import json,sys; print(json.load(open(sys.argv[1])).get(sys.argv[2]) or "")' "$1/state.json" "$2" 2>/dev/null; }
expired() { [ $(( $(date +%s) - started )) -ge $(( SERIES_DEADLINE_H * 3600 )) ]; }

status() {   # status <state> [detail] — one key per series in $VERO_STATE/series-status.json
  "$VERO_PYTHON" - "$VERO_STATE/series-status.json" "$method" "$task" "$1" "${2:-}" <<'PY'
import datetime, json, os, sys
path, method, task, state, detail = sys.argv[1:6]
data = json.load(open(path)) if os.path.exists(path) else {}
data[f"{method} {task}"] = dict(state=state, detail=detail,
                                at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds"))
with open(path + ".tmp", "w") as fh:
    json.dump(data, fh, indent=1, ensure_ascii=False)
os.replace(path + ".tmp", path)
PY
}

archive() {  # archive <dir> <name>: keep an invalid run for the record, out of every result table
  local dst="$VERO_STATE/archive/$2-$(date -u +%Y%m%dT%H%M%SZ)"
  mkdir -p "$VERO_STATE/archive"
  if mv "$1" "$dst"; then note "archived $1 -> $dst"; else note "could not archive $1"; fi
}

mark_invalid() {  # mark_invalid <dir> <reason>
  "$VERO_PYTHON" - "$1/invalid.json" "$2" <<'PY'
import datetime, json, sys
json.dump(dict(reason=sys.argv[2], at=datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")),
          open(sys.argv[1], "w"), indent=1)
PY
}

v41_valid() {  # 0 plausible seed calibration, 1 implausible (the services were down), 2 no calibration yet
  [ -f "$1/state.json" ] || return 2
  PYTHONPATH="$HERE/../../src" "$VERO_PYTHON" - "$1/state.json" "$FLOOR" <<'PY'
import json, sys
from rag_eval.vero.integrity import run_verdict
verdict = run_verdict(json.load(open(sys.argv[1])), floor=float(sys.argv[2]))
if verdict["seed_val"] is None:
    print("gate calibration           none yet")
    raise SystemExit(2)
print(f"gate calibration           seed validation {verdict['seed_val']:.3f} (floor {float(sys.argv[2]):.2f}), "
      f"rerun_var {verdict['rerun_var']}")
raise SystemExit(0 if verdict["valid"] else 1)
PY
}

endpoint_ok() {  # 0 when the test-split seed score is plausible, 1 when it is not
  "$VERO_PYTHON" - "$1/endpoint.json" "$FLOOR" <<'PY'
import json, sys
try:
    summary = json.load(open(sys.argv[1]))["summary"]
except Exception:
    raise SystemExit(1)
seed = summary.get("seed_score")
print(f"gate test endpoint         seed {seed} on the test split (floor {float(sys.argv[2]):.2f})")
raise SystemExit(0 if seed is not None and seed >= float(sys.argv[2]) else 1)
PY
}

wait_healthy() {  # wait_healthy <run label>: 0 when the gate passes, 1 when the series must give up
  local fails=0
  if [ "$GATE" = 0 ]; then note "gate skipped (GATE=0)"; return 0; fi
  while :; do
    if expired; then note "$task $1: series deadline (${SERIES_DEADLINE_H} h) reached"; return 1; fi
    if "$HERE/gate.sh" "$root_task"; then return 0; fi
    fails=$((fails + 1))
    if [ $fails -ge "$MAX_GATE_FAILURES" ]; then
      note "$task $1: services unhealthy after $fails gate checks; series stopped"; return 1
    fi
    note "$task $1: gate failed ($fails/$MAX_GATE_FAILURES); waiting ${RETRY_WAIT}s"
    sleep "$RETRY_WAIT"
  done
}

clear_test() {  # drop the cached test-split measurements of one run, so `cli test` measures again
  find "$1/evals" -path '*/test-*/*' -name 'a*.json' -delete 2>/dev/null
  rm -f "$1/endpoint.json"
}

v41_halt() {  # 0 when run $1 is halted with plausible measurements, 1 when the series must give up
  local r=$1 dir=$root/runs/$1 resumes=0 restarts=0 rc reason detail kind v
  while :; do
    wait_healthy "$r" || return 1
    note "$task $r: run (resumes $resumes, restarts $restarts)"
    cli run --state-root "$root" --run-dir "$dir" --v4-config "$config"; rc=$?
    reason=$(field "$dir" halt_reason); detail=$(field "$dir" failure_detail); kind=$(field "$dir" failure_kind)
    note "$task $r: exit $rc, halt ${reason:-none}${detail:+ ($detail)}"
    if [ "$reason" != stopped ]; then
      v41_valid "$dir"; v=$?
      if [ $v = 1 ]; then        # the seed was measured while the services were down: no result can come out of this
        mark_invalid "$dir" "seed calibration implausible (the services were down while the seed was measured)"
        archive "$dir" "$method-$task-$r-invalid"
        restarts=$((restarts + 1)); resumes=0
        if [ $restarts -gt "$MAX_RESTARTS" ]; then
          note "$task $r: measurements invalid $restarts times; series stopped"; return 1
        fi
        note "$task $r: measurements invalid; restarting from scratch ($restarts/$MAX_RESTARTS)"
        sleep "$RETRY_WAIT"; continue
      fi
    fi
    case "$reason" in
      budget_exhausted|time_budget) break ;;
      "") if [ $rc = 0 ]; then break; fi
          note "$task $r: crashed without a halt reason; treating it as an outage" ;;
      stopped) note "$task $r: stopped by an operator; series stopped"; return 1 ;;
      process_failure)
        if [ "$kind" = agent_request ]; then
          resumes=$((resumes + 1))
          if [ $resumes -gt "$MAX_AGENT_FAILURES" ]; then
            note "$task $r: the agent command keeps failing ($detail); series stopped"; return 1
          fi
          sleep "$RETRY_WAIT"; continue
        fi
        resumes=$((resumes + 1))
        if [ $resumes -gt "$MAX_RESUMES" ]; then
          case "$detail" in
            *"no cards"*|*"no candidate reached measurement"*)
              note "$task $r: the method is stuck after $MAX_RESUMES resumes ($detail); the run ends here"; break ;;
            *) note "$task $r: still failing after $MAX_RESUMES resumes ($detail); service outage, waiting ${RETRY_WAIT}s"
               resumes=0; sleep "$RETRY_WAIT"; continue ;;
          esac
        fi
        sleep "$RESUME_WAIT"; continue ;;
      *) note "$task $r: unexpected halt ($reason); treating it as an outage" ;;
    esac
    restarts=$((restarts + 1))
    if [ $restarts -gt "$MAX_RESTARTS" ]; then
      note "$task $r: failed $restarts times; series stopped"; return 1
    fi
    sleep "$RETRY_WAIT"
  done
  return 0
}

v41_one() {
  local r=$1 dir=$root/runs/$1 retests
  if [ -f "$dir/endpoint.json" ]; then note "$task $r: already finished"; return 0; fi
  while :; do
    v41_halt "$r" || return 1
    retests=0
    while :; do
      if cli test --state-root "$root" --run-dir "$dir" --k "${TEST_K:-1}" && endpoint_ok "$dir"; then
        note "$task $r: test endpoint recorded"; return 0
      fi
      retests=$((retests + 1))
      if [ $retests -ge 3 ]; then break; fi
      clear_test "$dir"
      note "$task $r: the test split was measured while the services were down; measuring it again ($retests/3)"
      sleep "$RETRY_WAIT"
    done
    # The optimisation itself is valid (its calibration passed): keep the run and stop here. Re-running the
    # series later measures the test split again; nothing is archived or optimised twice.
    clear_test "$dir"
    note "$task $r: the test split could not be measured on healthy services; run kept, series stopped"
    return 1
  done
}

gepa_one() {
  local r=$1 out=$VERO_STATE/gepa/$task/$1 attempts=0 rc
  if [ -f "$out/summary.json" ]; then note "gepa $task $r: already finished"; return 0; fi
  while :; do
    wait_healthy "$r" || return 1
    attempts=$((attempts + 1))
    note "gepa $task $r: attempt $attempts"
    "$VERO_ROOT/vero-benchmarking/.venv/bin/python" "$HERE/run_gepa.py" --task "$task" --run "$r" --floor "$FLOOR"; rc=$?
    case "$rc" in
      0) note "gepa $task $r: finished"; return 0 ;;
      3) note "gepa $task $r: measurements invalid (see invalid.json); archived"
         archive "$out" "$method-$task-$r-invalid" ;;
      *) note "gepa $task $r: failed (exit $rc)" ;;
    esac
    if [ $attempts -ge "$MAX_RESTARTS" ] || expired; then
      note "gepa $task $r: failed $attempts times; series stopped"; return 1
    fi
    sleep "$RETRY_WAIT"
  done
}

note "$method $task: series start (deadline ${SERIES_DEADLINE_H} h, gate floor $FLOOR)"
case "$method" in
v41)
  root=$VERO_STATE/$root_task; config=${V41_CONFIG:-$HERE/v41-$task.json}
  [ -f "$root/setup.json" ] || { echo "$root is not initialised (rag_eval.vero.cli init)"; exit 2; }
  status running
  for r in "$@"; do v41_one "$r" || { status stopped "run $r"; exit 1; }; done ;;
gepa)
  status running
  for r in "$@"; do gepa_one "$r" || { status stopped "run $r"; exit 1; }; done ;;
*) echo "unknown method $method"; exit 2 ;;
esac
status finished
note "$method $task: series finished"
