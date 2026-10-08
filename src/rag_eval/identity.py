"""Canonical identities shared by benchmark artifacts and model configurations."""
import hashlib
import json


def fingerprint(value) -> str:
    content = json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False)
    return hashlib.sha256(content.encode()).hexdigest()

def require_text(value, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be nonempty text")
    return value
