"""Offline checks for the opt-in live harness; no API or personal database access."""
import importlib.util
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
from test_app import client, account

spec=importlib.util.spec_from_file_location('synthetic_live_harness',Path(__file__).resolve().parents[2]/'scripts/verify_deepseek.py')
harness=importlib.util.module_from_spec(spec)
spec.loader.exec_module(harness)


def test_fixture_sequence_and_no_automatic_approval(client):
    headers=account(client)
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    for scenario in ('joint','reduce','pain'):
        harness.seed_scenario(client,headers,scenario,today)
        result=client.post('/api/v1/plan-proposals',headers=headers,json={'start_date':today.isoformat()})
        assert result.status_code==201
        assert harness.inspect_scenario(result.json(),scenario)
        assert client.get('/api/v1/plans/current').json()['plan'] is None
        assert len(client.get('/api/v1/workouts').json())==1
        assert client.get('/api/v1/strength-workouts').json()==[]
        if scenario=='reduce':
            assert all(s['duration_minutes']<=15 for s in result.json()['sessions'])
            assert all(e['sets']==1 for s in result.json()['sessions'] for e in s['exercises'])
    assert all(p['state']=='expired' for p in client.get('/api/v1/plans/current').json()['proposals'][1:])


def test_all_rest_cannot_pass_joint_acceptance():
    rest={'sessions':[{'type':'recovery'}]}
    assert not harness.inspect_scenario(rest,'joint')
    assert not harness.inspect_scenario(rest,'reduce')
    assert harness.inspect_scenario(rest,'pain')
    assert not harness.inspect_scenario({'sessions':[]},'pain')


def test_provider_contract_uses_confirmed_exercises_and_literal_resistance():
    import json
    from app.coach_provider import messages_for
    from app.schemas import Profile
    from app.planning import PlanData
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    profile=Profile(strength_equipment=['resistance_band'],familiar_exercises=['band_biceps_curl','calf_raise'],band_resistance='合成已熟悉阻力',strength_max_minutes=30)
    context={'profile':profile,'workouts':[],'strength':[],'checkins':[],'days':[],'old':None,'latest':{'date':today.isoformat(),'pain_level':0,'soreness_level':0,'function_affected':False,'training_readiness':'reduce'}}
    messages,_,ids=messages_for(context,today,'合成角色',PlanData.model_json_schema())
    facts=json.loads(messages[1]['content'].split('\n',1)[1])
    assert [e['id'] for e in facts['allowed_exercises']]==['band_biceps_curl']
    assert facts['server_policy']['band_resistance_exact_literal']=='合成已熟悉阻力'
    assert facts['server_policy']['strength_duration_cap_minutes']==10
    assert facts['server_policy']['same_day_run_and_strength_allowed'] is False
    assert ids==set()
    profile.band_resistance=''
    messages,_,_=messages_for(context,today,'合成角色',PlanData.model_json_schema())
    facts=json.loads(messages[1]['content'].split('\n',1)[1])
    assert facts['allowed_exercises']==[]
