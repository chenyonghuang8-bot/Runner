import json
from uuid import uuid4
from datetime import datetime, timedelta, timezone
from threading import Event
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.models import CoachTurn, AICall, User
from app.recognition import VisionFailure
from test_app import client, account


def question():return {'request_id':str(uuid4()),'message':'合成测试：今天累了，力量训练如何调整？'}
def response():return json.dumps({'reply':'合成回复：请保存身体反馈后生成联合草稿。','questions':[],'uncertainties':[],'evidence_ids':[],'guidance_mode':'clarification_only'})


def test_chat_persistence_idempotency_and_fact_separation(client):
    h=account(client);q=question();revision=client.get('/api/v1/plans/current').json()['facts_revision']
    one=client.post('/api/v1/coach/messages',headers=h,json=q)
    two=client.post('/api/v1/coach/messages',headers=h,json=q)
    assert one.status_code==200 and one.json()['id']==two.json()['id']
    assert one.json()['is_mock'] and not one.json()['plan_changed']
    assert len(client.get('/api/v1/coach/messages').json()['turns'])==1
    assert client.get('/api/v1/plans/current').json()['facts_revision']==revision
    assert client.get('/api/v1/checkins').json()==[]
    assert client.post('/api/v1/coach/messages',headers=h,json={**q,'message':'更换内容'}).status_code==409
    client.post('/api/v1/checkins',headers=h,json={'date':'2026-10-08','fatigue':3})
    assert client.get('/api/v1/coach/messages').json()['turns'][0]['context_stale']


def test_chat_consent_context_budget_and_provider_idempotency(client):
    h=account(client);cfg=client.app.state.settings
    first=client.post('/api/v1/coach/messages',headers=h,json=question()).json();cfg.ai_provider='deepseek'
    class Synthetic:
        calls=0
        def read(self,messages):
            self.calls+=1
            assert 'AI 跑步训练教练' in messages[0]['content']
            facts=json.loads(messages[1]['content'].split('\n',1)[1])
            assert facts['conversation']['recent_turns'][0]['question']==first['message']
            assert 'display_name' not in facts['profile'] and 'city' not in facts['profile']
            return response(),{'prompt_tokens':1000,'completion_tokens':500},'stop'
    fake=Synthetic();client.app.state.chat_provider=fake;q=question()
    assert client.post('/api/v1/coach/messages',headers=h,json=q).status_code==422
    good=client.post('/api/v1/coach/messages',headers=h,json={**q,'allow_external_ai':True})
    assert good.status_code==200 and not good.json()['is_mock']
    assert client.post('/api/v1/coach/messages',headers=h,json=q).json()['id']==good.json()['id']
    assert fake.calls==1
    with Session(client.app.state.engine) as db:
        call=db.query(AICall).one();assert call.task_kind=='coach_chat' and call.charged_micro==6000
    cfg.ai_monthly_budget_cny=0
    assert client.post('/api/v1/coach/messages',headers=h,json={**question(),'allow_external_ai':True}).status_code==402
    assert client.get('/api/v1/coach/messages').json()['turns'][-1]['state']=='failed'


def test_invalid_outputs_and_timeout_keep_plan_unchanged(client):
    h=account(client);client.app.state.settings.ai_provider='deepseek'
    cases=[(response().replace('"evidence_ids": []','"evidence_ids": ["foreign-record"]'),'stop'),(response().replace('clarification_only','general_information'),'stop'),(response()[:-2],'length'),('{"reply":"假称已更新", "sessions":[]}','stop')]
    class Synthetic:
        def read(self,messages):return item,{'prompt_tokens':100,'completion_tokens':100},finish
    client.app.state.chat_provider=Synthetic()
    for item,finish in cases:
        q={**question(),'allow_external_ai':True}
        assert client.post('/api/v1/coach/messages',headers=h,json=q).status_code==502
        assert client.post('/api/v1/coach/messages',headers=h,json=q).status_code==409
    class Timeout:
        def read(self,messages):raise VisionFailure('合成超时')
    client.app.state.chat_provider=Timeout()
    assert client.post('/api/v1/coach/messages',headers=h,json={**question(),'allow_external_ai':True}).status_code==502
    assert all(t['state']=='failed' and 'reply' not in t for t in client.get('/api/v1/coach/messages').json()['turns'])
    assert client.get('/api/v1/ai/usage').json()['committed_cny']>0
    assert client.get('/api/v1/plans/current').json()['plan'] is None


def test_concurrent_same_question_calls_once(client):
    h=account(client);client.app.state.settings.ai_provider='deepseek';entered=Event();release=Event()
    class Synthetic:
        calls=0
        def read(self,messages):
            self.calls+=1;entered.set();assert release.wait(5)
            return response(),{'prompt_tokens':100,'completion_tokens':100},'stop'
    fake=Synthetic();client.app.state.chat_provider=fake;q={**question(),'allow_external_ai':True}
    def call():
        with TestClient(client.app) as other:
            other.cookies.update(client.cookies)
            return other.post('/api/v1/coach/messages',headers=h,json=q)
    with ThreadPoolExecutor(max_workers=2) as pool:
        future=pool.submit(call);assert entered.wait(5)
        duplicate=call();assert duplicate.status_code==409
        release.set();assert future.result().status_code==200
    assert fake.calls==1


def test_chat_history_is_private_bounded_and_interruption_visible(client):
    h=account(client)
    from app.security import hash_password
    with Session(client.app.state.engine) as db:
        db.add(User(id='other',username='other',password_hash=hash_password('synthetic-only-password'),profile={}));db.flush()
        for owner,n in [('other',1),('owner',51)]:
            for i in range(n):db.add(CoachTurn(user_id=owner,request_id=str(uuid4()),message=owner+str(i),response={},state='processing',provider='mock',facts_revision=1,expires_at=datetime.now(timezone.utc)-timedelta(seconds=1)))
        db.commit()
    history=client.get('/api/v1/coach/messages').json()['turns']
    assert len(history)==50 and all(t['state']=='interrupted' and t['message'].startswith('owner') for t in history)
    assert client.get('/api/v1/ai/usage').json()['calls']==0
    client.post('/api/v1/auth/logout',headers=h)
    assert client.get('/api/v1/coach/messages').status_code==401
