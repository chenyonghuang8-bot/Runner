import copy,json
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from app.models import WeatherState,PlanProposal,PlanVersion,PlanOutbox,User
from app.weather import VARIABLES
from app.cycle_planning import validate_cycle,local_blueprint
from app.planning import gather
from test_app import client
from test_cycle import seed,make,approve_cycle,TODAY
import pytest


def weather(c,code=95,rain=0):
    t=datetime.combine(TODAY,datetime.strptime('20:00','%H:%M').time(),ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc)
    hours=[{'time':(t+timedelta(hours=i)).isoformat(),**{k:code if k=='weather_code' else rain if k=='precipitation' else 20 for k in VARIABLES}} for i in range(7*24)]
    evidence={'hours':hours,'units':{'time':'unixtime',**VARIABLES},'source':'https://open-meteo.com/en/docs','fetched_at':datetime.now(timezone.utc).isoformat(),'expires_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()}
    c.app.state.settings.weather_provider='open_meteo'
    with Session(c.app.state.engine) as db:db.add(WeatherState(user_id='owner',revision=1,payload={'location':{'name':'合成城市'},'forecast':evidence}));db.commit()


def request(c,h,**extra):
    state=c.get('/api/v1/plans/current').json()
    return c.post('/api/v1/cycle-proposals',headers=h,json={'start_date':TODAY.isoformat(),'expected_facts_revision':state['facts_revision'],'expected_plan_version':state['plan_version'],'include_weather':True,'expected_weather_revision':1,**extra})


def change(c,expire=False):
    with Session(c.app.state.engine) as db:
        row=db.get(WeatherState,'owner')
        if expire:
            payload=copy.deepcopy(row.payload);payload['forecast']['expires_at']=(datetime.now(timezone.utc)-timedelta(seconds=1)).isoformat();row.payload=payload
        else:row.revision+=1
        db.commit()


def test_opt_in_thunder_joint_plan_no_debt_and_validation(client):
    h=seed(client);weather(client);plain=make(client,h)
    assert 'weather_snapshot' not in plain and any(s['type']=='run' for s in plain['sessions'] if s['date']<=(TODAY+timedelta(days=6)).isoformat())
    r=request(client,h);assert r.status_code==201,r.text
    p=r.json();excluded={d['date'] for d in p['weather_snapshot']['days'] if d['blocked_reason']}
    assert not any(s['type']=='run' and s['date'] in excluded for s in p['sessions'])
    assert all(s['type']=='recovery' for s in p['sessions'] if s['date'] in excluded)
    assert any(s['type']=='run' and s['date']>(TODAY+timedelta(days=6)).isoformat() for s in p['sessions'])
    assert client.get('/api/v1/plans/current').json()['plan'] is None
    with Session(client.app.state.engine) as db:
        context=gather(db,db.get(User,'owner'));context['weather_snapshot']=p['weather_snapshot']
        forged={k:plain[k] for k in ('summary','assessment','questions','uncertainties','evidence_ids','sessions')}
        with pytest.raises(ValueError,match='天气'):validate_cycle(forged,context,TODAY)
    assert approve_cycle(client,h,p).status_code==200
    assert approve_cycle(client,h,p).status_code==200
    with Session(client.app.state.engine) as db:assert db.query(PlanVersion).count()==1 and db.query(PlanOutbox).count()==1


@pytest.mark.parametrize('expire',[False,True])
def test_weather_change_stales_only_bound_drafts_and_retains_formal(client,expire):
    h=seed(client);weather(client,code=0);plain=make(client,h);p=request(client,h).json()
    assert approve_cycle(client,h,p).status_code==200
    bound=request(client,h).json();unbound=make(client,h)
    change(client,expire)
    state=client.get('/api/v1/plans/current').json();assert state['needs_review'] and state['plan']['version']==1
    assert next(x for x in state['proposals'] if x['id']==bound['id'])['state']=='expired'
    assert next(x for x in state['proposals'] if x['id']==unbound['id'])['state']=='pending'
    assert approve_cycle(client,h,bound).status_code==409
    assert approve_cycle(client,h,p).status_code==200 # Already applied stays idempotent.
    assert approve_cycle(client,h,unbound).status_code==200


def test_rain_option_unknowns_and_preflight_failure(client):
    h=seed(client)
    assert request(client,h).status_code==503
    weather(client,code=0,rain=1)
    assert request(client,h,expected_weather_revision=0).status_code==409
    assert request(client,h,include_weather=False,avoid_rain=True).status_code==422
    p=request(client,h).json();q=request(client,h,avoid_rain=True).json()
    assert not any(d['blocked_reason'] for d in p['weather_snapshot']['days'])
    assert all(d['blocked_reason'] for d in q['weather_snapshot']['days'])
    change(client,True);assert request(client,h).status_code==422


def test_generation_weather_race_refuses_draft_and_filters_external_coordinates(client):
    h=seed(client);weather(client,code=0);client.app.state.settings.ai_provider='deepseek'
    with Session(client.app.state.engine) as db:blueprint=local_blueprint(gather(db,db.get(User,'owner')),TODAY)
    def read(messages):
        facts=json.loads(messages[1]['content'].split('\n',1)[1]);assert facts['weather_snapshot']['city']=='合成城市'
        assert 'latitude' not in facts['weather_snapshot'] and 'longitude' not in facts['weather_snapshot']
        change(client)
        return json.dumps(blueprint),{'prompt_tokens':100,'completion_tokens':100},'stop'
    client.app.state.planner.read=read
    r=request(client,h,allow_external_ai=True);assert r.status_code==409,r.text
    with Session(client.app.state.engine) as db:assert db.query(PlanProposal).count()==0 and db.query(PlanVersion).count()==0


def test_later_hour_storm_is_considered_and_unknown_hours_preserved(client):
    h=seed(client);weather(client,code=0)
    with Session(client.app.state.engine) as db:
        row=db.get(WeatherState,'owner');p=copy.deepcopy(row.payload);p['forecast']['hours']=p['forecast']['hours'][:3];p['forecast']['hours'][2]['weather_code']=95;row.payload=p;db.commit()
    r=request(client,h);assert r.status_code==201,r.text
    days=r.json()['weather_snapshot']['days'];assert days[0]['blocked_reason'] and days[0]['window_complete']
    assert days[1]['hour'] is None and not days[1]['window_complete'] and days[1]['blocked_reason']==''


def test_simultaneous_weather_selection_and_approval_serializes(client):
    from concurrent.futures import ThreadPoolExecutor
    h=seed(client);weather(client,code=0);p=request(client,h).json()
    def select_city():return client.put('/api/v1/weather/location',headers=h,json={'candidate_id':'geonames-1797353','expected_revision':1})
    with ThreadPoolExecutor(max_workers=2) as pool:
        confirmation=pool.submit(approve_cycle,client,h,p);selection=pool.submit(select_city)
        result=confirmation.result();changed=selection.result()
    assert result.status_code in (200,409),result.text
    assert changed.status_code==200,changed.text
    state=client.get('/api/v1/plans/current').json()
    assert state['plan_version']==(1 if result.status_code==200 else 0)
    if result.status_code==200:assert state['needs_review'] and state['plan']['weather_snapshot']['revision']==1
    else:assert state['plan'] is None
