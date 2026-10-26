"""P9: preparation_workers may not exceed SN's per-actor chat queue (2 x max_concurrency)."""
import pytest

from rag_eval.model_service_limits import check_preparation_workers, preparation_chat_limit

CONFIG = '''
[services.general]
kind = "chat"
max_concurrency = 2
[services.reasoning]
kind = "chat"
max_concurrency = 8
[services.embedding]
kind = "embedding"
max_concurrency = 1
[bindings]
source_summary = "general"
paper_metadata = "general"
notebook_metadata = "reasoning"
chunk_embedding = "embedding"
'''


def config(tmp_path, text=CONFIG):
    path = tmp_path / 'model-services.toml'
    path.write_text(text)
    return path


def test_limit_is_the_smallest_chat_service_preparation_uses(tmp_path):
    assert preparation_chat_limit(config(tmp_path)) == ('general', 2)
    check_preparation_workers(4, config(tmp_path))
    with pytest.raises(ValueError, match="preparation_workers 8 exceeds 2 x max_concurrency 2 of chat service 'general' = 4"):
        check_preparation_workers(8, config(tmp_path))


def test_unknown_or_unparseable_configs_are_not_guessed(tmp_path):
    assert preparation_chat_limit(config(tmp_path, '{}')) is None
    assert preparation_chat_limit(tmp_path / 'absent.toml') is None
    check_preparation_workers(64, config(tmp_path, '[bindings]\nsource_summary = "missing"\n'))


def test_runner_refuses_before_creating_the_run(tmp_path):
    from rag_eval.notebook_runner import execute
    from pathlib import Path
    from test_notebook_execution import bundle_fixture
    partition = bundle_fixture(tmp_path)
    with pytest.raises(ValueError, match='per-actor queue cap'):
        execute(root=Path(__file__).resolve().parents[1], project=tmp_path / 'product', bundle_dir=tmp_path / 'bundle',
                run=tmp_path / 'run', mode='chunk', partition_id=partition, model_config=config(tmp_path),
                preparation_workers=8)
    assert not (tmp_path / 'run').exists()


def test_workbench_rejects_the_execution_spec(tmp_path):
    from rag_eval.workbench.job_execution import ExecutionRejected
    from rag_eval.workbench.jobs_api import build_execution_spec
    from test_workbench_notebook_job import prepared, settings
    jobs, job, prep, args = prepared(tmp_path)
    values = settings(tmp_path, args)
    config(tmp_path)  # replaces the placeholder model.toml path below
    values['model_config'] = str(tmp_path / 'model-services.toml')
    with pytest.raises(ExecutionRejected, match='preparation_workers 8 exceeds'):
        build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
                             dict(values, preparation_workers=8), request_key='cap',
                             budget={'timeout_seconds': 600})
    spec = build_execution_spec(jobs, job['task_id'], job['job_id'], prep['preparation_id'],
                                dict(values, preparation_workers=4), request_key='ok', budget={'timeout_seconds': 600})
    assert '"preparation_workers": 4' in spec['argv'][-1]
