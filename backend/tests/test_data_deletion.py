from datetime import datetime,timedelta,timezone
from pathlib import Path
from sqlalchemy.orm import Session
from sqlalchemy import select
from app import models as m
from test_app import client,account,workout,image
from test_data_management import uploaded

BODY={'password':'synthetic-test-only','confirmation':'清空我的训练数据'}
def preview(c,h):
    r=c.post('/api/v1/data/deletion-preview',headers=h);assert r.status_code==200,r.text;return r.json()['id']
def confirm(c,h,id,body=BODY):return c.post(f'/api/v1/data/deletions/{id}/confirm',headers=h,json=body)

def test_reset_scope_fees_isolation_idempotency(client):
    h,imp=uploaded(client)
    with Session(client.app.state.engine) as db:
        db.add(m.User(id='other',username='other',password_hash='other',profile={},availability=[]));db.flush()
        db.add(m.Workout(user_id='other',payload=workout()))
        db.add(m.AIBudget(month='2026-10',committed_micro=123,calls=1))
        db.add(m.AICall(user_id='owner',import_id=imp,task_kind='vision',month='2026-10',reserved_micro=123,charged_micro=123,status='estimated',usage={'prompt_tokens':5}))
        db.add(m.Recognition(import_id=imp,cache_key='synthetic',region={},tiles=[]))
        db.add(m.ImageTileCache(key='cache',user_id='owner',readings=[{'raw':'synthetic'}]))
        db.commit();old=db.get(m.User,'owner').facts_revision
    identifier=preview(client,h)
    assert confirm(client,h,identifier).json()['state']=='done'
    assert not list(client.app.state.settings.private_storage_dir.iterdir())
    assert client.get('/api/v1/workouts').json()==[]
    with Session(client.app.state.engine) as db:
        assert db.get(m.User,'owner').facts_revision==old+1
        assert db.scalar(select(m.Workout).where(m.Workout.user_id=='other'))
        call=db.scalar(select(m.AICall));assert call.import_id is None and call.usage=={} and call.charged_micro==123
        assert db.get(m.AIBudget,'2026-10').committed_micro==123
        assert db.scalar(select(m.Session)) and not db.scalar(select(m.ImageTileCache))
    # Repeating a successful request never deletes newly created data.
    client.post('/api/v1/workouts',headers=h,json=workout())
    assert confirm(client,h,identifier).json()['state']=='done'
    assert len(client.get('/api/v1/workouts').json())==1

def test_confirmation_auth_csrf_password_phrase_and_rate_limit(client):
    assert client.post('/api/v1/data/deletion-preview').status_code==401
    h,_=uploaded(client);identifier=preview(client,h)
    assert confirm(client,{},identifier).status_code==403
    assert confirm(client,h,identifier,{**BODY,'confirmation':'yes'}).status_code==422
    for _ in range(4):assert confirm(client,h,identifier,{**BODY,'password':'incorrect'}).status_code==403
    assert confirm(client,h,identifier).status_code==429
    assert len(client.get('/api/v1/workouts').json())==1

def test_stale_preview_detects_unconfirmed_import_and_expiry(client):
    h,_=uploaded(client);identifier=preview(client,h)
    # Draft edits need not bump the athlete facts revision.
    with Session(client.app.state.engine) as db:
        draft=db.scalar(select(m.ImportDraft));draft.fields={'notes':'new draft'};db.commit()
    assert confirm(client,h,identifier).status_code==409
    identifier=preview(client,h)
    with Session(client.app.state.engine) as db:
        job=db.get(m.DataDeletion,identifier);job.expires_at=datetime.now(timezone.utc)-timedelta(seconds=1);db.commit()
    assert confirm(client,h,identifier).status_code==409
    assert len(client.get('/api/v1/workouts').json())==1

def test_busy_ai_and_cross_user_preview_refused(client):
    h,_=uploaded(client);identifier=preview(client,h)
    with Session(client.app.state.engine) as db:
        db.add(m.User(id='other',username='other',password_hash='other',profile={},availability=[]));db.flush()
        db.add(m.DataDeletion(id='foreign',user_id='other',fingerprint='x',expires_at=datetime.now(timezone.utc)+timedelta(hours=1),pending_files=[]))
        db.add(m.AICall(user_id='owner',month='2026-10',task_kind='coach',reserved_micro=1,status='reserved'));db.commit()
    assert confirm(client,h,'foreign').status_code==404
    assert client.post('/api/v1/data/deletion-preview',headers=h).status_code==409
    assert confirm(client,h,identifier).status_code==409

