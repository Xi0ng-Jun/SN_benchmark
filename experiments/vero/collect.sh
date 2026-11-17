#!/usr/bin/env bash
# Read-only evidence collection for the VeRO experiment. Starts, stops and changes nothing.
# Run from <BASE>/sn-benchmark:   bash collect.sh        -> prints the path of one redacted text file
set -u
source experiments/vero/env.sh || { echo "run this from <BASE>/sn-benchmark"; exit 2; }
[ -n "${VERO_STATE:-}" ] && [ -d "$VERO_STATE" ] || { echo "VERO_STATE is not set or missing"; exit 2; }
S=$VERO_STATE; PY=$VERO_PYTHON
OUT=$S/collect-$(date -u +%Y%m%dT%H%M%SZ).txt
sec() { printf '\n######## %s\n' "$*"; }
cli() { PYTHONPATH=src "$PY" -m rag_eval.vero.cli "$@" 2>&1; }
{
sec "0 environment"
echo "local: $(date '+%F %T %Z %z')   utc: $(date -u '+%F %T')"
git log --oneline -1; git status -sb | head -8
experiments/vero/services.sh status 2>&1
echo "-- processes"; pgrep -af "run_queue.sh|run_series.sh|rag_eval.vero.cli|run_gepa.py|claude -p" || echo "(none)"
echo "-- settings in effect (no secrets)"
env | grep -E '^(MAX_|GATE|RETRY_WAIT|RESUME_WAIT|SERIES_DEADLINE_H|QUEUE_DEADLINE_H|VERO_SAMPLE_CONCURRENCY|WIKI_RATE|TEST_K|V41_CONFIG|VERO_(TARGET|GRADER|OPTIMIZER)_MODEL)' | sort
echo "-- env.local.sh (values of KEY/BASE/PROXY/URL lines hidden)"
sed -E 's/^(\s*(export\s+)?[A-Za-z_]*(KEY|BASE|PROXY|URL|TOKEN|PASS)[A-Za-z_]*)=.*/\1=<hidden>/' experiments/vero/env.local.sh
echo "-- setup.json"
for t in retail simpleqa; do "$PY" - "$S/$t/setup.json" "$t" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
keep = {k: d.get(k) for k in ("preset", "splits", "models", "agent_timeout", "sample_timeout", "sample_concurrency",
                                "analyst_cmd", "implementer_cmd")}
print(sys.argv[2], json.dumps(keep, ensure_ascii=False))
PY
done
du -sh "$S" 2>/dev/null; df -h "$S" | tail -1

sec "1 queue"
cat "$S/queue.log" 2>&1 | tail -n 80
echo "-- series-status.json"; cat "$S/series-status.json" 2>&1
echo "-- report.md"; cat "$S/report.md" 2>&1
echo "-- archive"; ls -la "$S/archive" 2>&1 | tail -n 30

for t in retail simpleqa; do
  R=$S/$t/runs/run1
  sec "2 v41 $t run1"
  echo "-- series log (last 60)"; tail -n 60 "$S/v41-$t.log" 2>&1 | cut -c1-400
  echo "-- cli status"; cli status --run-dir "$R"
  echo "-- state.json digest"
  "$PY" - "$R/state.json" <<'PY'
import json, sys
try:
    st = json.load(open(sys.argv[1]))
except Exception as exc:
    print("no state:", exc); raise SystemExit
def short(v, n=110):
    s = v if isinstance(v, str) else json.dumps(v, ensure_ascii=False)
    return s if len(s) <= n else s[:n] + "…"
for k in ("halt_reason", "failure_kind", "failure_detail", "stop_reason", "elapsed_s", "run_failures", "no_adoption",
          "started_at", "updated_at"):
    print(f"{k}: {short(st.get(k), 400)}")
cal = st.get("calibration") or {}
print("calibration:", short({k: (v if not isinstance(v, dict) else {a: b for a, b in v.items() if not isinstance(b, (dict, list))})
                             for k, v in cal.items() if not isinstance(v, list)}, 900))
print("sessions:")
for s in st.get("sessions") or []:
    print("  ", short(s, 300))
print("lineage:")
for n in st.get("lineage") or []:
    print("  ", short({k: (v[:12] if k == "version" and isinstance(v, str) else v) for k, v in n.items()
                       if not isinstance(v, (dict, list))}, 400))
rounds = list(st.get("rounds") or []) + ([st["current_round"]] if st.get("current_round") else [])
for rd in rounds:
    dec = rd.get("decision") or {}
    print(f"round {rd.get('round')} parent {str(rd.get('parent'))[:12]} cards {len(rd.get('cards') or [])} "
          f"decision {short({k: v for k, v in dec.items() if not isinstance(v, (dict, list))}, 300)}")
    for ref, rec in (rd.get("records") or {}).items():
        keys = ("stage", "label", "delta_val", "se_val", "n_val", "dropped_val", "own_failures", "reason", "failure_kind",
                "attempt", "diff_lines", "started_at", "finished_at")
        flat = {k: rec.get(k) for k in keys if rec.get(k) is not None}
        flat["candidate"] = str(rec.get("candidate"))[:12]
        q, sg, tm = rec.get("qualification") or {}, rec.get("safeguards") or {}, rec.get("timings") or {}
        print("    ", ref, short(rec.get("title", ""), 70))
        print("        ", short(flat, 420))
        print("         qualified", q.get("qualified"), short(q.get("reasons"), 200), "| safeguards ok", sg.get("ok"),
              short(sg.get("breached"), 120), "| agent_s", round(tm.get("agent_s") or 0), "validation_s", round(tm.get("validation_s") or 0),
              "| remeasure", short({k: v for k, v in (rec.get("remeasure") or {}).items() if k in ("status", "passed", "delta_val")}, 120))
PY
  echo "-- run.log: agent / halt lines (last 80)"
  grep -nE "analys|implement|timeout|Agent request|halt|round [0-9]|adopt|ERROR|WARNING|Traceback" "$R/run.log" 2>/dev/null | tail -n 80 | cut -c1-300
  echo "-- run.log: last 25 lines"; tail -n 25 "$R/run.log" 2>&1 | cut -c1-300
  echo "-- analyses (prompt written -> cards written = analyst duration)"
  for a in "$R"/agents/analysis-*; do
    [ -d "$a" ] || continue
    n=$(ls "$a/cases" 2>/dev/null | wc -l); kb=$(du -sk "$a/cases" 2>/dev/null | cut -f1)
    echo "$(basename "$a"): cases=$n cases_kb=${kb:-0} prompt_bytes=$(stat -c %s "$a/analyst.prompt.md" 2>/dev/null)" \
         "prompt_at=$(stat -c %y "$a/analyst.prompt.md" 2>/dev/null | cut -c1-19)" \
         "log_at=$(stat -c %y "$a/analyst.log" 2>/dev/null | cut -c1-19) log_bytes=$(stat -c %s "$a/analyst.log" 2>/dev/null)" \
         "cards_at=$(stat -c %y "$a/cards.json" 2>/dev/null | cut -c1-19)"
    echo "   analyst.log tail:"; tail -c 1500 "$a/analyst.log" 2>/dev/null | sed 's/^/   | /'
  done
  echo "-- implementer logs (name, bytes, prompt time -> log time)"
  for f in "$R"/agents/implement/*.log; do
    [ -f "$f" ] || continue
    echo "$(basename "$f") bytes=$(stat -c %s "$f") prompt_at=$(stat -c %y "${f%.log}.prompt.md" 2>/dev/null | cut -c12-19) log_at=$(stat -c %y "$f" | cut -c12-19)"
  done
  echo "-- executions"; ls "$R/evals" 2>/dev/null | wc -l
done

sec "3 Claude Code transcripts of the analyst / implementer calls"
CL=${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects
"$PY" - "$CL" <<'PY'
import glob, json, os, sys
root = sys.argv[1]
dirs = sorted((d for d in glob.glob(os.path.join(root, "*")) if "worktrees" in d and ("analyst-" in d or "vero-state" in d)),
              key=os.path.getmtime)
print(f"{len(dirs)} matching project dir(s) under {root}")
def load(path):
    rows = []
    for line in open(path, errors="replace"):
        try:
            rows.append(json.loads(line))
        except Exception:
            pass
    return rows
for d in dirs[-80:]:
    for path in sorted(glob.glob(os.path.join(d, "*.jsonl")), key=os.path.getmtime):
        rows = load(path)
        stamps = [r.get("timestamp") for r in rows if r.get("timestamp")]
        tools, texts, errors, usage = [], [], [], 0
        for r in rows:
            msg = r.get("message") or {}
            if r.get("isApiErrorMessage"):
                errors.append(str(msg.get("content"))[:200])
            content = msg.get("content")
            if r.get("type") == "assistant" and isinstance(content, list):
                u = msg.get("usage") or {}
                usage = max(usage, (u.get("input_tokens") or 0) + (u.get("cache_read_input_tokens") or 0)
                            + (u.get("cache_creation_input_tokens") or 0))
                for c in content:
                    if c.get("type") == "tool_use":
                        tools.append((r.get("timestamp", "")[11:19], c.get("name"),
                                      json.dumps(c.get("input"), ensure_ascii=False)[:140]))
                    elif c.get("type") == "text" and c.get("text", "").strip():
                        texts.append((r.get("timestamp", "")[11:19], c["text"].strip()[:200].replace("\n", " ")))
        name = os.path.basename(d)[-60:]
        print(f"\n{name}  {os.path.basename(path)[:8]}  lines={len(rows)} {stamps[0][:19] if stamps else '?'} -> "
              f"{stamps[-1][11:19] if stamps else '?'}  tool_calls={len(tools)} max_context_tokens={usage} api_errors={len(errors)}")
        detail = "analyst-r" in name            # full tail for analyst calls, one line for the rest
        if detail:
            for t in tools[-15:]:
                print("    tool", *t)
            for t in texts[-3:]:
                print("    text", *t)
        for e in errors[-3:]:
            print("    api_error", e)
PY

sec "4 GEPA"
for g in tau-bench simpleqa; do
  for d in "$S"/gepa/$g/*; do
    [ -d "$d" ] || continue
    echo "== $g $(basename "$d")"; cat "$d/summary.json" "$d/invalid.json" 2>/dev/null
    grep -nE "Task completed with metrics|Initial test score" "$d/gepa.log" 2>/dev/null | cut -c1-260 | sed -n '1,12p;$p'
    echo "   evaluations: $(grep -c 'Task completed with metrics' "$d/gepa.log" 2>/dev/null)" \
         "with errors: $(grep 'Task completed with metrics' "$d/gepa.log" 2>/dev/null | grep -vc "'num_errors': 0")"
    grep -niE "stopp|threshold|metric calls|budget|best score|Iteration [0-9]+: (New|Best)" "$d/gepa.log" 2>/dev/null | tail -n 12 | cut -c1-260
  done
done
echo "-- gepa-retail.log (last 20)"; tail -n 20 "$S/gepa-retail.log" 2>&1 | cut -c1-300

sec "5 services"
echo "wiki stats: $(grep stats "$S/wiki-proxy.log" 2>/dev/null | tail -n 1)"
echo "wiki 429: $(grep -c '429 from upstream' "$S/wiki-proxy.log" 2>/dev/null)"
echo "litellm.log: $(wc -l < "$S/litellm.log" 2>/dev/null) lines, ERROR blocks: $(grep -c 'LiteLLM Proxy:ERROR' "$S/litellm.log" 2>/dev/null)," \
     "500: $(grep -c 'Error code: 500' "$S/litellm.log" 2>/dev/null), channel limit: $(grep -c 'Channel concurrency limit' "$S/litellm.log" 2>/dev/null)"
echo "-- line format of the ERROR blocks (first 70 characters only)"
grep -m3 'LiteLLM Proxy:ERROR' "$S/litellm.log" 2>/dev/null | cut -c1-70
grep 'LiteLLM Proxy:ERROR' "$S/litellm.log" 2>/dev/null | tail -n 2 | cut -c1-70
echo "-- gate lines of the last night"; grep -h "^gate " "$S"/v41-*.log "$S"/gepa-*.log 2>/dev/null | tail -n 30
} 2>&1 | sed -E -e 's#https?://[^][ "<>),'"'"']*#<url>#g' -e 's#sk-[A-Za-z0-9_-]{8,}#<key>#g' \
               -e "s#$HOME#~#g" -e "s#$(whoami)#<USER>#g" -e "s#$(hostname)#<HOST>#g" > "$OUT"
echo "written: $OUT ($(wc -l < "$OUT") lines, $(du -h "$OUT" | cut -f1))"
