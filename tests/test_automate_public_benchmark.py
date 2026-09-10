import importlib.util
from pathlib import Path


def load_automation():
    path = Path(__file__).resolve().parents[1] / "scripts/automate_public_benchmark.py"
    spec = importlib.util.spec_from_file_location("automate_public_benchmark", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_terminal_failure_is_persisted(tmp_path):
    module = load_automation()
    state = {"run_dir": str(tmp_path), "stages": []}

    module.persist_failure(state, ValueError("incompatible baseline"))

    saved = __import__("json").loads((tmp_path / "automation.json").read_text())
    assert saved["status"] == "failed"
    assert saved["failure"] == {"error_type": "ValueError", "message": "incompatible baseline"}
