# source experiments/vero/env.sh  — environment of the VeRO experiment (LiteLLM proxy, Wikipedia proxy, paths).
# Machine-specific values (paths, model endpoint, keys) come from experiments/vero/env.local.sh, which is
# not tracked: copy env.local.example.sh. Nothing secret is written to a tracked or generated file.
_VERO_HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
_VERO_REPO="$(cd "$_VERO_HERE/../.." && pwd)"
_VERO_LOCAL=${VERO_ENV_LOCAL:-$_VERO_HERE/env.local.sh}
if [ -f "$_VERO_LOCAL" ]; then source "$_VERO_LOCAL"; fi
# baseline.sh compares target models: its choice wins over env.local.sh for the processes it starts.
if [ -n "${VERO_TARGET_MODEL_OVERRIDE:-}" ]; then export VERO_TARGET_MODEL=$VERO_TARGET_MODEL_OVERRIDE; fi

# -- paths ------------------------------------------------------------------------------------------
export VERO_ROOT=${VERO_ROOT:?set VERO_ROOT (VeRO checkout at tag paper-v1) in experiments/vero/env.local.sh}
export VERO_AGENTS_PATH=$VERO_ROOT/vero-agents
export VERO_STATE=${VERO_STATE:-$_VERO_REPO/.vero-state}
export VERO_HOME_DIR=${VERO_HOME_DIR:-$VERO_STATE/vero-home}   # VeRO's own state (GEPA sessions), not ~/.vero
if [ -z "${VERO_PYTHON:-}" ]; then          # python that runs rag_eval (this repository's environment)
  for _p in "$_VERO_REPO/.venv/bin/python" "$_VERO_REPO/../../.venv/bin/python"; do
    if [ -x "$_p" ]; then VERO_PYTHON=$_p; break; fi
  done
fi
export VERO_PYTHON=${VERO_PYTHON:?set VERO_PYTHON (python with the dependencies of this repository) in env.local.sh}

# -- models -----------------------------------------------------------------------------------------
# VeRO paper-v1 hard-codes model names; the local LiteLLM proxy serves each of them with an upstream
# model (LiteLLM model string, e.g. openai/<name> for an OpenAI-compatible endpoint):
#   target     gpt-4.1-mini-2025-04-14 (web_search_agent), gpt-4.1-mini (tau-bench agent and simulated user)
#   grader     gpt-4.1-2025-04-14 (SimpleQA grader)
#   optimizer  GEPA's reflection model; use the model behind the analyst / implementer agent command
export VERO_TARGET_MODEL=${VERO_TARGET_MODEL:?set VERO_TARGET_MODEL in env.local.sh}
export VERO_TARGET_API_BASE=${VERO_TARGET_API_BASE:?set VERO_TARGET_API_BASE in env.local.sh}
export VERO_TARGET_API_KEY=${VERO_TARGET_API_KEY:?set VERO_TARGET_API_KEY in env.local.sh}
for _role in GRADER OPTIMIZER; do            # default: the target's model, endpoint and key
  for _f in MODEL API_BASE API_KEY; do
    _v=VERO_${_role}_$_f; _t=VERO_TARGET_$_f
    if [ -z "${!_v:-}" ] && [ -n "${!_t:-}" ]; then export "$_v=${!_t}"; fi
  done
done
# Concurrency is bounded on the client side (the LiteLLM proxy rejects, not queues, above a cap). One series
# sends at most VERO_SAMPLE_CONCURRENCY x 3 model requests at once: v4.1 runs up to 3 evaluations of
# VERO_SAMPLE_CONCURRENCY samples (cli init --sample-concurrency), GEPA one evaluation of 3 x that many
# (VeRO's default is 100). Keep (number of series) x 3 x VERO_SAMPLE_CONCURRENCY below the endpoint's limit.
export VERO_SAMPLE_CONCURRENCY=${VERO_SAMPLE_CONCURRENCY:-6}
export VERO_GEPA_MAX_CONCURRENCY=${VERO_GEPA_MAX_CONCURRENCY:-$((3 * VERO_SAMPLE_CONCURRENCY))}
export LITELLM_PORT=${LITELLM_PORT:-4000}
export LITELLM_BASE_URL=http://127.0.0.1:$LITELLM_PORT
export LITELLM_API_KEY=sk-vero-local            # key of the local proxy only
export OPENAI_API_KEY=$LITELLM_API_KEY
export OPENAI_AGENTS_DISABLE_TRACING=1          # no trace upload to OpenAI from the target agents
export LITELLM_LOCAL_MODEL_COST_MAP=True        # bundled price table, no download on import

# -- network ----------------------------------------------------------------------------------------
# UPSTREAM_PROXY: proxy this machine needs to reach Wikipedia ("" = direct). Default: the shell's HTTPS proxy.
export UPSTREAM_PROXY=${UPSTREAM_PROXY-${https_proxy:-${HTTPS_PROXY:-}}}
# The local proxies and the model endpoints are reached directly. Exact host names: curl, httpx and
# requests ignore glob entries such as 127.*.
_hosts=127.0.0.1,localhost
for _u in "$VERO_TARGET_API_BASE" "$VERO_GRADER_API_BASE" "$VERO_OPTIMIZER_API_BASE"; do
  _h=$(printf %s "$_u" | sed -E 's#^[a-z]+://##; s#[:/].*$##')
  case ",$_hosts," in *",$_h,"*) ;; *) _hosts=$_hosts,$_h ;; esac
done
case ",${NO_PROXY:-}," in ",$_hosts,"*) ;; *) export NO_PROXY=$_hosts${NO_PROXY:+,$NO_PROXY} ;; esac
export no_proxy=$NO_PROXY
# Plain-HTTP traffic (web_search_agent calls http://en.wikipedia.org) goes through the local Wikipedia
# proxy (rag_eval.vero.wiki_proxy): one paced connection pool, retries, a response cache and a
# policy-compliant User-Agent (Wikimedia throttles the agent library's generic one). HTTPS is unchanged.
export WIKI_PROXY_PORT=${WIKI_PROXY_PORT:-4010}
export WIKI_USER_AGENT=${WIKI_USER_AGENT:-"SNBenchmarkEval/0.1 (research benchmark) python-httpx/0.28"}
export WIKI_RATE=${WIKI_RATE:-4}
export HTTP_PROXY=http://127.0.0.1:$WIKI_PROXY_PORT
export http_proxy=$HTTP_PROXY
unset _p _role _f _v _t _u _h _hosts
