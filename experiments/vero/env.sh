# source experiments/vero/env.sh  — environment for the VeRO experiment.
# No secret is stored here: the DeepSeek key is read from pi's credential store when sourced.
export VERO_ROOT=/home/wabiwabi/silicon-notebook/external/vero
export VERO_AGENTS_PATH=$VERO_ROOT/vero-agents
export DEEPSEEK_API_KEY=$(pi auth print-api-key --provider deepseek)
export LITELLM_PORT=${LITELLM_PORT:-4000}
export LITELLM_BASE_URL=http://127.0.0.1:$LITELLM_PORT
export LITELLM_API_KEY=sk-vero-local            # local proxy key only
export OPENAI_API_KEY=$LITELLM_API_KEY
# The system proxy's NO_PROXY uses globs (127.*) that curl/httpx/requests ignore: list the local proxy exactly.
# DeepSeek answers directly in ~0.1 s but is slow/flaky through the system proxy; Wikipedia needs the proxy.
case ",$NO_PROXY," in *,api.deepseek.com,*) ;; *) export NO_PROXY=127.0.0.1,localhost,api.deepseek.com${NO_PROXY:+,$NO_PROXY} ;; esac
export no_proxy=$NO_PROXY
# Target agents: no OpenAI trace upload (fails with 401 and stalls on the slow system proxy), and LiteLLM
# uses its bundled price table instead of fetching it from GitHub on every import.
export OPENAI_AGENTS_DISABLE_TRACING=1
# Plain-HTTP traffic (web_search_agent's http://en.wikipedia.org calls) goes through the local Wikipedia
# proxy (rag_eval.vero.wiki_proxy): pooled keep-alive connections + retries + a shared response cache.
# HTTPS keeps using the system proxy.
export SYSTEM_PROXY=${SYSTEM_PROXY:-http://127.0.0.1:7897}
export WIKI_PROXY_PORT=${WIKI_PROXY_PORT:-4010}
# Wikimedia throttles the agent's generic User-Agent as a group (429 on every request); the proxy sends a
# policy-compliant one instead (infrastructure only: the agent code and the content it gets are unchanged).
export WIKI_USER_AGENT=${WIKI_USER_AGENT:-"SNBenchmarkEval/0.1 (https://github.com/Xi0ng-Jun; research benchmark) python-httpx/0.28"}
export WIKI_RATE=${WIKI_RATE:-4}
export HTTP_PROXY=http://127.0.0.1:$WIKI_PROXY_PORT
export http_proxy=$HTTP_PROXY
export LITELLM_LOCAL_MODEL_COST_MAP=True
