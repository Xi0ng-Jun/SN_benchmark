# method-v4.1 and GEPA on VeRO-Bench (paper-v1): SimpleQA and tau-bench Retail

Two tasks x two methods x three runs, same sample-execution budget, one test evaluation per run.
Everything machine-specific lives in `experiments/vero/env.local.sh` (not tracked). State and logs go to
`$VERO_STATE`. Nothing here uses the SN workbench.

## Setup on a new machine

Needs `git`, `uv`, `curl`, and access to GitHub, PyPI and Wikipedia.

1. `cp experiments/vero/env.local.example.sh experiments/vero/env.local.sh` and fill in `VERO_ROOT`,
   `VERO_STATE`, the model endpoint, the key and the model names. The key stays in this untracked file (or
   a command that prints it).
   - `VERO_TARGET_MODEL`: the agent being optimised. Chosen in step 4; any tool-calling model for now.
   - `VERO_GRADER_MODEL`: SimpleQA grader; a strong model, the same for every run.
   - `VERO_OPTIMIZER_MODEL`: GEPA's reflection model = the model behind the analyst / implementer command.
   - `VERO_STATE`: outside every git repository and every directory tree with a `CLAUDE.md` / `AGENTS.md`.

2. One-time setup (VeRO at `paper-v1` + GEPA patch, the three environments, datasets, LiteLLM, `.venv`):

       experiments/vero/setup_vero.sh

3. Services and checks. The agent command is run exactly as a run does; it must read a case file outside
   its working directory (`{case_dir}`) and edit a file inside it.

       experiments/vero/services.sh start
       experiments/vero/preflight.sh "claude -p --add-dir {case_dir} --permission-mode acceptEdits"

   The prompt is appended as the last argument, so the command must not end with an option that takes
   several values. A warning on the Wikipedia burst probe means throttling: lower `WIKI_RATE` (default 4).

4. Target model. Seed (unoptimised agent) on the test split of both tasks, once per candidate model:

       experiments/vero/baseline.sh openai/<model-a> openai/<model-b>      # LIMIT=3 first as a smoke test

   Pick a model whose seed leaves room (about 0.35 to 0.75 on both tasks, answered rate near 1), put it
   into `env.local.sh` and run `experiments/vero/services.sh start` again (it applies the new mapping;
   `services.sh status` fails while the proxy serves another mapping).

5. State roots of the two tasks (bound to the models of the current environment):

       experiments/vero/init_tasks.sh "claude -p --add-dir {case_dir} --permission-mode acceptEdits"

## Runs

    source experiments/vero/env.sh
    setsid nohup experiments/vero/run_series.sh v41  retail    run1 run2 run3 > $VERO_STATE/v41-retail.log    2>&1 &
    setsid nohup experiments/vero/run_series.sh gepa tau-bench run1 run2 run3 > $VERO_STATE/gepa-retail.log   2>&1 &
    setsid nohup experiments/vero/run_series.sh v41  simpleqa  run1 run2 run3 > $VERO_STATE/v41-simpleqa.log  2>&1 &
    setsid nohup experiments/vero/run_series.sh gepa simpleqa  run1 run2 run3 > $VERO_STATE/gepa-simpleqa.log 2>&1 &

The PID printed by the shell is the wrapper's: check `pgrep -af "rag_eval.vero.cli|run_gepa.py"`.

- One series sends at most `3 x VERO_SAMPLE_CONCURRENCY` (default 3 x 6 = 18) model requests at once, for
  both methods. Four series: 72. Keep the total below the endpoint's limit; the proxy does not queue.
- v4.1: a `process_failure` halt is resumed after `RESUME_WAIT` seconds (default 600), at most
  `MAX_RESUMES` times (default 3). If the method itself is stuck (no new card, or no card reaches
  measurement) the run ends there and is tested; an outage, a crash or a stop ends the series, and later
  runs are not started. Finished runs are skipped, so the same command continues a stopped series.
- GEPA has no resume: a failed run restarts from scratch and its logs move to `<run>.failed-<time>`.

Progress and results:

    PYTHONPATH=src $VERO_PYTHON -m rag_eval.vero.cli status --run-dir $VERO_STATE/<task>/runs/<run>
    $VERO_PYTHON experiments/vero/summarize.py          # the result tables (finished runs only), markdown

Stop: SIGINT to the `rag_eval.vero.cli run` process pauses (resumable); SIGTERM ends the run for good
(`stopped`). Stop the `run_series.sh` wrapper first. Kill by PID, never `pkill -f` (it matches the caller).

## Method parameters (`v41-simpleqa.json`, `v41-retail.json`)

| | SimpleQA | Retail | why |
|---|---|---|---|
| budget (sample executions on train + validation) | 728 | 960 | 8 x (train + validation), GEPA's rule |
| `calibration_k` | 2 | 2 | seed A/A on validation: the smallest that yields a rerun variance |
| `b` = `cards_per_analysis` | 2 | 4 | one candidate costs a validation pass (45 / 20): about 5 / 9 rounds |
| `diagnose_size` | 0 (all 46) | 30 | train cases the analyst's evidence is drawn from |
| `smoke`, `minibatch`, `ablation` | off | off | each spends budgeted executions; validation is small |
| `review`, speculative analysis / prefetch | off | off | no reviewer agent; speculation spends budget on unused work |
| `safeguards.tokens_per_task_ratio` | +100% | +100% | GEPA has no cost limit; the default +50% would reject multi-step fixes |
| `safeguards.answer_produced_rate` | -2 points | -2 points | default |

Everything else is the method default (adoption threshold c = 1 SE, re-measurement before adoption, halt
after 3 consecutive run failures). The time budget (24 h) is not meant to bind; the execution budget is.

## Deviations from the paper to report

- Target, grader (paper: GPT-4.1) and optimizer models are the ones in `env.local.sh`.
- Budget: 8 x (train + validation) sample executions for both methods; v4.1 counts retries of unscored samples.
- GEPA runs with `patches/gepa-paper-v1.patch` (the tagged `gepa.py` does not run) and at most
  `VERO_GEPA_MAX_CONCURRENCY` samples at once instead of 100.
- Wikipedia requests go through a local pacing/caching proxy with its own User-Agent (same for both methods).
- Retail's validation split has 20 tasks (method-v4.1 asks for at least 40).
