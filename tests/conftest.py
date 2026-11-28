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
    """make_harness(mode, **settings) -> a copy of the stub's initial harness whose solver
    writes ``mode`` (and uses the other solver settings, e.g. model_calls=2)."""
    def make(mode: str, **settings) -> Path:
        name = "-".join([mode] + [f"{k}={v}" for k, v in sorted(settings.items())])
        dst = tmp_path / f"harness-{name}"
        if not dst.exists():
            shutil.copytree(STUB / "harness", dst)
            (dst / "settings.json").write_text(json.dumps({"answer": mode, **settings}))
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


@pytest.fixture
def designate():
    """designate(state, folder) -> version record; what the request channel does on `hb deliver`."""
    def designate(state, folder) -> dict:
        meta = state.repo.save(folder)
        state.ledger.append("delivery_designated", version=meta["version"], hash=meta["hash"])
        return meta
    return designate


@pytest.fixture
def stub_model():
    from stub_model import StubModel
    m = StubModel().start()
    yield m
    m.stop()


@pytest.fixture
def make_gateway(stub_model, tmp_path):
    """make_gateway(**kw) -> a started Gateway in front of the stub model (stopped afterwards)."""
    from harness_builder.gateway import Gateway
    started = []

    def make(**kw):
        kw.setdefault("log_path", tmp_path / "requests.jsonl")
        g = Gateway(upstream_url=stub_model.url, upstream_key=stub_model.key, **kw).start()
        started.append(g)
        return g
    yield make
    for g in started:
        g.stop()