def test_file_failure_is_durable_and_retry_does_not_reset_new_facts(client,monkeypatch):
    h,_=uploaded(client);identifier=preview(client,h);original=Path.unlink
    def fail(p,*a,**kw):raise PermissionError('synthetic failure')
    monkeypatch.setattr(Path,'unlink',fail)
    r=confirm(client,h,identifier);assert r.status_code==200 and r.json()['state']=='cleanup_pending'
    assert client.get('/api/v1/data/deletions').json()[0]['remaining_originals']==1
    client.post('/api/v1/workouts',headers=h,json=workout())
    monkeypatch.setattr(Path,'unlink',original)
    assert confirm(client,h,identifier).json()['state']=='done'
    assert len(client.get('/api/v1/workouts').json())==1
    assert client.get('/api/v1/data/deletions').json()==[]

def test_cleanup_never_follows_link(client,tmp_path):
    h,_=uploaded(client);identifier=preview(client,h)
    p=next(client.app.state.settings.private_storage_dir.iterdir());p.unlink()
    target=tmp_path/'protected';target.write_text('keep');p.symlink_to(target)
    assert confirm(client,h,identifier).json()['state']=='cleanup_pending'
    assert target.read_text()=='keep' and p.is_symlink()

def test_concurrent_confirm_one_reset(client):
    from concurrent.futures import ThreadPoolExecutor
    h,_=uploaded(client);identifier=preview(client,h)
    with Session(client.app.state.engine) as db:old=db.get(m.User,'owner').facts_revision
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures=[pool.submit(confirm,client,h,identifier) for _ in range(2)]
        results=[f.result() for f in futures]
    assert all(r.status_code==200 and r.json()['state']=='done' for r in results)
    with Session(client.app.state.engine) as db:assert db.get(m.User,'owner').facts_revision==old+1

def test_full_joint_history_and_preferences_reset(client):
    h,imp=uploaded(client);now=datetime.now(timezone.utc)
    with Session(client.app.state.engine) as db:
        user=db.get(m.User,'owner');user.profile={'health_notes':'synthetic pain'};user.availability=[{'date':'2026-10-08'}]
        db.add(m.StrengthWorkout(user_id='owner',payload={'exercises':['synthetic']}))
        db.add(m.Checkin(user_id='owner',payload={'pain_level':2}))
        db.add(m.PlanVersion(user_id='owner',version=1,facts_revision=1,parent_version=0,payload={}))
        db.add(m.PlanProposal(user_id='owner',base_version=0,facts_revision=1,payload={},expires_at=now+timedelta(hours=1)))
        db.add(m.PlanOutbox(user_id='owner',plan_version=1))
        db.add(m.CoachTurn(user_id='owner',request_id='synthetic',message='private synthetic',response={},state='succeeded',provider='mock',facts_revision=1,expires_at=now))
        db.add(m.ImportMetricReview(user_id='owner',import_id=imp,draft_revision=1,payload={}))
        db.add(m.WeatherState(user_id='owner',payload={'location':{}}))
        db.add(m.NotificationSettings(user_id='owner',payload={'enabled':True}))
        db.add(m.NotificationJob(user_id='owner',dedupe_key='synthetic',plan_version=1,facts_revision=1,settings_revision=1,due_at=now,expires_at=now+timedelta(hours=1),payload={}))
        db.commit()
    assert confirm(client,h,preview(client,h)).json()['state']=='done'
    from app.data_deletion import TABLES
    with Session(client.app.state.engine) as db:
        assert all(not db.scalar(select(model)) for model in TABLES)
        user=db.get(m.User,'owner');assert user.profile['health_notes']=='' and user.availability==[]

def test_preview_order_is_stable_and_old_previews_invalidated(client):
    h,_=uploaded(client);first=preview(client,h);second=preview(client,h)
    assert confirm(client,h,first).status_code==404
    assert confirm(client,h,second).status_code==200
