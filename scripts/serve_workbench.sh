#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.."
if [[ -f configs/sn-workbench-local.json ]]; then
  default_config=configs/sn-workbench-local.json
  default_state=var/sn-workbench
else
  default_config=var/sn-workbench-server/config.json
  default_state=var/sn-workbench-server/state
fi
config="${SN_WORKBENCH_CONFIG:-$default_config}"
state="${SN_WORKBENCH_STATE:-$default_state}"
if [[ ! -f "$config" || ! -f apps/sn-workbench/dist/index.html || ! -f apps/sn-agent-bridge/dist/cli.js ]]; then
  echo 'Missing configuration or build. Follow docs/server-workbench-handoff.md.' >&2
  exit 1
fi
export PYTHONPATH="$PWD/src${PYTHONPATH:+:$PYTHONPATH}"
exec "${SN_WORKBENCH_PYTHON:-.venv/bin/python}" -m rag_eval.workbench.cli \
  --state "$state" serve --config "$config" --port "${PASEO_PORT:-${SN_WORKBENCH_PORT:-8091}}"
