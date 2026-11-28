# Manifest

- `start`: the solving agent's start command. In this stub it is a script that calls no model and writes answer.json according to `settings.json`. It came with the general agent; without it nothing runs.
- `settings.json`: which answer `start` writes (`correct`, `wrong`, `none`, `crash`, `break_scorer`, `unreachable`, `hang`). The general agent as-is writes `wrong`. `forge_settings` makes `start` try to replace the researcher's solver settings in its output; `unreachable_once` makes the first run on each task an invalid one.
