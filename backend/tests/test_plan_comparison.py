from datetime import date, timedelta
from types import SimpleNamespace
from app.plan_comparison import compare_plans
from test_app import client
from test_planning import healthy, generate, approve, TODAY


def item(day,kind,minutes,sets=0):
    return {'date':day,'type':kind,'duration_minutes':minutes,'exercises':[{'sets':sets}] if sets else []}


def test_shared_day_totals_and_independent_groups():
    start=date(2026,10,8)
    old=SimpleNamespace(payload={'start_date':start.isoformat(),'sessions':[item(start.isoformat(),'run',25),item(start.isoformat(),'strength',15,2)]})
    new={'sessions':[item(start.isoformat(),'run',12),item(start.isoformat(),'strength',10,1),item('2026-10-09','recovery',0)]}
    result=compare_plans(old,new,start,[{'date':start.isoformat(),'available':True,'duration_minutes':30}])
    assert result['same_window'] and result['has_previous_plan']
    assert result['before']['total_minutes']==40 and result['after']['total_minutes']==22
    assert result['after']['strength']['sets']==1 and result['after']['recovery']['sessions']==1
    assert result['days'][0]['after']['total_minutes']==22
    assert result['days'][0]['availability']['duration_minutes']==30
    assert result['days'][1]['availability'] is None


def test_shifted_window_keeps_history_and_missing_baseline():
    start=date(2026,10,8)
    old=SimpleNamespace(payload={'start_date':'2026-10-07','sessions':[item('2026-10-07','run',20)]})
    new={'sessions':[item(start.isoformat(),'recovery',0)]}
    result=compare_plans(old,new,start,[])
    assert not result['same_window'] and result['before_window']['end']=='2026-10-13'
    assert result['before']['run']['minutes']==20 and result['days'][0]['before']['run']['minutes']==0
    first=compare_plans(None,new,start,[])
    assert not first['has_previous_plan'] and first['before_window']['start'] is None
    assert first['before']['total_minutes']==0


def test_proposal_snapshot_survives_schedule_change_without_applying(client):
    headers=healthy(client);proposal=generate(client,headers)
    snapshot=proposal['comparison']
    assert snapshot['version']=='runner-plan-comparison-1'
    assert snapshot['days'][0]['availability']['duration_minutes']==30
    assert snapshot['after']['run']['sessions']==3 and snapshot['after']['strength']['sessions']==0
    assert client.put('/api/v1/availability',headers=headers,json={'days':[{'date':TODAY.isoformat(),'available':False,'duration_minutes':30}]}).status_code==200
    current=client.get('/api/v1/plans/current').json()
    stored=next(p for p in current['proposals'] if p['id']==proposal['id'])
    assert stored['comparison']==snapshot and stored['state']=='expired'
    assert approve(client,headers,proposal).status_code==409
    assert current['plan'] is None
