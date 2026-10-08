from datetime import datetime,timedelta,timezone
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.orm import Session
from sqlalchemy import select
import pytest
from app.notifications import Preferences,quiet_until,simulate,Simulator
from app.models import User,NotificationJob,PlanOutbox
from test_app import client,account
from test_planning import healthy,generate,approve,TODAY

T=datetime.combine(TODAY,datetime.strptime('08:00','%H:%M').time(),timezone.utc)

def enable(c,h,**overrides):
    current=c.get('/api/v1/notification-settings').json()
    payload=Preferences.model_validate(current if False else {k:v for k,v in current.items() if k in Preferences.model_fields}).model_dump()
    return c.put('/api/v1/notification-settings',headers=h,json={**payload,'enabled':True,**overrides,'expected_revision':current['revision']})

def setup(c):
    h=healthy(c);assert approve(c,h,generate(c,h)).status_code==200
    assert enable(c,h,quiet_enabled=False).status_code==200
    assert c.post('/api/v1/notifications/preview',headers=h).status_code==200
    with Session(c.app.state.engine) as db:
        box=db.scalar(select(PlanOutbox));box.created_at=T-timedelta(minutes=10)
        j=db.scalar(select(NotificationJob).where(NotificationJob.payload['kind'].as_string()=='plan_updated'));j.due_at=T-timedelta(minutes=10);j.expires_at=T+timedelta(hours=24);db.commit()
    return h

def tick(c,instant=T,sender=None):
    with Session(c.app.state.engine) as db:return simulate(db,db.get(User,'owner'),sender or Simulator(),instant)

def jobs(c):return c.get('/api/v1/notifications').json()


def test_auth_consent_preferences_revision_and_default_off(client):
    assert client.get('/api/v1/notifications').status_code==401
    h=account(client);s=client.get('/api/v1/notification-settings').json()
    assert not s['enabled'] and not s['external_enabled'] and not s['scheduler_running']
    assert client.post('/api/v1/notifications/simulate').status_code==403
    assert enable(client,h).status_code==200
    assert client.put('/api/v1/notification-settings',headers=h,json={'expected_revision':0}).status_code==409
    assert enable(client,h,quiet_start='08:00',quiet_end='08:00').status_code==422
    assert client.post('/api/v1/notifications/preview',headers=h).status_code==200 and jobs(client)==[]


def test_confirmed_only_dedupe_restart_and_privacy(client):
    h=healthy(client);p=generate(client,h);assert enable(client,h).status_code==200
    client.post('/api/v1/notifications/preview',headers=h);assert not jobs(client)
    assert approve(client,h,p).status_code==200
    client.post('/api/v1/notifications/preview',headers=h);before=jobs(client)
    assert before and all(j['plan_version']==1 for j in before)
    client.post('/api/v1/notifications/preview',headers=h);assert len(jobs(client))==len(before)
    assert all(set(j)<= {'id','state','attempts','due_at','expires_at','processed_at','reason','plan_version','title','kind','body'} for j in before)
    assert not any('pain' in str(j) or '地图' in str(j) for j in before)
    with Session(client.app.state.engine) as db:
        for j in db.scalars(select(NotificationJob)):j.due_at=T-timedelta(minutes=1);j.expires_at=T+timedelta(days=2)
        db.commit()
    assert tick(client)['simulated_count']==2
    assert tick(client)['simulated_count']==0
    assert sum(j['state']=='simulated' for j in jobs(client))==2
    assert all(j['reason']=='仅本地模拟，未发送微信' for j in jobs(client) if j['state']=='simulated')


@pytest.mark.parametrize('kind',['pain','schedule','plan'])
def test_changed_facts_and_version_cancel_pending_immediately(client,kind):
    h=setup(client)
    if kind=='pain':client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':2,'pain_level':2,'pain_location':'合成膝部','function_affected':False,'training_readiness':'ready'})
    elif kind=='schedule':client.put('/api/v1/availability',headers=h,json={'days':[]})
    else:assert approve(client,h,generate(client,h)).status_code==200
    assert all(j['state']=='cancelled' for j in jobs(client))
    if kind!='plan':assert tick(client)['simulated_count']==0


@pytest.mark.parametrize('a,b,hour,expected',[('22:00','08:00',23,8),('22:00','08:00',7,8),('12:00','14:00',13,14),('22:00','08:00',9,9)])
def test_quiet_windows_timezone(a,b,hour,expected):
    from zoneinfo import ZoneInfo
    p=Preferences(quiet_start=a,quiet_end=b)
    local=datetime.combine(TODAY,datetime.min.time(),ZoneInfo('Asia/Shanghai')).replace(hour=hour)
    result=quiet_until(local.astimezone(timezone.utc),p,'Asia/Shanghai').astimezone(ZoneInfo('Asia/Shanghai'))
    assert result.hour==expected
    assert result.date()==TODAY+timedelta(days=1 if hour==23 else 0)


