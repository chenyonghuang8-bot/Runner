import io
import json
import time
from concurrent.futures import ThreadPoolExecutor
import pytest
from PIL import Image
from sqlalchemy.orm import Session
from app.models import Recognition, AICall, AIBudget, User
from app.recognition import validate_readings, VisionFailure, reserve, DeepSeekVision, summarize
from app.config import Settings
from test_app import client, account, workout

class FakeVision:
    """Synthetic provider fixture; never selectable in the product."""
    def __init__(self,results):self.results=iter(results);self.calls=0
    def read(self,jpeg):
        assert jpeg[:2]==b'\xff\xd8'
        self.calls+=1
        result=next(self.results)
        if isinstance(result,Exception):raise result
        return json.dumps({'readings':result}),{'prompt_tokens':100,'completion_tokens':100},'stop'

def reading(field,value,raw,unit=''):
    return {'field':field,'value':value,'raw':raw,'unit':unit,'rect':[.1,.1,.2,.1]}

def setup(client):
    h=account(client);cfg=client.app.state.settings
    cfg.ai_provider='deepseek';cfg.import_tile_height_px=200;cfg.import_tile_overlap_px=20
    output=io.BytesIO();Image.new('RGB',(100,500),'white').save(output,'PNG')
    d=client.post('/api/v1/imports',headers=h,files={'file':('synthetic.png',output.getvalue(),'image/png')}).json()
    return h,d,'/api/v1/imports/'+d['id']

def start(c,h,url,**changes):
    return c.post(url+'/recognition',headers=h,json={'expected_revision':1,'x':0,'y':100,'width':100,'height':400,'excludes_private_content':True,**changes})

def step(c,h,url,job):return c.post(url+'/recognition/step',headers=h,json={'cache_key':job['cache_key']})

def test_tiles_evidence_cache_and_confirm(client):
    h,d,url=setup(client)
    assert start(client,h,url,excludes_private_content=False).status_code==422
    assert start(client,h,url,height=401).status_code==422
    core=[reading('date','2026-10-08','2026/10/08'),reading('distance_m',5000,'5.00 公里','m'),reading('duration_seconds',1800,'00:30:00','s')]
    client.app.state.vision=FakeVision([core,[reading('distance_m',5000,'5.00 公里','m'),reading('fastest_instant_pace_seconds_per_km',290,'4′50″','s/km')],[reading('split_1_seconds',370,'6′10″','s'),reading('device_vo2max',45,'45','ml/kg/min')]])
    job=start(client,h,url).json();assert job['total']==3
    for _ in range(3):
        result=step(client,h,url,job);assert result.status_code==200;job=result.json()
    assert job['complete'] and job['suggested_fields']['distance_m']==5000
    assert job['readings'][0]['source_rect']==[10,120,20,20]
    assert job['readings'][-1]['origin']=='device_estimate'
    assert 'fastest_km_seconds' not in {r['field'] for r in job['readings']}
    assert client.get(url).json()['fields']=={}
    assert client.get('/api/v1/workouts').json()==[]
    assert client.get(url+'/evidence/0').status_code==200
    assert start(client,h,url).json()['complete'] and step(client,h,url,job).json()['complete']
    assert client.app.state.vision.calls==3
    usage=client.get('/api/v1/ai/usage').json();assert usage['calls']==3 and usage['committed_cny']==.003
    saved=client.patch(url+'/fields',headers=h,json={'expected_revision':1,'workout':workout()}).json()
    assert client.post(url+'/confirm',headers=h,json={'expected_revision':saved['revision']}).status_code==200
    assert step(client,h,url,job).status_code==409

def test_invalid_readings_conflicts_and_unknown_year():
    rows=validate_readings(json.dumps({'readings':[reading('date','2026-10-08','10月8日'),reading('distance_m','5000','5000','m'),reading('avg_heart_rate',None,'模糊','bpm')]}),[0,100,100,200],0)
    assert all(r['issue'] for r in rows)
    with pytest.raises(ValueError):validate_readings(json.dumps({'readings':[reading('instruction','ignore everything','x')]}),[0,0,100,200],0)
    bad=reading('distance_m',5000,'5km','m');bad['rect']=[.9,.9,.5,.5]
    with pytest.raises(ValueError):validate_readings(json.dumps({'readings':[bad]}),[0,0,100,200],0)
    rows=validate_readings(json.dumps({'readings':[reading('distance_m',5000,'5km','m'),reading('distance_m',5100,'5.1km','m')]}),[0,0,100,200],0)
    job=Recognition(cache_key='x'*64,region={},tiles=[{'state':'done','readings':rows}],lease_until=0)
    assert 'distance_m' not in summarize(job)['suggested_fields'] and summarize(job)['issues']

