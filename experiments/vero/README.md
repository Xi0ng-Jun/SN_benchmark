# method-v4.1 and GEPA on VeRO-Bench (paper-v1): SimpleQA and tau-bench Retail

Everything machine-specific lives in `experiments/vero/env.local.sh` (not tracked). State and logs go to
`$VERO_STATE` (default `.vero-state/`). Nothing here uses the SN workbench.

## Setup on a new machine

1. VeRO at the paper tag, plus the GEPA patch (paper-v1's `gepa.py` does not run as tagged):

       git clone --depth 1 --branch paper-v1 <VeRO repository> $VERO_ROOT
       git -C $VERO_ROOT apply <this repo>/experiments/vero/patches/gepa-paper-v1.patch

2. Environments (needs `uv`):

       (cd $VERO_ROOT/vero-agents/agents/web_search_agent && uv sync)
       (cd $VERO_ROOT/vero-agents/agents/tau-bench && uv sync)
       (cd $VERO_ROOT/vero-benchmarking && uv sync --extra gepa)
       uv tool install 'litellm[proxy]'

3. Datasets: copy `vero-benchmarking/datasets/{simple_qa_verified_wiki_unanswered,tau_bench_retail}` from the
   machine that built them into the same place under `$VERO_ROOT` (164 KB; identical splits on every machine).

4. `cp experiments/vero/env.local.example.sh experiments/vero/env.local.sh` and fill in the paths, the model
   endpoint and the key. `VERO_OPTIMIZER_MODEL` must be the model behind the analyst / implementer command.

5. Services and checks. The agent command is checked for what the method needs: reading a file outside its
   working directory and editing a file inside it.

       experiments/vero/services.sh start
       experiments/vero/preflight.sh "<analyst/implementer command>"

6. One state root per task (binds the task to the models of the current environment):

       source experiments/vero/env.sh
       for t in simpleqa:simple_qa:web_search_agent retail:retail:tau-bench; do
         IFS=: read name preset agent <<< "$t"
         PYTHONPATH=src $VERO_PYTHON -m rag_eval.vero.cli init --state-root $VERO_STATE/$name --preset $preset \
           --vero-root $VERO_ROOT --agent-python $VERO_ROOT/vero-agents/agents/$agent/.venv/bin/python \
           --analyst-cmd "<command>" --implementer-cmd "<command>" --sample-timeout 180 --sample-concurrency 8
       done

7. Seed baseline on the test split (also the end-to-end smoke test; add `--limit 3` first):

       PYTHONPATH=src $VERO_PYTHON -m rag_eval.vero.cli eval --state-root $VERO_STATE/retail --split test

## Runs

    setsid nohup experiments/vero/run_series.sh v41  retail    run1 run2 run3 > $VERO_STATE/v41-retail.log    2>&1 &
    setsid nohup experiments/vero/run_series.sh v41  simpleqa  run1 run2 run3 > $VERO_STATE/v41-simpleqa.log  2>&1 &
    setsid nohup experiments/vero/run_series.sh gepa tau-bench run1 run2 run3 > $VERO_STATE/gepa-retail.log   2>&1 &
    setsid nohup experiments/vero/run_series.sh gepa simpleqa  run1 run2 run3 > $VERO_STATE/gepa-simpleqa.log 2>&1 &

A v4.1 run that halts with `process_failure` (model service or agent outage) is resumed after `RESUME_WAIT`
seconds (default 600), at most `MAX_RESUMES` times (default 3); then the series stops instead of starting the
next run. Finished runs are skipped, so the same command continues a stopped series. `TEST_K` sets the
repetitions of the test endpoint (default 1). GEPA has no resume: a failed run restarts from scratch and its
logs move to `<run>.failed-<time>`.

Progress: `PYTHONPATH=src $VERO_PYTHON -m rag_eval.vero.cli status --run-dir $VERO_STATE/<task>/runs/<run>`;
results: `<run dir>/endpoint.json` (v4.1) and `$VERO_STATE/gepa/<task>/<run>/summary.json` (GEPA).

Stop: SIGINT to the `rag_eval.vero.cli run` process pauses (resumable); SIGTERM ends the run for good
(`stopped`). Kill by PID, never `pkill -f` (it matches the calling shell).

## Deviations from the paper to report

- Target, grader (paper: GPT-4.1) and optimizer models are the ones in `env.local.sh`.
- Budget: 8 x (train + validation) sample executions for both methods; v4.1 counts retries of unscored samples.
- Wikipedia requests go through a local pacing/caching proxy with its own User-Agent (same for both methods).
- Retail's validation split has 20 tasks (method-v4.1 asks for at least 40).
