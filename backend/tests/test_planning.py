import copy
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from zoneinfo import ZoneInfo
from datetime import datetime
import pytest
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.models import PlanVersion, PlanOutbox, User
from app.planning import gather, validate_plan
from test_app import client, account, workout

TODAY=datetime.now(ZoneInfo('Asia/Shanghai')).date()

def healthy(client):
    h=account(client)
    p=client.get('/api/v1/profile').json();p.update(strength_experience='some',strength_equipment=['bodyweight','resistance_band'],strength_max_minutes=30,familiar_exercises=['wall_press_up'])
    assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    slots=[{'date':(TODAY+timedelta(days=i)).isoformat(),'available':True,'duration_minutes':30} for i in range(7)]
    assert client.put('/api/v1/availability',headers=h,json={'days':slots}).status_code==200
    assert client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':2,'pain_level':0,'soreness_level':0,'function_affected':False,'training_readiness':'ready'}).status_code==201
    return h

def generate(c,h):
    r=c.post('/api/v1/plan-proposals',headers=h,json={'start_date':TODAY.isoformat()});assert r.status_code==201,r.text;return r.json()

def approve(c,h,p):return c.post('/api/v1/plan-proposals/'+p['id']+'/approve',headers=h,json={'expected_plan_version':p['base_version'],'expected_facts_revision':p['facts_revision']})

def test_joint_plan_requires_confirmation_and_idempotent(client):
    h=healthy(client);p=generate(client,h)
    assert p['is_mock'] and len(p['sessions'])==7
    assert {'run','recovery'}=={s['type'] for s in p['sessions']}
    assert all(s['duration_minutes']<=30 for s in p['sessions'])
    assert client.get('/api/v1/plans/current').json()['plan'] is None
    first=approve(client,h,p);again=approve(client,h,p)
    assert first.status_code==200 and again.json()['id']==first.json()['id']
    with Session(client.app.state.engine) as db:
        assert db.query(PlanVersion).count()==1 and db.query(PlanOutbox).count()==1
    assert 'AI 跑步训练教练' in client.app.state.coach_role

def test_strength_disabled_preserves_history_and_running_context(client):
    from app.models import StrengthWorkout
    from app.coach_provider import messages_for
    from app.planning import PlanData
    import json
    h=healthy(client)
    with Session(client.app.state.engine) as db:
        db.add(StrengthWorkout(id='legacy-strength',user_id='owner',payload={'date':TODAY.isoformat(),'duration_seconds':600,'status':'completed'}));db.commit()
        context=gather(db,db.get(User,'owner'))
        assert context['running_only'] and context['strength']==[]
        messages,_,ids=messages_for(context,TODAY,'合成跑步教练',PlanData.model_json_schema())
        facts=json.loads(messages[1]['content'].split('\n',1)[1])
        assert 'strength' not in facts and not any(k.startswith('strength_') for k in facts['profile'])
        assert 'legacy-strength' not in ids and facts['allowed_exercises']==[]
    p=generate(client,h)
    assert not any('力量' in q for q in p['questions'])
    assert client.get('/api/v1/exercises').json()==[]
    assert client.get('/api/v1/strength-workouts').json()==[]
    assert client.post('/api/v1/strength-workouts',headers=h,json={'date':TODAY.isoformat(),'duration_seconds':0,'status':'skipped'}).status_code==410
    with Session(client.app.state.engine) as db:assert db.get(StrengthWorkout,'legacy-strength') is not None
    assert approve(client,h,p).status_code==200

def test_soreness_pain_and_reduce_affect_both_types(client):
    h=healthy(client);p=generate(client,h);assert approve(client,h,p).status_code==200
    client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':5,'pain_level':0,'soreness_level':0,'function_affected':False,'training_readiness':'reduce'})
    reduced=generate(client,h)
    assert any(s['type']=='run' for s in reduced['sessions']) and not any(s['type']=='strength' for s in reduced['sessions'])
    assert all(s['duration_minutes']<=15 for s in reduced['sessions'])
    assert all(e['sets']==1 for s in reduced['sessions'] for e in s['exercises'])
    assert client.get('/api/v1/plans/current').json()['needs_review']
    with Session(client.app.state.engine) as db:
        assert db.query(PlanOutbox).filter_by(state='pending').count()==0
        assert db.query(PlanOutbox).filter_by(state='cancelled').count()==1
    client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':2,'pain_level':2,'pain_location':'膝内侧','soreness_level':3,'soreness_location':'大腿','function_affected':False,'training_readiness':'ready'})
    resting=generate(client,h)
    assert all(s['type']=='recovery' for s in resting['sessions'])
    assert any('关节痛' in q for q in resting['questions'])
    assert approve(client,h,reduced).status_code==409
    assert approve(client,h,resting).status_code==200

