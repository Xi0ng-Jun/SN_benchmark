"""Local startup facts, not dependency capture or an executed-byte verifier.

The declaration bytes are an independent input captured at ``captured_at``;
the caller persists them. This module neither writes files nor imports target
modules. The observer inspects only already loaded modules and installed wheel
metadata. It supports ordinary filesystem Python/extension loaders and RECORD
ownership, rejecting namespace/editable-without-files/custom origins.

Disk source/cache/binary hashes are observations at startup, not proof of bytes
executed (timestamp pyc may be stale even with -B). Later imports, mutations,
extras/transitive closure, native dynamic linking and readiness remain unverified.
All paths are local private runtime facts, not public portable identities.
"""
from __future__ import annotations

from datetime import datetime, timezone
import hashlib
from importlib import machinery, metadata
import os
from pathlib import Path
import re
import stat
import struct
import sys
import sysconfig

from packaging.markers import UndefinedComparison, UndefinedEnvironmentName, default_environment
from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from .identity import fingerprint


DEFAULT_STARTUP_MODULES = (
    'pydantic', 'pydantic_settings', 'dotenv', 'pydantic_core', 'pydantic_core._pydantic_core',
)
DECLARATIONS_FORMAT = 'sn-dependency-declarations-v1'
OBSERVATION_FORMAT = 'sn-startup-dependencies-v1'
_LIMITS = {
    'dependencies': 'observed_not_captured', 'executed_bytes': 'unverified',
    'extras_closure': 'unverified', 'transitive_closure': 'unverified',
    'dynamic_linking': 'unverified', 'later_imports': 'unverified',
    'mutable_disk': 'observed_at_startup',
}
_LOADERS = {
    machinery.SourceFileLoader: 'source', machinery.SourcelessFileLoader: 'bytecode',
    machinery.ExtensionFileLoader: 'extension',
}
_HEX = re.compile(r'[a-f0-9]{64}\Z')
_MODULE = re.compile(r'[A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*\Z', re.ASCII)
_FLAG_NAMES = frozenset({
    'debug', 'inspect', 'interactive', 'optimize', 'dont_write_bytecode', 'no_user_site',
    'no_site', 'ignore_environment', 'verbose', 'bytes_warning', 'quiet', 'hash_randomization',
    'isolated', 'dev_mode', 'utf8_mode', 'warn_default_encoding', 'safe_path', 'int_max_str_digits',
    'gil', 'thread_inherit_context', 'context_aware_warnings',
})
_MARKER_KEYS = (
    'implementation_name', 'implementation_version', 'os_name', 'platform_machine',
    'platform_release', 'platform_system', 'platform_version', 'python_full_version',
    'platform_python_implementation', 'python_version', 'sys_platform', 'extra',
)


def _marker_active(requirement, environment):
    try:
        return requirement.marker is None or requirement.marker.evaluate(environment=environment)
    except (UndefinedComparison, UndefinedEnvironmentName, KeyError, ValueError):
        raise ValueError('Unsupported dependency marker/context') from None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sealed(payload: dict) -> dict:
    return {**payload, 'fingerprint': fingerprint(payload)}


def _entry(statement: str, comment: str, line: int) -> dict:
    try:
        req = Requirement(statement)
    except InvalidRequirement:
        raise ValueError(f'Unsupported dependency requirement at line {line}') from None
    if req.url or '\\' in statement or statement.startswith('-'):
        raise ValueError(f'Unsupported dependency requirement at line {line}')
    # Validate the ordinary requirements marker context independently of host
    # truth. Set-valued extras/dependency_groups require a different declaration
    # input and must not silently become inactive in this context.
    _marker_active(req, {**dict.fromkeys(_MARKER_KEYS, ''), 'python_version': '3.0',
                         'python_full_version': '3.0.0', 'implementation_version': '3.0.0'})
    return {
        'line': line, 'requirement': statement, 'comment': comment,
        'name': canonicalize_name(req.name), 'specifier': str(req.specifier),
        'extras': sorted(req.extras), 'marker': str(req.marker) if req.marker else None,
    }