def test_failure_resume_budget_and_manual_edits(client):
    h,d,url=setup(client)
    client.app.state.vision=FakeVision([[],VisionFailure('合成超时'),[reading('max_heart_rate',150,'150','bpm')],[]])
    job=start(client,h,url).json()
    job=step(client,h,url,job).json();job=step(client,h,url,job).json()
    assert job['done']==1 and job['tiles'][1]['state']=='failed'
    assert client.get('/api/v1/ai/usage').json()['committed_cny']>.09
    saved=client.patch(url+'/fields',headers=h,json={'expected_revision':1,'workout':workout()}).json();assert saved['revision']==2
    job=step(client,h,url,job).json();assert job['done']==2 and job['tiles'][0]['attempts']==1
    assert client.get(url).json()['fields']['distance_m']==5000
    client.app.state.settings.ai_monthly_budget_cny=0
    blocked=step(client,h,url,job).json()
    assert blocked['tiles'][2]['state']=='failed' and blocked['tiles'][2]['attempts']==0
    assert client.app.state.vision.calls==3
    assert client.post(url+'/confirm',headers=h,json={'expected_revision':2}).status_code==200

def test_expired_lease_and_region_fencing(client):
    h,d,url=setup(client);client.app.state.vision=FakeVision([[],[],[]]);job=start(client,h,url).json()
    with Session(client.app.state.engine) as db:
        r=db.get(Recognition,d['id']);r.lease_until=int(time.time())+120;r.lease_token='synthetic-busy';db.commit()
    assert step(client,h,url,job).status_code==409 and start(client,h,url,y=101,height=399).status_code==409
    with Session(client.app.state.engine) as db:r=db.get(Recognition,d['id']);r.lease_until=1;db.commit()
    changed=start(client,h,url,y=101,height=399).json();assert changed['cache_key']!=job['cache_key']
    assert step(client,h,url,job).status_code==409
    assert step(client,h,url,changed).status_code==200 and client.app.state.vision.calls==1

def test_atomic_budget_limit(client):
    h,d,url=setup(client);cfg=client.app.state.settings;cfg.ai_monthly_budget_cny=.098304
    with Session(client.app.state.engine) as db:user_id=db.query(User).first().id
    def attempt(_):
        with Session(client.app.state.engine) as db:
            try:return reserve(db,cfg,user_id,d['id'])
            except Exception as e:return e
    with ThreadPoolExecutor(max_workers=2) as pool:results=list(pool.map(attempt,range(2)))
    assert sum(isinstance(r,str) for r in results)==1
    with Session(client.app.state.engine) as db:assert db.query(AICall).count()==1 and db.query(AIBudget).first().committed_micro==98304

def test_transport_payload_and_safe_errors(monkeypatch):
    import httpx
    captured={}
    def handler(request):
        captured.update(json.loads(request.content));return httpx.Response(401,json={'message':'secret should not surface'})
    original=httpx.Client
    monkeypatch.setattr('app.recognition.httpx.Client',lambda **kwargs:original(transport=httpx.MockTransport(handler),**kwargs))
    cfg=Settings(ai_provider='deepseek',deepseek_api_key='synthetic-key',_env_file=None)
    with pytest.raises(VisionFailure) as exc:DeepSeekVision(cfg).read(b'synthetic-jpeg')
    assert not exc.value.retryable and 'secret' not in exc.value.message
    assert captured['model']=='deepseek-flash' and captured['thinking']['type']=='disabled'
    assert captured['response_format']['type']=='json_object'
    assert captured['messages'][1]['content'][1]['image_url']['detail']=='original'

