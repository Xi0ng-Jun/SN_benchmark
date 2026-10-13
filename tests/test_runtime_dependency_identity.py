"""Isolated witnesses for declarations, actual startup origins and offline facts."""
import copy
import hashlib
import importlib
import importlib.metadata
import json
import os
from pathlib import Path
import py_compile
import subprocess
import sys
import textwrap
import venv

import pytest


def api():
    from rag_eval import runtime_dependency_identity
    return runtime_dependency_identity


def expected():
    return {'executable': sys.executable, 'prefix': sys.prefix}


def distribution(root, name='e3c-witness', version='1.2', files=None):
    """A real filesystem distribution, discoverable without pip/network."""
    root.mkdir(parents=True, exist_ok=True)
    info = root / (name.replace('-', '_') + '-' + version + '.dist-info')
    info.mkdir()
    (info / 'METADATA').write_text(f'Metadata-Version: 2.1\nName: {name}\nVersion: {version}\n')
    (info / 'RECORD').write_text(''.join(f'{file},,\n' for file in (files or []))
                              + f'{info.name}/METADATA,,\n{info.name}/RECORD,,\n')
    return info


@pytest.fixture
def installed(tmp_path, monkeypatch):
    site = tmp_path / 'site'
    site.mkdir()
    (site / 'e3c_witness.py').write_text('VALUE = "installed"\n')
    info = distribution(site, files=['e3c_witness.py'])
    monkeypatch.syspath_prepend(str(site))
    importlib.invalidate_caches()
    module = importlib.import_module('e3c_witness')
    yield site, info, module
    sys.modules.pop('e3c_witness', None)


def observe(installed, declarations=None):
    return api().observe_startup_dependencies(
        declarations or api().parse_dependency_requirements(b'e3c-witness>=1,<2\n'),
        expected_interpreter=expected(), module_names=('e3c_witness',))


def test_parse_exact_bytes_comments_markers_and_extras():
    data = b'# complete direct input\r\ne3c-witness[fast]>=1,<2 ; python_version >= "3" # retained\r\nother ; python_version < "1"\r\n'
    value = api().parse_dependency_requirements(data)
    assert value['requirements_sha256'] == hashlib.sha256(data).hexdigest()
    assert value['requirements_size'] == len(data)
    assert value['entries'][0]['name'] == 'e3c-witness'
    assert value['entries'][0]['extras'] == ['fast']
    assert value['entries'][0]['marker'] == 'python_version >= "3"'
    assert value['entries'][0]['comment'] == 'retained'
    assert value['entries'][1]['name'] == 'other'
    assert value['captured_at']


@pytest.mark.parametrize('line', [b'-r other.txt', b'--index-url https://example.invalid',
    b'e3c-witness @ https://example.invalid/a.whl', b'not a requirement', b'e3c-witness \\\n>=1', b''])
def test_unsupported_or_missing_declarations_are_rejected(line):
    with pytest.raises(ValueError):
        api().parse_dependency_requirements(line)


@pytest.mark.parametrize('line', [b'e3c-witness; "fast" in extras',
    b'e3c-witness; "test" in dependency_groups'])
def test_markers_needing_unsupplied_set_context_are_explicitly_rejected(line):
    with pytest.raises(ValueError, match='marker'):
        api().parse_dependency_requirements(line)


def test_full_active_direct_check_keeps_inactive_markers_and_unverified_extras(installed):
    declarations = api().parse_dependency_requirements(
        b'e3c-witness[fast]>=1\nabsent-package; python_version < "1"\n')
    result = observe(installed, declarations)
    assert result['status'] == 'startup_verified'
    assert [row['active'] for row in result['direct_requirements']] == [True, False]
    assert result['direct_requirements'][0]['installed_version'] == '1.2'
    assert result['limits']['extras_closure'] == 'unverified'
    assert result['limits']['dependencies'] == 'observed_not_captured'
    assert result['modules'][0]['origin_file_sha256'] == hashlib.sha256(
        (installed[0] / 'e3c_witness.py').read_bytes()).hexdigest()
    assert result['modules'][0]['distribution']['name'] == 'e3c-witness'


@pytest.mark.parametrize('line', [b'e3c-witness>=2', b'e3c-witness>=1\nabsent-package>=1'])
def test_missing_or_incompatible_any_direct_requirement_rejects_startup(installed, line):
    with pytest.raises(ValueError, match='requirement|distribution|version'):
        observe(installed, api().parse_dependency_requirements(line))


