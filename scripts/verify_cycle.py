"""Opt-in synthetic full-cycle DeepSeek acceptance; never reads the personal DB."""
import json,os,sys
from pathlib import Path
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings
from app.main import create_app
from app.models import AIBudget,AICall,WeatherState
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from alembic.config import Config
from alembic import command


def main():
    base=Settings()
    if not base.deepseek_api_key or base.deepseek_base_url.rstrip('/')!='https://api.deepseek.com':
        print('请配置官方DeepSeek接口与本机密钥，密钥勿发到聊天。');return 2
    scenario=sys.argv[1] if len(sys.argv)>1 else 'joint'
    if scenario not in ('joint','pain','reduce','weather','weather_rain'):print('场景仅支持joint/pain/reduce/weather/weather_rain');return 2
    url='sqlite:///'+str(ROOT/'private/cycle-smoke.db');os.environ['DATABASE_URL']=url
    command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')
    cfg=base.model_copy(update={'app_env':'test','database_url':url,'private_storage_dir':ROOT/'private/cycle-smoke-images','app_public_url':'http://localhost:5174','ai_provider':'mock','ai_monthly_budget_cny':min(base.ai_monthly_budget_cny,.5)})
    app=create_app(cfg);today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    with TestClient(app) as c:
        route='/api/v1/auth/login' if c.get('/api/v1/auth/status').json()['initialized'] else '/api/v1/auth/setup'
        result=c.post(route,json={'username':'cycle-synthetic','password':'synthetic-cycle-test-only'});result.raise_for_status();h={'X-CSRF-Token':result.json()['csrf_token']}
        p=c.get('/api/v1/profile').json();p.update(display_name='合成周期样本',age=35,sex='male',goal_date=(today+timedelta(days=45)).isoformat(),goal_duration_seconds=7200,health_notes='',doctor_restrictions='',strength_experience='some',strength_equipment=['bodyweight'],strength_max_minutes=30,familiar_exercises=['calf_raise','sit_to_stand','wall_press_up'])
        c.patch('/api/v1/profile',headers=h,json=p).raise_for_status()
        if not c.get('/api/v1/workouts').json():
            for offset in (1,4,8,11,15,18,22,25):
                c.post('/api/v1/workouts',headers=h,json={'date':(today-timedelta(days=offset)).isoformat(),'sport':'road_run','distance_m':5000,'duration_seconds':1800,'effort':3,'ascent_m':10,'notes':'合成周期联调，不是真实个人资料'}).raise_for_status()
        c.post('/api/v1/checkins',headers=h,json={'date':today.isoformat(),'fatigue':5 if scenario=='reduce' else 2,'pain_level':2 if scenario=='pain' else 0,'pain_location':'合成膝部症状' if scenario=='pain' else '', 'soreness_level':0,'function_affected':False,'training_readiness':'reduce' if scenario=='reduce' else 'ready','notes':'合成场景，非用户真实状态'}).raise_for_status()
        state=c.get('/api/v1/plans/current').json()
        c.put('/api/v1/cycle-schedule',headers=h,json={'start_date':today.isoformat(),'slots':[{'weekday':i,'duration_minutes':60} for i in range(7)],'preserve_existing':False,'expected_facts_revision':state['facts_revision']}).raise_for_status()
        weather_revision=None
        if scenario.startswith('weather'):
            # Entirely synthetic forecast: neither a real city nor personal location.
            from app.weather import VARIABLES,SOURCE
            t=datetime.combine(today,datetime.strptime(p['preferred_time'],'%H:%M').time(),ZoneInfo(p['timezone'])).astimezone(timezone.utc)
            hours=[{'time':(t+timedelta(hours=i)).isoformat(),**{k:95 if k=='weather_code' and scenario=='weather' else 0 if k=='weather_code' else 1 if k=='precipitation' and scenario=='weather_rain' else 0 if k=='precipitation' else 20 for k in VARIABLES}} for i in range(168)]
            with Session(app.state.engine) as db:
                row=db.get(WeatherState,'owner');weather_revision=row.revision+1 if row else 1
                weather_payload={'location':{'name':'合成城市（非真实预报）'},'forecast':{'hours':hours,'units':{'time':'unixtime',**VARIABLES},'source':SOURCE,'provider':'synthetic_fixture','fetched_at':datetime.now(timezone.utc).isoformat(),'expires_at':(datetime.now(timezone.utc)+timedelta(hours=2)).isoformat()}}
                if row:row.revision=weather_revision;row.payload=weather_payload
                else:db.add(WeatherState(user_id='owner',revision=weather_revision,payload=weather_payload))
                db.commit()
            cfg.weather_provider='open_meteo'
        with Session(app.state.engine) as db:before_ids={r.id for r in db.query(AICall).all()}
        cfg.ai_provider='deepseek'
        original_read=app.state.planner.read
        def capture(messages):
            result=original_read(messages)
            (ROOT/f'private/cycle-provider-{scenario}.json').write_text(json.dumps({'messages':messages,'content':result[0],'finish':result[2]},ensure_ascii=False,indent=2))
            return result
        app.state.planner.read=capture
        state=c.get('/api/v1/plans/current').json()
        r=c.post('/api/v1/cycle-proposals',headers=h,json={'start_date':today.isoformat(),'allow_external_ai':True,'expected_facts_revision':state['facts_revision'],'expected_plan_version':state['plan_version'],**({'include_weather':True,'avoid_rain':scenario=='weather_rain','expected_weather_revision':weather_revision} if weather_revision else {})})
        payload=r.json();types={s['type'] for s in payload.get('sessions',[])}
        passed=r.status_code==201 and (types=={'recovery'} if scenario=='pain' else {'run','recovery'}<=types)
        if scenario.startswith('weather') and r.status_code==201:
            evidence=payload.get('weather_snapshot',{});blocked={d['date'] for d in evidence.get('days',[]) if d['blocked_reason']}
            passed=passed and len(blocked)==7 and not any(s['type']=='run' and s['date'] in blocked for s in payload['sessions']) and any(s['type']=='run' and s['date'] not in blocked for s in payload['sessions'])
            with Session(app.state.engine) as db:
                row=db.get(WeatherState,'owner');row.revision+=1;db.commit()
            proposal=next(x for x in c.get('/api/v1/plans/current').json()['proposals'] if x['id']==payload['id'])
            confirmation=c.post('/api/v1/plan-proposals/'+payload['id']+'/approve',headers=h,json={'expected_plan_version':payload['base_version'],'expected_facts_revision':payload['facts_revision']})
            passed=passed and proposal['state']=='expired' and confirmation.status_code==409
        assert c.get('/api/v1/plans/current').json()['plan'] is None
        with Session(app.state.engine) as db:
            budget=sum(b.committed_micro for b in db.query(AIBudget).all())/1e6
            calls=[r for r in db.query(AICall).all() if r.id not in before_ids]
            call_ids=[r.id for r in calls];run_cost=sum(r.charged_micro if r.charged_micro is not None else r.reserved_micro for r in calls)/1e6
        report={'call_ids':call_ids,'run_estimated_cost_cny':run_cost,'weather_kind':'synthetic_only' if weather_revision else None,'scenario':scenario,'data_kind':'synthetic_only','http':r.status_code,'passed':passed,'no_plan_approved':True,'session_count':len(payload.get('sessions',[])),'provider':payload.get('provider'),'types':sorted(types),'monthly_budget_used_cny':budget,'response':payload}
        (ROOT/f'private/cycle-smoke-{scenario}.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
        print(json.dumps({k:v for k,v in report.items() if k!='response'},ensure_ascii=False))
        return 0 if passed else 1

if __name__=='__main__':raise SystemExit(main())
