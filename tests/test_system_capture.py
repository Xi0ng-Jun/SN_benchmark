import pytest


def test_capture_keeps_delivered_context_when_synthesis_fails():
    from rag_eval.system_capture import capture_synthesis

    class Service:
        def _answer_chunks(self, question, baseline_sink=None):
            baseline_sink.update(context_block='k1: actual evidence', id_map={'k1': {'source_id': 's1', 'object_id': 'c1'}})
            raise ValueError('provider failure')

    service = Service()
    original = service._answer_chunks
    with capture_synthesis(service) as calls:
        with pytest.raises(ValueError):
            service._answer_chunks('q')
    assert calls[0]['context_block'] == 'k1: actual evidence'
    assert calls[0]['succeeded'] is False
    assert service._answer_chunks == original


def test_multiple_sections_are_not_silently_unioned_for_faithfulness():
    from rag_eval.system_capture import final_context

    calls = [dict(context_block='k1: a', id_map={}, succeeded=True, sectioned=True, answer='a'),
             dict(context_block='k10001: b', id_map={}, succeeded=True, sectioned=True, answer='b')]
    result = final_context(calls)
    assert result['context_supported'] is False
    assert result['retrieval_context'] == []


def test_successful_retry_uses_last_actual_context_not_failed_attempt():
    from rag_eval.system_capture import final_context

    calls = [dict(context_block='k1: old', id_map={'k1': {'source_id': 's0'}}, succeeded=False, sectioned=False),
             dict(context_block='k2: new', id_map={'k2': {'source_id': 's1'}}, succeeded=True, sectioned=False, answer='new')]
    result = final_context(calls)
    assert result['context_supported'] is True
    assert result['retrieval_context'] == ['k2: new']
    assert result['source_ids'] == ['s1']
