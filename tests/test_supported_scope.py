"""Public planning/reporting boundaries expose only the maintained benchmarks."""
import json

import pytest

from rag_eval.metric_catalog import benchmark_rows
from rag_eval.run_report import load_run
from rag_eval.run_results import planned_result


def test_catalog_contains_only_the_five_supported_benchmarks():
    assert {suite.split('/')[0] for suite, _, _ in benchmark_rows()} == {
        'qasper', 'multihop_rag', 'alce', 'qmsum', 'hotpotqa',
    }


@pytest.mark.parametrize('suite', ['squad', 'unknown-benchmark'])
def test_planning_rejects_unsupported_suite(suite):
    with pytest.raises(ValueError, match='Unsupported benchmark'):
        planned_result({'suite': suite, 'case_id': 'q1', 'sample_id': 'q1'},
                       run_id='run', protocol_id='identity', track='R', mode='chunk', scorer='metric')


def test_report_rejects_retired_suite_before_loading_its_artifacts(tmp_path):
    (tmp_path / 'manifest.json').write_text(json.dumps({
        'format': 'public-starter-run-v1', 'suite': 'squad',
    }))
    with pytest.raises(ValueError, match='Unsupported benchmark'):
        load_run(tmp_path)
