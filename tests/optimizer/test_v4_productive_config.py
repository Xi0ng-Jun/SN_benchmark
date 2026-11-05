"""Slow rounds may consume the remaining budget without a predictive early exit."""
import dataclasses
import hashlib
import json

import pytest

from rag_eval.optimizer.v4.config import V4Config, digest, load
from rag_eval.optimizer.v4.loop import V4Run
from .v4_mock import V4Mock


@pytest.mark.parametrize('predictive,expected', [(True, 'time_budget'), (False, None)])
def test_predictive_stop_is_optional_but_actual_deadline_is_not(predictive, expected):
    now = [0.0]
    mock = V4Mock([])
    cfg = V4Config(time_budget_hours=1, predictive_time_stop=predictive)
    run = V4Run(mock, mock.seed, cfg, search=mock.search, validation=mock.validation, clock=lambda: now[0])
    run.rounds = [dict(wall_s=2400)]
    now[0] = 1800
    assert run._stop_reason() == expected
    now[0] = 3601
    assert run._stop_reason() == 'time_budget'


def test_default_config_digest_preserves_existing_run_identity():
    cfg = V4Config(time_budget_hours=12)
    old = dataclasses.asdict(cfg)
    old.pop('predictive_time_stop')
    expected = hashlib.sha256(json.dumps(old, sort_keys=True).encode()).hexdigest()
    assert digest(cfg) == expected
    assert digest(dataclasses.replace(cfg, predictive_time_stop=False)) != expected


def test_load_slow_service_settings(tmp_path):
    path = tmp_path / 'v4.json'
    path.write_text(json.dumps(dict(time_budget_hours=12, predictive_time_stop=False,
                                   adapter=dict(wait_for_running=True))))
    loaded = load(path)
    assert not loaded.config.predictive_time_stop
    assert loaded.adapter_settings['wait_for_running'] is True
