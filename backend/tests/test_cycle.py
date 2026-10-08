import copy,json
from datetime import timedelta
from concurrent.futures import ThreadPoolExecutor
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
import pytest
from app.ability import assess
from app.cycle_planning import local_blueprint,expand,validate_cycle
from app.planning import gather
from app.models import User,PlanVersion,PlanOutbox
from test_app import client,workout
from test_planning import healthy,TODAY


def seed(c):
    h=healthy(c)
    p=c.get('/api/v1/profile').json();p.update(goal_date=(TODAY+timedelta(days=45)).isoformat(),goal_duration_seconds=6600,familiar_exercises=['calf_raise','wall_press_up','sit_to_stand'])
    assert c.patch('/api/v1/profile',headers=h,json=p).status_code==200
    for offset in (1,4,8,11,15,18,22,25):
        assert c.post('/api/v1/workouts',headers=h,json={**workout(),'date':(TODAY-timedelta(days=offset)).isoformat(),'effort':3,'ascent_m':10}).status_code==201
    state=c.get('/api/v1/plans/current').json()
    r=c.put('/api/v1/cycle-schedule',headers=h,json={'start_date':TODAY.isoformat(),'slots':[{'weekday':i,'duration_minutes':60} for i in range(7)],'preserve_existing':False,'expected_facts_revision':state['facts_revision']})
    assert r.status_code==200,r.text
    return h


def make(c,h):
    state=c.get('/api/v1/plans/current').json()
    r=c.post('/api/v1/cycle-proposals',headers=h,json={'start_date':TODAY.isoformat(),'expected_facts_revision':state['facts_revision'],'expected_plan_version':state['plan_version']})
    assert r.status_code==201,r.text
    return r.json()


def approve_cycle(c,h,p):
    return c.post('/api/v1/plan-proposals/'+p['id']+'/approve',headers=h,json={'expected_plan_version':p['base_version'],'expected_facts_revision':p['facts_revision']})


def test_assessment_basis_not_goal_or_trail_prediction(client):
    h=seed(client);a=client.get('/api/v1/assessment').json()['ability']
    assert a['status']=='established' and a['road_records']==8 and a['occupied_weeks']==4
    assert a['baseline_week_minutes']==60 and a['longest_easy_minutes']==30
    assert a['observed_easy_pace']['median_seconds_per_km']==360
    assert not a['observed_easy_pace']['is_training_prescription']
    assert a['goal']['feasibility']=='insufficient_race_specific_evidence'
    assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':TODAY.isoformat(),'sport':'trail_run','duration_seconds':600,'effort':3}).status_code==201
    after=client.get('/api/v1/assessment').json()['ability']
    assert after['envelope']==a['envelope'] and after['road_records']==8


def test_full_cycle_and_whole_approval_idempotency(client):
    h=seed(client);p=make(client,h)
    assert p['scope']=='cycle' and p['provider']=='local_rules' and p['is_mock']
    assert p['end_date']==(TODAY+timedelta(days=52)).isoformat()
    assert len({s['date'] for s in p['sessions']})==53
    assert {'run','recovery'}=={s['type'] for s in p['sessions']}
    assert {'foundation','build','consolidate','taper','race_week','post_race'}<={w['phase'] for w in p['cycle_weeks']}
    for s in p['sessions']:
        if s['date']>=(TODAY+timedelta(days=45)).isoformat():assert s['type']=='recovery'
        if s['type']=='run':assert sum(b['minutes'] for b in s['blocks'])==s['duration_minutes']
    assert client.get('/api/v1/plans/current').json()['plan'] is None
    one=approve_cycle(client,h,p);assert one.status_code==200,one.text
    assert approve_cycle(client,h,p).json()['id']==one.json()['id']
    with Session(client.app.state.engine) as db:
        assert db.query(PlanVersion).count()==1 and db.query(PlanOutbox).count()==1


@pytest.mark.parametrize('mutation',['pain','illness','soreness','high_fatigue','schedule'])
def test_dynamic_update_expires_old_and_joint_limits(client,mutation):
    h=seed(client);old=make(client,h);assert approve_cycle(client,h,old).status_code==200
    pending=make(client,h)
    if mutation=='schedule':assert client.put('/api/v1/availability',headers=h,json={'days':[]}).status_code==200
    else:
        body={'date':TODAY.isoformat(),'fatigue':2,'pain_level':0,'soreness_level':0,'function_affected':False,'training_readiness':'ready'}
        body.update({'pain_level':2,'pain_location':'膝内侧'} if mutation=='pain' else {'illness_notes':'合成：发热'} if mutation=='illness' else {'soreness_level':3,'soreness_location':'大腿'} if mutation=='soreness' else {'fatigue':8})
        assert client.post('/api/v1/checkins',headers=h,json=body).status_code==201
    assert approve_cycle(client,h,pending).status_code==409
    state=client.get('/api/v1/plans/current').json();assert state['needs_review'] and state['plan']['version']==1
    new=make(client,h)
    assert all(s['type']=='recovery' for s in new['sessions'])
    assert client.get('/api/v1/plans/current').json()['plan']['version']==1


