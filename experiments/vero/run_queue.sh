#!/usr/bin/env bash
# The whole unattended pass: the four series one after another, each behind the health gate, on a global
# deadline. Retail first (it never touches Wikipedia); SimpleQA last, in the quiet hours of the shared proxy.
# Every series is resumable and idempotent: finished runs are skipped, so re-running this script continues
# where the previous pass stopped.
#
#   setsid nohup experiments/vero/run_queue.sh > $VERO_STATE/queue.log 2>&1 < /dev/null &
#
# Per-series output: $VERO_STATE/v41-retail.log, gepa-retail.log, v41-simpleqa.log, gepa-simpleqa.log.
# Machine-readable state of every series: $VERO_STATE/series-status.json.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh" || exit 2
cd "$HERE/../.." || exit 2
QUEUE_DEADLINE_H=${QUEUE_DEADLINE_H:-16}
started=$(date +%s)
stamp() { date -u +%Y-%m-%dT%H:%M:%SZ; }
note() { echo "[$(stamp)] $*"; }
expired() { [ $(( $(date +%s) - started )) -ge $(( QUEUE_DEADLINE_H * 3600 )) ]; }

note "queue start (deadline ${QUEUE_DEADLINE_H} h)"
rc_all=0
for spec in "v41 retail" "gepa tau-bench" "v41 simpleqa" "gepa simpleqa"; do
  set -- $spec; method=$1; task=$2
  tag="$method-$([ "$task" = tau-bench ] && echo retail || echo "$task")"
  log="$VERO_STATE/$tag.log"
  if expired; then
    note "$tag: skipped, the queue deadline (${QUEUE_DEADLINE_H} h) has passed"
    rc_all=1
    continue
  fi
  note "$tag: start (log: $log)"
  printf '\n===== %s run_queue start %s =====\n' "$tag" "$(stamp)" >> "$log"
  "$HERE/run_series.sh" "$method" "$task" run1 >> "$log" 2>&1
  rc=$?
  note "$tag: exit $rc ($(tail -n 3 "$log" | tr '\n' ' ' | cut -c1-300))"
  [ $rc = 0 ] || rc_all=1
done
note "queue end (exit $rc_all); writing $VERO_STATE/report.md"
"$VERO_PYTHON" "$HERE/summarize.py" > "$VERO_STATE/report.md" 2>&1 || note "summarize.py failed (see report.md)"
tail -n 40 "$VERO_STATE/report.md" 2>/dev/null
exit $rc_all