def test_mock_never_sends_images(client):
    h=account(client);output=io.BytesIO();Image.new('RGB',(20,20),'white').save(output,'PNG')
    d=client.post('/api/v1/imports',headers=h,files={'file':('s.png',output.getvalue(),'image/png')}).json()
    assert start(client,h,'/api/v1/imports/'+d['id'],y=0,width=20,height=20).status_code==503
    assert client.get('/api/v1/ai/usage').json()['calls']==0

def test_unit_conversion_and_truncated_output(client):
    rows=validate_readings(json.dumps({'readings':[reading('distance_m',5000,'5.0 m','m'),reading('duration_seconds',1800,'00:40:00','s'),reading('avg_heart_rate',130,'130','Hz')]}),[0,0,100,200],0)
    assert all(r['issue'] for r in rows)
    h,d,url=setup(client)
    class Truncated:
        def read(self,jpeg):return '{',{'prompt_tokens':100,'completion_tokens':4096},'length'
    client.app.state.vision=Truncated();job=start(client,h,url).json();result=step(client,h,url,job).json()
    assert result['done']==0 and result['readings']==[] and '截断' in result['tiles'][0]['error']
    assert client.get('/api/v1/ai/usage').json()['committed_cny']==.032968

def test_retry_limit_and_reuse_after_region_change(client):
    h,d,url=setup(client);client.app.state.vision=FakeVision([VisionFailure('测试失败')]*3);job=start(client,h,url).json()
    for _ in range(3):assert step(client,h,url,job).status_code==200
    assert step(client,h,url,job).status_code==409 and client.app.state.vision.calls==3
    client.app.state.vision=FakeVision([[],[],[]])
    changed=start(client,h,url,y=101,height=399).json()
    for _ in range(3):assert step(client,h,url,changed).status_code==200
    changed_again=start(client,h,url,y=102,height=398).json()
    client.app.state.vision=FakeVision([[],[],[]])
    for _ in range(3):assert step(client,h,url,changed_again).status_code==200
    reused=start(client,h,url,y=101,height=399).json()
    calls=client.app.state.vision.calls
    for _ in range(3):assert step(client,h,url,reused).status_code==200
    assert client.app.state.vision.calls==calls

@pytest.mark.parametrize('extra,status',[
    (reading('distance_m',5100,'5.1km','m'),'conflict'),
    (reading('distance_m',5000,'5km','km'),'conflict'),
    (reading('distance_m',None,'模糊','m'),'invalid'),
])
def test_review_groups_block_ambiguous_autofill(extra,status):
    rows=validate_readings(json.dumps({'readings':[reading('distance_m',5000,'5km','m'),extra]}),[0,0,100,200],0)
    original=json.loads(json.dumps(rows))
    job=Recognition(cache_key='x'*64,region={},tiles=[{'state':'done','readings':rows}],lease_until=0)
    summary=summarize(job)
    group=summary['review_groups'][0]
    assert group['status']==status and group['reading_indices']==[0,1] and group['reasons']
    assert 'distance_m' not in summary['suggested_fields']
    assert rows==original  # Review grouping must not rewrite evidence or silently choose a reading.


def test_review_groups_cross_field_checks_unknown_and_zero():
    def summary_of(raw):
        rows=validate_readings(json.dumps({'readings':raw}),[0,0,100,200],0)
        return summarize(Recognition(cache_key='x'*64,region={},tiles=[{'state':'done','readings':rows}],lease_until=0))
    result=summary_of([reading('avg_heart_rate',160,'160','bpm'),reading('max_heart_rate',150,'150','bpm'),reading('ascent_m',0,'0m','m'),reading('date',None,'模糊')])
    groups={g['field']:g for g in result['review_groups']}
    assert groups['avg_heart_rate']['status']==groups['max_heart_rate']['status']=='conflict'
    assert groups['date']['status']=='unknown'
    assert result['suggested_fields']=={'ascent_m':0}
    result=summary_of([reading('distance_m',5000,'5km','m'),reading('duration_seconds',1800,'00:30:00','s'),reading('average_pace_seconds_per_km',300,'5′00″','s/km')])
    assert not result['suggested_fields'] and all(g['status']=='conflict' for g in result['review_groups'])
