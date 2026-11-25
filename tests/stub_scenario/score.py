"""Stub score command: compare HB_PRODUCT/answer.json with the task's fields one by one.

Writes HB_OUT/score.json = {"score": <share of fields right>, "fields": {name: right}}.
A product containing {"break_scorer": true} makes it fail on purpose (exit 2).
"""
import json
import os
import sys

task, product, out = os.environ["HB_TASK"], os.environ["HB_PRODUCT"], os.environ["HB_OUT"]
with open(os.path.join("tasks", task, "task.json")) as f:
    fields = json.load(f)["fields"]
try:
    with open(os.path.join(product, "answer.json")) as f:
        answer = json.load(f)
except (OSError, ValueError):
    answer = None
if not isinstance(answer, dict):
    answer = {}
if answer.get("break_scorer") is True:
    print("score: failing on purpose", file=sys.stderr)
    sys.exit(2)
right = {k: k in answer and json.dumps(answer[k]) == json.dumps(v) for k, v in fields.items()}
result = {"score": sum(right.values()) / len(fields), "fields": right}
with open(os.path.join(out, "score.json"), "w") as f:
    json.dump(result, f)
print("score: %s" % json.dumps(result))