@pytest.mark.parametrize('change',['profile','schedule','run','checkin'])
def test_new_facts_expire_proposals(client,change):
    h=healthy(client);p=generate(client,h)
    if change=='profile':
        value=client.get('/api/v1/profile').json();value['display_name']='修改测试';client.patch('/api/v1/profile',headers=h,json=value)
    elif change=='schedule':client.put('/api/v1/availability',headers=h,json={'days':[]})
    elif change=='run':client.post('/api/v1/workouts',headers=h,json={**workout(),'date':TODAY.isoformat()})
    else:client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':3})
    assert approve(client,h,p).status_code==409

def test_validator_rejects_unknown_equipment_shared_time_and_bad_evidence(client):
    h=healthy(client);p=generate(client,h)
    with Session(client.app.state.engine) as db:
        context=gather(db,db.get(User,'owner'))
        payload={k:p[k] for k in ('summary','assessment','questions','uncertainties','evidence_ids','sessions')}
        wrong=copy.deepcopy(payload);next(s for s in wrong['sessions'] if s['type']=='run')['exercises']=[{'exercise_id':'wall_press_up','sets':1,'repetitions':5,'rest_seconds':60}]
        with pytest.raises(ValueError):validate_plan(wrong,context,TODAY)
        wrong=copy.deepcopy(payload);wrong['evidence_ids']=['other-user-record']
        with pytest.raises(ValueError):validate_plan(wrong,context,TODAY)
        wrong=copy.deepcopy(payload);next(s for s in wrong['sessions'] if s['type']=='run')['duration_minutes']=31
        with pytest.raises(ValueError):validate_plan(wrong,context,TODAY)
        wrong=copy.deepcopy(payload);run=next(s for s in wrong['sessions'] if s['type']=='run');extra=copy.deepcopy(next(s for s in wrong['sessions'] if s['type']=='run'));extra['date']=run['date'];extra['id']='extra';wrong['sessions'].append(extra)
        with pytest.raises(ValueError):validate_plan(wrong,context,TODAY)

def test_reject_restore_and_preserve_history(client):
    h=healthy(client);p=generate(client,h);assert approve(client,h,p).status_code==200
    new=generate(client,h)
    assert client.post('/api/v1/plan-proposals/'+new['id']+'/reject',headers=h).status_code==200
    assert approve(client,h,new).status_code==409
    current=client.get('/api/v1/plans/current').json()
    r=client.post('/api/v1/plans/restore-proposal',headers=h,json={'source_version':0,'start_date':TODAY.isoformat(),'expected_plan_version':current['plan_version'],'expected_facts_revision':current['facts_revision']})
    assert r.status_code==201
    assert client.get('/api/v1/plans/current').json()['plan_version']==1
    assert approve(client,h,r.json()).status_code==200
    assert len(client.get('/api/v1/plans/versions').json())==2
    with Session(client.app.state.engine) as db:
        assert db.query(PlanOutbox).filter_by(state='pending').count()==1
        assert db.query(PlanOutbox).filter_by(state='cancelled').count()==1

def test_concurrent_approval_creates_one_whole_plan(client):
    h=healthy(client);p=generate(client,h)
    def call(_):
        with TestClient(client.app) as other:
            other.cookies.update(client.cookies)
            return approve(other,h,p)
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(call,range(2)))
    assert any(r.status_code==200 for r in results)
    assert all(r.status_code in (200,409) for r in results)
    with Session(client.app.state.engine) as db:assert db.query(PlanVersion).count()==1 and db.query(PlanOutbox).count()==1

def test_user_scope_and_incomplete_profile(client):
    h=account(client);p=generate(client,h)
    assert all(s['type']=='recovery' for s in p['sessions'])
    assert p['questions']
    saved=client.post('/api/v1/checkins',headers=h,json={'date':TODAY.isoformat(),'fatigue':2}).json()
    assert client.get('/api/v1/plan-evidence/'+saved['id']).json()['fatigue']==2
    from app.security import hash_password
    from app.schemas import Profile
    with Session(client.app.state.engine) as db:db.add(User(id='second',username='second',password_hash=hash_password('synthetic-only-password'),profile=Profile().model_dump(mode='json')));db.commit()
    client.post('/api/v1/auth/logout',headers=h)
    auth=client.post('/api/v1/auth/login',json={'username':'second','password':'synthetic-only-password'}).json()
    assert approve(client,{'X-CSRF-Token':auth['csrf_token']},p).status_code==404
    assert client.get('/api/v1/strength-workouts').json()==[]
    assert client.get('/api/v1/plan-evidence/'+saved['id']).status_code==404