def test_repeated_fatigue_and_breakthrough_do_not_upgrade(client):
    h=seed(client);before=make(client,h)
    for offset in (1,0):
        assert client.post('/api/v1/checkins',headers=h,json={'date':(TODAY-timedelta(days=offset)).isoformat(),'fatigue':5 if offset else 2,'pain_level':0,'soreness_level':0,'function_affected':False,'training_readiness':'reduce' if not offset else 'ready'}).status_code==201
    reduced=make(client,h)
    assert reduced['ability']['reduce']
    assert reduced['ability']['envelope']['week_run_minutes_cap']<=before['ability']['envelope']['week_run_minutes_cap']//2
    assert all(e['sets']==1 for s in reduced['sessions'] for e in s['exercises'])
    assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':TODAY.isoformat(),'duration_seconds':1200,'effort':3,'ascent_m':10}).status_code==201
    faster=make(client,h)
    assert faster['ability']['envelope']['single_run_minutes_cap']==reduced['ability']['envelope']['single_run_minutes_cap']
    assert all(s['intensity'] in ('easy','rest') for s in faster['sessions'])


@pytest.mark.parametrize('change',['time','day','exercise','resistance','recovery_blocks','missing_day','duplicate','intensity','unknown_evidence'])
def test_cycle_validator_independent_of_model(client,change):
    seed(client)
    with Session(client.app.state.engine) as db:
        context=gather(db,db.get(User,'owner'));payload,_,_=expand(local_blueprint(context,TODAY),context,TODAY)
        wrong=copy.deepcopy(payload)
        run=next(s for s in wrong['sessions'] if s['type']=='run');rest=next(s for s in wrong['sessions'] if s['type']=='recovery')
        if change=='time':run['duration_minutes']=133
        elif change=='day':run['date']=(TODAY-timedelta(days=1)).isoformat()
        elif change=='exercise':run['exercises']=[{'exercise_id':'invented','sets':1,'repetitions':5,'rest_seconds':60}]
        elif change=='resistance':run['type']='strength';run['exercises']=[{'exercise_id':'wall_press_up','sets':1,'repetitions':5,'rest_seconds':60}]
        elif change=='recovery_blocks':rest['blocks']=[{'label':'偷加训练','minutes':30}]
        elif change=='missing_day':wrong['sessions']=[s for s in wrong['sessions'] if s['date']!=rest['date']]
        elif change=='duplicate':wrong['sessions'].append(copy.deepcopy(run))
        elif change=='intensity':run['intensity']='hard'
        else:wrong['evidence_ids']=['invented-record']
        with pytest.raises(ValueError):validate_cycle(wrong,context,TODAY)


def test_doctor_permission_is_self_report_new_symptoms_still_block(client):
    h=seed(client)
    p=client.get('/api/v1/profile').json();p.update(health_notes='合成：既往膝痛，已就诊',running_permission='user_reports_doctor_allows',medical_review_date=TODAY.isoformat())
    assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    assert not client.get('/api/v1/assessment').json()['ability']['blocked_reasons']
    p['medical_review_date']=(TODAY+timedelta(days=1)).isoformat();assert client.patch('/api/v1/profile',headers=h,json=p).status_code==422
    assert client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':2,'pain_level':2,'pain_location':'膝盖','soreness_level':0,'function_affected':False,'training_readiness':'ready'}).status_code==201
    assert all(s['type']=='recovery' for s in make(client,h)['sessions'])


def test_cycle_concurrent_confirmation_one_version(client):
    h=seed(client);p=make(client,h);cookie=dict(client.cookies)
    def execute(_):
        with TestClient(client.app) as c:
            c.cookies.update(cookie);return approve_cycle(c,h,p).status_code
    with ThreadPoolExecutor(max_workers=2) as pool:statuses=list(pool.map(execute,range(2)))
    assert 200 in statuses and set(statuses)<={200,409}
    with Session(client.app.state.engine) as db:assert db.query(PlanVersion).count()==1 and db.query(PlanOutbox).count()==1


