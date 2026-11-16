#!/usr/bin/env bash
# Health gate in front of every series start: services up, a quiet model endpoint, and a seed probe whose
# score can only be produced by a working endpoint + Wikipedia. Exit 0 = go, 1 = not now (the caller waits).
#   experiments/vero/gate.sh simpleqa|retail
# The probe re-measures a small slice of the validation split with --fresh, so a result cached during an
# outage can never make a later check pass.
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh" || exit 2
task=${1:?task: simpleqa|retail}
root=$VERO_STATE/$task
[ -f "$root/setup.json" ] || { echo "gate: $root is not initialised"; exit 1; }
PROBE_N=${GATE_PROBE_N:-12}
FLOOR=${GATE_FLOOR:-0.25}
QUIET_MIN=${GATE_QUIET_MIN:-15}
MAX_ERRORS=${GATE_MAX_ERRORS:-0}
case "$task" in
  simpleqa) ANSWERED_FLOOR=${GATE_ANSWERED_FLOOR:-0.40} ;;   # the searches fail before the agent answers
  *)        ANSWERED_FLOOR=${GATE_ANSWERED_FLOOR:-0.0} ;;
esac
gate() { printf 'gate %-22s %s\n' "$1" "$2"; }

# 1. services: a crashed proxy is restarted here (start is idempotent), then re-checked.
if [ -n "${GATE_SKIP_SERVICES:-}" ]; then      # the offline test only
  gate services "check skipped (GATE_SKIP_SERVICES)"
elif ! "$HERE/services.sh" status >/dev/null; then
  gate services "down: starting"
  "$HERE/services.sh" start >/dev/null
  "$HERE/services.sh" status >/dev/null || { gate services "still down"; exit 1; }
else
  gate services "up"
fi

# 2. model endpoint: no upstream error in the last QUIET_MIN minutes. LiteLLM writes one ERROR block per
# call that failed after its retries; a few of those mean the endpoint is still unstable.
errors=$("$VERO_PYTHON" - "$VERO_STATE/litellm.log" "$QUIET_MIN" <<'PY'
import re, sys, time
path, minutes = sys.argv[1], int(sys.argv[2])
now = time.gmtime()
now_min = now.tm_hour * 60 + now.tm_min
try:
    text = open(path, errors="replace").read()
except FileNotFoundError:
    print(0); raise SystemExit
count = 0
for line in text.splitlines():
    m = re.match(r"^(\d\d):(\d\d):\d\d - .*LiteLLM Proxy:ERROR", line)
    if not m:
        continue
    stamp = int(m.group(1)) * 60 + int(m.group(2))
    if now_min < minutes and stamp > 1440 - minutes:      # just after midnight
        stamp -= 1440
    if now_min - minutes < stamp <= now_min:
        count += 1
print(count)
PY
)
if [ "${errors:-0}" -gt "$MAX_ERRORS" ]; then
  gate endpoint "$errors upstream error(s) in the last ${QUIET_MIN} min (max ${MAX_ERRORS})"; exit 1
fi
gate endpoint "quiet for ${QUIET_MIN} min"

# 3. seed probe on the validation split: the score is only reachable with a working endpoint and Wikipedia.
out=$(PYTHONPATH="$HERE/../../src" "$VERO_PYTHON" -m rag_eval.vero.cli eval \
      --state-root "$root" --split validation --limit "$PROBE_N" --k 1 --fresh --parallel 3 2>/dev/null) || {
  gate probe "the evaluation could not run"; exit 1; }
PROBE_JSON="$out" PYTHONPATH="$HERE/../../src" "$VERO_PYTHON" - "$FLOOR" "$ANSWERED_FLOOR" <<'PY' || exit 1
import json, os, sys
from rag_eval.vero.integrity import probe_verdict
payload = json.loads(os.environ["PROBE_JSON"])
v = probe_verdict(payload, floor=float(sys.argv[1]), answered_floor=float(sys.argv[2]))
print(f"gate {'probe':<22} {v['scored']}/{v['tasks']} scored, mean {v['mean_score']}, answered {v['answered_rate']}")
if not v["valid"]:
    for reason in v["reasons"]:
        print(f"gate {'':<22} {reason}")
    raise SystemExit(1)
PY
exit 0
