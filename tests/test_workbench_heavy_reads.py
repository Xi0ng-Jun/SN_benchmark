"""Heavy workbench GETs: one computation per key, cached by version, a global cap on threads."""
import asyncio
import threading
import time

import pytest

from rag_eval.workbench.heavy_reads import HeavyReads


def run(coroutine):
    return asyncio.run(coroutine)


def test_concurrent_requests_for_one_key_share_one_computation():
    heavy, calls = HeavyReads(limit=2), []

    def compute():
        calls.append(threading.get_ident())
        time.sleep(0.2)
        return {'tree': len(calls)}

    async def main():
        return await asyncio.gather(*[heavy.get(('show', 'i1'), compute, prepare=lambda: 'v1') for _ in range(20)])
    results = run(main())
    assert len(calls) == 1 and results == [{'tree': 1}] * 20


def test_a_result_is_served_while_its_version_holds_and_recomputed_after():
    heavy, version, calls = HeavyReads(), ['v1'], []

    def compute():
        calls.append(version[0])
        return list(calls)

    async def main():
        first = await heavy.get(('ledger', 'i1'), compute, prepare=lambda: version[0])
        again = await heavy.get(('ledger', 'i1'), compute, prepare=lambda: version[0])
        version[0] = 'v2'
        changed = await heavy.get(('ledger', 'i1'), compute, prepare=lambda: version[0])
        unversioned = [await heavy.get(('changes', 'c1'), compute) for _ in range(2)]
        return first, again, changed, unversioned
    first, again, changed, unversioned = run(main())
    assert first == again == ['v1'] and changed == ['v1', 'v2']
    assert len(unversioned[1]) == len(unversioned[0]) + 1  # no version: shared while running, never cached


def test_computations_across_keys_hold_at_most_limit_threads():
    heavy, lock, active, peak = HeavyReads(limit=2), threading.Lock(), [0], [0]

    def compute():
        with lock:
            active[0] += 1
            peak[0] = max(peak[0], active[0])
        time.sleep(0.1)
        with lock:
            active[0] -= 1
        return True

    async def main():
        return await asyncio.gather(*[heavy.get(('comparisons', f'c{i}'), compute, prepare=lambda: 'v')
                                      for i in range(8)])
    assert all(run(main()))
    assert peak[0] == 2 and heavy.peak == 2 and heavy.computations == 8


def test_a_disconnected_request_neither_cancels_nor_loses_the_shared_computation():
    heavy, calls = HeavyReads(), []

    def compute():
        calls.append(1)
        time.sleep(0.2)
        return 'tree'

    async def main():
        leaving = asyncio.ensure_future(heavy.get('k', compute, prepare=lambda: 'v'))
        staying = asyncio.ensure_future(heavy.get('k', compute, prepare=lambda: 'v'))
        await asyncio.sleep(0.05)
        leaving.cancel()
        result = await staying
        cached = await heavy.get('k', compute, prepare=lambda: 'v')
        return leaving.cancelled(), result, cached
    assert run(main()) == (True, 'tree', 'tree') and calls == [1]


def test_a_failure_reaches_every_waiter_and_is_not_cached():
    heavy, calls = HeavyReads(), []

    def compute():
        calls.append(1)
        time.sleep(0.05)
        if len(calls) == 1:
            raise LookupError('Unknown iteration identity')
        return 'ok'

    async def main():
        failed = await asyncio.gather(*[heavy.get('k', compute, prepare=lambda: 'v') for _ in range(3)],
                                      return_exceptions=True)
        return failed, await heavy.get('k', compute, prepare=lambda: 'v')
    failed, retried = run(main())
    assert all(isinstance(f, LookupError) for f in failed) and retried == 'ok' and len(calls) == 2


def test_scope_errors_are_raised_before_any_computation():
    heavy = HeavyReads()

    def refuse():
        raise LookupError('outside this workspace')

    async def main():
        await heavy.get('k', lambda: pytest.fail('must not compute'), prepare=refuse)
    with pytest.raises(LookupError):
        run(main())
    assert heavy.computations == 0
