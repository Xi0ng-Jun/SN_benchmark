"""Config validation, digest stability, and strictness."""
import dataclasses
import hashlib
import json

import pytest

from rag_eval.optimizer.v4.config import V4Config, ConfigError, digest, load
from rag_eval.optimizer.v4.loop import V4Run
from .v4_mock import V4Mock


# v4.1 removed predictive_time_stop: stopping only on budget, not predictions


def test_config_is_strict_and_reports_variants():
    cfg = V4Config(time_budget_hours=12)
    # v4.1: dataclass rejects unknown kwargs at construction
    with pytest.raises(TypeError):
        V4Config(time_budget_hours=12, unknown_key=True)
    # v4.1: removed keys also rejected by the dataclass
    with pytest.raises(TypeError):
        V4Config(time_budget_hours=12, no_adoption_rounds=1)
    assert cfg.variants() == {}
    assert V4Config(time_budget_hours=12, c=2.0).variants() == {"c": dict(value=2.0, default=1.0)}


def test_default_config_digest_preserves_existing_run_identity():
    # v4.1: digest includes all config fields (user keys + method constants)
    # Fields added later (LATER_FIELDS) stay out of the digest at their defaults: recorded runs keep their identity.
    from rag_eval.optimizer.v4.config import LATER_FIELDS
    cfg = V4Config(time_budget_hours=12)
    legacy = {k: v for k, v in dataclasses.asdict(cfg).items() if k not in LATER_FIELDS}
    expected = hashlib.sha256(json.dumps(legacy, sort_keys=True).encode()).hexdigest()
    assert digest(cfg) == expected


def test_load_slow_service_settings(tmp_path):
    path = tmp_path / 'v4.json'
    path.write_text(json.dumps(dict(time_budget_hours=12,
                                   adapter=dict(wait_for_running=True))))
    loaded = load(path)
    assert loaded.config.time_budget_hours == 12
    assert loaded.adapter_settings['wait_for_running'] is True
