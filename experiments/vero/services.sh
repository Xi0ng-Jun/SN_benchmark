#!/usr/bin/env bash
# Local services of the VeRO experiment: LiteLLM proxy (DeepSeek) and the Wikipedia proxy.
#   experiments/vero/services.sh start|stop|status
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
STATE="$ROOT/.vero-state"
mkdir -p "$STATE"
# shellcheck source=env.sh
source "$HERE/env.sh"

up() { curl -s -m 3 -o /dev/null "$1"; }

start() {
  if up "http://127.0.0.1:$LITELLM_PORT/health/liveliness"; then echo "litellm: already up"; else
    setsid nohup litellm --config "$HERE/litellm.yaml" --port "$LITELLM_PORT" --host 127.0.0.1 \
      > "$STATE/litellm.log" 2>&1 < /dev/null & echo $! > "$STATE/litellm.pid"
    echo -n "litellm: starting"
    for _ in $(seq 1 180); do up "http://127.0.0.1:$LITELLM_PORT/health/liveliness" && break; echo -n .; sleep 1; done; echo
  fi
  if up "http://127.0.0.1:$WIKI_PROXY_PORT/"; then echo "wiki proxy: already up"; else
    PYTHONPATH="$ROOT/src" setsid nohup "$ROOT/../../.venv/bin/python" -m rag_eval.vero.wiki_proxy \
      --port "$WIKI_PROXY_PORT" --upstream "$SYSTEM_PROXY" --cache "$STATE/wiki-cache.sqlite" \
      --rate "$WIKI_RATE" --user-agent "$WIKI_USER_AGENT" \
      > "$STATE/wiki-proxy.log" 2>&1 < /dev/null & echo $! > "$STATE/wiki-proxy.pid"
    sleep 2
  fi
  status
}

stop() {
  for name in litellm wiki-proxy; do
    if [ -f "$STATE/$name.pid" ]; then kill "$(cat "$STATE/$name.pid")" 2>/dev/null && echo "$name: stopped"; rm -f "$STATE/$name.pid"; fi
  done
}

status() {
  up "http://127.0.0.1:$LITELLM_PORT/health/liveliness" && echo "litellm: up (:$LITELLM_PORT)" || echo "litellm: DOWN"
  # the wiki proxy answers 400 to a non-proxy request: any HTTP answer means it is up
  curl -s -m 3 -o /dev/null -w "%{http_code}" "http://127.0.0.1:$WIKI_PROXY_PORT/" | grep -q 400 \
    && echo "wiki proxy: up (:$WIKI_PROXY_PORT)" || echo "wiki proxy: DOWN"
}

case "${1:-status}" in start) start ;; stop) stop ;; status) status ;; *) echo "usage: $0 start|stop|status"; exit 2 ;; esac
