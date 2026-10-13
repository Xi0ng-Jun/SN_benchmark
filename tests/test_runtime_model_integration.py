"""The captured fresh runner consumes model facts, not an unused side probe."""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


def write_model_fixture(project):
    """Synthetic native-shaped consumer, no credentials or transports."""
    app = project / 'backend/app'
    core = app / 'core'
    services = app / 'services'
    config = (core / 'config.py').read_text()
    config = config.replace('    def __init__(self, **kw):', '''    model_bindings_strict: bool = True
    openai_compat_timeout_seconds: int = 60
    openai_compat_max_retries: int = 2
    openai_compat_max_tokens: int = 8192
    answer_max_tokens: int = 16384
    embed_dim: int = 1024
    embed_batch_size: int = 10
    embed_rate_limit_retries: int = 5
    embed_rate_limit_base_delay: float = 2.0
    notebook_metadata_batch_chars: int = 12000
    notebook_metadata_batch_sources: int = 16
    def __init__(self, **kw):''')
    (core / 'config.py').write_text(config)
    (core / 'event_logging.py').write_text('''
class EventLogger:
    def __init__(self, settings, channel='events', per_user=False): pass
    def emit(self, event): pass
''')
    (services / 'model_registry.py').write_text('''
import hashlib
from pathlib import Path
from types import SimpleNamespace
import tomllib
WORKLOADS = {'ask_answer': SimpleNamespace(id='ask_answer', kind='chat', default_thinking_mode='disabled')}
class ModelBindingGapError(ValueError): pass
class Registry:
    def __init__(self, settings):
        data = tomllib.loads(Path(settings.model_services_config).read_text())
        if settings.model_bindings_strict and not data.get('bindings', {}).get('ask_answer'):
            raise ModelBindingGapError('missing binding')
        self.item = SimpleNamespace(id='chat', kind='chat', protocol='openai', model='synthetic_a',
            base_url='https://synthetic.invalid/v1', api_key_env='SYNTHETIC_FIXTURE_KEY',
            max_concurrency=2, top_p=None, fingerprint=hashlib.sha256(b'synthetic-a').hexdigest())
    def services(self): return (self.item,)
    def service_for(self, name): return self.item
    def thinking_mode_for(self, name): return 'disabled'
''')
    (services / 'model_provider.py').write_text('''
from concurrent.futures import Future
from pathlib import Path
from app.services.model_registry import Registry
class RuntimeModelProvider:
    def __init__(self, settings, event_log):
        self.registry = Registry(settings)
        self._owns_registry = True
        self._config_path = Path(settings.model_services_config)
        self.closed = False
    def _submit_scheduled(self, runtime, *, context, invoke):
        future = Future()
        future.set_result(invoke())
        return future
    def close(self): self.closed = True
''')


@pytest.mark.parametrize('complete', [True, False])
def test_captured_model_bootstrap_precedes_repository_and_is_offline(tmp_path, complete):
    from test_runtime_config_snapshot import CONFIG

    project = tmp_path / 'original'
    core = project / 'backend/app/core'
    core.mkdir(parents=True)
    (core.parent / '__init__.py').write_text('')
    (core / '__init__.py').write_text('')
    (core / 'config.py').write_text(CONFIG)
    services = core.parent / 'services'
    services.mkdir()
    (services / '__init__.py').write_text('')
    write_model_fixture(project)
    model = tmp_path / 'model.toml'
    model.write_text('[bindings]\nask_answer="chat"\n' if complete else '# incomplete\n')
    run = tmp_path / 'run'
    run.mkdir()
    source = tmp_path / 'source'
    import shutil
    shutil.copytree(project / 'backend', source / 'backend')
    manifest = {str(p.relative_to(source)): {'sha256': hashlib.sha256(p.read_bytes()).hexdigest(),
                                            'size': p.stat().st_size}
                for p in source.rglob('*.py')}
    identity = tmp_path / 'identity.json'
    identity.write_text(json.dumps({'actual_manifest': manifest}))
    script = r'''
import json,sys
from pathlib import Path
from rag_eval.runtime_environment import configure_environment, initialize_captured_model_services
project,run,source,model,identity=map(Path,sys.argv[1:6])
code=json.loads(identity.read_text())
settings,runtime=configure_environment(project,run,product_track=True,model_config=model,
    source_root=source,source_identity=code)
try:
    provider,observed=initialize_captured_model_services(settings,run,source_identity=code)
except ValueError:
    assert sys.argv[6]=='False'
    assert not (run/'runtime/database.db').exists()
    print('strict-rejected')
else:
    assert sys.argv[6]=='True'
    assert provider._owns_registry is True
    assert provider.closed is False
    assert observed['observation']['effective_settings']['model_bindings_strict'] is True
    assert {item['module'] for item in observed['source_modules']} >= {'app.services.model_registry','app.services.model_provider'}
    assert not (run/'runtime/database.db').exists()
    assert 'app.services.sqlite_repository' not in sys.modules
    provider.close()
    print('loaded-before-repository')
'''
    env = {'PATH': os.environ['PATH'], 'PYTHONPATH': str(Path(__file__).resolve().parents[1] / 'src'),
           'PYTHONDONTWRITEBYTECODE': '1'}
    # Helper assumes the real captured runner's source location.
    shutil.move(source, run / 'captured-source')
    result = subprocess.run([sys.executable, '-B', '-c', script, str(project), str(run),
        str(run / 'captured-source'), str(model), str(identity), str(complete)],
        env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ('loaded-before-repository' if complete else 'strict-rejected')
