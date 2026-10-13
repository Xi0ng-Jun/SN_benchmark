"""User-approved attention threshold and explicit Task hypothesis association."""
import json

import pytest

from rag_eval.workbench.run_views import RunEntry, RunViews


def attention_views(tmp_path, monkeypatch, thresholds=None):
    (tmp_path/'manifest.json').write_text(json.dumps(dict(suite='qasper')))
    (tmp_path/'state.json').write_text(json.dumps(dict(phase='finished')))
    rows=[dict(case_id=str(i),status=status,prediction='answer' if status=='success' else '')
          for i,status in enumerate(['success','success','success','clarification','success'])]
    (tmp_path/'outputs.jsonl').write_text(''.join(json.dumps(row)+'\n' for row in rows))
    (tmp_path/'planned.jsonl').write_text(''.join(json.dumps(dict(case_id=row['case_id']))+'\n' for row in rows))
    scores=[0.49,0.5,0.7,1,None]
    monkeypatch.setattr(RunViews,'_official',lambda *a:dict(status='available',primary_metric='answer_f1',
        per_case={str(i):dict(metrics={'answer_f1':s} if s is not None else {}) for i,s in enumerate(scores)}))
    return RunViews([RunEntry('run',tmp_path)],needs_read_thresholds=thresholds)


def test_default_attention_threshold_is_strict_and_not_a_decision(tmp_path,monkeypatch):
    views=attention_views(tmp_path,monkeypatch)
    view=views.run('run')
    assert [r['needs_read'] for r in view['cases']]==[True,False,False,True,False]
    assert view['attention']['count']==2
    assert [r['case_id'] for r in views.cases('run',needs_read=True)['cases']]==['0','3']
    assert view['cases'][4]['official']['metrics']=={}
    assert views.task_overview('unbound')['pending']==0


def test_configurable_threshold_changes_only_attention(tmp_path,monkeypatch):
    default=attention_views(tmp_path,monkeypatch).run('run')
    raised=attention_views(tmp_path,monkeypatch,{'qasper':0.7}).run('run')
    assert [r['needs_read'] for r in raised['cases']]==[True,True,False,True,False]
    assert [r['official'] for r in default['cases']]==[r['official'] for r in raised['cases']]


@pytest.mark.parametrize('threshold',[float('nan'),float('inf'),-0.1,1.1,True,'0.5'])
def test_threshold_config_rejects_invalid_values(threshold):
    with pytest.raises(ValueError):RunViews([],needs_read_thresholds={'qasper':threshold})


def test_hypothesis_cards_are_explicitly_task_scoped(tmp_path):
    section=dict(hypotheses=[dict(id='H1',task_id='task-h1',title='Single-source clarification',
                                 path='docs/hypotheses/h1-reasoning-clarification.md',status='authorized')])
    views=RunViews.from_config(section,tmp_path)
    cards=views.task_changes('task-h1')['hypotheses']
    assert cards['status']=='available' and cards['items'][0]['id']=='H1'
    assert views.task_changes('another-task')['hypotheses']['items']==[]
    assert views.task_overview('task-h1')['runs']==[]
