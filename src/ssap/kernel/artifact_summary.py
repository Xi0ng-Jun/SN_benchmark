"""Per-trial ``artifact_summary.json`` / ``artifact_summary.md`` (review Item 21).

The summary is the authority on whether a candidate touched the graded artifact: a run can make
the same model requests as the baseline (``model_request_diverged = False``) while a post-step
hook still rewrites the canonical artifact (``submission_artifact_diverged = True``).

``phase_a_reused`` = Phase A output was taken from an archived snapshot (offline Path 1);
``phase_b_invoked`` = a repair sub-invocation was started. All string values are scrubbed of
``scheme://userinfo@`` credentials (any scheme, any characters up to the last ``@`` of the
token) and of literal secret values before anything is written.
"""
from __future__ import annotations

import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1
FIELDS = (
    "task_id", "seed", "version_id", "model_id", "key_slot", "agent_image", "watermarks",
    "pre_repair_gds_sha256", "post_repair_gds_sha256", "submission_gds_sha256",
    "submission_src_tree_sha256", "draft_latest_gds_sha256",
    "m_pre", "u_pre", "raw_u_pre", "m_post", "u_post", "raw_u_post",
    "gate_triggered", "trigger_reasons", "repair_ran", "ratchet_promoted", "promote_reason",
    "phase_a_reused", "phase_b_invoked", "model_request_diverged", "submission_artifact_diverged",
    "infra_error", "infra_reason", "outer_metrics", "cost_ledger",
)
OUTER_METRIC_KEYS = ("Y_star", "C_star", "d_star", "score_is_lower_bound", "evaluation_error")
COST_KEYS = ("input_tokens", "output_tokens", "cached_tokens", "usd", "tool_cpu_sec",
             "peak_ram_mb", "wall_sec")
URL_CREDENTIALS = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)\S*@")
REDACTED = "***"
SECRET_ENV_DEFAULT = ("SSAP_UPSTREAM_KEY", "SSAP_UPSTREAM_KEYS")


def literal_secrets_from_env(names: Iterable[str] = SECRET_ENV_DEFAULT,
                             env: dict | None = None) -> list[str]:
    env = os.environ if env is None else env
    out: list[str] = []
    for n in names:
        v = env.get(n)
        if v:
            out += [s.strip() for s in re.split(r"[,\s]+", v) if len(s.strip()) >= 4]
    return out


def scrub_text(text: str, secrets: Iterable[str] = ()) -> str:
    text = URL_CREDENTIALS.sub(lambda m: m.group(1) + REDACTED + "@", text)
    for s in sorted({s for s in secrets if s}, key=len, reverse=True):
        text = text.replace(s, REDACTED)
    return text


def scrub(obj: Any, secrets: Iterable[str] = ()) -> Any:
    secrets = list(secrets)
    if isinstance(obj, str):
        return scrub_text(obj, secrets)
    if isinstance(obj, dict):
        return {scrub_text(str(k), secrets): scrub(v, secrets) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [scrub(v, secrets) for v in obj]
    return obj


def submission_artifact_diverged(summary: dict, baseline: dict | None) -> bool | None:
    if baseline is None:
        return None
    return (summary.get("submission_gds_sha256") != baseline.get("submission_gds_sha256")
            or summary.get("submission_src_tree_sha256") != baseline.get("submission_src_tree_sha256"))


def build_summary(*, baseline: dict | None = None, secrets: Iterable[str] = (), **fields) -> dict:
    """Complete summary: every ``FIELDS`` key present (``None`` when unknown)."""
    unknown = set(fields) - set(FIELDS) - {"extra"}
    if unknown:
        raise ValueError(f"unknown summary fields: {sorted(unknown)}")
    s: dict[str, Any] = {"schema_version": SCHEMA_VERSION}
    for k in FIELDS:
        s[k] = fields.get(k)
    om = dict(s["outer_metrics"] or {})
    s["outer_metrics"] = {k: om.get(k) for k in OUTER_METRIC_KEYS} | {
        k: v for k, v in om.items() if k not in OUTER_METRIC_KEYS}
    cl = dict(s["cost_ledger"] or {})
    s["cost_ledger"] = {k: cl.get(k) for k in COST_KEYS} | {
        k: v for k, v in cl.items() if k not in COST_KEYS}
    for k in ("trigger_reasons",):
        s[k] = list(s[k] or [])
    for k in ("gate_triggered", "repair_ran", "ratchet_promoted", "phase_a_reused",
              "phase_b_invoked", "infra_error"):
        s[k] = bool(s[k]) if s[k] is not None else False
    if s["submission_artifact_diverged"] is None:
        s["submission_artifact_diverged"] = submission_artifact_diverged(s, baseline)
    if "extra" in fields:
        s["extra"] = fields["extra"]
    return scrub(s, secrets)


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.6g}"
    if isinstance(v, (list, tuple)):
        return "[" + ", ".join(_fmt(x) for x in v) + "]"
    if isinstance(v, dict):
        return ", ".join(f"{k}={_fmt(x)}" for k, x in v.items()) or "—"
    return str(v)


def render_markdown(summary: dict, secrets: Iterable[str] = ()) -> str:
    lines = [f"# Artifact summary: {summary.get('task_id') or 'unknown task'}", "",
             "| field | value |", "|---|---|"]
    for k in FIELDS:
        if k in ("outer_metrics", "cost_ledger", "watermarks"):
            continue
        lines.append(f"| `{k}` | {_fmt(summary.get(k)).replace('|', '/')} |")
    for section in ("watermarks", "outer_metrics", "cost_ledger"):
        lines += ["", f"## {section}", "", "| key | value |", "|---|---|"]
        for k, v in (summary.get(section) or {}).items():
            lines.append(f"| `{k}` | {_fmt(v).replace('|', '/')} |")
    return scrub_text("\n".join(lines) + "\n", secrets)


def write_summary(dest_dir: str | Path, summary: dict, secrets: Iterable[str] = ()) -> tuple[Path, Path]:
    dest = Path(dest_dir)
    dest.mkdir(parents=True, exist_ok=True)
    clean = scrub(summary, secrets)
    out = []
    for name, text in (("artifact_summary.json", json.dumps(clean, indent=1, sort_keys=False, default=str)),
                       ("artifact_summary.md", render_markdown(clean, secrets))):
        p = dest / name
        tmp = p.with_name(f".{name}.{uuid.uuid4().hex}")
        tmp.write_text(text)
        os.replace(tmp, p)
        out.append(p)
    return out[0], out[1]
