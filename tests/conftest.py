import json
import shutil
from pathlib import Path

import pytest

from harness_builder import scenario as sc
from harness_builder.state import open_state

STUB = Path(__file__).parent / "stub_scenario"


@pytest.fixture
def stub_dir(tmp_path):
    """A fresh copy of the stub scenario, safe to modify."""
    dst = tmp_path / "scenario"
    shutil.copytree(STUB, dst, ignore=shutil.ignore_patterns("__pycache__"))
    return dst


@pytest.fixture
def make_harness(tmp_path):
    """make_harness(mode) -> a copy of the stub's initial harness whose solver writes ``mode``."""
    def make(mode: str) -> Path:
        dst = tmp_path / f"harness-{mode}"
        if not dst.exists():
            shutil.copytree(STUB / "harness", dst)
            (dst / "settings.json").write_text(json.dumps({"answer": mode}))
        return dst
    return make


@pytest.fixture
def edit_config(stub_dir):
    """edit_config(change) applies ``change(config_dict)`` to the stub copy's scenario.json."""
    def edit(change) -> None:
        path = stub_dir / "scenario.json"
        config = json.loads(path.read_text())
        change(config)
        path.write_text(json.dumps(config))
    return edit


@pytest.fixture
def open_stub_state(stub_dir, tmp_path):
    """open_stub_state() -> State for the stub copy (after any edit_config calls)."""
    return lambda: open_state(tmp_path / "state", sc.load(stub_dir))