def test_controlled_work_requires_opt_in_repeated_evidence_and_fades_on_reduce(client):
    h=seed(client)
    for offset in (2,9):
        assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':(TODAY-timedelta(days=offset)).isoformat(),'effort':5,'ascent_m':10}).status_code==201
    normal=make(client,h)
    assert not any(s['run_kind']=='controlled' for s in normal['sessions'])
    state=client.get('/api/v1/plans/current').json()
    r=client.post('/api/v1/cycle-proposals',headers=h,json={'start_date':TODAY.isoformat(),'allow_controlled_work':True,'expected_facts_revision':state['facts_revision'],'expected_plan_version':state['plan_version']})
    assert r.status_code==201,r.text
    proposal=r.json();controlled=[s for s in proposal['sessions'] if s['run_kind']=='controlled']
    assert controlled and all(s['intensity']=='moderate' for s in controlled)
    assert approve_cycle(client,h,proposal).status_code==200
    assert client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':5,'pain_level':0,'soreness_level':0,'function_affected':False,'training_readiness':'reduce'}).status_code==201
    state=client.get('/api/v1/plans/current').json()
    reduced=client.post('/api/v1/cycle-proposals',headers=h,json={'start_date':TODAY.isoformat(),'allow_controlled_work':True,'expected_facts_revision':state['facts_revision'],'expected_plan_version':state['plan_version']})
    assert reduced.status_code==201,reduced.text
    assert not any(s['run_kind']=='controlled' for s in reduced.json()['sessions'])


def test_empty_evidence_and_schedule_conflict_and_foreign_read(client):
    h=healthy(client)
    assert client.post('/api/v1/cycle-proposals',headers=h,json={'start_date':TODAY.isoformat(),'expected_facts_revision':1,'expected_plan_version':0}).status_code==409
    p=client.get('/api/v1/profile').json();p['goal_date']=(TODAY+timedelta(days=20)).isoformat();assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    draft=make(client,h)
    assert not any(s['type']=='run' for s in draft['sessions'])
    assert draft['ability']['status']=='insufficient'
    state=client.get('/api/v1/plans/current').json()
    bad=client.put('/api/v1/cycle-schedule',headers=h,json={'start_date':TODAY.isoformat(),'slots':[],'expected_facts_revision':state['facts_revision']-1})
    assert bad.status_code==409
    with TestClient(client.app) as anonymous:assert anonymous.get('/api/v1/assessment').status_code==401


def test_external_cycle_schema_and_failure_keep_manual_records(client):
    h=seed(client)
    cfg=client.app.state.settings;cfg.ai_provider='deepseek';cfg.deepseek_api_key='synthetic-test-key'
    seen=[]
    def read(messages):
        seen.append(json.loads(messages[1]['content'].split('\n',1)[1]))
        with Session(client.app.state.engine) as db:
            blueprint=local_blueprint(gather(db,db.get(User,'owner')),TODAY)
        return json.dumps(blueprint,ensure_ascii=False),{'prompt_tokens':100,'completion_tokens':100},'stop'
    client.app.state.planner.read=read
    state=client.get('/api/v1/plans/current').json();request={'start_date':TODAY.isoformat(),'expected_facts_revision':state['facts_revision'],'expected_plan_version':0}
    assert client.post('/api/v1/cycle-proposals',headers=h,json=request).status_code==422
    r=client.post('/api/v1/cycle-proposals',headers=h,json={**request,'allow_external_ai':True})
    assert r.status_code==201,r.text
    assert r.json()['provider']=='deepseek' and not r.json()['is_mock']
    assert len(seen[0]['workouts'])<=10 and len(seen[0]['checkins'])<=3
    assert 'server_policy' not in seen[0] and 'record_summary' not in seen[0]
    assert seen[0]['week_envelopes'] and seen[0]['availability'][-1]['date']==r.json()['end_date']
    from app.recognition import VisionFailure
    def fail(_):raise VisionFailure('合成网络失败')
    client.app.state.planner.read=fail
    assert client.post('/api/v1/cycle-proposals',headers=h,json={**request,'allow_external_ai':True}).status_code==502
    assert client.get('/api/v1/plans/current').json()['plan'] is None
    assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':TODAY.isoformat(),'effort':3}).status_code==201


def test_cross_week_consecutive_runs_rejected(client):
    seed(client)
    with Session(client.app.state.engine) as db:
        context=gather(db,db.get(User,'owner'));payload,_,_=expand(local_blueprint(context,TODAY),context,TODAY)
        runs=[s for s in payload['sessions'] if s['type']=='run']
        a=next(s for s in runs if s['date']>= (TODAY+timedelta(days=7)).isoformat())
        from datetime import date
        boundary=date.fromisoformat(a['date']);boundary-=timedelta(days=boundary.weekday())
        b=copy.deepcopy(a);b['id']='cross-week-test';b['date']=(boundary-timedelta(days=1)).isoformat()
        original_day=a['date'];filler=copy.deepcopy(next(s for s in payload['sessions'] if s['type']=='recovery'));filler['id']='original-day-rest';filler['date']=original_day
        a['date']=boundary.isoformat();payload['sessions']=[s for s in payload['sessions'] if s['date'] not in (a['date'],b['date']) or s['id']==a['id']]+[b]+([filler] if original_day not in (a['date'],b['date']) else [])
        with pytest.raises(ValueError,match='跨周'):validate_cycle(payload,context,TODAY)


