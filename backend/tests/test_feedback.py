from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from app.models import User, Checkin
from app.planning import gather
from test_app import client, account, workout

TODAY=datetime.now(ZoneInfo('Asia/Shanghai')).date()
DAY=(TODAY-timedelta(days=1)).isoformat()


def test_observation_local_clock_owner_link_and_legacy(client):
    h=account(client)
    run=client.post('/api/v1/workouts',headers=h,json={**workout(),'date':DAY}).json()
    r=client.post('/api/v1/checkins',headers=h,json={'date':DAY,'fatigue':4,'observed_time':'09:30','related_training_type':'run','related_training_id':run['id'],'feedback_phase':'after'})
    assert r.status_code==201,r.text
    assert r.json()['observed_at']==DAY+'T01:30:00Z'
    assert 'observed_time' not in r.json()
    assert r.json()['related_training_id']==run['id']
    assert r.json()['observation_timezone']=='Asia/Shanghai'
    with Session(client.app.state.engine) as db:
        db.add(Checkin(user_id='owner',payload={'date':DAY,'fatigue':2}));db.commit()
    old=next(r for r in client.get('/api/v1/checkins').json() if r['fatigue']==2)
    assert old['observation_timezone'] is None
    assert old['observed_at'] is None and old['feedback_phase']=='unknown'
    assert old['related_training_id'] is None
    assert client.get('/api/v1/plans/current').json()['facts_revision']>=3


def test_explicit_event_order_shared_by_api_and_coach(client):
    h=account(client)
    late=client.post('/api/v1/checkins',headers=h,json={'date':DAY,'fatigue':5,'observed_at':DAY+'T10:00:00+08:00'}).json()
    early=client.post('/api/v1/checkins',headers=h,json={'date':DAY,'fatigue':1,'observed_at':DAY+'T08:00:00+08:00'}).json()
    assert client.get('/api/v1/checkins').json()[0]['id']==late['id']
    with Session(client.app.state.engine) as db:assert gather(db,db.get(User,'owner'))['latest']['id']==late['id']
    assert early['observed_at']==DAY+'T00:00:00Z'


def test_invalid_reference_and_chronology_leave_no_fact(client):
    h=account(client)
    from app.models import StrengthWorkout
    with Session(client.app.state.engine) as db:
        db.add(StrengthWorkout(id='legacy-strength',user_id='owner',payload={'date':DAY,'duration_seconds':0,'status':'skipped'}));db.commit()
    strength={'id':'legacy-strength'}
    run=client.post('/api/v1/workouts',headers=h,json={**workout(),'date':DAY}).json()
    before=client.get('/api/v1/plans/current').json()['facts_revision']
    cases=[
        ({'related_training_type':'run'},422),
        ({'observation_timezone':'UTC'},422),
        ({'related_training_type':'run','related_training_id':strength['id']},404),
        ({'related_training_type':'run','related_training_id':'missing'},404),
        ({'observed_at':DAY+'T08:00:00'},422),
        ({'observed_at':DAY+'T20:00:00Z'},422),
        ({'observed_time':'09:00','observed_at':DAY+'T01:00:00Z'},422),
        ({'related_training_type':'strength','related_training_id':strength['id'],'feedback_phase':'after'},422),
        ({'related_training_type':'run','related_training_id':run['id'],'feedback_phase':'next_day'},422),
        ({'date':(TODAY-timedelta(days=2)).isoformat(),'related_training_type':'run','related_training_id':run['id'],'feedback_phase':'after'},422),
        ({'date':(TODAY+timedelta(days=1)).isoformat(),'observed_time':'00:00'},422),
    ]
    for fields,status in cases:
        response=client.post('/api/v1/checkins',headers=h,json={'date':DAY,'fatigue':2,**fields})
        assert response.status_code==status,response.text
    assert client.get('/api/v1/checkins').json()==[]
    assert client.get('/api/v1/plans/current').json()['facts_revision']==before


def test_reference_is_owner_scoped(client):
    from app.security import hash_password
    h=account(client)
    from app.models import StrengthWorkout
    with Session(client.app.state.engine) as db:
        db.add(User(id='other',username='other',password_hash=hash_password('synthetic-only-password'),profile={}));db.flush()
        db.add(StrengthWorkout(id='private-other-strength',user_id='other',payload={'date':DAY,'duration_seconds':600,'status':'completed'}));db.commit()
    result=client.post('/api/v1/checkins',headers=h,json={'date':DAY,'fatigue':2,'related_training_type':'strength','related_training_id':'private-other-strength'})
    assert result.status_code==422
    assert client.get('/api/v1/checkins').json()==[]


def test_profile_timezone_and_dst_are_not_browser_timezone(client):
    h=account(client)
    p=client.get('/api/v1/profile').json();p['timezone']='America/New_York'
    assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    result=client.post('/api/v1/checkins',headers=h,json={'date':'2026-02-01','fatigue':2,'observed_time':'09:30'})
    assert result.status_code==201 and result.json()['observed_at']=='2026-02-01T14:30:00Z'
    p['timezone']='Asia/Shanghai';client.patch('/api/v1/profile',headers=h,json=p)
    assert client.get('/api/v1/checkins').json()[0]['observation_timezone']=='America/New_York'
    p['timezone']='America/New_York';client.patch('/api/v1/profile',headers=h,json=p)
    assert client.post('/api/v1/checkins',headers=h,json={'date':'2026-03-08','fatigue':2,'observed_time':'02:30'}).status_code==422
    assert client.post('/api/v1/checkins',headers=h,json={'date':'2026-11-01','fatigue':2,'observed_time':'01:30'}).status_code==422
