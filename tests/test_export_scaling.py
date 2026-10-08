import json
from time import perf_counter
import tracemalloc

import pytest

from rag_eval.benchmark_submission import build_submission
from test_benchmark_submission import method


class CountedCaseId(str):
    comparisons = 0
    __hash__ = str.__hash__

    def __eq__(self, other):
        type(self).comparisons += 1
        return super().__eq__(other)


@pytest.mark.parametrize('count', [1000, 5000, 10000])
def test_synthetic_submission_scope_lookup_is_linear(count, record_property):
    bundle = dict(manifest=dict(suite='qmsum', adaptation_revision='notebook-data-v3'),
                  cases=[dict(case_id=CountedCaseId(f'qmsum:{index}')) for index in range(count)])
    predictions = [dict(case_id=CountedCaseId(case['case_id']), status='success', prediction='Observed summary')
                   for case in bundle['cases']]
    CountedCaseId.comparisons = 0
    tracemalloc.start()
    started = perf_counter()
    result = build_submission(bundle, method=method(), predictions=predictions,
                              case_ids=[case['case_id'] for case in bundle['cases']])
    elapsed = perf_counter() - started
    _, peak = tracemalloc.get_traced_memory()
    tracemalloc.stop()
    size = len(json.dumps(result))
    record_property('case_count', count)
    record_property('export_seconds', elapsed)
    record_property('peak_memory_bytes', peak)
    record_property('submission_bytes', size)
    print(f'cases={count} seconds={elapsed:.6f} peak_bytes={peak} submission_bytes={size} comparisons={CountedCaseId.comparisons}')
    assert result['coverage']['planned'] == count
    assert result['coverage']['generation_complete'] is True
    assert CountedCaseId.comparisons <= count * 20
