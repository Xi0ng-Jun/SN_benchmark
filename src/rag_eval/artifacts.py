import hashlib
import json
import os
from pathlib import Path


# json.dumps(ensure_ascii=False) leaves these raw, but str.splitlines() treats
# them as line breaks, so many JSONL readers would split one row in two. The
# escaped form decodes to the same value and only appears inside strings.
_LINE_BREAK_ESCAPES = str.maketrans({'\x85': '\\u0085', '\u2028': '\\u2028', '\u2029': '\\u2029'})


def jsonl_line(row):
    """One JSONL row without a trailing newline; parsed value is unchanged."""
    return json.dumps(row, ensure_ascii=False, allow_nan=False).translate(_LINE_BREAK_ESCAPES)


def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def save_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as f:
        os.chmod(temporary, 0o600)
        json.dump(value, f, ensure_ascii=False, indent=2, allow_nan=False)
        f.write('\n')
    temporary.replace(path)


def save_jsonl(path, rows):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8') as f:
        os.chmod(temporary, 0o600)
        for row in rows:
            f.write(jsonl_line(row) + '\n')
    temporary.replace(path)