def test_deepseek_plan_consent_role_budget_and_validation(client):
    import json
    from app.planning import mock_plan
    from app.models import AICall
    h=healthy(client);cfg=client.app.state.settings
    with Session(client.app.state.engine) as db:example=mock_plan(gather(db,db.get(User,'owner')),TODAY)
    class SyntheticPlanner:
        def read(self,messages):
            assert 'AI 跑步训练教练' in messages[0]['content']
            assert '只能提出草稿' in messages[0]['content']
            assert 'selection_note' in messages[1]['content']
            assert 'password' not in messages[1]['content']
            return json.dumps(example,ensure_ascii=False),{'prompt_tokens':2000,'completion_tokens':1000},'stop'
    cfg.ai_provider='deepseek';client.app.state.planner=SyntheticPlanner()
    assert client.post('/api/v1/plan-proposals',headers=h,json={'start_date':TODAY.isoformat()}).status_code==422
    result=client.post('/api/v1/plan-proposals',headers=h,json={'start_date':TODAY.isoformat(),'allow_external_ai':True})
    assert result.status_code==201 and result.json()['is_mock'] is False
    assert client.get('/api/v1/plans/current').json()['plan'] is None
    with Session(client.app.state.engine) as db:
        call=db.query(AICall).one();assert call.task_kind=='coach_plan' and call.import_id is None and call.charged_micro==12000
    assert client.get('/api/v1/ai/usage').json()['committed_cny']==.012
    cfg.ai_monthly_budget_cny=0
    assert client.post('/api/v1/plan-proposals',headers=h,json={'start_date':TODAY.isoformat(),'allow_external_ai':True}).status_code==402

def test_failed_coach_never_changes_formal_plan(client):
    from app.recognition import VisionFailure
    h=healthy(client);p=generate(client,h);assert approve(client,h,p).status_code==200
    class Failing:
        def read(self,messages):raise VisionFailure('合成超时')
    client.app.state.settings.ai_provider='deepseek';client.app.state.planner=Failing()
    result=client.post('/api/v1/plan-proposals',headers=h,json={'start_date':TODAY.isoformat(),'allow_external_ai':True})
    assert result.status_code==502
    assert client.get('/api/v1/plans/current').json()['plan_version']==1
    assert client.get('/api/v1/ai/usage').json()['committed_cny']>0


def test_band_requires_familiar_action_and_confirmed_resistance(client):
    h=healthy(client);p=client.get('/api/v1/profile').json()
    p.update(strength_equipment=['resistance_band'],familiar_exercises=['band_biceps_curl'],band_resistance='')
    assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    unconfirmed=generate(client,h)
    assert not any(s['type']=='strength' for s in unconfirmed['sessions'])
    p['band_resistance']='合成测试：自己的已熟悉轻阻力带'
    client.patch('/api/v1/profile',headers=h,json=p)
    draft=generate(client,h)
    prescriptions=[e for s in draft['sessions'] for e in s['exercises']]
    assert prescriptions==[]
    assert not any(s['type']=='strength' for s in draft['sessions'])
    with Session(client.app.state.engine) as db:
        context=gather(db,db.get(User,'owner'))
        bad={k:copy.deepcopy(draft[k]) for k in ('summary','assessment','questions','uncertainties','evidence_ids','sessions')}
        injected=next(s for s in bad['sessions'] if s['type']=='run');injected['type']='strength';injected['exercises']=[{'exercise_id':'wall_press_up','sets':1,'repetitions':5,'rest_seconds':60}]
        with pytest.raises(ValueError):validate_plan(bad,context,TODAY)
    assert approve(client,h,draft).status_code==200


def test_race_date_is_separate_review_and_profile_change_stales_draft(client):
    h=healthy(client);old=generate(client,h)
    profile=client.get('/api/v1/profile').json();profile['goal_date']=TODAY.isoformat()
    assert client.patch('/api/v1/profile',headers=h,json=profile).status_code==200
    assert approve(client,h,old).status_code==409
    proposal=generate(client,h)
    race=[s for s in proposal['sessions'] if s['date']==TODAY.isoformat()]
    assert len(race)==1 and race[0]['type']=='recovery' and race[0]['duration_minutes']==0
    with Session(client.app.state.engine) as db:
        context=gather(db,db.get(User,'owner'))
        payload={k:proposal[k] for k in ('summary','assessment','questions','uncertainties','evidence_ids','sessions')}
        for kind in ('run',):
            wrong=copy.deepcopy(payload)
            replacement=copy.deepcopy(next(s for s in wrong['sessions'] if s['type']==kind))
            replacement['date']=TODAY.isoformat();replacement['id']=race[0]['id']
            wrong['sessions'][0]=replacement
            with pytest.raises(ValueError,match='比赛日期须单独核对'):
                validate_plan(wrong,context,TODAY)
    summary=client.get('/api/v1/assessment').json()
    assert summary['race_cycle']['days_remaining']==0
    assert client.get('/api/v1/plans/current').json()['plan'] is None


def test_legacy_strength_proposal_cannot_apply(client):
    from app.models import PlanProposal
    h=healthy(client);p=generate(client,h)
    with Session(client.app.state.engine) as db:
        row=db.get(PlanProposal,p['id']);payload=copy.deepcopy(row.payload)
        course=next(s for s in payload['sessions'] if s['type']=='run')
        course['type']='strength';course['exercises']=[{'exercise_id':'wall_press_up','sets':1,'repetitions':5,'rest_seconds':60}]
        row.payload=payload;db.commit()
    assert client.get('/api/v1/plans/current').json()['proposals'][0]['state']=='expired'
    assert approve(client,h,p).status_code==409
    assert client.get('/api/v1/plans/current').json()['plan'] is None
