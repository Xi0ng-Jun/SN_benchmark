#!/usr/bin/env bash
# Checks before any run: services, tool calling through every proxy alias, Wikipedia, the agent command.
#   experiments/vero/preflight.sh ["<analyst/implementer command>" ...]
# e.g. experiments/vero/preflight.sh "pi -p --no-session --model deepseek-flash"
#      experiments/vero/preflight.sh "claude -p --add-dir {case_dir} --permission-mode acceptEdits"
set -u
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$HERE/env.sh" || exit 2
fail=0
say() { printf '%-34s %s\n' "$1" "$2"; }

"$HERE/services.sh" status || { echo "start them: experiments/vero/services.sh start"; exit 1; }

body() { cat <<JSON
{"model": "$1", "max_tokens": 200,
 "messages": [{"role": "user", "content": "What is the weather in Paris? Use the tool."}],
 "tools": [{"type": "function", "function": {"name": "get_weather", "description": "Weather of a city",
   "parameters": {"type": "object", "properties": {"city": {"type": "string"}}, "required": ["city"]}}}]}
JSON
}
for alias in gpt-4.1-mini-2025-04-14 gpt-4.1-mini gpt-4.1-2025-04-14 optimizer; do
  out=$(curl -s -m 120 "$LITELLM_BASE_URL/v1/chat/completions" -H "Authorization: Bearer $LITELLM_API_KEY" \
        -H "Content-Type: application/json" -d "$(body "$alias")")
  verdict=$(printf %s "$out" | "$VERO_PYTHON" -c '
import json, sys
try:
    d = json.load(sys.stdin)
    m = d["choices"][0]["message"]
    calls = m.get("tool_calls") or []
    print("ok: tool call " + calls[0]["function"]["name"] if calls else "NO TOOL CALL: " + str(m.get("content"))[:80])
except Exception as e:
    print("FAILED: " + str(d.get("error", e) if isinstance(locals().get("d"), dict) else e)[:160])')
  say "model alias $alias" "$verdict"
  case "$verdict" in ok*) ;; *) fail=1 ;; esac
done

for _ in 1 2 3; do          # the proxy gives up after its deadline; a flaky upstream may need a second try
  code=$(curl -s -m 60 -o /dev/null -w "%{http_code}" -x "http://127.0.0.1:$WIKI_PROXY_PORT" \
         "http://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=Alan+Turing&format=json")
  [ "$code" = 200 ] && break
done
say "Wikipedia through the proxy" "$([ "$code" = 200 ] && echo ok || echo "FAILED (HTTP $code)")"
[ "$code" = 200 ] || fail=1

# Throttling probe: 12 different searches at once (about what one SimpleQA batch sends in a few seconds).
for q in turing hopper lovelace knuth dijkstra shannon babbage ritchie liskov hamilton backus mccarthy; do
  curl -s -m 90 -o /dev/null -w "%{http_code}\n" -x "http://127.0.0.1:$WIKI_PROXY_PORT" \
    "http://en.wikipedia.org/w/api.php?action=query&list=search&srsearch=$q+computer+scientist&format=json" &
done > "$VERO_STATE/preflight-wiki.txt"; wait
ok=$(grep -c '^200$' "$VERO_STATE/preflight-wiki.txt")
say "Wikipedia burst (12 searches)" "$([ "$ok" = 12 ] && echo ok || echo "WARNING: only $ok of 12 served (throttled? see $VERO_STATE/wiki-proxy.log; lower WIKI_RATE)")"

# The agent command, run exactly as a run does (prompt appended, {case_dir} replaced): it must read a case
# file outside its working directory and edit a file inside it.
for cmd in "$@"; do
  PYTHONPATH="$HERE/../../src" "$VERO_PYTHON" -m rag_eval.vero.cli agent-check --dir "$VERO_STATE" --cmd "$cmd" || fail=1
done
[ $fail = 0 ] && echo "preflight passed" || echo "preflight FAILED"
exit $fail