def test_observer_uses_loaded_modules_only_and_rejects_shadow_same_version(installed, tmp_path, monkeypatch):
    site, info, loaded = installed
    shadow = tmp_path / 'shadow'
    shadow.mkdir()
    (shadow / 'e3c_witness.py').write_text('VALUE = "shadow"\n')
    sys.modules.pop('e3c_witness')
    monkeypatch.syspath_prepend(str(shadow))
    importlib.invalidate_caches()
    assert importlib.import_module('e3c_witness').VALUE == 'shadow'
    with pytest.raises(ValueError, match='owner|distribution|origin'):
        observe(installed)
    sys.modules.pop('e3c_witness')
    with pytest.raises(ValueError, match='loaded|module'):
        observe(installed)
    assert 'e3c_witness' not in sys.modules


def test_same_version_changed_disk_source_changes_origin_observation(installed):
    before = observe(installed)
    (installed[0] / 'e3c_witness.py').write_text('VALUE = "modified bytes"\n')
    after = observe(installed)
    assert before['modules'][0]['distribution']['version'] == after['modules'][0]['distribution']['version']
    assert before['modules'][0]['origin_file_sha256'] != after['modules'][0]['origin_file_sha256']
    assert 'executed_byte_sha256' not in after['modules'][0]
    assert installed[2].VALUE == 'installed'


@pytest.mark.parametrize('change', ['no_record', 'duplicate_owner', 'custom_loader', 'namespace'])
def test_unsupported_or_ambiguous_loaded_ownership_never_succeeds(installed, change):
    site, info, loaded = installed
    if change == 'no_record':
        (info / 'RECORD').unlink()
    elif change == 'duplicate_owner':
        distribution(site, name='other-owner', files=['e3c_witness.py'])
    elif change == 'namespace':
        loaded.__spec__.origin = None
    else:
        class CustomLoader:
            pass
        loaded.__spec__.loader = CustomLoader()
    with pytest.raises(ValueError, match='RECORD|owner|loader|origin|unsupported|distribution'):
        observe(installed)


@pytest.mark.parametrize('expectation', [None, {}, {'executable': sys.executable},
    {'executable': sys.executable, 'prefix': '/wrong-venv'},
    {'executable': '/wrong-python', 'prefix': sys.prefix},
    {'executable': sys.executable, 'prefix': sys.prefix, 'extra': True}])
def test_explicit_interpreter_expectation_is_required_and_checked(installed, expectation):
    with pytest.raises(ValueError, match='interpreter|executable|prefix|expectation'):
        api().observe_startup_dependencies(api().parse_dependency_requirements(b'e3c-witness'),
            expected_interpreter=expectation, module_names=('e3c_witness',))


def test_offline_validator_rejects_tamper_and_does_not_read_host(installed, monkeypatch):
    observation = observe(installed)
    assert api().validate_startup_observation(observation) == observation
    tampered = copy.deepcopy(observation)
    tampered['modules'][0]['origin_file_sha256'] = '0' * 64
    with pytest.raises(ValueError, match='digest|fingerprint'):
        api().validate_startup_observation(tampered)
    monkeypatch.setattr(Path, 'read_bytes', lambda *args: pytest.fail('offline host read'))
    monkeypatch.setattr(importlib.metadata, 'distributions', lambda *args, **kwargs: pytest.fail('offline metadata read'))
    assert api().validate_startup_observation(json.loads(json.dumps(observation))) == observation


@pytest.mark.parametrize('change', ['schema', 'version', 'origin', 'missing_module', 'missing_direct', 'bad_module', 'bad_loader', 'bad_requirement', 'flags', 'release_level', 'path_role', 'requirement_type'])
def test_offline_validator_checks_facts_even_if_bad_payload_is_rehashed(installed, change):
    observation = observe(installed)
    if change == 'schema':
        observation['future'] = True
    elif change == 'version':
        observation['format'] = 'unknown-version'
    elif change == 'origin':
        observation['modules'][0]['distribution']['located_file'] = '/other/origin.py'
    elif change == 'missing_module':
        observation['modules'] = []
    elif change == 'missing_direct':
        observation['direct_requirements'] = []
    elif change == 'bad_module':
        observation['modules'][0]['module'] = None
    elif change == 'bad_loader':
        observation['modules'][0]['loader']['identity'] = []
    elif change == 'bad_requirement':
        observation['direct_requirements'][0]['requirement'] = 'not a requirement'
    elif change == 'flags':
        observation['interpreter']['flags']['made_up_flag'] = 1
    elif change == 'release_level':
        observation['interpreter']['implementation']['version'][3] = []
    elif change == 'path_role':
        observation['sys_path'][0]['role'] = []
    else:
        observation['direct_requirements'][0]['requirement'] = None
    payload = {key: value for key, value in observation.items() if key != 'fingerprint'}
    observation['fingerprint'] = hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False,
                                                        allow_nan=False).encode()).hexdigest()
    with pytest.raises(ValueError):
        api().validate_startup_observation(observation)