def parse_dependency_requirements(data: bytes) -> dict:
    """Parse complete ordinary PEP508 lines; no includes/options/URLs/ignores."""
    if not isinstance(data, bytes):
        raise ValueError('Dependency requirements must be explicit bytes')
    try:
        text = data.decode('utf-8')
    except UnicodeError:
        raise ValueError('Dependency requirements must be UTF-8') from None
    entries = []
    for line, raw in enumerate(text.splitlines(), 1):
        stripped = raw.strip()
        if not stripped or stripped.startswith('#'):
            continue
        pieces = re.split(r'\s+#', stripped, maxsplit=1)
        statement, comment = pieces[0].strip(), pieces[1].strip() if len(pieces) == 2 else ''
        entries.append(_entry(statement, comment, line))
    if not entries:
        raise ValueError('Missing direct dependency declarations')
    return _sealed({
        'format': DECLARATIONS_FORMAT,
        'requirements_sha256': hashlib.sha256(data).hexdigest(),
        'requirements_size': len(data), 'captured_at': _now(), 'entries': entries,
    })


def _keys(value, names, label):
    if not isinstance(value, dict) or set(value) != set(names):
        raise ValueError(f'Invalid {label} schema')


def _text(value, label):
    if not isinstance(value, str) or not value or '\x00' in value:
        raise ValueError(f'Invalid {label}')


def _hash(value):
    if not isinstance(value, str) or not _HEX.fullmatch(value):
        raise ValueError('Invalid observation hash')


def _integer(value, label, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f'Invalid {label}')


def _time(value):
    _text(value, 'capture/observation time')
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        raise ValueError('Invalid capture/observation time') from None
    if parsed.tzinfo is None or parsed.utcoffset().total_seconds() != 0:
        raise ValueError('Capture/observation time must be UTC')


def _path(value):
    _text(value, 'local path')
    if not os.path.isabs(value) or os.path.normpath(value) != value:
        raise ValueError('Invalid normalized absolute local path')


def _verify_digest(value):
    _hash(value.get('fingerprint'))
    try:
        digest = fingerprint({k: v for k, v in value.items() if k != 'fingerprint'})
    except (TypeError, ValueError):
        raise ValueError('Invalid observation/declaration serialization') from None
    if value['fingerprint'] != digest:
        raise ValueError('Observation/declaration fingerprint mismatch')


def _validate_entries(entries):
    if not isinstance(entries, list) or not entries:
        raise ValueError('Missing direct dependency declarations')
    previous = 0
    for item in entries:
        _keys(item, ('line', 'requirement', 'comment', 'name', 'specifier', 'extras', 'marker'), 'declaration entry')
        _integer(item['line'], 'declaration line', 1)
        _text(item['requirement'], 'dependency requirement')
        if item['line'] <= previous or not isinstance(item['comment'], str):
            raise ValueError('Invalid declaration lines/comments')
        previous = item['line']
        if item != _entry(item['requirement'], item['comment'], item['line']):
            raise ValueError('Inconsistent dependency declaration fields')


def _validate_declarations(declarations):
    _keys(declarations, ('format', 'requirements_sha256', 'requirements_size', 'captured_at', 'entries', 'fingerprint'), 'declarations')
    if declarations['format'] != DECLARATIONS_FORMAT:
        raise ValueError('Unknown dependency declarations format')
    _hash(declarations['requirements_sha256'])
    _integer(declarations['requirements_size'], 'requirements size', 1)
    _time(declarations['captured_at'])
    _validate_entries(declarations['entries'])
    _verify_digest(declarations)


