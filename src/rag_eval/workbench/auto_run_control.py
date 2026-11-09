"""Confirmed v4.1 process control over the existing CLI and automatic-run binding."""
from __future__ import annotations

from contextlib import contextmanager
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import secrets
import signal
import subprocess
import sys
import time

from fastapi import APIRouter
from pydantic import BaseModel, ConfigDict, Field

from . import auto_runs
from .repository import Conflict
from ..optimizer import ledger_io
from ..optimizer.run_view import run_alive, RunLocked, hold_run_lock
from ..optimizer.v4 import calibration_session as session, proposal
from ..optimizer.v4.config import load
from ..optimizer.v4.simulate import simulate


class Action(BaseModel):
    model_config = ConfigDict(extra='forbid')
    confirmation_token: str | None = None
    inputs: dict = Field(default_factory=dict)
    retry_failed: bool = False
    recovery_execution_timeout_seconds: int | None = Field(default=None, strict=True, ge=1, le=86400)


class Edit(BaseModel):
    model_config = ConfigDict(extra='forbid')
    key: str
    value: object


class Simulation(BaseModel):
    model_config = ConfigDict(extra='forbid')
    params: dict = Field(default_factory=dict)


def process_identity(pid):
    """PID plus Linux boot and start ticks; a reused PID is never our process."""
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().rsplit(')', 1)[1].split()
        if fields[0] == 'Z':
            return None
        return Path('/proc/sys/kernel/random/boot_id').read_text().strip() + ':' + fields[19]
    except (OSError, ValueError, IndexError):
        return None


