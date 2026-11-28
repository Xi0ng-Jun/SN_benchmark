# ALW scenario

AnalogLayoutWorld as a scenario for `harness_builder` (interface: `docs/scenario-interface.md`).

| File | What it is |
|---|---|
| `facts.json` | the researcher's facts: practice and exam tasks, groups, limits, solver settings, exam settings, programs and image on this site |
| `prepare.py` | writes `site.json`, `scenario.json` and `tasks/` from `facts.json` and the benchmark checkout (none of the three is in git) |
| `run.py` | run command: one Harbor trial, the harness's `start` in place of Claude Code |
| `score.py` | score command: `alw verify` on `<product>/submission` |
| `enter.py` | enter command: a command in the task's container, no agent, no network |
| `invalid.py` | invalid-run condition |
| `container/` | mounted into the task container by `run.py`: the entry that runs `start`, and the launcher that applies the fixed solver settings |
| `harness/` | the starting harness: Claude Code as it is |
| `materials/` | what the researcher tells the developer agent about the operations |

Needs on the machine: the benchmark checkout, Docker, `harbor`, `alw`, the agent image
named in `facts.json`.

Known limit: fixed solver settings are applied by `container/claude`, which `start` is told
to use. A `start` that launched the product another way would not get them.
