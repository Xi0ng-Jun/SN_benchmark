"""Browser locators select SDK evidence; they never supply authoritative text."""
import copy

import pytest

from rag_eval.workbench.repository import Conflict
from test_workbench_timeline import page, setup_timeline


def setup_source(tmp_path):
    from rag_eval.workbench.sourced_candidates import SourcedCandidates
    repo, task, session, bridge, timeline = setup_timeline(tmp_path, page())
    entry = timeline.read_page(task['id'], session['id'])['result']['entries'][1]
    return repo, task, session, bridge, SourcedCandidates(repo, timeline), entry['source_locator']


def test_candidate_source_is_reread_from_sdk_and_keeps_exact_origin(tmp_path):
    repo, task, session, bridge, service, locator = setup_source(tmp_path)
    saved = service.save(task['id'], session['id'], locator, kind='finding', text='Check this', actor='alice', request_key='save')
    assert bridge.calls[-1]['cursor'] == {'epoch': 'epoch-example', 'seq': 14}
    assert saved['source']['text'] == 'Example read.'
    assert saved['source']['sha256'] == locator['sha256']
    assert saved['source']['source_seq_ranges'] == locator['source_seq_ranges']
    assert saved['origin'] == 'agent' and saved['selected_by'] == 'alice'
    assert 'judgment' not in saved
    assert repo.task(task['id'])['history'][-1]['kind'] == 'candidate.saved'


def test_changed_hash_role_ranges_epoch_or_release_cannot_create_candidate(tmp_path):
    repo, task, session, bridge, service, locator = setup_source(tmp_path)
    changed = page()
    changed['result']['entries'][1]['item']['text']['value'] = 'changed after selection'
    user = page()
    user['result']['entries'][1]['item']['type'] = 'user_message'
    ranges = page()
    ranges['result']['entries'][1]['sourceSeqRanges'] = [{'startSeq': 11, 'endSeq': 13}]
    epoch = page()
    epoch['result']['epoch'] = 'replacement'
    release = page()
    release['release'] = {'status': 'unavailable', 'code': 'CLOSE_FAILED'}
    for response in (changed, user, ranges, epoch, release):
        bridge.response = response
        with pytest.raises(Conflict):
            service.save(task['id'], session['id'], locator, kind='finding', text='Check', actor='alice', request_key='save')
    assert not any(e['kind'] == 'candidate.saved' for e in repo.task(task['id'])['history'])


def test_saved_receipt_survives_remote_loss_but_reused_key_cannot_change_selection(tmp_path):
    _, task, session, bridge, service, locator = setup_source(tmp_path)
    args = dict(kind='direction', text='Check mapping', actor='alice', request_key='save')
    saved = service.save(task['id'], session['id'], locator, **args)
    bridge.response = {'result': {'status': 'unavailable', 'code': 'READ_FAILED'}, 'release': {'status': 'ok'}}
    reads = len(bridge.calls)
    assert service.save(task['id'], session['id'], locator, **args) == saved
    assert len(bridge.calls) == reads
    altered = copy.deepcopy(locator)
    altered['sha256'] = '0' * 64
    for changes in ({'text': 'Different'}, {'kind': 'next_step'}):
        with pytest.raises(Conflict):
            service.save(task['id'], session['id'], locator, **{**args, **changes})
    with pytest.raises(Conflict):
        service.save(task['id'], session['id'], altered, **args)


def test_browser_cannot_supply_source_text_or_foreign_session(tmp_path):
    _, task, session, bridge, service, locator = setup_source(tmp_path)
    reads = len(bridge.calls)
    for target, supplied in (('foreign', locator), (session['id'], {**locator, 'text': 'forged SDK message'})):
        with pytest.raises(ValueError):
            service.save(task['id'], target, supplied, kind='finding', text='Check', actor='alice', request_key='save')
    assert len(bridge.calls) == reads
