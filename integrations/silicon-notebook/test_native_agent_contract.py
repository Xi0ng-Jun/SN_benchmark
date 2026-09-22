"""Explicit paired-repository check. Set SN_EVALUATION_SOURCE to the SN worktree.

Uses the actual optional tracing module and SDK, with local business/judge fakes.
It never loads SN model services or opens a connection.
"""
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from contextvars import copy_context
import importlib.util
import json
import os
from pathlib import Path
import socket

import pytest


@pytest.mark.parametrize('judge_fails', [False, True])
def test_native_producer_consumer_contract(tmp_path, monkeypatch, judge_fails):
    monkeypatch.chdir(tmp_path)
    def blocked(*args, **kwargs):
        raise AssertionError('Offline integration must not access the network')
    monkeypatch.setattr(socket.socket, 'connect', blocked)
    monkeypatch.setattr(socket, 'create_connection', blocked)
    source = Path(os.environ['SN_EVALUATION_SOURCE']) / 'backend/app/core/evaluation_tracing.py'
    monkeypatch.syspath_prepend(str(source.parents[2]))
    spec = importlib.util.spec_from_file_location('sn_native_contract', source)
    sn = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(sn)
    from deepeval.models import DeepEvalBaseLLM
    from rag_eval.native_agent import NativeAgentEvaluation, NATIVE_TRACE_VERSION
    assert sn.NATIVE_TRACE_VERSION == NATIVE_TRACE_VERSION
    persisted = tmp_path / 'answer.json'
    invocations = []

    class Judge(DeepEvalBaseLLM):
        def load_model(self):
            return self
        def get_model_name(self):
            return 'local-contract-judge'
        @contextmanager
        def for_case(self, case_id, request_id):
            yield self
        def generate(self, prompt, schema=None):
            assert persisted.is_file()
            assert (tmp_path / 'agent/native-traces.jsonl').is_file()
            if judge_fails:
                raise RuntimeError('expected local judge failure')
            fields = schema.model_fields
            if 'verdicts' in fields:
                return schema.model_validate({'verdicts': [{'verdict': 'yes', 'statement': 'Ada wrote notes.'}]})
            for key in ('truths', 'claims', 'statements'):
                if key in fields:
                    return schema.model_validate({key: ['Ada wrote notes.']})
            if 'reason' in fields:
                return schema.model_validate({'reason': 'Supported by this evidence.'})
            raise AssertionError(str(fields))
        async def a_generate(self, prompt, schema=None):
            raise AssertionError('This integration must stay synchronous')

    @sn.traced_evaluation('sn.retrieve.chunks', 'retriever', stage='retrieve',
                         input_selector=lambda b: {'query': b['query']}, output_selector=lambda value: value)
    def retrieve(query, opaque_client):
        invocations.append('retrieve')
        return [{'chunk_id': 'c1', 'source_id': 's1', 'text': 'Ada wrote notes.'}]

    @sn.traced_evaluation('sn.synthesis.chunks', 'agent', stage='synthesis',
                         input_selector=lambda b: {'question': b['question']},
                         output_selector=lambda answer: {'answer': answer})
    def synthesize(question, actual_context):
        invocations.append('synthesis')
        sn.set_evaluation_metadata(question=question, context_block=actual_context)
        return 'Ada wrote notes.'

    def product():
        with ThreadPoolExecutor(max_workers=1) as pool:
            evidence = pool.submit(copy_context().run, retrieve, 'Who wrote notes?', object()).result()
        answer = synthesize('Who wrote notes?', evidence[0]['text'])
        row = {'status': 'success', 'answer': answer, 'usage': {'total_tokens': 12}}
        persisted.write_text(json.dumps(row))
        return row

    runner = NativeAgentEvaluation(tmp_path / 'agent', judge=Judge('fixture'),
                                   judge_identity={'model_id': 'local-contract-judge'})
    result = runner.run_case(case_id='native-contract', mode='chunk', question='Who wrote notes?',
                             invoke_and_persist=product, session_factory=sn.evaluation_session)
    assert result == json.loads(persisted.read_text())
    assert invocations == ['retrieve', 'synthesis']
    assert runner.has_errors is judge_fails
    samples = [json.loads(line) for line in (tmp_path / 'agent/components.jsonl').read_text().splitlines()]
    samples = [row for row in samples if row['record_type'] == 'sample']
    assert len(samples) == 2 and all(row['span_id'] for row in samples)
    assert all(row['retrieval_context'] == ['Ada wrote notes.'] for row in samples)
    trace = json.loads((tmp_path / 'agent/native-traces.jsonl').read_text().splitlines()[-1])['trace']
    def flatten(nodes):
        return [item for node in nodes for item in [node, *flatten(node['children'])]]
    spans = flatten(trace['root_spans'])
    retrieval = next(span for span in spans if span['name'] == 'sn.retrieve.chunks')
    assert retrieval['parent_uuid'] in {span['uuid'] for span in spans}
    scores = [json.loads(line) for line in (tmp_path / 'agent/native-scores.jsonl').read_text().splitlines()]
    component_scores = [row for row in scores if row.get('grouping') == 'native_span']
    assert len(component_scores) == 3
    assert {row['status'] for row in component_scores} == ({'error'} if judge_fails else {'scored'})