class AutoRunControl:
    def __init__(self, api, config):
        self.api, self.repository, self.config = api, api.repository, config
        self.root = self.repository.root / 'automatic-v41'
        self.creation_api = None  # wired by create_app; same store/bridge as real dispatch

    def directory(self, iteration_id):
        self.api._scope(iteration_id=iteration_id)
        bound = auto_runs.binding(self.repository, iteration_id)
        if bound:
            if bound.get('method') != 'method-v4':
                raise Conflict('This iteration belongs to another automatic method')
            return Path(bound['state_dir'])
        # Scope validation above resolves the identifier before using it as a path.
        return self.root / hashlib.sha256(iteration_id.encode()).hexdigest()[:24]

    @contextmanager
    def control_lock(self, root):
        root.mkdir(parents=True, exist_ok=True)
        with (root / 'control.lock').open('a') as stream:
            try:
                fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Conflict('Another control action is in progress') from None
            try:
                yield
            except RunLocked:
                raise Conflict('An automatic process is already running') from None

    @staticmethod
    def _read(root, name):
        path = root / name
        return ledger_io.read_json(path) if path.exists() else None

    def process(self, root):
        record = self._read(root, 'process.json')
        return record if record and record.get('identity') and process_identity(record['pid']) == record['identity'] else None

    def busy(self, root):
        return run_alive(root) or self.process(root) is not None

    def _audit(self, iteration_id, action, digest, **extra):
        tree = self.api.service.iteration_record(iteration_id)
        with self.repository.connect(write=True) as conn:
            self.repository._event(conn, tree['task_id'], 'v41.control.' + action, iteration_id,
                                   dict(actor=self.api.actor, config_sha256=digest, **extra))

    def _calibration_input(self, inputs):
        allowed = {'time_budget_hours', 'calibration_k', 'calibration_concurrency', 'safeguards', 'min_meaningful_improvement',
                   'adapter', 'spend_budget', 'price_per_1k_tokens', 'test_set_authorized'}
        if set(inputs) - allowed:
            raise ValueError('Unknown calibration input')
        if 'test_set_authorized' in inputs and type(inputs['test_set_authorized']) is not bool:
            raise ValueError('test_set_authorized must be a boolean')
        for key in ('spend_budget', 'price_per_1k_tokens'):
            value = inputs.get(key)
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError(key + ' must be a finite nonnegative number')
        if inputs.get('spend_budget') is not None and inputs.get('price_per_1k_tokens') is None:
            raise ValueError('spend_budget needs price_per_1k_tokens')
        adapter = inputs.get('adapter') or {}
        if set(adapter) - {'analyst_profile', 'implementer_profile', 'reviewer_profile', 'calibrator_profile',
                           'execution_timeout_seconds'}:
            raise ValueError('Only agent profiles and execution timeout may be selected in the browser')
        if 'execution_timeout_seconds' in adapter and (type(adapter['execution_timeout_seconds']) is not int
                or not 1 <= adapter['execution_timeout_seconds'] <= 86400):
            raise ValueError('执行时限必须是 1–86400 秒的整数')
        defaults = self.config.get('optimizer_v4') or {}
        config = dict(defaults, **{k: v for k, v in inputs.items()
                      if k not in ('spend_budget', 'price_per_1k_tokens', 'test_set_authorized', 'adapter')})
        config['adapter'] = dict(defaults.get('adapter') or {}, **adapter)
        config.setdefault('calibration_k', 3)
        config.setdefault('calibration_concurrency', 2)
        # The CLI config validator remains authoritative; validate before a confirmation is issued.
        from tempfile import NamedTemporaryFile
        with NamedTemporaryFile(mode='w', suffix='.json') as file:
            json.dump(config, file)
            file.flush()
            load(file.name)
        proposal.normalize_inputs(dict(time_budget_hours=config['time_budget_hours'],
            calibration_k=config['calibration_k'], safeguards=config.get('safeguards'),
            spend_budget=inputs.get('spend_budget'), price_per_1k_tokens=inputs.get('price_per_1k_tokens')))
        return config

    def _agent_profiles(self, settings, roles=('analyst', 'implementer', 'reviewer', 'calibrator')):
        from ..optimizer.v4.agent_profiles import resolve
        from .agent_creations import require_profile_binding
        return resolve(settings, lambda profile: require_profile_binding(self.config.get('agent_creation'), profile), roles)

    def _proposal(self, root):
        bundle = session.load_bundle(root / session.CALIBRATION_FILE)
        proposed = proposal.Proposal.from_dict(ledger_io.read_json(root / session.PROPOSAL_FILE))
        checked = proposal.validate(proposed, bundle['summary'], bundle['history'])
        if not checked['ok']:
            raise ValueError('Proposal contains invalid values: ' + str(checked['fatal']))
        return checked['proposal'], bundle

    def _snapshot(self, iteration_id, action, command, root):
        """Hash the concrete reviewed configuration and process state, not only a requested verb."""
        tree = self.api.service.iteration_record(iteration_id)
        owner = auto_runs.binding(self.repository, iteration_id)
        if owner and owner['actor'] != self.api.actor:
            raise Conflict('Continue this run using its original operator identity')
        launch = self._read(root, 'launch.json') or {}
        if action in ('calibrate', 'run', 'resume'):
            from ..optimizer.v4.calibration_import import require_complete
            require_complete(root)
        if command.recovery_execution_timeout_seconds is not None and action != 'calibrate':
            raise ValueError('恢复执行时限只用于校准')
        if action == 'calibrate':
            if self.busy(root):
                raise Conflict('An automatic process is already running')
            if (root / session.RUN_CONFIG_FILE).exists():
                raise Conflict('This run is frozen; calibration cannot be changed')
            config = self._calibration_input(command.inputs)
            if launch.get('inputs') is not None:
                if command.inputs != launch['inputs']:
                    raise Conflict('Resume calibration with its original inputs')
                config = self._read(root, 'calibration-input.json') or config
            config = dict(config, calibration_concurrency=session.calibration_concurrency(
                root, config.get('calibration_concurrency', 1)))
            profiles = self._agent_profiles(config.get('adapter'))
            if launch.get('inputs') is None:
                config['adapter'] = dict(config.get('adapter') or {}, **profiles)
            from ..optimizer.v4.calibration_recovery import configure
            from ..optimizer.v4.config import ADAPTER_DEFAULTS
            recovery_timeout = configure(root, command.recovery_execution_timeout_seconds, write=False)
            timeout = recovery_timeout or config['adapter'].get('execution_timeout_seconds',
                                                                ADAPTER_DEFAULTS['execution_timeout_seconds'])
            payload = dict(config=config, inputs=command.inputs, recovery_execution_timeout_seconds=recovery_timeout)
            summary = dict(action=action, config=config, test_set_authorized=bool(command.inputs.get('test_set_authorized')),
                           agent_profiles=profiles, execution_timeout_seconds=timeout,
                           recovery_execution_timeout_seconds=recovery_timeout,
                           incurs_cost=True, budget_hours=config['time_budget_hours'])
        elif action in ('run', 'resume'):
            if self.busy(root):
                raise Conflict('An automatic process is already running')
            state = self._read(root, 'state.json') or {}
            if state.get('halt_reason') not in (None, 'process_failure', 'budget_exhausted'):
                raise Conflict('This run has stopped permanently')
            proposed, bundle = self._proposal(root)
            profiles = self._agent_profiles(bundle['identity'].get('adapter_settings'), ('analyst', 'implementer', 'reviewer'))
            frozen = proposal.freeze(proposed, bundle['summary'], bundle['history'])
            _, groups = session._papers(self.api.service, iteration_id)
            search, validation = session.materialize_split(bundle, groups, frozen['config']['split']['validation_size'])
            payload = dict(proposal_sha256=session.canonical_hash(proposed.to_dict()),
                           calibration_sha256=bundle['payload_sha256'], frozen=frozen,
                           launch=launch, retry_failed=command.retry_failed,
                           saved_config={name: hashlib.sha256((root / name).read_bytes()).hexdigest()
                                         for name in ('run-config.json', 'resolved-config.json') if (root / name).exists()})
            summary = dict(action=action, config=frozen['config'], sources=frozen['sources'],
                agent_profiles=profiles,
                budget_hours=frozen['inputs']['time_budget_hours'], spend_budget=frozen['inputs'].get('spend_budget'),
                spend_scope='measurement estimate; agent costs excluded',
                test_set_authorized=bool(launch.get('test_set_authorized')), search_size=len(search),
                validation_size=len(validation), forecast=session.forecast(frozen, bundle), incurs_cost=True)
        else:
            record = self.process(root)
            if not record:
                raise Conflict('No owned process is available to signal')
            payload = dict(process=record)
            summary = dict(action=action, incurs_cost=False,
                           behavior='keep remote work for resume' if action == 'pause' else 'cancel remote work and stop')
        payload.update(iteration_id=iteration_id, actor=self.api.actor, action=action,
                       root_candidate=tree['root_candidate_id'])
        if action in ('calibrate', 'run', 'resume'):
            from .agent_preflight import AgentPreflightError
            if self.creation_api is None:
                raise AgentPreflightError('BRIDGE_UNAVAILABLE')
            self.creation_api.preflight(tree['task_id'], next(iter(profiles.values())))
            # Bind the confirmed IDs to their actual server definitions without
            # exposing provider settings or credentials in the public preview.
            from ..optimizer.v4.agent_profiles import binding_digest
            payload['agent_profiles_sha256'] = binding_digest(self.config['agent_creation'], profiles)
        return payload, summary

    def preview(self, iteration_id, action, command):
        self.api._write()
        root = self.directory(iteration_id)
        with self.control_lock(root):
            return self._preview_locked(iteration_id, action, command, root)

    def _preview_locked(self, iteration_id, action, command, root):
        payload, summary = self._snapshot(iteration_id, action, command, root)
        token = secrets.token_urlsafe(32)
        digest = session.canonical_hash(payload)
        ledger_io.atomic_write_json(root / 'confirmation.json', dict(token_hash=session.canonical_hash(token),
            config_sha256=digest, actor=self.api.actor, action=action, expires_at=time.time() + 600, consumed=False,
            agent_profiles_sha256=payload.get('agent_profiles_sha256')))
        return dict(summary, confirmation_token=token, config_sha256=digest, expires_in_seconds=600)

    def execute(self, iteration_id, action, command):
        self.api._write()
        root = self.directory(iteration_id)
        with self.control_lock(root):
            if not command.confirmation_token:
                return self._preview_locked(iteration_id, action, command, root)
            payload, summary = self._snapshot(iteration_id, action, command, root)
            digest = session.canonical_hash(payload)
            saved = self._read(root, 'confirmation.json') or {}
            if (saved.get('consumed') or saved.get('expires_at', 0) < time.time()
                    or saved.get('actor') != self.api.actor or saved.get('action') != action
                    or saved.get('config_sha256') != digest
                    or not secrets.compare_digest(saved.get('token_hash', ''), session.canonical_hash(command.confirmation_token))):
                raise Conflict('Confirmation expired or configuration changed; preview again')
            saved['consumed'] = True
            ledger_io.atomic_write_json(root / 'confirmation.json', saved)
            self._audit(iteration_id, action + '.confirmed', digest)
            try:
                if action in ('pause', 'stop'):
                    self._signal(payload['process'], signal.SIGINT if action == 'pause' else signal.SIGTERM)
                else:
                    bound = auto_runs.binding(self.repository, iteration_id)
                    run_id = bound['run_id'] if bound else 'v41-' + iteration_id
                    auto_runs.register(self.repository, iteration_id, run_id=run_id, state_dir=str(root.resolve()),
                                       actor=self.api.actor, method='method-v4')
                    if action == 'calibrate':
                        # Only the CLI writes the recovery receipt, after identity
                        # validation under the run lock (also serializes direct CLI use).
                        ledger_io.atomic_write_json(root / 'calibration-input.json', payload['config'])
                        ledger_io.atomic_write_json(root / 'launch.json', dict(
                            test_set_authorized=bool(command.inputs.get('test_set_authorized')), inputs=command.inputs))
                    self._launch(iteration_id, run_id, root, action, command)
            except Exception:
                self._audit(iteration_id, action + '.failed', digest)
                raise
            self._audit(iteration_id, action + '.requested', digest)
            return dict(accepted=True, action=action, config_sha256=digest)

    def _launch(self, iteration_id, run_id, root, action, command):
        config_path = self.config.get('_config_path')
        if not config_path or not Path(config_path).is_file():
            raise ValueError('Automatic launching requires a trusted server config file')
        argv = [sys.executable, '-m', 'rag_eval.optimizer.cli', 'v4', 'calibrate' if action == 'calibrate' else 'run',
                '--config', str(config_path), '--state', str(self.repository.root), '--iteration', iteration_id,
                '--actor', self.api.actor, '--run-id', run_id, '--state-dir', str(root.resolve())]
        expected = (self._read(root, 'confirmation.json') or {}).get('agent_profiles_sha256')
        if expected:
            argv += ['--expected-agent-profiles-sha256', expected]
        if action == 'calibrate':
            argv += ['--v4-config', str(root / 'calibration-input.json')]
            if command.recovery_execution_timeout_seconds is not None:
                argv += ['--recovery-execution-timeout-seconds', str(command.recovery_execution_timeout_seconds)]
            for key in ('spend_budget', 'price_per_1k_tokens'):
                if command.inputs.get(key) is not None:
                    argv += ['--' + key.replace('_', '-'), str(command.inputs[key])]
        else:
            argv += ['--from-proposal', str(root / session.PROPOSAL_FILE)]
            if not (self._read(root, 'launch.json') or {}).get('test_set_authorized'):
                argv += ['--no-test-set']
            if command.retry_failed:
                argv += ['--retry-failed']
        from ..optimizer.v4.startup import clear
        clear(root)  # before spawn: never remove a fast child's new failure receipt
        with (root / 'process.log').open('ab') as log:
            child = subprocess.Popen(argv, cwd=Path(__file__).resolve().parents[3],
                stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True,
                env=dict(os.environ, PYTHONUNBUFFERED='1'))
        identity = process_identity(child.pid)
        ledger_io.atomic_write_json(root / 'process.json', dict(pid=child.pid, identity=identity,
                                   action=action, started_at=time.time()))

    @staticmethod
    def _signal(record, sig):
        # pidfd pins the target even if it exits and its numeric PID gets reused.
        fd = os.pidfd_open(record['pid'])
        try:
            if process_identity(record['pid']) != record['identity']:
                raise Conflict('Process identity changed; refusing to signal')
            signal.pidfd_send_signal(fd, sig)
        finally:
            os.close(fd)

    def calibration(self, iteration_id):
        root = self.directory(iteration_id)
        path = root / session.CALIBRATION_FILE
        launch_inputs = (self._read(root, 'launch.json') or {}).get('inputs')
        process = self.process(root)
        elapsed_s = max(0, time.time() - process['started_at']) if process and process.get('started_at') else None
        from ..optimizer.v4.startup import read as read_startup_error
        startup_error = read_startup_error(root, auto_runs.binding(self.repository, iteration_id), alive=self.busy(root))
        if not path.exists():
            return dict(complete=False, alive=self.busy(root), startup_error=startup_error, elapsed_s=elapsed_s, launch_inputs=launch_inputs, phase='starting' if self.busy(root) else 'not_started')
        bundle = session._load(path)
        from ..optimizer.v4.calibration_recovery import configure
        from ..optimizer.v4.config import ADAPTER_DEFAULTS
        recovery_timeout = configure(root, write=False)
        execution_limits = dict(recovery_execution_timeout_seconds=recovery_timeout,
            execution_timeout_seconds=recovery_timeout or bundle['identity'].get('adapter_settings', {}).get(
                'execution_timeout_seconds', ADAPTER_DEFAULTS['execution_timeout_seconds']))
        if not bundle.get('complete'):
            return dict(complete=False, alive=self.busy(root), startup_error=startup_error, elapsed_s=elapsed_s, launch_inputs=launch_inputs, phase='calibration',
                        **execution_limits,
                        inputs=bundle['identity']['user_inputs'],
                        **session.scheduling_summary(bundle),
                        probe_size=len(bundle['probe']), diagnosis_size=len(bundle['diagnosis']),
                        completed_stages=list(bundle['results']))
        return dict(session.public_summary(bundle), alive=self.busy(root), startup_error=startup_error, elapsed_s=elapsed_s, launch_inputs=launch_inputs,
                    **execution_limits)

    def read_proposal(self, iteration_id):
        root = self.directory(iteration_id)
        proposed, bundle = self._proposal(root)
        return dict(proposal=proposed.to_dict(), forecast=simulate(proposed.params(), bundle['summary'], bundle['history']),
                    frozen=(root / session.RUN_CONFIG_FILE).exists())

    def edit_proposal(self, iteration_id, edit):
        self.api._write()
        root = self.directory(iteration_id)
        with self.control_lock(root):
            owner = auto_runs.binding(self.repository, iteration_id)
            if owner and owner['actor'] != self.api.actor:
                raise Conflict('Continue this run using its original operator identity')
            if self.busy(root) or (root / session.RUN_CONFIG_FILE).exists():
                raise Conflict('Proposal cannot change while running or after freeze')
            with hold_run_lock(root):
                proposed, bundle = self._proposal(root)
                proposed = proposal.apply_user_edit(proposed, edit.key, edit.value)
                checked = proposal.validate(proposed, bundle['summary'], bundle['history'])
                if not checked['ok']:
                    raise ValueError(str(checked['fatal']))
                ledger_io.atomic_write_json(root / session.PROPOSAL_FILE, checked['proposal'].to_dict())
            self._audit(iteration_id, 'proposal.edited', session.canonical_hash(proposed.to_dict()), parameter=edit.key)
        return self.read_proposal(iteration_id)

    def router(self):
        router = APIRouter(prefix='/api/iterations/{iteration_id}/v4')

        @router.post('/calibrate')
        def calibrate(iteration_id: str, command: Action):
            return self.execute(iteration_id, 'calibrate', command)

        @router.get('/calibration')
        def calibration(iteration_id: str):
            return self.calibration(iteration_id)

        @router.get('/proposal')
        def get_proposal(iteration_id: str):
            return self.read_proposal(iteration_id)

        @router.patch('/proposal')
        def edit_proposal(iteration_id: str, command: Edit):
            return self.edit_proposal(iteration_id, command)

        @router.post('/simulate')
        def simulation(iteration_id: str, command: Simulation):
            self.api._write()
            proposed, bundle = self._proposal(self.directory(iteration_id))
            return simulate(dict(proposed.params(), **command.params), bundle['summary'], bundle['history'])

        @router.post('/run/preview')
        def preview(iteration_id: str, command: Action):
            return self.preview(iteration_id, 'run', command)

        @router.post('/run')
        def run(iteration_id: str, command: Action):
            if not command.confirmation_token:
                raise Conflict('Preview the configuration and confirm before starting')
            return self.execute(iteration_id, 'run', command)

        @router.post('/run/resume')
        def resume(iteration_id: str, command: Action):
            return self.execute(iteration_id, 'resume', command)

        @router.post('/run/pause')
        def pause(iteration_id: str, command: Action):
            return self.execute(iteration_id, 'pause', command)

        @router.post('/run/stop')
        def stop(iteration_id: str, command: Action):
            return self.execute(iteration_id, 'stop', command)
        return router
