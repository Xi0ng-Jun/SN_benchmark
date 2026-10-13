"""Read-only run views: real E5 fixture (skipped when absent) and a synthetic git worktree."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess

import pytest

from rag_eval.workbench.run_views import NotRegistered, RunEntry, RunViews, WorktreeEntry, _Locator, _paragraphs

ROOT = Path(__file__).resolve().parents[1]
E5 = ROOT / 'apps/sn-workbench/test-results/source-execution-20261012/e5-s3-real-run-20261013'
RUN, SCORES = E5 / 'attempt-1/cli-run', E5 / 'official/scores/scores.json'
CASE = 'qasper:397a1e851aab41c455c2b284f5e4947500d797f0'
needs_fixture = pytest.mark.skipif(not (RUN / 'manifest.json').is_file(), reason='local E5 run fixture not present')


def _tree_state(path: Path) -> dict:
    return {str(p.relative_to(path)): (p.stat().st_mtime_ns, p.stat().st_size)
            for p in path.rglob('*') if p.is_file() and 'runtime' not in p.relative_to(path).parts}


@pytest.fixture(scope='module')
def views():
    return RunViews([RunEntry('e5', RUN, SCORES, 'E5'), RunEntry('e5-copy', RUN, SCORES, 'E5 copy')])


@needs_fixture
def test_run_summary_keeps_official_stage_and_state_separate(views):
    before = _tree_state(RUN)
    run = views.run('e5')
    assert run['state'] == {'phase': 'finished', 'terminal': True, 'updated_at': run['state']['updated_at']}
    assert run['official']['status'] == 'available'
    assert run['official']['metrics'] == {'answer_f1': pytest.approx(2 / 3), 'evidence_f1': 1.0}
    assert run['official']['metric_denominators'] == {'answer_f1': 1, 'evidence_f1': 1}
    stage = run['stage']
    assert stage['metric_role'] == 'diagnostic'
    assert (stage['context_precision']['numerator'], stage['context_precision']['denominator']) == (1, 16)
    assert stage['gold_evidence_recall']['value'] == 1.0 and stage['citation_precision']['value'] == 1.0
    assert stage['scheduled']['submission_count'] == 20 and stage['scheduled']['history_exhaustive'] is False
    assert run['attention'] == {'rule': 'status != success or official answer_f1 < 0.5', 'count': 0, 'case_ids': []}
    assert run['provenance']['source']['head_revision'] == '0680aa12313a5d3e67f4e0a4be94144a5b6af28c'
    assert run['provenance']['features']['status'] == 'not_projected'
    services = run['provenance']['model_services']['services']
    assert {s['kind'] for s in services} == {'embedding', 'chat', 'rerank'}
    assert all(set(s) == {'service_id', 'kind', 'protocol', 'model', 'generation'} for s in services)
    dumped = json.dumps(run)
    assert not any(leak in dumped for leak in ('endpoint_sha256', 'credential_variable_name', '_API_KEY', 'https://', 'settings.env'))
    assert _tree_state(RUN) == before and not (RUN / 'stage-metrics.json').exists()


@needs_fixture
def test_case_detail_highlights_gold_and_cited_text_in_paper(views):
    case = views.case('e5', CASE)
    assert case['answer'].startswith('The ANTISCAM dataset contains 220')
    assert ' 3,044 sentences in 100 dialogs' in case['references']
    paper, = case['papers']
    assert paper['first_gold'] is not None and paper['unmatched'] == []
    text = ''.join(seg['text'] for p in paper['paragraphs'] for seg in p['segments'])
    marked = [seg for p in paper['paragraphs'] for seg in p['segments'] if 'gold' in seg['marks']]
    assert any('We collected 220 human-human dialogs' in seg['text'] for seg in marked)
    assert any('cited' in seg['marks'] for seg in marked)  # k1 is the gold paragraph
    assert len(text) <= paper['characters']
    k1, = case['citations']
    assert (k1['key'], k1['rank'], k1['contains_gold']) == ('k1', 1, True)
    assert sum(r['contains_gold'] for r in case['retrieved']) == 1 and len(case['retrieved']) == 16


@needs_fixture
def test_cases_filters_and_admitted_self_flip(views):
    views = RunViews([RunEntry('e5', RUN, SCORES), RunEntry('e5-copy', RUN, SCORES)],
                     needs_read_thresholds={'qasper': 1})
    assert [r['case_id'] for r in views.cases('e5', needs_read=True)['cases']] == [CASE]
    assert views.cases('e5', status='error')['cases'] == []
    against = views.cases('e5', against='e5-copy')
    assert against['cases'][0]['delta'] == 0
    assert views.cases('e5', against='e5-copy', delta='worse')['cases'] == []
    flips = views.flips('e5', 'e5')
    assert flips['kind'] == 'paired' and flips['admission'] == 'admitted' and flips['same_run']
    assert flips['counts'] == {'better': 0, 'worse': 0, 'same': 1, 'unpaired': 0}


@needs_fixture
def test_missing_official_scores_stay_unscored(tmp_path):
    views = RunViews([RunEntry('e5', RUN, None), RunEntry('gone', RUN, tmp_path / 'absent.json')])
    assert views.run('e5')['official']['status'] == 'not_registered'
    run = views.run('gone')
    assert run['official']['status'] == 'missing'
    assert run['cases'][0]['official'] == {'state': 'missing', 'metrics': {}}
    assert run['attention']['count'] == 0  # no primary metric: only non-success statuses need a read


def test_only_registered_ids_are_readable(tmp_path):
    views = RunViews([], [])
    for bad in ('../etc', '/abs', 'a b', ''):
        with pytest.raises((ValueError, NotRegistered)):
            views.run(bad)
    with pytest.raises(NotRegistered):
        views.run('unknown')
    assert views.task_overview('t')['status'] == 'unbound'
    assert views.task_changes('t')['status'] == 'unbound'


def test_smoke_execution_projection_does_not_claim_model_cost(tmp_path):
    repo = tmp_path / 'sn'
    repo.mkdir()
    _git(repo, 'init', '-q', '-b', 'main')
    (repo / 'app.py').write_text('a = 1\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'base')
    base = _git(repo, 'rev-parse', 'HEAD')
    views = RunViews([], [WorktreeEntry('w', repo, 'main', base, 'task-1', 'wks_x')],
                     execution_enabled=True, execution_config={'mode': 'smoke'})

    verification = views.task_changes('task-1')['verification']

    assert verification['executable'] is True
    assert '不会加载 SN' in verification['budget']['note']
    assert verification['commands']['dry_run'].startswith('本地 smoke executor')


def test_locator_folds_whitespace_and_splits_heading_chunks():
    text = 'Title\n\nDatasets ::: A\n\nWe   collected\n220 dialogs here.\n\nOther.'
    locator = _Locator(text)
    a, b = locator.find('We collected 220 dialogs')
    assert text[a:b] == 'We   collected\n220 dialogs'
    spans = locator.spans('Datasets ::: A heading\nWe collected 220 dialogs here.', minimum=10)
    assert [text[x:y] for x, y in spans] == ['We   collected\n220 dialogs here.']
    paragraphs = _paragraphs(text, [(a, b, 'gold', 'g1')])
    assert [p['marked'] for p in paragraphs] == [False, False, True, False]
    assert paragraphs[2]['segments'][0] == {'text': 'We   collected\n220 dialogs', 'marks': ['gold'], 'labels': ['g1']}


def _git(root, *args):
    env = {**os.environ, 'GIT_AUTHOR_NAME': 't', 'GIT_AUTHOR_EMAIL': 't@x', 'GIT_COMMITTER_NAME': 't', 'GIT_COMMITTER_EMAIL': 't@x'}
    return subprocess.run(['git', '-C', str(root), *args], check=True, capture_output=True, text=True, env=env).stdout.strip()


def test_fallback_worktree_read_matches_task_worktree_shape(tmp_path):
    repo = tmp_path / 'sn'
    repo.mkdir()
    _git(repo, 'init', '-q', '-b', 'main')
    (repo / 'app.py').write_text('a = 1\n')
    (repo / 'gone.py').write_text('x\n')
    _git(repo, 'add', '.')
    _git(repo, 'commit', '-q', '-m', 'base')
    base = _git(repo, 'rev-parse', 'HEAD')
    (repo / 'app.py').write_text('a = 2\nb = 3\n')
    (repo / 'gone.py').unlink()
    (repo / 'new.py').write_text('n = 1\n')
    (repo / '.env').write_text('SECRET=do-not-read\n')
    index = (repo / '.git/index').stat().st_mtime_ns
    views = RunViews([], [WorktreeEntry('w', repo, 'main', base, 'task-1', 'wks_x')])
    changes = views.task_changes('task-1')
    assert changes['status'] == 'available' and changes['source'] == 'run_views_fallback'
    read = changes['read']
    assert read['binding']['base_revision'] == base and read['current_head'] == base
    files = {f['path']: f for f in read['files']}
    assert set(files) == {'app.py', 'gone.py', 'new.py', '.env'}
    assert '+b = 3' in files['app.py']['diff'] and '-a = 1' in files['app.py']['diff']
    assert files['gone.py']['kind'] == 'missing' and '-x' in files['gone.py']['diff']
    assert '+n = 1' in files['new.py']['diff'] and files['new.py']['untracked']
    assert files['.env']['private'] and files['.env']['diff'] is None
    assert 'do-not-read' not in json.dumps(changes)
    assert all(f['preexisting_dirty'] is None and f['changed_since_start'] is None for f in files.values())
    assert changes['verification']['executable'] is False and '--dry-run' in changes['verification']['commands']['dry_run']
    assert changes['hypotheses']['status'] == 'not_available'
    assert (repo / '.git/index').stat().st_mtime_ns == index and not (repo / '.git/index.lock').exists()
    assert _git(repo, 'status', '--porcelain') != ''  # nothing was reverted or committed


def test_injected_worktree_service_is_preferred(tmp_path):
    class Service:
        def binding(self, task_id):
            return {'task_id': task_id, 'worktree_path': str(tmp_path), 'branch': 'b', 'base_revision': 'f' * 40,
                    'paseo_workspace_id': None, 'start_snapshot': {'source_ref': {'id': 'ref-1'}}}

        def read(self, task_id):
            return {'binding': self.binding(task_id), 'current_head': 'f' * 40, 'files': [], 'file_count': 0, 'files_truncated': False}

    views = RunViews([], [], worktree_service=Service())
    changes = views.task_changes('t')
    assert changes['source'] == 'task_worktree' and changes['read']['files'] == []
    assert views.task_overview('t')['status'] == 'idle'


@needs_fixture
def test_router_rejects_unregistered_and_requires_registration(views):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    app = FastAPI()
    app.include_router(views.router())
    client = TestClient(app)
    assert client.get('/api/run-views/runs').json()['runs'][0]['id'] == 'e5'
    assert client.get('/api/run-views/runs/nope').status_code == 404
    assert client.get('/api/run-views/runs/e5/case', params={'case_id': 'x'}).status_code == 404
    body = client.get('/api/run-views/runs/e5/case', params={'case_id': CASE}).json()
    assert body['papers'][0]['first_gold'] is not None
    flips = client.get('/api/run-views/flips', params={'left': 'e5', 'right': 'e5-copy'}).json()
    assert flips['same_run'] is False and flips['counts']['same'] == 1
