"""Captured-source runtime config is pinned before all Settings consumers load."""
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


CONFIG = '''
import os
from pathlib import Path
from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=os.environ.get('SILICON_NOTEBOOK_ENV_FILE',
        str(Path(__file__).resolve().parents[3] / '.env')) or None, extra='ignore')
    marker: str = ''
    storage_dir: str = Field('', validation_alias='SILICON_NOTEBOOK_STORAGE_DIR')
    llm_cache_path: str = ''
    event_log_dir: str = ''
    llm_log_path: str = ''
    llm_cache_enabled: bool = True
    agent_profile_enabled: bool = True
    user_search_profile_enabled: bool = True
    retrieval_experience_enabled: bool = True
    retrieval_experience_inject_enabled: bool = True
    reasoning_consult_memory_enabled: bool = True
    shadow_database_url: str | None = None
    model_services_config: str = ''
    def __init__(self, **kw):
        super().__init__(**kw)
        if self.shadow_database_url == '':
            self.shadow_database_url = None
'''


@pytest.mark.parametrize('present', [True, False])
@pytest.mark.parametrize('mode', ['compatible', 'ignores_override', 'class_drift'])
def test_all_config_consumers_use_private_snapshot_after_original_changes(tmp_path, present, mode):
    project = tmp_path / 'original'
    project.mkdir()
    original = project / '.env'
    if present:
        original.write_text('MARKER=before\n')
    service = project / 'services.toml'
    service.write_text('# synthetic services only\n')
    source = tmp_path / 'captured'
    core = source / 'backend/app/core'
    core.mkdir(parents=True)
    (core.parent / '__init__.py').write_text('')
    (core / '__init__.py').write_text('')
    config = CONFIG
    if mode == 'ignores_override':
        config = config.replace("os.environ.get('SILICON_NOTEBOOK_ENV_FILE',\n"
            "        str(Path(__file__).resolve().parents[3] / '.env')) or None",
            "str(Path(__file__).resolve().parents[3] / '.env')")
        (source / '.env').write_text('MARKER=wrong-source-default\n')
    if mode == 'class_drift':
        config = config.replace('super().__init__(**kw)', '''super().__init__(**kw)
        object.__setattr__(self, 'model_config', dict(type(self).model_config))
        type(self).model_config['env_file'] = str(Path(__file__).resolve().parents[3] / '.env')''')
    (core / 'config.py').write_text(config)
    run = tmp_path / 'run'
    run.mkdir()
    script = r'''
import hashlib, json, os, sys
from pathlib import Path
from dotenv import dotenv_values
from rag_eval.runtime_environment import configure_environment
project, source, run, service = map(Path, sys.argv[1:5])
honors_override = sys.argv[5] == 'compatible'
module = source / 'backend/app/core/config.py'
data = module.read_bytes()
identity = {'actual_manifest': {'backend/app/core/config.py':
    {'sha256': hashlib.sha256(data).hexdigest(), 'size': len(data)}}}
try:
    settings, runtime = configure_environment(project, run, product_track=True,
        model_config=service, source_root=source, source_identity=identity)
except ValueError as exc:
    assert not honors_override
    assert str(exc) == 'Captured Settings dotenv default does not match private snapshot'
    assert not (run / 'runtime-identity.json').exists()
    print(json.dumps({'rejected_incompatible_default': True}))
    raise SystemExit(0)
assert honors_override, 'incompatible Settings returned a successful runtime identity'
before = settings.marker
(project / '.env').write_text('MARKER=after\n')
service.write_text('# changed original services\n')
env_file = settings.model_config.get('env_file')
registry_values = dotenv_values(env_file) if env_file else {}
snapshot = runtime['dotenv_snapshot']
assert registry_values.get('MARKER', '') == before
assert type(settings)().marker == before
assert (run / 'runtime/model-services.toml').read_text() == '# synthetic services only\n'
if before:
    private = Path(env_file)
    assert private.is_relative_to(run / 'runtime') and private != project / '.env'
    assert private.stat().st_mode & 0o777 == 0o600
    assert snapshot == {'status': 'captured', 'sha256': hashlib.sha256(private.read_bytes()).hexdigest()}
else:
    assert env_file is None
    assert snapshot == {'status': 'absent', 'sha256': None}
assert os.environ['SILICON_NOTEBOOK_ENV_FILE'] == (env_file or '')
assert 'MARKER' not in (run / 'runtime-identity.json').read_text()
print(json.dumps({'status': snapshot['status'], 'stable': True}))
'''
    environment = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'))
    environment.pop('MARKER', None)
    environment.pop('SILICON_NOTEBOOK_ENV_FILE', None)
    result = subprocess.run([sys.executable, '-c', script, str(project), str(source), str(run), str(service), mode],
                            env=environment, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    expected = ({'status': 'captured' if present else 'absent', 'stable': True} if mode == 'compatible'
                else {'rejected_incompatible_default': True})
    assert json.loads(result.stdout) == expected
