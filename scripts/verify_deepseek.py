"""Opt-in live checks with synthetic fixtures and an isolated persistent budget."""
import json
import os
import sys
from pathlib import Path
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from app.config import Settings
from app.main import create_app
from fastapi.testclient import TestClient
from alembic.config import Config
from alembic import command


def seed_scenario(client, headers, scenario, today):
    """Explicitly synthetic fixtures; this client is bound only to the smoke database."""
    profile=client.get('/api/v1/profile').json()
    profile.update(display_name='合成联合训练样本',age=35,sex='male',running_months=12,
                   health_notes='',doctor_restrictions='',goal_date=(today+timedelta(days=90)).isoformat(),
                   goal_duration_seconds=7200,strength_experience='some',
                   strength_equipment=['bodyweight','resistance_band'],strength_max_minutes=20,
                   band_resistance='合成样本已熟悉的轻阻力',familiar_exercises=['wall_press_up','band_biceps_curl'],
                   same_day_strength=False)
    client.patch('/api/v1/profile',headers=headers,json=profile).raise_for_status()
    client.put('/api/v1/availability',headers=headers,json={'days':[
        {'date':(today+timedelta(days=i)).isoformat(),'available':i in (0,1,3,5),'duration_minutes':30}
        for i in range(7)]}).raise_for_status()
    if not client.get('/api/v1/workouts').json():
        client.post('/api/v1/workouts',headers=headers,json={
            'date':(today-timedelta(days=2)).isoformat(),'title':'合成公路跑样本',
            'sport':'road_run','distance_m':4000,'duration_seconds':1800,'effort':3,
            'pain_notes':'','notes':'仅用于接口验收，不是真实个人资料'}).raise_for_status()
    client.post('/api/v1/checkins',headers=headers,json={
        'date':today.isoformat(),'fatigue':2,'soreness_level':0,
        'pain_level':2 if scenario=='pain' else 0,
        'pain_location':'合成膝内侧症状' if scenario=='pain' else '',
        'pain_timing':'after' if scenario=='pain' else 'none',
        'function_affected':False,'training_readiness':'reduce' if scenario=='reduce' else 'ready',
        'notes':{'joint':'合成跑步排期验收，未反馈疼痛或酸胀。历史 joint 字样只是测试场景代码，不是关节描述。','reduce':'合成主动减量验收，希望缩短跑步并保留恢复；历史场景代码不是身体症状。','pain':'合成疼痛验收，有跑后膝内侧症状，尚未评估，不生成带负荷安排。'}[scenario]}).raise_for_status()


def inspect_scenario(result, scenario):
    sessions=result.get('sessions',[])
    types={s['type'] for s in sessions}
    # A structurally accepted all-rest answer does not pass the joint/reduce scenario.
    if scenario=='pain':return bool(sessions) and types=={'recovery'}
    return {'run','recovery'} <= types


def main():
    try:base=Settings()
    except Exception:
        print('配置无效，请检查本机 .env；不输出配置值。');return 2
    if not base.deepseek_api_key:
        print('尚未配置 DEEPSEEK_API_KEY。请在本机 .env 填写，勿发到聊天。');return 2
    if base.deepseek_base_url.rstrip('/')!='https://api.deepseek.com':
        print('合成联调只允许官方 https://api.deepseek.com 地址。');return 2
    storage=ROOT/'private/deepseek-smoke-images';storage.mkdir(parents=True,exist_ok=True)
    url='sqlite:///'+str(ROOT/'private/deepseek-smoke.db')
    os.environ['DATABASE_URL']=url
    command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')
    cfg=base.model_copy(update={'app_env':'test','database_url':url,'private_storage_dir':storage,'app_public_url':'http://localhost:5174','ai_provider':'deepseek','ai_coach_max_output_tokens':8192,'ai_monthly_budget_cny':min(base.ai_monthly_budget_cny,.5)})
    scenario=next((arg.split('=',1)[1] for arg in sys.argv if arg.startswith('--scenario=')),None)
    if scenario not in (None,'joint','reduce','pain'):
        print('scenario 仅支持 joint、reduce、pain');return 2
    app=create_app(cfg);report={'data_kind':'synthetic_only','monthly_smoke_budget_cny':cfg.ai_monthly_budget_cny,'checks':[]}
    with TestClient(app) as c:
        route='/api/v1/auth/login' if c.get('/api/v1/auth/status').json()['initialized'] else '/api/v1/auth/setup'
        auth=c.post(route,json={'username':'synthetic-smoke','password':'synthetic-local-smoke-only'});auth.raise_for_status()
        headers={'X-CSRF-Token':auth.json()['csrf_token']}
        today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
        if scenario:
            seed_scenario(c,headers,scenario,today)
            report['scenario']=scenario
        tests=[('/api/v1/coach/messages',{'message':'这是合成测试，尚无训练资料。请询问制定跑步课程还缺哪些信息，不生成具体训练或医疗处方。','allow_external_ai':True}),('/api/v1/plan-proposals',{'start_date':datetime.now(ZoneInfo('Asia/Shanghai')).date().isoformat(),'allow_external_ai':True})]
        if '--plan-only' in sys.argv or scenario:tests=tests[1:]
        for path,payload in tests:
            r=c.post(path,headers=headers,json=payload)
            report['checks'].append({'endpoint':path,'status':r.status_code,'result':r.json()})
            if scenario and r.status_code==201:
                report['scenario_behavior_passed']=inspect_scenario(r.json(),scenario)
            print(path,'通过' if r.status_code in (200,201) else '未通过',r.status_code)
        report['usage']=c.get('/api/v1/ai/usage').json()
        report['formal_plan_unchanged']=c.get('/api/v1/plans/current').json()['plan'] is None
        print('合成库累计预算占用（含预留）：',report['usage']['committed_cny'],'元；正式计划未更新：',report['formal_plan_unchanged'])
    (ROOT/('private/deepseek-'+scenario+'-report.json' if scenario else 'private/deepseek-smoke-report.json')).write_text(json.dumps(report,ensure_ascii=False,indent=2))
    return 0 if all(r['status'] in (200,201) for r in report['checks']) and report['formal_plan_unchanged'] and report.get('scenario_behavior_passed',True) else 1

if __name__=='__main__':raise SystemExit(main())
