"""Server default/profile mismatch must fail before model work."""
import json

import pytest

from rag_eval.optimizer.v4 import advisor, cli
from .test_v4_rehearsal import rig4, env, eight_papers  # noqa: F401


def restricted(rig):
    binding = rig.creation.store._bindings[rig.env['workspace']['id']]
    profile = binding['profiles'].pop('pi-flash')
    binding['profiles']['server-model'] = profile
    return binding


def test_cli_default_mismatch_refused_before_measurement(rig4, monkeypatch):
    from test_workbench_iterations import iteration
    rig4.tree = iteration(rig4.env, request_key='preflight', case_ids=rig4.case_ids, split_mode='deferred')
    restricted(rig4)
    # Original code starts measurements; do not let a regression wait for work.
    from rag_eval.optimizer.v4.sn_adapter_v4 import SNAdapterV4
    def forbidden(*a, **kw):
        pytest.fail('measurement launched before profile validation')
    monkeypatch.setattr(SNAdapterV4, 'evaluate_subset', forbidden)
    assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config)) == 2
    assert not (rig4.run_dir / 'advisor-intent.json').exists()
    assert rig4.creator.commands == []


def test_cli_checks_approved_profile_definitions_from_actual_loaded_config(rig4, monkeypatch, tmp_path):
    from test_workbench_iterations import iteration
    from rag_eval.optimizer.v4.agent_profiles import binding_digest, resolve
    from rag_eval.optimizer.v4.sn_adapter_v4 import SNAdapterV4
    rig4.tree = iteration(rig4.env, request_key='profile-file-change', case_ids=rig4.case_ids, split_mode='deferred')
    config_path = tmp_path / 'server.json'
    config_path.write_text(json.dumps(rig4.config))
    binding = rig4.config['agent_creation']
    profiles = resolve({}, lambda p: None)
    expected = binding_digest(binding, profiles)
    changed = json.loads(config_path.read_text())
    changed['agent_creation']['profiles']['pi-flash']['provider'] = 'codex/changed-on-disk'
    config_path.write_text(json.dumps(changed))
    build = rig4._build4
    def reload_config(*args, **kwargs):
        services = build(*args, **kwargs)
        services.adapter._config = json.loads(config_path.read_text())
        return services
    monkeypatch.setattr(cli, 'build_services', reload_config)
    monkeypatch.setattr(SNAdapterV4, 'evaluate_subset', lambda *a, **k: pytest.fail('unapproved model work'))
    assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config),
        '--expected-agent-profiles-sha256', expected) == 2
    assert rig4.creator.commands == []


@pytest.mark.parametrize('profile', ['pi-flash', 'server-model'])
def test_real_creation_path_rejects_default_and_accepts_registered_profile(rig4, monkeypatch, profile):
    from test_workbench_iterations import iteration
    from test_workbench_loop_services import SAFEGUARDS
    rig4.tree = iteration(rig4.env, request_key='advisor-profile', case_ids=rig4.case_ids,
        split_mode='deferred', gate_rule='lcd-v2', safeguards=SAFEGUARDS, k_base=2)
    rig4.protocol = rig4.tree['protocol']
    rig4.task_id = rig4.tree['task_id']
    rig4.role = {task: 'search' for task in rig4.case_ids}
    with rig4:
        assert rig4.v4('calibrate', '--v4-config', str(rig4.v4_config), '--no-advisor') == 0
    restricted(rig4)
    settings = cli.load(rig4.v4_config).adapter_settings
    services = rig4._build4(None, None, rig4.tree['id'], actor='operator', run_id='r4', adapter_settings=settings)
    adapter = services.adapter
    root = adapter.version_for(rig4.tree['root_candidate_id'])
    def finish_agent(seconds):
        rig4.reads.done.update(c['agentId'] for c in rig4.creator.commands)
        (rig4.run_dir / 'proposal.agent.json').write_text(json.dumps(dict(values={}, message='synthetic advice', failure_modes=[])))
    monkeypatch.setattr(adapter, '_runtime_wait', finish_agent)
    out = advisor.run(adapter, root, rig4.run_dir, profile=profile)
    if profile == 'server-model':
        assert out.notes['advisor']['status'] == 'complete'
        assert out.notes['advisor']['message'] == 'synthetic advice'
        assert len(rig4.creator.commands) == len(rig4.messenger.commands) == 1
        return
    assert out.notes['advisor']['reason'] == 'agent_profile_not_allowed'
    assert out.notes['advisor']['stage'] == 'agent_request'
    assert '模型配置' in out.notes['advisor']['message']
    assert not (rig4.run_dir / 'proposal.agent.json').exists()
    assert rig4.creator.commands == []
    with rig4.repo.connect() as conn:
        assert conn.execute('SELECT count(*) FROM agent_creation_intents').fetchone()[0] == 0
    # Valid profile crosses the real creation boundary (fake bridge only).
    intent = rig4.creation.store.prepare(rig4.task_id, 'server-model', actor='operator',
        request_key='valid-calibrator', role='calibrator', candidate_id=adapter.candidate_id(root), brief='method-v4')
    assert intent['profile_id'] == 'server-model'
