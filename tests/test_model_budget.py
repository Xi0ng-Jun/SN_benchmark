import json
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest

from rag_eval.model_budget import ModelBudget, BudgetStopped, install_budget_guard


def test_reservations_share_limit_and_unknown_usage_is_not_free(tmp_path):
    path = tmp_path / 'budget.json'
    ModelBudget.create(path, limit_cny='0.11')
    budget = ModelBudget(path)
    payload = {'model': 'qwen-turbo', 'messages': [{'content': 'private prompt'}]}
    with ThreadPoolExecutor(4) as pool:
        results = list(pool.map(lambda _: budget.reserve(payload), range(2)))
    assert len(results) == 2
    with pytest.raises(BudgetStopped):
        budget.reserve(payload)
    budget.settle(results[0], {'prompt_tokens': 10, 'completion_tokens': 20})
    assert budget.reserve(payload)
    saved = path.read_text()
    assert 'private prompt' not in saved
    assert json.loads(saved)['requests'][results[1]]['status'] == 'reserved'


def test_unknown_model_never_reaches_transport(tmp_path):
    path = tmp_path / 'budget.json'
    ModelBudget.create(path, limit_cny='10')
    called = []
    with install_budget_guard(path):
        with httpx.Client(transport=httpx.MockTransport(lambda r: called.append(r))) as client:
            with pytest.raises(BudgetStopped):
                client.post('https://example.invalid', json={'model': 'not-approved'})
    assert called == []


def test_stream_is_byte_identical_and_settled_only_after_clean_eof(tmp_path):
    path = tmp_path / 'budget.json'
    ModelBudget.create(path, limit_cny='10')
    chunks = [b'data: {"choices":[]}\n\n',
              b'data: {"usage":{"prompt_tokens":100,"completion_tokens":200}}\n\n', b'data: [DONE]\n\n']
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield from chunks
    with install_budget_guard(path):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Stream()))) as client:
            with client.stream('POST', 'https://example.invalid', json={'model': 'qwen-turbo', 'messages': []}) as response:
                assert b''.join(response.iter_bytes()) == b''.join(chunks)
    row, = json.loads(path.read_text())['requests'].values()
    assert row['status'] == 'observed'
    assert row['cost_cny'] == pytest.approx(.00063)


def test_partial_stream_and_malformed_usage_keep_upper_reservation(tmp_path):
    path = tmp_path / 'budget.json'
    ModelBudget.create(path, limit_cny='10')
    budget = ModelBudget(path)
    rid = budget.reserve({'model': 'text-embedding-v4', 'input': ['x', 'y']})
    budget.settle(rid, {'prompt_tokens': -1})
    assert json.loads(path.read_text())['requests'][rid]['status'] == 'reserved'
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"usage":{"prompt_tokens":1,"completion_tokens":1}}\n\n'
            raise httpx.ReadError('private address')
    with install_budget_guard(path):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Stream()))) as client:
            with pytest.raises(httpx.ReadError):
                client.post('https://example.invalid', json={'model': 'qwen-turbo'})
    assert all(r['status'] == 'reserved' for r in json.loads(path.read_text())['requests'].values())


def test_sdk_can_stop_at_done_without_reading_http_eof(tmp_path):
    path = tmp_path / 'budget.json'
    ModelBudget.create(path, limit_cny='10')
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield b'data: {"usage":{"prompt_tokens":1,"completion_tokens":2}}\n\n'
            yield b'data: [DONE]\n\n'
            raise AssertionError('SDK does not read past DONE')
    with install_budget_guard(path):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, stream=Stream()))) as client:
            with client.stream('POST', 'https://example.invalid', json={'model': 'qwen-turbo'}) as response:
                for chunk in response.iter_bytes():
                    if b'[DONE]' in chunk:
                        break
    row, = json.loads(path.read_text())['requests'].values()
    assert row['status'] == 'observed'


def test_gzip_sse_usage_is_observed_without_changing_decoder(tmp_path):
    import gzip
    path = tmp_path / 'budget.json'
    ModelBudget.create(path, limit_cny='10')
    data = b'data: {"usage":{"prompt_tokens":3,"completion_tokens":4}}\n\ndata: [DONE]\n\n'
    class Stream(httpx.SyncByteStream):
        def __iter__(self):
            yield gzip.compress(data)
    with install_budget_guard(path):
        with httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, headers={'content-encoding': 'gzip'}, stream=Stream()))) as client:
            with client.stream('POST', 'https://example.invalid', json={'model': 'qwen-turbo'}) as response:
                assert b''.join(response.iter_bytes()) == data
    row, = json.loads(path.read_text())['requests'].values()
    assert row['status'] == 'observed'
