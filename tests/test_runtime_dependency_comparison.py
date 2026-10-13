"""Saved synthetic startup facts; no live collection, dependencies or SN imports."""
import builtins
import copy
import importlib.metadata
import json
import os
from pathlib import Path

import pytest

from rag_eval.identity import fingerprint
from rag_eval import runtime_dependency_identity as identity


RUN = '/recorded/runs/chunk'


def projection(observation, coordinate=RUN):
    from rag_eval.runtime_dependency_comparison import startup_comparison_projection
    return startup_comparison_projection(observation, run_coordinate=coordinate)


def seal(value):
    value['fingerprint'] = fingerprint({key: fact for key, fact in value.items() if key != 'fingerprint'})
    return value


@pytest.fixture
def saved():
    value = {
        'format': 'sn-startup-dependencies-v1', 'status': 'startup_verified',
        'observed_at': '2026-10-12T12:00:00+00:00',
        'requirements': {'sha256': '1' * 64, 'size': 123, 'captured_at': '2026-10-12T11:00:00+00:00'},
        'interpreter': {
            'expected': {'executable': '/venv/bin/python', 'prefix': '/venv'},
            'invocation_executable': '/venv/bin/python', 'resolved_executable': '/python/bin/python',
            'binary': {'status': 'observed', 'sha256': '2' * 64, 'size': 1000},
            'version': '3.13.15 (synthetic saved interpreter)',
            'implementation': {'name': 'cpython', 'version': [3, 13, 15, 'final', 0], 'cache_tag': 'cpython-313'},
            'prefix': '/venv', 'resolved_prefix': '/venv',
            'base_prefix': '/python', 'resolved_base_prefix': '/python',
            'abi': {'soabi': 'cpython-313-x86_64-linux-gnu', 'extension_suffix': '.cpython-313.so',
                    'abiflags': '', 'pointer_bits': 64},
            'flags': {'isolated': 1, 'dont_write_bytecode': 1, 'no_site': 0, 'optimize': 0,
                      'ignore_environment': 1, 'no_user_site': 1},
        },
        'sys_path': [
            {'index': 0, 'path': RUN + '/captured-source/backend', 'role': 'search_path'},
            {'index': 1, 'path': '/evaluator/src', 'role': 'search_path'},
            {'index': 2, 'path': RUN, 'role': 'cwd'},
            {'index': 3, 'path': '/venv/site-packages', 'role': 'search_path'},
        ],
        'marker_environment': {
            'implementation_name': 'cpython', 'implementation_version': '3.13.15', 'os_name': 'posix',
            'platform_machine': 'x86_64', 'platform_release': 'synthetic-release', 'platform_system': 'Linux',
            'platform_version': 'synthetic-version', 'python_full_version': '3.13.15',
            'platform_python_implementation': 'CPython', 'python_version': '3.13',
            'sys_platform': 'linux', 'extra': '',
        },
        'direct_requirements': [
            {'line': 2, 'requirement': 'e3c-witness[fast]>=1,<3', 'comment': 'explicit direct input',
             'name': 'e3c-witness', 'specifier': '<3,>=1', 'extras': ['fast'], 'marker': None,
             'active': True, 'installed_version': '1.2'},
            {'line': 4, 'requirement': 'other; python_version < "1"', 'comment': 'inactive retained',
             'name': 'other', 'specifier': '', 'extras': [], 'marker': 'python_version < "1"',
             'active': False, 'installed_version': None},
        ],
        'modules': [{
            'module': 'e3c_witness', 'origin': '/venv/site-packages/e3c_witness.py',
            'resolved_origin': '/venv/site-packages/e3c_witness.py',
            'loader': {'identity': '_frozen_importlib_external.SourceFileLoader', 'kind': 'source'},
            'origin_file_sha256': '3' * 64, 'origin_file_size': 18,
            'cached': {'status': 'observed', 'path': '/venv/site-packages/__pycache__/e3c_witness.cpython-313.pyc',
                       'resolved': '/venv/site-packages/__pycache__/e3c_witness.cpython-313.pyc',
                       'file': {'sha256': '4' * 64, 'size': 200}},
            'distribution': {'name': 'e3c-witness', 'version': '1.2', 'file': 'e3c_witness.py',
                             'located_file': '/venv/site-packages/e3c_witness.py',
                             'record': {'path': '/venv/site-packages/e3c_witness-1.2.dist-info/RECORD',
                                        'sha256': '5' * 64, 'size': 80}},
        }],
        'limits': {'dependencies': 'observed_not_captured', 'executed_bytes': 'unverified',
                   'extras_closure': 'unverified', 'transitive_closure': 'unverified',
                   'dynamic_linking': 'unverified', 'later_imports': 'unverified',
                   'mutable_disk': 'observed_at_startup'},
    }
    seal(value)
    # Validate the independently hand-built stored record before exercising the
    # new projection, so a fixture mistake cannot impersonate a meaningful red.
    assert identity.validate_startup_observation(value) == value
    return value


