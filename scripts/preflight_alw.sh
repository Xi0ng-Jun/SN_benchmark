#!/usr/bin/env bash
# Read-only preflight of an ALW evaluation host (docs/alw-progressive-plan.md Phase 0.1,
# docs/fable-5.2-plan-review.md A3-A5).
#
#   bash scripts/preflight_alw.sh > preflight-$(date -u +%Y%m%dT%H%M%SZ).txt 2>&1
#
# Changes nothing on the host. Prints no credential, no host name of a proxy or gateway and no
# variable value from the proxy / Claude / Anthropic / AWS families: only names and set/unset.
# The gateway section runs only when SSAP_GATEWAY, SSAP_UPSTREAM_KEY and SSAP_MODEL are set; it
# sends one 8-token request per auth style and prints the HTTP status, the latency and the
# response "model" field (to detect a silent model swap behind the gateway).
set -u
sec() { printf '\n===== %s\n' "$1"; }
have() { if command -v "$1" >/dev/null 2>&1; then echo "$1: found"; else echo "$1: MISSING"; fi; }

sec "machine"
uname -srm
echo "cpus (nproc): $(nproc 2>/dev/null)"
grep -m1 'model name' /proc/cpuinfo 2>/dev/null | sed 's/^model name[[:space:]]*:/cpu model:/'
free -g 2>/dev/null | sed -n '1,3p'
swap_total=$(awk '/^SwapTotal:/ {print $2}' /proc/meminfo 2>/dev/null)
avail_kb=$(awk '/^MemAvailable:/ {print $2}' /proc/meminfo 2>/dev/null)
echo "swap total kB: ${swap_total:-unknown}$([ "${swap_total:-1}" = 0 ] && echo '  (no swap: exceeding RAM means OOM exit 137)')"
if [ -n "${avail_kb:-}" ]; then
  avail_gib=$((avail_kb / 1048576)); cpus=$(nproc 2>/dev/null || echo 0)
  n_mem=$((avail_gib / 2 / 32)); n_cpu=$((cpus / 2 / 6))
  n=$(( n_mem < n_cpu ? n_mem : n_cpu ))
  echo "available GiB: $avail_gib; trials of 6 CPU / 32 GiB within half of the free resources: N <= $n (plan: N=4 default, N=6 ceiling, never 8)"
fi
uptime 2>/dev/null | sed 's/.*\(load average\)/\1/'

sec "disk (filesystem of each location)"
for d in / "$HOME" /localdata "${SSAP_STATE:-}" "${ALW_REPO:-}"; do
  [ -n "$d" ] && [ -e "$d" ] && df -h --output=size,used,avail,pcent,target "$d" 2>/dev/null | tail -1
done
echo "SSAP_STATE: $([ -n "${SSAP_STATE:-}" ] && echo set || echo unset); ALW_REPO: $([ -n "${ALW_REPO:-}" ] && echo set || echo unset)"

sec "tools"
for t in git curl python3 python3.12 python3.13 uv node npm docker harbor alw claude; do have "$t"; done
python3.12 --version 2>&1 | head -1
python3 --version 2>&1 | head -1

sec "docker"
docker_cmd=""
if docker info >/dev/null 2>&1; then docker_cmd="docker"; echo "docker access: direct"
elif sudo -n docker info >/dev/null 2>&1; then docker_cmd="sudo -n docker"; echo "docker access: passwordless sudo"
else echo "docker access: NONE (daemon stopped, not installed, or needs a password)"; fi
if [ -n "$docker_cmd" ]; then
  $docker_cmd info --format 'server {{.ServerVersion}}; cpus {{.NCPU}}; mem {{.MemTotal}}; cgroup {{.CgroupVersion}}; driver {{.Driver}}' 2>&1
  if $docker_cmd buildx version >/dev/null 2>&1; then
    $docker_cmd buildx version 2>&1 | head -1
  else
    echo "docker buildx: MISSING (install docker-buildx / docker-buildx-plugin; required for BuildKit --secret & Harbor compose build)"
  fi
  $docker_cmd compose version 2>&1 | head -1
  root=$($docker_cmd info --format '{{.DockerRootDir}}' 2>/dev/null)
  [ -n "$root" ] && df -h --output=size,used,avail,pcent,target "$root" 2>/dev/null | tail -1
  echo "images: $($docker_cmd images -q 2>/dev/null | wc -l); running containers: $($docker_cmd ps -q 2>/dev/null | wc -l)"
  $docker_cmd images --format '{{.Repository}}:{{.Tag}} {{.Size}}' 2>/dev/null | grep -i -E 'analog|alw|eda' | head -5
  $docker_cmd info --format '{{.HTTPProxy}}{{.HTTPSProxy}}' 2>/dev/null | grep -q . \
    && echo "dockerd proxy: configured" || echo "dockerd proxy: not configured"
