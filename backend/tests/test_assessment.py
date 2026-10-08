from datetime import date
from app.assessment import summarize
from app.schemas import Profile
from test_app import client, account, workout


def context():
    return {'profile':Profile(goal_duration_seconds=6600),'workouts':[], 'strength':[], 'checkins':[]}


def test_window_types_unknowns_and_goal():
    c=context()
    c['workouts']=[{'id':str(i),**workout(),'date':d,'sport':sport,'effort':effort} for i,(d,sport,effort) in enumerate([
        ('2026-09-11','road_run',0),('2026-10-08','trail_run',None),
        ('2026-09-10','road_run',3),('2026-10-09','road_run',3)])]
    c['strength']=[{'id':'s','date':'2026-10-08','status':'partial','duration_seconds':600}, {'id':'skip','date':'2026-10-08','status':'skipped','duration_seconds':0}]
    result=summarize(c,date(2026,10,8))
    assert result['window_start']=='2026-09-11'
    assert result['goal']['pace_seconds_per_km']==313
    assert [g['count'] for g in result['runs']]==[1,1,0]
    assert result['runs'][0]['missing_effort_count']==0
    assert result['runs'][1]['missing_effort_count']==1
    assert result['runs'][2]['longest'] is None
    assert result['strength']['partial']==1 and result['strength']['duration_seconds']==600
    assert sum(w['count'] for w in result['weeks'])==2


def test_assessment_authenticated_empty_and_nonmutating(client):
    assert client.get('/api/v1/assessment').status_code==401
    account(client)
    before=client.get('/api/v1/plans/current').json()
    result=client.get('/api/v1/assessment').json()
    assert result['goal']['pace_seconds_per_km'] is None
    assert all(r['count']==0 and r['longest'] is None for r in result['runs'])
    assert result['strength']['duration_seconds']==0
    assert client.get('/api/v1/plans/current').json()==before


def test_next_steps_empty_and_past_goal():
    from app.assessment import next_steps
    c={**context(),'days':[]}
    c['profile'].goal_date=date(2026,10,7)
    steps=next_steps(c,date(2026,10,8),['缺少身体反馈'])
    items={i['id']:i for i in steps['items']}
    assert items['body']['state']=='needs_info'
    assert items['goal']['state']=='needs_review'
    assert items['schedule']['state']=='needs_info'
    assert items['running']['evidence_ids']==[]
    assert '不要求额外补练' in items['strength_records']['detail']


def test_next_steps_recorded_is_not_clearance():
    from app.assessment import next_steps
    from datetime import timedelta
    c={**context(),'days':[{'date':(date(2026,10,8)+timedelta(days=i)).isoformat(),'available':False} for i in range(7)]}
    c['checkins']=[{'id':'check','date':'2026-10-08','pain_level':2}]
    c['profile'].strength_experience='some'
    c['profile'].strength_equipment=['resistance_band']
    c['profile'].strength_max_minutes=30
    c['profile'].familiar_exercises=['band_biceps_curl','unknown']
    items={i['id']:i for i in next_steps(c,date(2026,10,8),['疼痛待核对'])['items']}
    assert items['body']['state']=='needs_review'
    assert items['body']['evidence_ids']==['check']
    assert items['schedule']['state']=='needs_review'
    assert items['strength_profile']['state']=='needs_info'
    c['profile'].band_resistance='本人已熟悉的轻阻力'
    updated={i['id']:i for i in next_steps(c,date(2026,10,8),['疼痛待核对'])['items']}
    assert updated['strength_profile']['state']=='recorded'
    assert updated['body']['state']=='needs_review'


def test_next_steps_future_records_do_not_fill_gaps():
    from app.assessment import next_steps
    c={**context(),'days':[{'date':'2026-10-15','available':True}]}
    c['checkins']=[{'id':'future','date':'2026-10-09'}]
    c['workouts']=[{'id':'future-run',**workout(),'date':'2026-10-09'}]
    items={i['id']:i for i in next_steps(c,date(2026,10,8),[])['items']}
    assert items['body']['state']=='needs_info' and items['body']['evidence_ids']==[]
    assert items['running']['state']=='needs_info' and items['running']['evidence_ids']==[]
    assert items['schedule']['state']=='needs_info'