def test_two_saved_runs_ignore_only_audit_times_and_known_run_coordinates(saved):
    other = copy.deepcopy(saved)
    other_run = '/recorded/runs/reasoning'
    other['observed_at'] = '2026-10-13T12:00:00+00:00'
    other['requirements']['captured_at'] = '2026-10-13T11:00:00+00:00'
    other['sys_path'][0]['path'] = other_run + '/captured-source/backend'
    other['sys_path'][2]['path'] = other_run
    seal(other)
    before = copy.deepcopy((saved, other))
    left, right = projection(saved), projection(other, other_run)
    assert left == right
    assert fingerprint(left) == fingerprint(right)
    assert saved['fingerprint'] != other['fingerprint']
    assert (saved, other) == before


def test_exact_allowlisted_shape_preserves_all_remaining_facts_without_aliases(saved):
    expected = copy.deepcopy(saved)
    expected['observation_format'] = expected['format']
    expected['format'] = 'sn-startup-environment-v1'
    expected.pop('fingerprint')
    expected.pop('observed_at')
    expected['requirements'].pop('captured_at')
    expected['sys_path'][0]['path'] = '<RUN>/captured-source/backend'
    expected['sys_path'][2]['path'] = '<RUN>'
    result = projection(saved)
    assert result == expected
    assert result['status'] == 'startup_verified'
    before = copy.deepcopy(saved)
    result['modules'][0]['cached']['file']['sha256'] = 'f' * 64
    result['direct_requirements'][0]['extras'].append('another')
    result['interpreter']['implementation']['version'][0] = 9
    result['limits']['executed_bytes'] = 'not a claim'
    assert saved == before


def assert_changed(saved, changed):
    seal(changed)
    assert fingerprint(projection(changed)) != fingerprint(projection(saved))


def test_declaration_byte_facts_entries_and_distribution_versions_remain_comparable_facts(saved):
    changed = copy.deepcopy(saved)
    changed['requirements'].update(sha256='a' * 64, size=124)
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['direct_requirements'][0].update(line=3, comment='a different comment')
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['direct_requirements'][0].update(requirement='e3c-witness[slow]>=1,<3', extras=['slow'])
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['direct_requirements'][1].update(requirement='other; python_version < "2"', marker='python_version < "2"')
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['direct_requirements'][0]['installed_version'] = '1.3'
    changed['modules'][0]['distribution']['version'] = '1.3'
    assert_changed(saved, changed)


def test_module_source_cache_loader_and_record_ownership_remain_exact(saved):
    changed = copy.deepcopy(saved)
    changed['modules'][0].update(origin_file_sha256='a' * 64, origin_file_size=19)
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['modules'][0]['cached']['file'].update(sha256='b' * 64, size=201)
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['modules'][0]['loader'] = {'identity': '_frozen_importlib_external.SourcelessFileLoader', 'kind': 'bytecode'}
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    owner = changed['modules'][0]['distribution']
    owner['record'].update(sha256='c' * 64, size=81)
    owner['file'] = './e3c_witness.py'
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['modules'][0]['cached'] = {'status': 'not_applicable', 'path': None, 'resolved': None, 'file': None}
    assert_changed(saved, changed)


def test_interpreter_expectation_binary_abi_flags_and_marker_environment_remain_exact(saved):
    changed = copy.deepcopy(saved)
    changed['interpreter']['prefix'] = '/another-venv'
    changed['interpreter']['resolved_prefix'] = '/another-venv'
    changed['interpreter']['expected']['prefix'] = '/another-venv'
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['interpreter']['binary'].update(sha256='a' * 64, size=1001)
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['interpreter']['abi']['soabi'] = 'different-abi'
    changed['interpreter']['flags']['dont_write_bytecode'] = 0
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['interpreter']['implementation']['cache_tag'] = 'different-cache-tag'
    changed['interpreter']['version'] = 'a different interpreter build'
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['marker_environment']['platform_machine'] = 'arm64'
    assert_changed(saved, changed)


