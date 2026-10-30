"""cluster result --wait: one in-process wait instead of shell sleep loops, read like a normal read."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from rag_eval.workbench import agent_tools
from rag_eval.workbench.comparisons import ComparisonService
from test_workbench_clusters import frozen_cluster, env  # noqa: F401
from test_workbench_iterations import BUDGET


class Clock:
    """Fake time: sleeping advances it; ``on_sleep`` lets the outside world change meanwhile."""
    def __init__(self, on_sleep=lambda clock: None):
        self.now, self.sleeps, self.on_sleep = 0.0, [], on_sleep

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        assert seconds > 0
        self.sleeps.append(seconds)
        self.now += seconds
        self.on_sleep(self)


@pytest.fixture
def dispatched(frozen_cluster, monkeypatch, capsys):
    f = frozen_cluster
    service, candidate = f['service'], f['candidate']['id']
    (Path(f['candidate']['worktree']['path']) / 'backend/app/__init__.py').write_text('VALUE = 3\n')
    service.freeze(candidate, actor='test')
    assert service.cluster_run(candidate, 'c01', budget=BUDGET, actor='optimizer', request_key='cluster')['status'] == 'dispatched'
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (f['env']['repo'], f['views']))
    monkeypatch.setattr(agent_tools, 'iteration_service', lambda *a, **k: service)

    def finish_all():
        tree = f['tree']
        for cid, scores in [(tree['root_candidate_id'], {'qasper:q': 0, 'qasper:q2': 0}),
                            (candidate, {'qasper:q': 1, 'qasper:q2': 0})]:
            for a in service.candidate(cid)['attempts']:
                if a['batch_id'] == 'cluster:c01' and a['execution_state'] == 'running':
                    f['views'].scores[a['execution_id']] = scores
                    f['views'].partitions[a['execution_id']] = [tuple(scores)]
                    f['env']['executions']._finish(a['execution_id'], state='finished', exit_code=0,
                                                   error_type=None, run_state={'phase': 'finished'})

    def run(clock, *extra):
        monkeypatch.setattr(agent_tools, 'time', SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
        capsys.readouterr()
        agent_tools.main(['--task', f['tree']['task_id'], '--role', 'implementer', '--candidate', candidate,
                          'cluster', 'result', 'c01', *extra])
        return json.loads(capsys.readouterr().out)

    def recorded():
        with f['env']['repo'].connect() as conn:
            return conn.execute("SELECT id FROM candidate_comparisons WHERE candidate_id=? AND kind='cluster'",
                                (candidate,)).fetchone() is not None
    return SimpleNamespace(f=f, service=service, candidate=candidate, finish_all=finish_all, run=run, recorded=recorded)


def test_wait_returns_when_the_state_settles_and_counts_as_a_read(dispatched):
    d = dispatched
    clock = Clock(lambda c: d.finish_all() if len(c.sleeps) == 3 else None)
    out = d.run(clock, '--wait', '1200')
    assert out['state'] == 'admitted' and out['status'] == 'admitted'
    assert clock.sleeps == [10, 10, 10] and out['waited_seconds'] == 30
    assert [m['flip'] for m in out['members']] == ['better', 'same']
    assert not any(x in json.dumps(out) for x in ['qasper:', 'question', 'gold', d.f['secret']])
    # The round's cluster_result_not_read check now finds the stored read (loop_services.implementation()).
    assert d.recorded()
    candidate, parent = d.service.candidate(d.candidate), d.service.candidate(d.service.candidate(d.candidate)['parent_candidate_id'])
    current = ComparisonService(d.f['env']['repo'], d.service, d.f['views'])._stored(
        ComparisonService.scoped(candidate, 'cluster:c01'), ComparisonService.scoped(parent, 'cluster:c01'),
        'cluster', 'round', store=False)
    assert current.get('id')
    # Without --wait the output is the same projection.
    plain = d.run(Clock())
    assert plain == {k: v for k, v in out.items() if k != 'waited_seconds'}


def test_wait_times_out_with_the_current_state_and_never_sleeps_past_it(dispatched):
    d = dispatched
    clock = Clock()
    out = d.run(clock, '--wait', '25')
    assert out['state'] == 'pending' and out['waited_seconds'] == 25
    assert clock.sleeps == [10, 10, 5] and clock.now == 25
    assert not d.recorded()


def test_wait_is_capped_and_only_for_the_implementer_cluster_result(dispatched, capsys):
    d = dispatched
    clock = Clock()
    assert d.run(clock, '--wait', '99999')['waited_seconds'] == agent_tools.WAIT_CAP
    assert sum(clock.sleeps) == agent_tools.WAIT_CAP
    for argv in (['--role', 'implementer', '--candidate', d.candidate, 'cluster', 'show', 'c01', '--wait', '5'],
                 ['--node', d.candidate, 'cluster', 'result', 'c01', '--wait', '5'],
                 ['--role', 'implementer', '--candidate', d.candidate, 'cluster', 'result', 'c01', '--wait', '-1']):
        with pytest.raises(SystemExit):
            agent_tools.main(['--task', d.f['tree']['task_id'], *argv])


def test_wait_returns_at_once_when_nothing_was_dispatched(frozen_cluster, monkeypatch, capsys):
    f = frozen_cluster
    service, candidate = f['service'], f['candidate']['id']
    monkeypatch.setattr(agent_tools, 'build', lambda state, config: (f['env']['repo'], f['views']))
    monkeypatch.setattr(agent_tools, 'iteration_service', lambda *a, **k: service)
    clock = Clock()
    monkeypatch.setattr(agent_tools, 'time', SimpleNamespace(monotonic=clock.monotonic, sleep=clock.sleep))
    agent_tools.main(['--task', f['tree']['task_id'], '--role', 'implementer', '--candidate', candidate,
                      'cluster', 'result', 'c01', '--wait', '1200'])
    out = json.loads(capsys.readouterr().out)
    assert out['state'] == 'not_started' and clock.sleeps == [] and out['waited_seconds'] == 0
