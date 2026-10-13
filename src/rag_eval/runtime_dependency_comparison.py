"""Offline comparison facts for the captured runner's startup observation.

Raw observation fingerprints remain provenance. This projection removes only
audit times and that raw fingerprint, and names two supported sys.path run
coordinates. All other startup facts and limits remain exact. Equal projections
do not prove executed bytes, frozen dependencies or later/native imports.
"""
from __future__ import annotations

import posixpath

from .runtime_dependency_identity import validate_startup_observation


COMPARISON_FORMAT = 'sn-startup-environment-v1'


def _coordinate(value):
    # Recorded POSIX coordinates are lexical facts. Never resolve symlinks,
    # consult cwd, or substitute the directory from which a reader revisits.
    if (not isinstance(value, str) or not value.startswith('/')
            or value.startswith('//') or value == '/' or '\x00' in value
            or posixpath.normpath(value) != value):
        raise ValueError('Invalid recorded absolute normalized run coordinate')


def _file(fact):
    return {'sha256': fact['sha256'], 'size': fact['size']}


def _module(module):
    cached = module['cached']
    owner = module['distribution']
    record = owner['record']
    return {
        'module': module['module'], 'origin': module['origin'],
        'resolved_origin': module['resolved_origin'],
        'loader': {'identity': module['loader']['identity'], 'kind': module['loader']['kind']},
        'origin_file_sha256': module['origin_file_sha256'], 'origin_file_size': module['origin_file_size'],
        'cached': {'status': cached['status'], 'path': cached['path'], 'resolved': cached['resolved'],
                   'file': _file(cached['file']) if cached['file'] is not None else None},
        'distribution': {
            'name': owner['name'], 'version': owner['version'], 'file': owner['file'],
            'located_file': owner['located_file'],
            'record': {'path': record['path'], 'sha256': record['sha256'], 'size': record['size']},
        },
    }


def startup_comparison_projection(observation: dict, *, run_coordinate: str) -> dict:
    """Return detached comparison facts from a validated saved v1 observation.

    ``run_coordinate`` is the recorded original absolute normalized run path.
    The first sys.path fact must be its captured-source/backend search root;
    only this first fact and exact cwd-role run paths become <RUN> coordinates.
    Other paths, even children or lookalikes of this run path, remain literal.
    The caller fingerprints this return value and retains the original
    coordinate/raw binding separately, including after result relocation.
    """
    saved = validate_startup_observation(observation)
    _coordinate(run_coordinate)
    first = saved['sys_path'][0]
    if (first['role'] != 'search_path'
            or first['path'] != run_coordinate + '/captured-source/backend'):
        raise ValueError('First sys.path must be the recorded captured backend search path')
    paths = []
    for item in saved['sys_path']:
        path = item['path']
        if item['index'] == 0:
            path = '<RUN>/captured-source/backend'
        elif item['role'] == 'cwd' and path == run_coordinate:
            path = '<RUN>'
        paths.append({'index': item['index'], 'path': path, 'role': item['role']})

    interpreter = saved['interpreter']
    implementation = interpreter['implementation']
    abi = interpreter['abi']
    environment = saved['marker_environment']
    limits = saved['limits']
    return {
        'format': COMPARISON_FORMAT, 'observation_format': saved['format'], 'status': saved['status'],
        'requirements': {'sha256': saved['requirements']['sha256'], 'size': saved['requirements']['size']},
        'interpreter': {
            'expected': {'executable': interpreter['expected']['executable'],
                         'prefix': interpreter['expected']['prefix']},
            'invocation_executable': interpreter['invocation_executable'],
            'resolved_executable': interpreter['resolved_executable'],
            'binary': {'status': interpreter['binary']['status'],
                       'sha256': interpreter['binary']['sha256'], 'size': interpreter['binary']['size']},
            'version': interpreter['version'],
            'implementation': {'name': implementation['name'], 'version': list(implementation['version']),
                               'cache_tag': implementation['cache_tag']},
            'prefix': interpreter['prefix'], 'resolved_prefix': interpreter['resolved_prefix'],
            'base_prefix': interpreter['base_prefix'], 'resolved_base_prefix': interpreter['resolved_base_prefix'],
            'abi': {'soabi': abi['soabi'], 'extension_suffix': abi['extension_suffix'],
                    'abiflags': abi['abiflags'], 'pointer_bits': abi['pointer_bits']},
            # The v1 validator supplies the closed flag vocabulary. Retain
            # every recorded flag, including optional version-specific flags.
            'flags': dict(interpreter['flags']),
        },
        'sys_path': paths,
        'marker_environment': {
            'implementation_name': environment['implementation_name'],
            'implementation_version': environment['implementation_version'],
            'os_name': environment['os_name'], 'platform_machine': environment['platform_machine'],
            'platform_release': environment['platform_release'], 'platform_system': environment['platform_system'],
            'platform_version': environment['platform_version'], 'python_full_version': environment['python_full_version'],
            'platform_python_implementation': environment['platform_python_implementation'],
            'python_version': environment['python_version'], 'sys_platform': environment['sys_platform'],
            'extra': environment['extra'],
        },
        'direct_requirements': [
            {'line': entry['line'], 'requirement': entry['requirement'], 'comment': entry['comment'],
             'name': entry['name'], 'specifier': entry['specifier'], 'extras': list(entry['extras']),
             'marker': entry['marker'], 'active': entry['active'], 'installed_version': entry['installed_version']}
            for entry in saved['direct_requirements']
        ],
        'modules': [_module(module) for module in saved['modules']],
        'limits': {
            'dependencies': limits['dependencies'], 'executed_bytes': limits['executed_bytes'],
            'extras_closure': limits['extras_closure'], 'transitive_closure': limits['transitive_closure'],
            'dynamic_linking': limits['dynamic_linking'], 'later_imports': limits['later_imports'],
            'mutable_disk': limits['mutable_disk'],
        },
    }
