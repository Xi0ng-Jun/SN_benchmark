"""Lost write replies can be reconciled without executing another write."""
import pytest

from rag_eval.workbench.candidates import CandidateStore
from rag_eval.workbench.repository import WorkbenchRepository
from test_workbench_session_api import create_link, fixture

HEADERS = {'X-Workbench': '1'}


def save(repo, task, session, actor, key):
    return CandidateStore(repo).save(task['id'], session['id'],
        {'agent_id': session['agent_id'], 'epoch': 'epoch-example', 'seq_start': 1, 'seq_end': 1, 'text': 'Original'},
        kind='finding', text='Candidate', actor=actor, request_key=key)


def test_candidate_receipt_is_actor_and_session_scoped_and_never_writes(tmp_path):
    with fixture(tmp_path) as client:
        task, session = create_link(client)
        repo = WorkbenchRepository(tmp_path / 'state')
        saved = save(repo, task, session, 'alice', 'lost-save')
        save(repo, task, session, 'bob', 'other-actor')
        other = client.post('/api/tasks/' + task['id'] + '/sessions',
                            json={'agent_id': 'agent-2', 'request_key': 'other-session'}, headers=HEADERS).json()
        path = f"/api/tasks/{task['id']}/sessions/{session['id']}/candidate-receipt"
        before = repo.task(task['id'])['history']
        response = client.post(path, json={'request_key': 'lost-save'}, headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {'status': 'ok', 'receipt': saved}
        for key in ('absent-or-in-flight', 'other-actor'):
            assert client.post(path, json={'request_key': key}, headers=HEADERS).json() == {'status': 'missing'}
        assert client.post(path.replace(session['id'], other['id']),
                           json={'request_key': 'lost-save'}, headers=HEADERS).status_code == 422
        assert client.post(path.replace(session['id'], 'foreign'),
                           json={'request_key': 'absent'}, headers=HEADERS).status_code == 422
        assert client.post(path, json={'request_key': 'lost-save', 'actor': 'bob'}, headers=HEADERS).status_code == 422
        assert repo.task(task['id'])['history'] == before


def test_decision_receipt_is_candidate_and_task_scoped_without_head_rewrite(tmp_path):
    with fixture(tmp_path) as client:
        task, session = create_link(client)
        repo = WorkbenchRepository(tmp_path / 'state')
        store = CandidateStore(repo)
        first = save(repo, task, session, 'alice', 'first')
        second = save(repo, task, session, 'alice', 'second')
        ack = store.decide(task['id'], first['id'], judgment='confirmed', reason='Checked', actor='alice',
                           base_revision=None, request_key='lost-decision')
        later = store.decide(task['id'], first['id'], judgment='rejected', reason='Rechecked', actor='alice',
                             base_revision=ack['id'], request_key='later')
        other_task = repo.create_task(task['workspace_id'], 'Other', '', request_key='other')
        path = f"/api/tasks/{task['id']}/candidates/{first['id']}/decision-receipt"
        before = repo.task(task['id'])['history']
        response = client.post(path, json={'request_key': 'lost-decision'}, headers=HEADERS)
        assert response.status_code == 200
        assert response.json() == {'status': 'ok', 'receipt': ack}, 'an old receipt is not the current actor head'
        assert client.post(path, json={'request_key': 'absent-or-in-flight'}, headers=HEADERS).json() == {'status': 'missing'}
        assert client.post(path.replace(first['id'], second['id']), json={'request_key': 'lost-decision'}, headers=HEADERS).status_code == 422
        assert client.post(path.replace(task['id'], other_task['id']), json={'request_key': 'lost-decision'}, headers=HEADERS).status_code == 422
        assert client.post(path, json={'request_key': 'lost-decision', 'base_revision': None}, headers=HEADERS).status_code == 422
        assert repo.task(task['id'])['history'] == before
        assert store.list(task['id'])[0]['decision']['id'] == later['id']


@pytest.mark.parametrize('operation', ['candidate', 'decision'])
def test_receipt_lookup_preserves_the_exact_opaque_request_key(tmp_path, operation):
    with fixture(tmp_path) as client:
        task, session = create_link(client)
        repo = WorkbenchRepository(tmp_path / 'state')
        store = CandidateStore(repo)
        candidate = save(repo, task, session, 'alice', ' key ')
        if operation == 'candidate':
            spaced = candidate
            path = f"/api/tasks/{task['id']}/sessions/{session['id']}/candidate-receipt"
            spaced_key, plain_key = ' key ', 'key'
        else:
            spaced = store.decide(task['id'], candidate['id'], judgment='confirmed', reason='First', actor='alice',
                                  base_revision=None, request_key=' decision ')
            path = f"/api/tasks/{task['id']}/candidates/{candidate['id']}/decision-receipt"
            spaced_key, plain_key = ' decision ', 'decision'
        initial = client.post(path, json={'request_key': spaced_key}, headers=HEADERS).json()
        if operation == 'candidate':
            plain = save(repo, task, session, 'alice', plain_key)
        else:
            plain = store.decide(task['id'], candidate['id'], judgment='rejected', reason='Second', actor='alice',
                                 base_revision=spaced['id'], request_key=plain_key)
        before = repo.task(task['id'])['history']
        after = client.post(path, json={'request_key': spaced_key}, headers=HEADERS).json()
        bare = client.post(path, json={'request_key': plain_key}, headers=HEADERS).json()
        assert initial == {'status': 'ok', 'receipt': spaced}
        assert after == {'status': 'ok', 'receipt': spaced}, 'similar keys identify different operations'
        assert bare == {'status': 'ok', 'receipt': plain}
        assert spaced['id'] != plain['id']
        assert repo.task(task['id'])['history'] == before