def test_observed_race_result_is_not_a_prediction_and_trail_cannot_substitute(client):
    h=seed(client)
    assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':TODAY.isoformat(),'session_context':'race','sport':'trail_run','distance_m':21100,'duration_seconds':6500,'effort':8}).status_code==201
    assert client.get('/api/v1/assessment').json()['ability']['goal']['feasibility']=='insufficient_race_specific_evidence'
    assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':TODAY.isoformat(),'session_context':'race','distance_m':21100,'duration_seconds':6500,'effort':8}).status_code==201
    goal=client.get('/api/v1/assessment').json()['ability']['goal']
    assert goal['feasibility']=='recorded_at_target' and goal['record_id'] and '不能保证' in goal['note']

@pytest.mark.parametrize('variation,blocked', [('tolerable',False),('unknown',True),('worsening',True),('joint',True),('high',True)])
def test_soreness_requires_explicit_tolerability_and_reduces_both(client,variation,blocked):
    h=seed(client)
    body={'date':TODAY.isoformat(),'fatigue':2,'pain_level':0,'soreness_level':2,'soreness_location':'腿部','soreness_trend':'improving','soreness_tolerability':'tolerable','function_affected':False,'training_readiness':'ready'}
    if variation=='unknown':body['soreness_tolerability']='unknown'
    if variation=='worsening':body['soreness_trend']='worsening'
    if variation=='joint':body['soreness_location']='膝关节'
    if variation=='high':body['soreness_level']=9
    assert client.post('/api/v1/checkins',headers=h,json=body).status_code==201
    p=make(client,h)
    if blocked:assert all(s['type']=='recovery' for s in p['sessions'])
    else:
        assert any(s['type']=='run' for s in p['sessions'])
        assert not any(s['type']=='strength' for s in p['sessions'])
        assert all(e['exercise_id']=='wall_press_up' and e['sets']==1 for s in p['sessions'] for e in s['exercises'])
        assert all(s.get('run_kind')!='controlled' for s in p['sessions'])
    assert approve_cycle(client,h,p).status_code==200


def test_run_spacing_without_strength_and_previous_actual_run(client):
    from app.planning import mock_plan,validate_plan
    h=healthy(client)
    p=client.get('/api/v1/profile').json();p['familiar_exercises']=[]
    assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':(TODAY-timedelta(days=1)).isoformat()}).status_code==201
    with Session(client.app.state.engine) as db:
        context=gather(db,db.get(User,'owner'));payload=mock_plan(context,TODAY)
        validate_plan(payload,context,TODAY)
        runs=[s for s in payload['sessions'] if s['type']=='run']
        assert len(runs)==3 and runs[0]['date']!=(TODAY.isoformat())
        from datetime import date
        assert all((date.fromisoformat(b['date'])-date.fromisoformat(a['date'])).days>=2 for a,b in zip(runs,runs[1:]))
        runs[1]['date']=(date.fromisoformat(runs[0]['date'])+timedelta(days=1)).isoformat()
        # Keep all dates covered while forcing a consecutive run in the model output.
        replacement=copy.deepcopy(next(s for s in payload['sessions'] if s['type']=='recovery'))
        replacement['date']=(TODAY+timedelta(days=3)).isoformat();replacement['id']='fill-date'
        payload['sessions']=[s for s in payload['sessions'] if s['type']=='run' or s['date']!=runs[1]['date']]+[replacement]
        with pytest.raises(ValueError):validate_plan(payload,context,TODAY)


def test_old_doctor_report_unknown_date_does_not_clear_recent_pain(client):
    h=seed(client);p=client.get('/api/v1/profile').json();p.update(running_permission='user_reports_doctor_allows',medical_review_date=None)
    assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':(TODAY-timedelta(days=2)).isoformat(),'pain_notes':'膝内侧痛，尚未核对这次'}).status_code==201
    assert all(s['type']=='recovery' for s in make(client,h)['sessions'])


def test_weekly_three_includes_already_completed_runs(client):
    h=seed(client)
    # Synthetic historical consecutive runs are preserved facts, not instructions to add more.
    for offset in (2,3):
        assert client.post('/api/v1/workouts',headers=h,json={**workout(),'date':(TODAY-timedelta(days=offset)).isoformat(),'effort':3}).status_code==201
    p=make(client,h)
    monday=TODAY-timedelta(days=TODAY.weekday())
    assert not any(s['type']=='run' and s['date']<(monday+timedelta(days=7)).isoformat() for s in p['sessions'])
    assert approve_cycle(client,h,p).status_code==200