fi
for p in /etc/docker/daemon.json /etc/systemd/system/docker.service.d/proxy.conf /etc/systemd/system/containerd.service.d/proxy.conf; do
  if [ -e "$p" ]; then
    echo "daemon proxy file $p: present (mode $(stat -c '%a' "$p" 2>/dev/null || echo unknown))"
  else
    echo "daemon proxy file $p: absent (note: Ubuntu docker.io pulls use both dockerd & containerd; escape % -> %% in systemd Environment=)"
  fi
done
ip -brief link show docker0 >/dev/null 2>&1 && echo "docker0: present" || echo "docker0: not found"

sec "network from the host (HTTP status; 000 = unreachable)"
for u in https://ghcr.io/v2/ https://registry.npmjs.org/ https://pypi.org/simple/harbor/ \
         https://github.com/ https://downloads.claude.ai/claude-code-releases/bootstrap.sh; do
  printf '%s %s\n' "$(curl -s -o /dev/null -m 15 -w '%{http_code}' "$u")" "$u"
done

sec "environment variable names (values never printed)"
names() { env | cut -d= -f1 | grep -E "$1" | sort | tr '\n' ' '; echo; }
echo "proxy: $(names '^(https?|no|all|HTTPS?|NO|ALL)_(proxy|PROXY)$')"
for v in http_proxy https_proxy HTTP_PROXY HTTPS_PROXY all_proxy ALL_PROXY; do
  val="${!v:-}"
  if [ -n "$val" ]; then
    rest="${val#*://}"
    cred=$([[ "$val" == *"://"* && "${rest%%/*}" == *"@"* ]] && echo "embeds credentials" || echo "no credentials")
    pct=$([[ "$val" == *"%"* || "$val" == *"$"* ]] && echo ", contains % or $ (must quote & escape % -> %% in systemd proxy.conf)" || echo "")
    echo "$v: set, $cred$pct"
  fi
done
echo "anthropic/claude: $(names '^(ANTHROPIC_|CLAUDE_)')"
echo "aws / thinking / caching: $(names '^(AWS_|MAX_THINKING_TOKENS$|DISABLE_PROMPT_CACHING$|DISABLE_NON_ESSENTIAL_MODEL_CALLS$|API_TIMEOUT_MS$)')"
echo "node tls / proxy: $(names '^NODE_(TLS_REJECT_UNAUTHORIZED|USE_ENV_PROXY|EXTRA_CA_CERTS)$')"
echo "ssap: $(names '^SSAP_')"
command -v claude >/dev/null 2>&1 && echo "claude version: $(claude --version 2>/dev/null | head -1)"

sec "model gateway (Anthropic Messages), only if SSAP_GATEWAY / SSAP_UPSTREAM_KEY / SSAP_MODEL are set"
if [ -n "${SSAP_GATEWAY:-}" ] && [ -n "${SSAP_UPSTREAM_KEY:-}" ] && [ -n "${SSAP_MODEL:-}" ]; then
  echo "requested model: $SSAP_MODEL"
  body=$(printf '{"model":"%s","max_tokens":8,"messages":[{"role":"user","content":"Reply with OK"}]}' "$SSAP_MODEL")
  tmp=$(mktemp); trap 'rm -f "$tmp"' EXIT
  for style in x-api-key bearer; do
    if [ "$style" = x-api-key ]; then h="x-api-key: $SSAP_UPSTREAM_KEY"; else h="Authorization: Bearer $SSAP_UPSTREAM_KEY"; fi
    code=$(curl -s -o "$tmp" -m 120 -w '%{http_code} ttfb=%{time_starttransfer}s total=%{time_total}s' \
      -X POST "${SSAP_GATEWAY%/}/v1/messages" -H "$h" -H 'anthropic-version: 2023-06-01' \
      -H 'content-type: application/json' -d "$body")
    served=$(python3 -c 'import json,sys
try: print(json.load(open(sys.argv[1])).get("model"))
except Exception: print("<no JSON model field>")' "$tmp" 2>/dev/null)
    swap=""; [ "$served" != "$SSAP_MODEL" ] && swap="  <-- differs from the requested model"
    echo "auth $style -> HTTP $code; response model: $served$swap"
  done
else
  echo "skipped"
fi
sec "done"
