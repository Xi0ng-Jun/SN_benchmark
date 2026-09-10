import importlib.util
import json
from pathlib import Path


def test_stage_metadata_preserves_completed_sibling_updates(tmp_path):
    script = Path(__file__).resolve().parents[1] / 'scripts/run_public_system.py'
    spec = importlib.util.spec_from_file_location('public_system', script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    target = tmp_path / 'run.json'
    module.save_metadata(target, {'datasets': {'a': {'prepared': True}}})
    module.save_metadata(target, {'datasets': {'a': {'scale_index_built': True}}})
    module.save_metadata(target, {'datasets': {'a': {'prepared': True}, 'b': {'prepared': True}}})
    result = json.loads(target.read_text())
    assert result['datasets']['a'] == {'prepared': True, 'scale_index_built': True}
    assert result['datasets']['b']['prepared'] is True
