from rag_eval.usage_capture import capture_usage


def test_observation_preserves_logs_and_does_not_invent_missing_usage():
    class Logger:
        def log(self, record):
            received.append(record)
    received = []
    original = Logger.log
    with capture_usage(Logger) as usage:
        Logger().log({'status': 'error', 'prompt': 'private prompt'})
        Logger().log({'status': 'ok', 'usage': {'total_tokens': 12}})
    assert len(received) == 2
    assert Logger.log is original
    assert usage['call_count'] == 2
    assert usage['calls_with_usage'] == 1
    assert usage['total_tokens'] == 12
    assert usage['prompt_tokens'] is None
    assert 'prompt' not in usage['calls'][0]