def test_foreign_paths_order_roles_and_unknown_run_children_are_not_normalized(saved):
    changed = copy.deepcopy(saved)
    changed['sys_path'][1]['path'] = '/foreign/evaluator'
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['sys_path'][1], changed['sys_path'][3] = changed['sys_path'][3], changed['sys_path'][1]
    changed['sys_path'][1]['index'], changed['sys_path'][3]['index'] = 1, 3
    assert_changed(saved, changed)
    changed = copy.deepcopy(saved)
    changed['sys_path'][2]['role'] = 'search_path'
    assert_changed(saved, changed)
    for path in (RUN + '-lookalike/captured-source/backend', RUN + '/private-pycache',
                 RUN + '/captured-source/backend-extra', RUN + '/captured-source/backend'):
        changed = copy.deepcopy(saved)
        changed['sys_path'].append({'index': 4, 'path': path, 'role': 'search_path'})
        result = projection(seal(changed))
        assert result['sys_path'][4]['path'] == path
    changed = copy.deepcopy(saved)
    run_cache = RUN + '/private-pycache/e3c.pyc'
    changed['modules'][0]['cached'].update(path=run_cache, resolved=run_cache)
    assert projection(seal(changed))['modules'][0]['cached']['path'] == run_cache


def test_relocated_payload_uses_only_recorded_coordinate_offline(saved, monkeypatch):
    result = projection(saved)
    relocated = json.loads(json.dumps(saved))
    real_import = builtins.__import__

    def forbidden(*args, **kwargs):
        pytest.fail('projection observed the current host')

    def no_target_import(name, *args, **kwargs):
        if name.split('.')[0] in {'pydantic', 'pydantic_settings', 'pydantic_core', 'dotenv', 'app', 'e3c_witness'}:
            pytest.fail('projection imported a target')
        return real_import(name, *args, **kwargs)

    with monkeypatch.context() as offline:
        offline.setattr(identity, 'observe_startup_dependencies', forbidden)
        offline.setattr(identity, 'default_environment', forbidden)
        offline.setattr(importlib.metadata, 'distributions', forbidden)
        offline.setattr(Path, 'resolve', forbidden)
        offline.setattr(Path, 'open', forbidden)
        offline.setattr(Path, 'read_bytes', forbidden)
        offline.setattr(os, 'getcwd', forbidden)
        offline.setattr(builtins, 'open', forbidden)
        offline.setattr(builtins, '__import__', no_target_import)
        assert projection(relocated, RUN) == result
        with pytest.raises(ValueError, match='captured|first'):
            projection(relocated, '/relocated/runs/chunk')


def test_invalid_coordinates_first_source_contract_and_malformed_records_reject(saved):
    for coordinate in (None, 1, '', '/', '//', '//recorded/runs/chunk', 'relative', RUN + '/',
                       RUN + '/.', '/recorded/../chunk', RUN + '\x00'):
        with pytest.raises(ValueError, match='coordinate'):
            projection(saved, coordinate)
    for path, role in ((RUN + '/captured-source', 'search_path'),
                       (RUN + '/captured-source/backend', 'cwd'),
                       (RUN + '-lookalike/captured-source/backend', 'search_path')):
        changed = copy.deepcopy(saved)
        changed['sys_path'][0].update(path=path, role=role)
        with pytest.raises(ValueError, match='captured|first'):
            projection(seal(changed))
    changed = copy.deepcopy(saved)
    changed['modules'][0]['origin_file_sha256'] = 'e' * 64
    with pytest.raises(ValueError, match='fingerprint'):
        projection(changed)
    changed = copy.deepcopy(saved)
    changed['unexpected'] = True
    with pytest.raises(ValueError, match='schema'):
        projection(seal(changed))
    changed = copy.deepcopy(saved)
    changed['interpreter']['flags']['future_flag'] = 1
    with pytest.raises(ValueError):
        projection(seal(changed))
