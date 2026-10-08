"""Local JSONL readers; benchmark-specific adaptation lives in notebook_data."""
import json
from pathlib import Path


def iter_jsonl(path):
    with Path(path).open(encoding='utf-8') as handle:
        for number, line in enumerate(handle, 1):
            if not line.strip():
                continue
            try:
                value = json.loads(line)
            except json.JSONDecodeError:
                raise ValueError(f'invalid JSONL at line {number}') from None
            if not isinstance(value, dict):
                raise ValueError(f'expected object at line {number}')
            yield value

def load_jsonl(path):
    return list(iter_jsonl(path))
