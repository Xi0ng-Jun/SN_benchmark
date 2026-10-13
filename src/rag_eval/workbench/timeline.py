"""Explicit scoped history pages and exact, content-bound source locators."""
from __future__ import annotations

import hashlib

from ..identity import fingerprint
from .repository import now
from .sessions import TaskSessionReader, _outcome


_PAGE_FIELDS = ('agentId', 'direction', 'projection', 'epoch', 'reset', 'staleCursor', 'gap',
                'window', 'startCursor', 'endCursor', 'hasOlder', 'hasNewer', 'mergeWindow')
_MAX_SEQ = 9007199254740991


def _sequence(value):
    return type(value) is int and 0 <= value <= _MAX_SEQ


def _metadata(raw):
    if (raw['direction'] not in ('tail', 'before', 'after')
            or raw['projection'] not in ('projected', 'canonical')
            or not isinstance(raw['epoch'], str) or not raw['epoch'].strip()
            or any(type(raw[key]) is not bool for key in ('reset', 'staleCursor', 'gap', 'hasOlder', 'hasNewer'))
            or (raw['mergeWindow'] is not None and type(raw['mergeWindow']) is not bool)):
        raise ValueError('Invalid timeline metadata')
    window = raw['window']
    if (not isinstance(window, dict) or set(window) != {'minSeq', 'maxSeq', 'nextSeq'}
            or any(not _sequence(window[key]) for key in window)):
        raise ValueError('Invalid timeline window')
    for key in ('startCursor', 'endCursor'):
        cursor = raw[key]
        if cursor is not None and (not isinstance(cursor, dict) or set(cursor) != {'epoch', 'seq'}
                                   or not isinstance(cursor['epoch'], str) or not cursor['epoch'].strip()
                                   or not _sequence(cursor['seq'])):
            raise ValueError('Invalid timeline cursor')


def _fragment(value):
    if value is None:
        return None, False
    if not isinstance(value, dict) or not isinstance(value.get('value'), str) or type(value.get('truncated')) is not bool:
        raise ValueError('Invalid timeline fragment')
    return value['value'], value['truncated']


def _entry(entry, agent_id, epoch, page_blocked):
    start, end = entry['seqStart'], entry['seqEnd']
    if not _sequence(start) or not _sequence(end) or start > end:
        raise ValueError('Invalid source sequence')
    turn_id = entry['turnId']
    if turn_id is not None and (not isinstance(turn_id, str) or not turn_id.strip()):
        raise ValueError('Invalid source turn')
    ranges = entry['sourceSeqRanges']
    if not isinstance(ranges, list) or len(ranges) > 1000:
        raise ValueError('Invalid source ranges')
    for r in ranges:
        if (not isinstance(r, dict) or type(r.get('startSeq')) is not int or type(r.get('endSeq')) is not int
                or not start <= r['startSeq'] <= r['endSeq'] <= end):
            raise ValueError('Invalid source range')
    item = entry['item']
    if not isinstance(item, dict) or not isinstance(item['type'], str) or not item['type']:
        raise ValueError('Invalid timeline item')
    kind = {'user_message': 'user', 'assistant_message': 'assistant'}.get(item['type'], 'activity')
    text, truncated = _fragment(item.get('text') if kind != 'activity' else item.get('title'))
    source_type = item.get('sourceType')
    activity_type, _ = _fragment(source_type) if source_type is not None else (item['type'], False)
    locator = {'epoch': epoch, 'seq_start': start, 'seq_end': end,
               'source_seq_ranges': [{'start_seq': r['startSeq'], 'end_seq': r['endSeq']} for r in ranges],
               'turn_id': turn_id, 'sha256': hashlib.sha256((text or '').encode('utf-8')).hexdigest()}
    identity = fingerprint({'agent_id': agent_id, 'kind': kind, 'type': item['type'],
                            'source_type': source_type, **locator})
    reason = page_blocked
    if kind != 'assistant':
        reason = '仅助手消息可作为候选来源'
    elif truncated:
        reason = '正文已截断，不能作为完整候选来源'
    elif text is None or not text.strip():
        reason = '此条目没有可引用正文'
    elif not ranges:
        reason = '来源序号范围缺失'
    elif end == _MAX_SEQ or len(epoch) > 200 or (turn_id is not None and len(turn_id) > 200):
        reason = '来源标识超出当前候选保存范围'
    return {'id': identity, 'label': f'序号 {start}–{end}', 'kind': kind, 'text': text,
            'truncated': truncated, 'activityType': activity_type if kind == 'activity' else None,
            'candidateBlockedReason': reason, 'source_locator': locator}


class TaskTimelineReader:
    def __init__(self, repository, bridge):
        self.scope_reader = TaskSessionReader(repository, bridge)
        self.bridge = bridge

    def read_page(self, task_id, session_id, *, direction=None, cursor=None):
        session, cwd = self.scope_reader.scope(task_id, session_id)
        if self.bridge is None:
            return {'result': {'status': 'unavailable', 'code': 'BRIDGE_NOT_CONFIGURED'},
                    'release': {'status': 'not_needed'}, 'observed_at': now()}
        command = {'op': 'readTimeline', 'agentId': session['agent_id'], 'expectedCwd': cwd,
                   'projection': 'projected', 'limit': 100}
        if direction is not None:
            command['direction'] = direction
        if cursor is not None:
            command['cursor'] = cursor
        envelope = self.bridge.call(command)
        raw = envelope['result']
        result = _outcome(raw)
        if raw['status'] == 'ok':
            try:
                if raw['agentId'] != session['agent_id'] or not isinstance(raw['epoch'], str) or not raw['epoch']:
                    raise ValueError('Invalid timeline scope')
                _metadata(raw)
                if not isinstance(raw['entries'], list) or len(raw['entries']) > 100:
                    raise ValueError('Invalid timeline page')
                result = {field: raw[field] for field in _PAGE_FIELDS}
                blocked = ('连接释放未确认，先核对读取状态' if envelope['release']['status'] != 'ok'
                           else '历史页存在间隙或过期游标，请重新读取' if raw['gap'] or raw['staleCursor'] else None)
                result.update(status='ok', entries=[_entry(e, session['agent_id'], raw['epoch'], blocked) for e in raw['entries']])
            except (KeyError, TypeError, ValueError):
                result = {'status': 'unavailable', 'code': 'BRIDGE_INVALID_RESPONSE'}
        return {'result': result, 'release': _outcome(envelope['release']), 'observed_at': now()}