def test_quiet_defers_update_but_cancels_expiring_training(client):
    h=setup(client);enable(client,h,quiet_enabled=True,quiet_start='15:00',quiet_end='18:00')
    client.post('/api/v1/notifications/preview',headers=h)
    with Session(client.app.state.engine) as db:
        for j in db.scalars(select(NotificationJob)):
            j.due_at=T-timedelta(minutes=1);j.expires_at=T+timedelta(minutes=30) if j.payload['kind']=='training_reminder' else T+timedelta(hours=24)
        db.commit()
    assert tick(client)['simulated_count']==0
    result=jobs(client)
    assert any(j['reason']=='免打扰延期' and j['state']=='pending' for j in result)
    assert any(j['reason']=='免打扰结束时已过期' and j['state']=='cancelled' for j in result)


def test_disable_and_reenable_does_not_replay_simulated(client):
    h=setup(client);assert tick(client)['simulated_count']==1
    assert enable(client,h,enabled=False).status_code==200
    assert all(j['state']!='pending' for j in jobs(client))
    assert enable(client,h).status_code==200
    client.post('/api/v1/notifications/preview',headers=h)
    assert sum(j['state']=='simulated' for j in jobs(client))==1
    assert tick(client)['simulated_count']==0


def test_expiry_and_daily_limit_do_not_send_stale_training(client):
    h=setup(client);enable(client,h,daily_limit=1)
    client.post('/api/v1/notifications/preview',headers=h)
    with Session(client.app.state.engine) as db:
        for j in db.scalars(select(NotificationJob)):
            j.due_at=T-timedelta(minutes=1);j.expires_at=T+timedelta(minutes=10)
        db.commit()
    assert tick(client)['simulated_count']==1
    assert all(j['state'] in ('simulated','cancelled') for j in jobs(client))
    assert tick(client,T+timedelta(minutes=20))['simulated_count']==0


def test_failure_retry_bound_and_unknown_no_retry_no_raw_exception(client):
    h=setup(client)
    class Fail:
        def send(self,payload):return 'retryable_failure'
    for minutes in (0,5,10):assert tick(client,T+timedelta(minutes=minutes),Fail())['simulated_count']==0
    row=next(j for j in jobs(client) if j['kind']=='plan_updated');assert row['state']=='failed' and row['attempts']==3
    assert tick(client,T+timedelta(minutes=15),Fail())['simulated_count']==0
    # A new formal version creates a distinct pending update; ambiguous submissions never retry.
    assert approve(client,h,generate(client,h)).status_code==200
    client.post('/api/v1/notifications/preview',headers=h)
    with Session(client.app.state.engine) as db:
        for j in db.scalars(select(NotificationJob).where(NotificationJob.state=='pending')):j.due_at=T;j.expires_at=T+timedelta(hours=1)
        db.commit()
    class Unknown:
        def send(self,payload):raise RuntimeError('secret-must-never-appear')
    tick(client,sender=Unknown());snapshot=jobs(client);assert any(j['state']=='unknown' for j in snapshot)
    assert 'secret-must-never-appear' not in str(snapshot)
    tick(client);assert jobs(client)==snapshot


def test_concurrent_simulation_single_winner(client):
    h=setup(client)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(lambda _:tick(client),range(2)))
    assert sum(r['simulated_count'] for r in results)==1
    assert next(j for j in jobs(client) if j['kind']=='plan_updated')['attempts']==1


def test_weather_bound_plan_invalidates_notifications(client):
    from app.models import WeatherState,PlanVersion
    import copy
    h=setup(client)
    with Session(client.app.state.engine) as db:
        db.add(WeatherState(user_id='owner',revision=1,payload={'location':{'name':'合成城市'},'forecast':{'expires_at':(T+timedelta(days=1)).isoformat()}}))
        plan=db.scalar(select(PlanVersion));payload=copy.deepcopy(plan.payload);payload['weather_snapshot']={'revision':1,'expires_at':(T+timedelta(days=1)).isoformat()};plan.payload=payload;db.commit()
    with Session(client.app.state.engine) as db:row=db.get(WeatherState,'owner');row.revision+=1;db.commit()
    assert tick(client)['simulated_count']==0
    assert all(j['state']=='cancelled' and j['reason']=='天气依据已变化或过期' for j in jobs(client))
    assert client.get('/api/v1/plans/current').json()['plan_version']==1


def test_notification_history_is_user_scoped(client):
    h=setup(client);before=jobs(client)
    with Session(client.app.state.engine) as db:
        db.add(User(id='other',username='synthetic-other',password_hash='unused-synthetic',profile={},availability=[]));db.flush()
        db.add(NotificationJob(user_id='other',dedupe_key='other-only',plan_version=1,facts_revision=1,settings_revision=1,due_at=T,expires_at=T+timedelta(hours=1),payload={'title':'other-private'},state='pending',attempts=0,reason=''));db.commit()
    assert jobs(client)==before
    assert tick(client)['simulated_count']==1
    with Session(client.app.state.engine) as db:assert db.scalar(select(NotificationJob).where(NotificationJob.user_id=='other')).state=='pending'
