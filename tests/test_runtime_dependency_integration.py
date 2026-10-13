"""E3c startup checks are consumed by the actual configure/CLI boundaries."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from test_runtime_config_snapshot import CONFIG


def reseal_saved_startup(run, runtime):
    """Make mutations internally hash-consistent so semantic checks must catch them."""
    from rag_eval.artifacts import digest, save_json, save_jsonl
    from rag_eval.identity import fingerprint

    observation = runtime['startup_dependencies']
    observation['fingerprint'] = fingerprint({k: v for k, v in observation.items() if k != 'fingerprint'})
    save_json(run / 'runtime-identity.json', runtime)
    manifest = json.loads((run / 'manifest.json').read_text())
    manifest['run_id'] = run.name
    reference = manifest['identity']['runtime_dependencies']
    reference['observation_sha256'] = fingerprint(observation)
    if 'environment_sha256' in reference:
        from rag_eval.runtime_dependency_comparison import startup_comparison_projection
        reference['environment_sha256'] = fingerprint(startup_comparison_projection(
            observation, run_coordinate=reference['run_coordinate']))
    manifest['protocol_id'] = fingerprint({**manifest['identity'], 'mode': manifest['mode']})
    manifest['pairing_id'] = fingerprint(manifest['identity'])
    planned = [json.loads(row) for row in (run / 'planned.jsonl').read_text().splitlines()]
    for row in planned:
        row.pop('result_id')
        row.update(run_id=run.name, protocol_id=manifest['protocol_id'])
        row['result_id'] = fingerprint(row)
    save_jsonl(run / 'planned.jsonl', planned)
    manifest['planned_sha256'] = digest(run / 'planned.jsonl')
    save_json(run / 'manifest.json', manifest)


@pytest.mark.parametrize('wrong_prefix', [False, True])
def test_captured_configure_consumes_startup_inputs_before_success(tmp_path, wrong_prefix):
    source = tmp_path / 'captured'
    core = source / 'backend/app/core'
    core.mkdir(parents=True)
    (core.parent / '__init__.py').write_text('')
    (core / '__init__.py').write_text('')
    (core / 'config.py').write_text(CONFIG)
    project = tmp_path / 'original'
    project.mkdir()
    run = tmp_path / 'run'
    run.mkdir()
    requirements = tmp_path / 'requirements.txt'
    original = b'pydantic>=2\npydantic-settings>=2\npython-dotenv>=1\n'
    requirements.write_bytes(original)
    script = r'''
import hashlib,json,sys
from pathlib import Path
from rag_eval.runtime_environment import configure_environment
source,project,run,requirements=map(Path,sys.argv[1:5])
data=(source/'backend/app/core/config.py').read_bytes()
identity={'actual_manifest':{'backend/app/core/config.py':{'sha256':hashlib.sha256(data).hexdigest(),'size':len(data)}}}
expect={'executable':sys.executable,'prefix':sys.prefix if sys.argv[5]=='False' else str(run/'wrong-venv')}
try:
 settings,runtime=configure_environment(project,run,product_track=False,source_root=source,
   source_identity=identity,dependency_requirements=requirements,expected_interpreter=expect)
except ValueError:
 assert sys.argv[5]=='True'
 assert not (run/'runtime-identity.json').exists()
 print('rejected')
else:
 assert sys.argv[5]=='False'
 observed=runtime['startup_dependencies']
 assert observed['status']=='startup_verified'
 assert observed['requirements']['sha256']==hashlib.sha256(requirements.read_bytes()).hexdigest()
 assert (run/'dependency-requirements.txt').read_bytes()==requirements.read_bytes()
 requirements.write_bytes(b'not-the-saved-declaration\n')
 assert (run/'dependency-requirements.txt').read_bytes()!=requirements.read_bytes()
 assert json.loads((run/'runtime-identity.json').read_text())['startup_dependencies']==observed
 assert {row['module'] for row in observed['modules']} >= {'pydantic','pydantic_settings','dotenv','pydantic_core','pydantic_core._pydantic_core'}
 print('verified')
'''
    env = dict(os.environ, PYTHONPATH=str(Path(__file__).resolve().parents[1] / 'src'),
               PYTHONDONTWRITEBYTECODE='1')
    result = subprocess.run([sys.executable, '-c', script, str(source), str(project), str(run),
                             str(requirements), str(wrong_prefix)], env=env, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == ('rejected' if wrong_prefix else 'verified')


def test_fresh_cli_passes_explicit_capture_and_startup_inputs(tmp_path, monkeypatch):
    import runpy
    from rag_eval import notebook_runner

    ref = {'kind': 'sn', 'id': 'a' * 64, 'manifest_sha256': 'b' * 64}
    ref_file = tmp_path / 'source-ref.json'
    ref_file.write_text(json.dumps(ref))
    expectation = {'executable': sys.executable, 'prefix': sys.prefix}
    expectation_file = tmp_path / 'expectation.json'
    expectation_file.write_text(json.dumps(expectation))
    requirements = tmp_path / 'requirements.txt'
    requirements.write_text('pydantic>=2\n')
    observed = []
    monkeypatch.setattr(notebook_runner, 'execute', lambda **kwargs: observed.append(kwargs))
    monkeypatch.setattr(sys, 'argv', ['run_notebook_benchmarks.py', '--artifact-root', str(tmp_path/'store'),
        '--artifact-index-id', 'c'*64, '--bundle', str(tmp_path/'bundle'), '--partition-id', 'p',
        '--mode', 'chunk', '--run-dir', str(tmp_path/'run'), '--project-root', str(tmp_path/'project'),
        '--sn-source-ref', str(ref_file), '--dependency-requirements', str(requirements),
        '--runtime-expectation', str(expectation_file)])
    cli = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/run_notebook_benchmarks.py'))
    assert cli['main']() == 0
    assert len(observed) == 1
    assert observed[0]['sn_source_ref'] == ref
    assert observed[0]['dependency_requirements'] == requirements.resolve()
    assert observed[0]['expected_interpreter'] == expectation


def test_incomplete_cli_startup_contract_rejects_before_runner(tmp_path, monkeypatch):
    import runpy
    from rag_eval import notebook_runner

    calls = []
    monkeypatch.setattr(notebook_runner, 'execute', lambda **kw: calls.append(kw))
    monkeypatch.setattr(sys, 'argv', ['run_notebook_benchmarks.py', '--bundle', str(tmp_path/'bundle'),
        '--partition-id', 'p', '--mode', 'chunk', '--run-dir', str(tmp_path/'run'),
        '--project-root', str(tmp_path/'project'), '--sn-source-ref', str(tmp_path/'source.json')])
    cli = runpy.run_path(str(Path(__file__).resolve().parents[1]/'scripts/run_notebook_benchmarks.py'))
    with pytest.raises(SystemExit) as error:
        cli['main']()
    assert error.value.code == 2
    assert calls == []


@pytest.mark.parametrize('wrong_prefix', [False, True])
def test_real_fresh_cli_verifies_before_repository_and_reader_is_offline(tmp_path, wrong_prefix, monkeypatch):
    from test_notebook_execution import bundle_fixture
    from rag_eval.artifact_store import ArtifactStore
    from rag_eval.bundle_index import install_bundle
    from rag_eval.sn_source_capture import capture_sn_source
    from rag_eval.run_reader import load_run, validate_runtime_dependencies
    from rag_eval.identity import fingerprint

    part = bundle_fixture(tmp_path)
    installed = install_bundle(tmp_path / 'bundle', tmp_path / 'store')
    project = tmp_path / 'product'
    core = project / 'backend/app/core'
    core.mkdir(parents=True)
    (core.parent / '__init__.py').write_text('')
    (core / '__init__.py').write_text('')
    (core / 'config.py').write_text(CONFIG)
    services = core.parent / 'services'
    services.mkdir()
    (services / '__init__.py').write_text('')
    from test_runtime_model_integration import write_model_fixture
    write_model_fixture(project)
    # This sentinel is an actually imported captured repository, never a model call.
    (services / 'sqlite_repository.py').write_text('''
import json
from pathlib import Path
from types import SimpleNamespace
class SQLiteRepository:
    def __init__(self, settings, *, model_provider=None):
        run = Path.cwd()
        runtime = json.loads((run / 'runtime-identity.json').read_text())
        manifest = json.loads((run / 'manifest.json').read_text())
        assert runtime['startup_dependencies']['status'] == 'startup_verified'
        assert manifest['identity']['runtime_dependencies']['requirements_sha256'] == runtime['startup_dependencies']['requirements']['sha256']
        assert model_provider is not None and model_provider._owns_registry is True
        assert runtime['loaded_model_services']['observation']['effective_settings']['model_bindings_strict'] is True
        assert manifest['identity']['runtime_model_services']['observation_sha256']
        context=SimpleNamespace(workload_id='ask_answer', support_id='mdl-synthetic',
                                priority='interactive')
        handle=SimpleNamespace(service=model_provider.registry.service_for('ask_answer'))
        future=model_provider._submit_scheduled(handle,context=context,invoke=lambda:'synthetic')
        assert future.result()=='synthetic'
        (run / 'repository-reached').write_text('verified-before-repository')
        raise RuntimeError('synthetic-repository-stop')
''')
    for args in (['init', '-q'], ['add', '.'], ['-c', 'user.name=Test', '-c', 'user.email=test@example.org', 'commit', '-qm', 'fixture']):
        subprocess.run(['git', '-C', str(project), *args], check=True, capture_output=True)
    capture = capture_sn_source(project, ArtifactStore(tmp_path / 'store'))
    ref_file = tmp_path / 'ref.json'
    ref_file.write_text(json.dumps(capture.ref))
    expectation_file = tmp_path / 'expectation.json'
    expectation_file.write_text(json.dumps({'executable': sys.executable,
        'prefix': str(tmp_path / 'other-venv') if wrong_prefix else sys.prefix}))
    declarations = tmp_path / 'requirements.txt'
    declarations.write_bytes(b'pydantic>=2\npydantic-settings>=2\npython-dotenv>=1\n')
    model_config = tmp_path / 'synthetic-services.toml'
    model_config.write_text('[bindings]\nask_answer="chat"\n')
    run = tmp_path / 'run'
    command = [sys.executable, '-I', str(Path(__file__).resolve().parents[1] / 'scripts/run_notebook_benchmarks.py'),
        '--bundle', str(tmp_path / 'bundle'), '--partition-id', part, '--mode', 'chunk',
        '--project-root', str(project), '--run-dir', str(run), '--model-config', str(model_config),
        '--artifact-root', str(tmp_path / 'store'), '--artifact-index-id', installed['index']['id'],
        '--sn-source-ref', str(ref_file), '--dependency-requirements', str(declarations),
        '--runtime-expectation', str(expectation_file)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2, result.stderr
    assert not (run / 'runtime/database.db').exists()
    if wrong_prefix:
        assert not (run / 'repository-reached').exists()
        assert not (run / 'runtime-identity.json').exists()
        assert not (run / 'manifest.json').exists()
        assert json.loads((run / 'state.json').read_text())['error_type'] == 'ValueError'
        return
    assert (run / 'repository-reached').read_text() == 'verified-before-repository'
    assert json.loads((run / 'state.json').read_text())['error_type'] == 'RuntimeError'
    assert 'Report failed' not in result.stderr
    # The saved facts remain readable after original declaration/source inputs change.
    declarations.write_text('changed-original\n')
    (core / 'config.py').write_text('raise AssertionError("mutable source must not load")\n')
    import rag_eval.runtime_dependency_identity as dependencies
    monkeypatch.setattr(dependencies, 'observe_startup_dependencies', lambda *a, **kw: pytest.fail('offline reader observed host'))
    manifest = load_run(run, include_model_events=False)['manifest']
    runtime_file = run / 'runtime-identity.json'
    saved = json.loads(runtime_file.read_text())
    model_binding = manifest['identity']['runtime_model_services']
    assert model_binding['observation_sha256'] == fingerprint(saved['loaded_model_services']['observation'])
    assert model_binding['source_modules_sha256'] == fingerprint(saved['loaded_model_services']['source_modules'])
    events = [json.loads(row) for row in (run / 'model-events.jsonl').read_text().splitlines()]
    assert {row['kind'] for row in events} >= {'model_service_submission_attempt', 'model_service_future_terminal'}
    binding = manifest['identity']['runtime_dependencies']
    assert binding['observation_sha256'] == fingerprint(saved['startup_dependencies'])
    validate_runtime_dependencies(run, manifest['identity'])
    # Complete load_run must detect omissions even when all public bindings are resealed.
    for kind in ('direct', 'native'):
        clone = tmp_path / ('omitted-' + kind)
        shutil.copytree(run, clone)
        incomplete = json.loads((clone / 'runtime-identity.json').read_text())
        observation = incomplete['startup_dependencies']
        field, omitted = ('direct_requirements', 'pydantic-settings') if kind == 'direct' else ('modules', 'pydantic_core._pydantic_core')
        key = 'name' if kind == 'direct' else 'module'
        observation[field] = [row for row in observation[field] if row[key] != omitted]
        reseal_saved_startup(clone, incomplete)
        with pytest.raises(ValueError, match='declaration.*checks|startup.*module'):
            load_run(clone, include_model_events=False)

    # Run the actual CLI again; source/config/input/venv are identical, audit coordinates differ.
    second_run = tmp_path / 'reasoning'
    second_command = command.copy()
    second_command[second_command.index('--run-dir') + 1] = str(second_run)
    second_command[second_command.index('--mode') + 1] = 'reasoning'
    declarations.write_bytes((run / 'dependency-requirements.txt').read_bytes())
    second = subprocess.run(second_command, capture_output=True, text=True)
    assert second.returncode == 2 and (second_run / 'repository-reached').exists(), second.stderr
    from rag_eval.experiment_aggregation import aggregate_runs
    from rag_eval.run_report import paired_modes
    aggregated = aggregate_runs([run, second_run])
    assert aggregated['runs'][0]['config_family'] == aggregated['runs'][1]['config_family']
    pairs, warnings = paired_modes([load_run(run), load_run(second_run)])
    assert pairs and not warnings
    dashboard = tmp_path / 'dashboard.json'
    dashboard.write_text(json.dumps(aggregated))
    javascript = '''const fs=require('fs'),core=require(process.argv[1]);
const d=JSON.parse(fs.readFileSync(process.argv[2],'utf8')),scorer=d.entries[0].scorer;
console.log(JSON.stringify(core.compareCohorts(d.entries.filter(e=>e.mode==='chunk'&&e.scorer===scorer),d.entries.filter(e=>e.mode==='reasoning'&&e.scorer===scorer))));'''
    compared = subprocess.run(['node', '-e', javascript,
        str(Path(__file__).resolve().parents[1] / 'src/rag_eval/dashboard/core.js'), str(dashboard)],
        capture_output=True, text=True, check=True)
    assert json.loads(compared.stdout)['ok'] is True
    # Different recorded dependency bytes must split consumers even if version/name match.
    different = tmp_path / 'different-dependency'
    shutil.copytree(second_run, different)
    changed = json.loads((different / 'runtime-identity.json').read_text())
    changed['startup_dependencies']['modules'][0]['origin_file_sha256'] = '0' * 64
    reseal_saved_startup(different, changed)
    unequal = aggregate_runs([run, different])
    assert unequal['runs'][0]['config_family'] != unequal['runs'][1]['config_family']
    assert not paired_modes([load_run(run), load_run(different)])[0]
    (run / 'dependency-requirements.txt').write_text('pydantic<1\n')
    with pytest.raises(ValueError, match='identity changed'):
        load_run(run, include_model_events=False)


@pytest.mark.parametrize('mode', ['results', 'review'])
def test_result_package_keeps_explicit_declaration_without_private_runtime(tmp_path, mode):
    from test_result_package import campaign
    from rag_eval.result_package import package_campaign

    root = campaign(tmp_path / 'campaign')
    declaration = root / 'runs/one/dependency-requirements.txt'
    declaration.write_bytes(b'pydantic>=2\n')
    private = root / 'runs/one/runtime/settings.env'
    private.write_text('PRIVATE_SYNTHETIC_MARKER=not-exported\n')
    output = tmp_path / 'package'
    package_campaign(root, output, mode=mode)
    assert (output / declaration.relative_to(root)).read_bytes() == declaration.read_bytes()
    assert not (output / private.relative_to(root)).exists()