def test_malformed_requested_module_name_is_explicitly_rejected(installed):
    with pytest.raises(ValueError, match='module names'):
        api().observe_startup_dependencies(api().parse_dependency_requirements(b'e3c-witness'),
            expected_interpreter=expected(), module_names=([],))


def test_native_extension_file_is_observed_but_linking_is_unverified():
    # This interpreter makes stdlib extensions built-ins. Use the installed
    # Pydantic native leaf directly; no SN/product imports or instances.
    import pydantic_core._pydantic_core as native
    origin = Path(native.__spec__.origin).resolve()
    result = api().observe_startup_dependencies(api().parse_dependency_requirements(b'pydantic-core>=1'),
        expected_interpreter=expected(), module_names=('pydantic_core._pydantic_core',))
    assert result['modules'][0]['loader']['kind'] == 'extension'
    assert result['modules'][0]['origin_file_sha256'] == hashlib.sha256(origin.read_bytes()).hexdigest()
    assert result['modules'][0]['cached'] == {'status': 'not_applicable', 'path': None, 'resolved': None, 'file': None}
    assert result['limits']['dynamic_linking'] == 'unverified'


def test_default_startup_module_set_is_not_silently_reduced(installed, monkeypatch):
    result = observe(installed)
    assert result['modules'][0]['module'] == 'e3c_witness'
    assert result['requirements']['sha256'] == hashlib.sha256(b'e3c-witness>=1,<2\n').hexdigest()
    monkeypatch.delitem(sys.modules, 'pydantic_core._pydantic_core', raising=False)
    with pytest.raises(ValueError, match='loaded|owner|origin'):
        api().observe_startup_dependencies(api().parse_dependency_requirements(b'e3c-witness>=1'),
            expected_interpreter=expected())


def child(script, *args, executable=None):
    # The temporary child imports only its synthetic target; no SN/config/model.
    source = Path(__file__).resolve().parents[1] / 'src'
    prefix = f'import sys; sys.path.insert(0, {str(source)!r})\n'
    result = subprocess.run([executable or sys.executable, '-I', '-B', '-c', prefix + textwrap.dedent(script),
                             *map(str, args)], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def test_old_timestamp_pyc_executes_stale_code_even_with_B_but_observes_current_source(tmp_path):
    site = tmp_path / 'site'
    site.mkdir()
    source = site / 'e3c_witness.py'
    source.write_text('VALUE = "old"\n')
    timestamp = 1_700_000_000
    os.utime(source, (timestamp, timestamp))
    cached = Path(py_compile.compile(str(source), doraise=True))
    source.write_text('VALUE = "new"\n')
    os.utime(source, (timestamp, timestamp))
    distribution(site, files=['e3c_witness.py'])
    observed = child('''
        import json
        sys.path.insert(0, sys.argv[1])
        import e3c_witness
        from rag_eval.runtime_dependency_identity import parse_dependency_requirements, observe_startup_dependencies
        result = observe_startup_dependencies(parse_dependency_requirements(b'e3c-witness>=1'),
            expected_interpreter={'executable': sys.executable, 'prefix': sys.prefix},
            module_names=('e3c_witness',))
        print(json.dumps({'value': e3c_witness.VALUE, 'observation': result}))
    ''', site)
    assert observed['value'] == 'old'
    module = observed['observation']['modules'][0]
    assert module['origin_file_sha256'] == hashlib.sha256(source.read_bytes()).hexdigest()
    assert module['cached']['file']['sha256'] == hashlib.sha256(cached.read_bytes()).hexdigest()
    assert observed['observation']['interpreter']['flags']['dont_write_bytecode'] == 1
    assert observed['observation']['limits']['executed_bytes'] == 'unverified'


def test_two_venvs_with_same_binary_reject_wrong_selected_prefix(tmp_path):
    first, second = tmp_path / 'first', tmp_path / 'second'
    for path in (first, second):
        venv.EnvBuilder(with_pip=False, symlinks=True).create(path)
    left, right = first / 'bin/python', second / 'bin/python'
    assert left.resolve() == right.resolve()
    result = child('''
        import json
        # packaging is required by the owned module, not installed into this no-pip venv.
        sys.path.insert(0, sys.argv[1])
        from rag_eval.runtime_dependency_identity import parse_dependency_requirements, observe_startup_dependencies
        try:
            observe_startup_dependencies(parse_dependency_requirements(b'e3c-witness'),
                expected_interpreter={'executable': sys.executable, 'prefix': sys.argv[2]},
                module_names=('e3c_witness',))
        except ValueError as exc:
            print(json.dumps({'rejected': True, 'reason': str(exc), 'prefix': sys.prefix}))
        else:
            raise AssertionError('wrong venv accepted')
    ''', Path(importlib.import_module('packaging').__file__).parent.parent, first, executable=str(right))
    assert result['rejected'] and 'prefix' in result['reason']
    assert Path(result['prefix']).resolve() == second.resolve()
