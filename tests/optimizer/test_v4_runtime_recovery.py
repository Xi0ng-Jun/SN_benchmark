"""A resumable coordinator halt must preserve an unfinished remote Agent."""
import threading
from types import SimpleNamespace

from rag_eval.optimizer.adapter import EvaluationUnavailable
from rag_eval.optimizer.sn_adapter import SNAdapter
from rag_eval.optimizer.v4.config import V4Config
from rag_eval.optimizer.v4.loop import V4Run

from .v4_mock import V4Mock


def test_process_failure_preserves_unfinished_implementer_for_same_key_resume(tmp_path):
    class Mock(V4Mock):
        def __init__(self):
            super().__init__([[dict(name='bad', delta=.1), dict(name='slow', delta=.2)]])
            self.entered = threading.Event()
            self.archived = False
            self.sessions = 0
            self.agent_keys = []
            operations = SimpleNamespace(
                agent=self.agent,
                sessions=SimpleNamespace(
                    scope=lambda *args: (dict(agent_id='owned-implementer'), '/synthetic'),
                    bridge=SimpleNamespace(call=self.archive)))
            self.sn = SNAdapter(dict(run_id='recovery', actor='alice', task_id='task'),
                                round_operations=operations, agent_poll_interval=.01)

        def agent(self, role, candidate_id, **kwargs):
            self.agent_keys.append(kwargs['key'])
            self.entered.set()
            if self.archived:
                return dict(status='failed', session_id='session', reason='agent_archived')
            return dict(status='idle' if self.sessions > 1 else 'running', session_id='session')

        def archive(self, request):
            assert request == dict(op='archiveAgent', agentId='owned-implementer')
            self.archived = True
            return dict(result={})

        def configure_runtime(self, **kwargs):
            self.sessions += 1
            self.sn.configure_runtime(**kwargs)

        def check_runtime(self):
            self.sn.check_runtime()

        def cancel_pending(self):
            self.sn.cancel_pending()

        def stop_polling(self):
            self.sn.stop_polling()

        def implement(self, card, workspace):
            if card.failure_mode_desc == 'slow':
                self.sn._poll_agent('implementer', 'slow', dict(key='same-implementer-key'))
            return super().implement(card, workspace)

        def evaluate_subset(self, version, reference, subset, task_ids, k):
            if version.content_hash == 'seed+r1-bad' and subset == 'rest':
                assert self.entered.wait(.5)
            return super().evaluate_subset(version, reference, subset, task_ids, k)

    mock = Mock()
    mock.fail_once['rest:bad'] = EvaluationUnavailable('temporary measurement outage')
    cfg = V4Config(time_budget_hours=1, b=2, merge=False, ablation=False,
                   speculative_analysis=False, no_adoption_rounds=1,
                   max_consecutive_run_failures=1)

    def coordinator():
        return V4Run(mock, mock.seed, cfg, search=mock.search, validation=mock.validation,
                     state_dir=tmp_path)

    first = coordinator()
    assert first.run() == 'process_failure'
    assert not mock.archived
    assert first.current['records']['1']['stage'] == 'pending'
    second = coordinator()
    assert second.run() == 'no_adoption'
    assert second.lineage[-1]['version'] == 'seed+r1-slow'
    assert second.rounds[0]['records']['1']['stage'] == 'measured'
    assert set(mock.agent_keys) == {'same-implementer-key'}
