#!/usr/bin/env bash
# One bounded browser batch. Optional libs/fontconfig are caller-owned; no system setup here.
set -euo pipefail
cd "$(dirname "$0")/../../.."
exec 9>/tmp/v41-pytest.lock
flock -w 5 9
available=$(awk '/MemAvailable:/ {print $2}' /proc/meminfo)
if [ "$available" -lt 2621440 ]; then exit 75; fi
export OMP_NUM_THREADS=1 OPENBLAS_NUM_THREADS=1 MKL_NUM_THREADS=1
python_bin=${V41_PYTHON:-../../.venv/bin/python}
fixture_dir=$(mktemp -d /tmp/v41-browser.XXXXXX)
port=${V41_BROWSER_PORT:-8097}
PYTHONPATH=src:. nice -n 10 "$python_bin" apps/sn-workbench/tests/auto_run_fixture.py --root "$fixture_dir" --port "$port" > "$fixture_dir.log" 2>&1 9>&- &
fixture_pid=$!
trap 'kill "$fixture_pid" 2>/dev/null || true; wait "$fixture_pid" 2>/dev/null || true' EXIT
for attempt in $(seq 1 60); do
  if ! kill -0 "$fixture_pid" 2>/dev/null; then cat "$fixture_dir.log"; exit 1; fi
  if curl --silent --fail --max-time 1 "http://127.0.0.1:$port/" >/dev/null; then break; fi
  sleep 1
done
cd apps/sn-workbench
export WORKBENCH_TEST_URL="http://127.0.0.1:$port" NODE_OPTIONS=--max-old-space-size=1024
nice -n 10 node tests/auto-run-browser.mjs
