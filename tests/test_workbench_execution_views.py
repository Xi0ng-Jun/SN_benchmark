import json
from pathlib import Path
from rag_eval.workbench.run_views import RunViews


def test_execution_partition_runs_discovered_from_durable_execution_without_config_rewrite(tmp_path):
    root = tmp_path / 'execution'
    partition = root / 'partitions' / '0001-p-chunk'
    partition.mkdir(parents=True)
    (partition / 'state.json').write_text(json.dumps(dict(phase='finished')))
    (root / 'batch-plan.json').write_text(json.dumps([dict(run_dir=str(partition), case_ids=['c'])]))
    record = dict(execution_id='e1', task_id='t1', run_dir=str(root))
    views = RunViews([], execution_records=lambda: [record])
    runs = views.list_runs()['runs']
    assert len(runs) == 1
    assert runs[0]['id'] == 'e1-1' and runs[0]['task_id'] == 't1'
    assert views.run('e1-1')['state']['terminal'] is True


def test_execution_discovery_rejects_plan_paths_outside_own_execution(tmp_path):
    root = tmp_path / 'execution'
    root.mkdir()
    (root / 'batch-plan.json').write_text(json.dumps([dict(run_dir=str(tmp_path / 'untrusted'), case_ids=['c'])]))
    views = RunViews([], execution_records=lambda: [dict(execution_id='e1', task_id='t1', run_dir=str(root))])
    assert views.list_runs()['runs'] == []


def test_failed_execution_keeps_unstarted_partition_cases_missing(tmp_path):
    root = tmp_path / 'execution'
    root.mkdir()
    partition = root / 'partitions' / 'failed-partition'
    (root / 'batch-plan.json').write_text(json.dumps([dict(run_dir=str(partition), case_ids=['c'])]))
    record = dict(execution_id='e1', task_id='t1', run_dir=str(root), state='failed', terminal=True)
    views = RunViews([], execution_records=lambda: [record])
    view = views.run('e1-1')
    assert view['state']['terminal'] is True
    assert view['cases'][0]['status'] == 'missing'


def test_cancelled_execution_preserves_observed_phase_and_stops_live_projection(tmp_path):
    root = tmp_path / 'execution'
    partition = root / 'partitions' / 'p'
    partition.mkdir(parents=True)
    (partition / 'state.json').write_text(json.dumps(dict(phase='running')))
    (root / 'batch-plan.json').write_text(json.dumps([dict(run_dir=str(partition), case_ids=['c'])]))
    record = dict(execution_id='e1', task_id='t1', run_dir=str(root), state='cancelled', terminal=True)
    view = RunViews([], execution_records=lambda: [record]).run('e1-1')
    assert view['state']['terminal'] is True
    assert view['state']['runner_phase'] == 'running'
    assert view['cases'][0]['status'] == 'missing'


def test_failed_startup_displays_fixed_suite_and_mode_without_claiming_loaded_source(tmp_path):
    root = tmp_path / 'execution'
    root.mkdir()
    partition = root / 'partitions/p'
    (root / 'batch-plan.json').write_text(json.dumps([dict(run_dir=str(partition),case_ids=['c'],suite='qasper',mode='chunk')]))
    record=dict(execution_id='e1',task_id='t1',run_dir=str(root),state='failed',terminal=True)
    view=RunViews([],execution_records=lambda:[record]).run('e1-1')
    assert view['suite']=='qasper' and view['mode']=='chunk'
    assert view['official']['status']=='missing'
    assert not view['provenance']['source'].get('sn_source_ref')


def test_concurrent_discovery_never_publishes_a_signature_before_its_runs(tmp_path, monkeypatch):
    import threading, time
    import rag_eval.workbench.run_views as run_views
    records = []
    for name in ('e1', 'e2'):
        root = tmp_path / name
        partition = root / 'partitions' / '0001-p-chunk'
        partition.mkdir(parents=True)
        (root / 'batch-plan.json').write_text(json.dumps([dict(run_dir=str(partition), case_ids=['c'])]))
        records.append(dict(execution_id=name, task_id='t1', run_dir=str(root), state='finished', terminal=True))
    views = RunViews([], execution_records=lambda: list(records))
    entered, release, original = threading.Event(), threading.Event(), run_views._json
    def slow(path):
        if Path(path).parent.name == 'e2':
            entered.set()
            release.wait(5)
        return original(path)
    monkeypatch.setattr(run_views, '_json', slow)
    first = threading.Thread(target=views.execution_runs, args=('e1',))
    first.start()
    assert entered.wait(5)  # The first scan is between the two executions.
    seen = []
    second = threading.Thread(target=lambda: seen.append(views.execution_runs('e2')))
    second.start()
    time.sleep(0.2)
    release.set()
    first.join(5); second.join(5)
    assert [run.id for run in seen[0]] == ['e2-1']