def _file_fact(path: Path) -> dict:
    """Hash a regular disk file; reject observed replacement/mutation."""
    try:
        with path.open('rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode):
                raise ValueError('Unsupported nonregular dependency file')
            digest = hashlib.file_digest(handle, 'sha256').hexdigest()
            after = os.fstat(handle.fileno())
        current = path.stat()
    except OSError:
        raise ValueError('Dependency observation file is unreadable') from None
    signature = lambda value: (value.st_dev, value.st_ino, value.st_size, value.st_mtime_ns, value.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current):
        raise ValueError('Dependency file changed during observation')
    return {'sha256': digest, 'size': after.st_size}


def _interpreter(expectation) -> dict:
    _keys(expectation, ('executable', 'prefix'), 'interpreter expectation')
    for key in ('executable', 'prefix'):
        _text(expectation[key], 'interpreter expectation ' + key)
    selected = os.path.abspath(expectation['executable'])
    invocation = os.path.abspath(sys.executable)
    # Preserve the invocation symlink role: do not collapse two venv executables
    # to their common binary and silently bless a different environment.
    if selected != invocation:
        raise ValueError('Selected interpreter executable does not match invocation')
    prefix = str(Path(sys.prefix).resolve())
    if str(Path(expectation['prefix']).resolve()) != prefix:
        raise ValueError('Selected interpreter prefix does not match current environment')
    resolved = str(Path(invocation).resolve())
    binary = {'status': 'observed', **_file_fact(Path(resolved))}
    flags = {name: int(getattr(sys.flags, name)) for name in dir(sys.flags)
             if not name.startswith(('_', 'n_')) and type(getattr(sys.flags, name)) in (int, bool)}
    return {
        'expected': {'executable': selected, 'prefix': prefix},
        'invocation_executable': invocation, 'resolved_executable': resolved,
        'binary': binary, 'version': sys.version,
        'implementation': {'name': sys.implementation.name,
                           'version': list(sys.implementation.version), 'cache_tag': sys.implementation.cache_tag},
        'prefix': os.path.abspath(sys.prefix), 'resolved_prefix': prefix,
        'base_prefix': os.path.abspath(sys.base_prefix), 'resolved_base_prefix': str(Path(sys.base_prefix).resolve()),
        'abi': {'soabi': sysconfig.get_config_var('SOABI'),
                'extension_suffix': sysconfig.get_config_var('EXT_SUFFIX'),
                'abiflags': getattr(sys, 'abiflags', ''), 'pointer_bits': struct.calcsize('P') * 8},
        'flags': flags,
    }


def _installed() -> list:
    # Materialize once. No distribution entrypoint or package code is imported.
    return list(metadata.distributions())


def _named(distribution):
    name, version = distribution.metadata.get('Name'), distribution.version
    if not name or not version:
        raise ValueError('Installed distribution has incomplete metadata')
    return canonicalize_name(name), version


def _direct_checks(entries, installed, environment):
    by_name = {}
    for distribution in installed:
        name = distribution.metadata.get('Name')
        if name:
            by_name.setdefault(canonicalize_name(name), []).append(distribution)
    checks = []
    for entry in entries:
        requirement = Requirement(entry['requirement'])
        active = _marker_active(requirement, environment)
        version = None
        if active:
            matches = by_name.get(entry['name'], [])
            if len(matches) != 1:
                raise ValueError('Missing or ambiguous installed distribution for direct requirement: ' + entry['name'])
            _, version = _named(matches[0])
            try:
                compatible = requirement.specifier.contains(Version(version))
            except InvalidVersion:
                raise ValueError('Invalid installed distribution version') from None
            if not compatible:
                raise ValueError('Installed version does not satisfy direct requirement: ' + entry['name'])
        checks.append({**entry, 'active': active, 'installed_version': version})
    return checks


def _loaded_module(name):
    module = sys.modules.get(name)
    if module is None:
        raise ValueError('Required startup module is not already loaded: ' + name)
    spec = getattr(module, '__spec__', None)
    origin = getattr(spec, 'origin', None)
    loader = getattr(spec, 'loader', None)
    if not isinstance(origin, str) or not os.path.isabs(origin):
        raise ValueError('Unsupported namespace/nonfilesystem module origin: ' + name)
    if type(loader) not in _LOADERS or getattr(module, '__loader__', None) is not loader:
        raise ValueError('Unsupported or inconsistent module loader: ' + name)
    resolved = Path(origin).resolve()
    if (getattr(spec, 'name', None) != name or getattr(loader, 'name', None) != name
            or Path(loader.path).resolve() != resolved
            or not isinstance(getattr(module, '__file__', None), str)
            or Path(module.__file__).resolve() != resolved):
        raise ValueError('Inconsistent loaded module origin/loader: ' + name)
    fact = _file_fact(resolved)
    cached_path = getattr(module, '__cached__', None)
    if cached_path is None:
        cached = {'status': 'not_applicable', 'path': None, 'resolved': None, 'file': None}
    else:
        if not isinstance(cached_path, str) or not os.path.isabs(cached_path):
            raise ValueError('Unsupported module cache path')
        cached_resolved = Path(cached_path).resolve()
        cached = {'status': 'observed' if cached_resolved.exists() else 'absent',
                  'path': os.path.abspath(cached_path), 'resolved': str(cached_resolved),
                  'file': _file_fact(cached_resolved) if cached_resolved.exists() else None}
    return {
        'module': name, 'origin': origin, 'resolved_origin': str(resolved),
        'loader': {'identity': type(loader).__module__ + '.' + type(loader).__qualname__, 'kind': _LOADERS[type(loader)]},
        'origin_file_sha256': fact['sha256'], 'origin_file_size': fact['size'], 'cached': cached,
    }


def _owners(modules, installed):
    origins = {module['resolved_origin']: [] for module in modules}
    for distribution in installed:
        files = distribution.files
        if not files:
            continue
        found = {}
        # Ownership comes from RECORD-listed paths, never containment or a
        # packages_distributions heuristic. Compare resolved file paths.
        for file in files:
            located = str(Path(distribution.locate_file(file)).resolve())
            if located in origins:
                found.setdefault(located, []).append(str(file))
        if not found:
            continue
        name, version = _named(distribution)
        record = [file for file in files if file.name == 'RECORD' and file.parent.name.endswith('.dist-info')]
        if len(record) != 1:
            raise ValueError('Module distribution lacks unique RECORD ownership metadata')
        record_path = Path(distribution.locate_file(record[0])).resolve()
        record_fact = _file_fact(record_path)
        for origin, paths in found.items():
            if len(paths) != 1:
                raise ValueError('Ambiguous distribution file ownership')
            origins[origin].append({
                'name': name, 'version': version, 'file': paths[0], 'located_file': origin,
                'record': {'path': str(record_path), **record_fact},
            })
    for module in modules:
        matches = origins[module['resolved_origin']]
        if len(matches) != 1:
            raise ValueError('Missing or ambiguous distribution owner for module origin: ' + module['module'])
        module['distribution'] = matches[0]


def observe_startup_dependencies(
    declarations: dict, *, expected_interpreter: dict,
    module_names: tuple[str, ...] = DEFAULT_STARTUP_MODULES,
) -> dict:
    """Observe this process after Settings load; success is startup-only facts."""
    _validate_declarations(declarations)
    interpreter = _interpreter(expected_interpreter)
    if (not isinstance(module_names, tuple) or not module_names
            or any(not isinstance(name, str) or not _MODULE.fullmatch(name) for name in module_names)
            or len(set(module_names)) != len(module_names)):
        raise ValueError('Required startup module names must be a nonempty unique tuple')
    environment = {**default_environment(), 'extra': ''}
    installed = _installed()
    direct = _direct_checks(declarations['entries'], installed, environment)
    modules = [_loaded_module(name) for name in module_names]
    _owners(modules, installed)
    # Record paths in order, without reading environ or recording argv/config.
    paths = []
    for index, entry in enumerate(sys.path):
        if not isinstance(entry, str):
            raise ValueError('Unsupported effective sys.path entry')
        paths.append({'index': index, 'path': os.path.abspath(entry),
                      'role': 'cwd' if not entry else 'search_path'})
    payload = _sealed({
        'format': OBSERVATION_FORMAT, 'status': 'startup_verified', 'observed_at': _now(),
        'requirements': {'sha256': declarations['requirements_sha256'], 'size': declarations['requirements_size'],
                         'captured_at': declarations['captured_at']},
        'interpreter': interpreter, 'sys_path': paths, 'marker_environment': environment,
        'direct_requirements': direct, 'modules': modules, 'limits': dict(_LIMITS),
    })
    return validate_startup_observation(payload)


def _validate_file(fact):
    _keys(fact, ('sha256', 'size'), 'file fact')
    _hash(fact['sha256'])
    _integer(fact['size'], 'file size')


def _validate_interpreter(value):
    _keys(value, ('expected', 'invocation_executable', 'resolved_executable', 'binary', 'version',
                 'implementation', 'prefix', 'resolved_prefix', 'base_prefix', 'resolved_base_prefix', 'abi', 'flags'), 'interpreter')
    _keys(value['expected'], ('executable', 'prefix'), 'interpreter expectation')
    for name in ('invocation_executable', 'resolved_executable', 'prefix', 'resolved_prefix', 'base_prefix', 'resolved_base_prefix'):
        _path(value[name])
    if value['expected'] != {'executable': value['invocation_executable'], 'prefix': value['resolved_prefix']}:
        raise ValueError('Interpreter expectation does not match recorded facts')
    binary = value['binary']
    _keys(binary, ('status', 'sha256', 'size'), 'binary')
    if binary['status'] == 'observed':
        _validate_file({k: binary[k] for k in ('sha256', 'size')})
    elif binary != {'status': 'unsupported', 'sha256': None, 'size': None}:
        raise ValueError('Invalid binary observation status')
    _text(value['version'], 'interpreter version')
    implementation = value['implementation']
    _keys(implementation, ('name', 'version', 'cache_tag'), 'implementation')
    _text(implementation['name'], 'implementation name')
    if implementation['cache_tag'] is not None:
        _text(implementation['cache_tag'], 'implementation cache tag')
    version = implementation['version']
    if (not isinstance(version, list) or len(version) != 5
            or any(type(version[i]) is not int or version[i] < 0 for i in (0, 1, 2, 4))
            or version[3] not in ('alpha', 'beta', 'candidate', 'final')):
        raise ValueError('Invalid implementation version')
    abi = value['abi']
    _keys(abi, ('soabi', 'extension_suffix', 'abiflags', 'pointer_bits'), 'ABI')
    if (any(abi[key] is not None and not isinstance(abi[key], str) for key in ('soabi', 'extension_suffix'))
            or not isinstance(abi['abiflags'], str) or type(abi['pointer_bits']) is not int or abi['pointer_bits'] not in {32, 64}):
        raise ValueError('Invalid interpreter ABI')
    flags = value['flags']
    if (not isinstance(flags, dict) or not {'isolated', 'dont_write_bytecode', 'no_site', 'optimize', 'ignore_environment'} <= flags.keys()
            or not flags.keys() <= _FLAG_NAMES
            or any(not isinstance(k, str) or not k.isidentifier() or type(v) is not int for k, v in flags.items())):
        raise ValueError('Invalid effective interpreter flags')


def validate_startup_observation(observation: dict) -> dict:
    """Validate closed saved facts/digest offline; no file/metadata reads/imports.

    This is integrity/consistency checking, not attestation of an arbitrary
    author's truth or re-verification of a host. The caller binds this digest
    and the independently saved declaration bytes in its existing run manifest.
    """
    _keys(observation, ('format', 'status', 'observed_at', 'requirements', 'interpreter', 'sys_path',
                        'marker_environment', 'direct_requirements', 'modules', 'limits', 'fingerprint'), 'startup observation')
    if observation['format'] != OBSERVATION_FORMAT or observation['status'] != 'startup_verified':
        raise ValueError('Unknown startup observation format/status')
    _verify_digest(observation)
    _time(observation['observed_at'])
    requirements = observation['requirements']
    _keys(requirements, ('sha256', 'size', 'captured_at'), 'requirements')
    _hash(requirements['sha256'])
    _integer(requirements['size'], 'requirements size', 1)
    _time(requirements['captured_at'])
    _validate_interpreter(observation['interpreter'])
    paths = observation['sys_path']
    if not isinstance(paths, list) or not paths:
        raise ValueError('Missing effective sys.path')
    for index, path in enumerate(paths):
        _keys(path, ('index', 'path', 'role'), 'sys.path')
        if type(path['index']) is not int or path['index'] != index or path['role'] not in ('cwd', 'search_path'):
            raise ValueError('Invalid ordered sys.path facts')
        _path(path['path'])
    environment = observation['marker_environment']
    _keys(environment, _MARKER_KEYS, 'marker environment')
    if any(not isinstance(value, str) for value in environment.values()) or environment['extra'] != '':
        raise ValueError('Invalid recorded marker environment')
    direct = observation['direct_requirements']
    if not isinstance(direct, list) or not direct:
        raise ValueError('Missing direct requirements checks')
    entries = []
    for check in direct:
        _keys(check, ('line', 'requirement', 'comment', 'name', 'specifier', 'extras', 'marker', 'active', 'installed_version'), 'direct requirement check')
        entry = {k: v for k, v in check.items() if k not in {'active', 'installed_version'}}
        entries.append(entry)
    _validate_entries(entries)
    for entry, check in zip(entries, direct):
        req = Requirement(entry['requirement'])
        active = _marker_active(req, environment)
        if type(check['active']) is not bool or check['active'] != active:
            raise ValueError('Inconsistent direct requirement marker check')
        if active:
            try:
                version = Version(check['installed_version'])
            except (InvalidVersion, TypeError):
                raise ValueError('Invalid installed version in direct requirement') from None
            if not req.specifier.contains(version):
                raise ValueError('Incompatible installed direct requirement version')
        elif check['installed_version'] is not None:
            raise ValueError('Inactive requirement cannot claim installed version check')
    modules = observation['modules']
    if not isinstance(modules, list) or not modules:
        raise ValueError('Missing startup module facts')
    names = set()
    for module in modules:
        _keys(module, ('module', 'origin', 'resolved_origin', 'loader', 'origin_file_sha256', 'origin_file_size', 'cached', 'distribution'), 'loaded module')
        name = module['module']
        if not isinstance(name, str) or not _MODULE.fullmatch(name) or name in names:
            raise ValueError('Invalid/duplicate startup module name')
        names.add(name)
        _path(module['origin'])
        _path(module['resolved_origin'])
        _hash(module['origin_file_sha256'])
        _integer(module['origin_file_size'], 'module file size')
        loader = module['loader']
        _keys(loader, ('identity', 'kind'), 'loader')
        _text(loader['identity'], 'module loader identity')
        valid_loaders = {cls.__module__ + '.' + cls.__qualname__: kind for cls, kind in _LOADERS.items()}
        if valid_loaders.get(loader['identity']) != loader['kind'] or loader['kind'] not in _LOADERS.values():
            raise ValueError('Unsupported recorded module loader')
        cached = module['cached']
        _keys(cached, ('status', 'path', 'resolved', 'file'), 'module cache')
        if cached['status'] == 'not_applicable':
            if any(cached[key] is not None for key in ('path', 'resolved', 'file')):
                raise ValueError('Invalid not-applicable cache fact')
        else:
            _path(cached['path'])
            _path(cached['resolved'])
            if cached['status'] == 'observed':
                _validate_file(cached['file'])
            elif cached['status'] != 'absent' or cached['file'] is not None:
                raise ValueError('Invalid module cache status')
        owner = module['distribution']
        _keys(owner, ('name', 'version', 'file', 'located_file', 'record'), 'distribution owner')
        _text(owner['name'], 'distribution name')
        if canonicalize_name(owner['name']) != owner['name']:
            raise ValueError('Invalid canonical distribution name')
        try:
            Version(owner['version'])
        except (InvalidVersion, TypeError):
            raise ValueError('Invalid owning distribution version') from None
        _text(owner['file'], 'distribution file')
        _path(owner['located_file'])
        if owner['located_file'] != module['resolved_origin']:
            raise ValueError('Module origin differs from distribution ownership')
        _keys(owner['record'], ('path', 'sha256', 'size'), 'distribution RECORD')
        _path(owner['record']['path'])
        _validate_file({k: owner['record'][k] for k in ('sha256', 'size')})
        for check in direct:
            if check['active'] and check['name'] == owner['name'] and check['installed_version'] != owner['version']:
                raise ValueError('Module distribution version differs from checked direct declaration')
    if observation['limits'] != _LIMITS:
        raise ValueError('Invalid startup observation limits/capabilities')
    return observation
