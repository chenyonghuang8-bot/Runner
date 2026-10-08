import io
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image
from alembic.config import Config
from alembic import command
from app.config import Settings
from app.main import create_app

@pytest.fixture
def client(tmp_path, monkeypatch):
    url = 'sqlite:///'+str(tmp_path/'test.db')
    monkeypatch.setenv('DATABASE_URL',url)
    config = Config(str(Path(__file__).resolve().parents[1]/'alembic.ini'))
    command.upgrade(config,'head')
    app = create_app(Settings(app_env='test',database_url=url,private_storage_dir=tmp_path/'images',_env_file=None))
    with TestClient(app) as c:
        yield c
    app.state.engine.dispose()

def account(client):
    result=client.post('/api/v1/auth/setup',json={'username':'test-runner','password':'synthetic-test-only'})
    assert result.status_code==201
    return {'X-CSRF-Token':result.json()['csrf_token']}

def workout():
    return {'date':'2026-10-08','sport':'road_run','title':'合成训练','distance_m':5000,'duration_seconds':1800,'avg_heart_rate':130,'max_heart_rate':150}

def image():
    buf=io.BytesIO()
    Image.new('RGB',(32,64),'white').save(buf,format='PNG')
    return buf.getvalue()

def test_auth_csrf_and_origin(client):
    assert client.get('/api/v1/profile').status_code==401
    assert client.post('/api/v1/auth/setup',headers={'Origin':'https://untrusted.example'},json={'username':'x','password':'synthetic-test-only'}).status_code==403
    headers=account(client)
    assert client.cookies.get('runner_session')
    assert client.get('/api/v1/auth/me').json()['csrf_token']==headers['X-CSRF-Token']
    assert client.post('/api/v1/workouts',json=workout()).status_code==403
    assert client.post('/api/v1/workouts',headers=headers,json=workout()).status_code==201
    assert client.post('/api/v1/auth/setup',json={'username':'other','password':'synthetic-test-only'}).status_code==409
    assert client.post('/api/v1/auth/logout',headers=headers).status_code==200
    assert client.get('/api/v1/workouts').status_code==401

def test_persistence_and_validation(client):
    h=account(client)
    p=client.get('/api/v1/profile').json()
    p.update(display_name='测试跑者',city='测试城市',health_notes='合成测试反馈')
    assert client.patch('/api/v1/profile',headers=h,json=p).status_code==200
    assert client.get('/api/v1/profile').json()['city']=='测试城市'
    assert client.put('/api/v1/availability',headers=h,json={'days':[{'date':'2026-10-08','available':True,'duration_minutes':60}]}).status_code==200
    assert client.get('/api/v1/availability').json()['days'][0]['available']
    invalid={**workout(),'avg_heart_rate':160}
    assert client.post('/api/v1/workouts',headers=h,json=invalid).status_code==422
    r=client.post('/api/v1/workouts',headers=h,json=workout())
    assert r.json()['pace_seconds_per_km']==360
    client.post('/api/v1/checkins',headers=h,json={'date':'2026-10-08','fatigue':3,'pain_location':'测试部位','pain_timing':'after'})
    assert len(client.get('/api/v1/checkins').json())==1
    client.post('/api/v1/auth/logout',headers=h)
    result=client.post('/api/v1/auth/login',json={'username':'test-runner','password':'synthetic-test-only'})
    assert result.status_code==200
    assert len(client.get('/api/v1/workouts').json())==1

def test_import_private_duplicate_and_confirmation(client):
    h=account(client)
    r=client.post('/api/v1/imports',headers=h,files={'file':('synthetic.png',image(),'image/png')})
    assert r.status_code==201
    draft=r.json()
    assert draft['recognition_performed'] is False
    assert client.get('/api/v1/workouts').json()==[]
    assert client.get(draft['image_url']).status_code==200
    dup=client.post('/api/v1/imports',headers=h,files={'file':('again.png',image(),'image/png')})
    assert dup.json()['id']==draft['id'] and dup.json()['duplicate']
    url='/api/v1/imports/'+draft['id']
    assert client.post(url+'/confirm',headers=h,json={'expected_revision':1}).status_code==422
    changed=client.patch(url+'/fields',headers=h,json={'expected_revision':1,'workout':workout()})
    assert changed.status_code==200 and changed.json()['revision']==2
    assert client.patch(url+'/fields',headers=h,json={'expected_revision':1,'workout':workout()}).status_code==409
    assert client.post(url+'/confirm',headers=h,json={'expected_revision':1}).status_code==409
    first=client.post(url+'/confirm',headers=h,json={'expected_revision':2})
    second=client.post(url+'/confirm',headers=h,json={'expected_revision':2})
    assert first.status_code==200 and first.json()['id']==second.json()['id']
    assert len(client.get('/api/v1/workouts').json())==1
    client.post('/api/v1/auth/logout',headers=h)
    assert client.get(draft['image_url']).status_code==401

def test_invalid_upload_and_mock(client):
    h=account(client)
    assert client.post('/api/v1/imports',headers=h,files={'file':('bad.jpg',b'not an image','image/jpeg')}).status_code==422
    reply=client.post('/api/v1/coach/messages',headers=h,json={'message':'测试反馈'})
    assert reply.json()['is_mock'] and reply.json()['plan_changed'] is False

def test_config_validation():
    with pytest.raises(ValueError):Settings(import_tile_height_px=200,import_tile_overlap_px=200,_env_file=None)
    with pytest.raises(ValueError):Settings(ai_provider='deepseek',deepseek_api_key='',_env_file=None)
    with pytest.raises(ValueError):Settings(app_env='production',session_secret='',_env_file=None)


def test_records_and_images_are_scoped_to_user(client):
    from sqlalchemy.orm import Session
    from app.models import User
    from app.schemas import Profile
    from app.security import hash_password
    h=account(client)
    client.post('/api/v1/workouts',headers=h,json=workout())
    draft=client.post('/api/v1/imports',headers=h,files={'file':('synthetic.png',image(),'image/png')}).json()
    with Session(client.app.state.engine) as db:
        db.add(User(id='second-test-user',username='second',password_hash=hash_password('synthetic-second-password'),profile=Profile().model_dump(mode='json')))
        db.commit()
    client.post('/api/v1/auth/logout',headers=h)
    assert client.post('/api/v1/auth/login',json={'username':'second','password':'synthetic-second-password'}).status_code==200
    assert client.get('/api/v1/workouts').json()==[]
    assert client.get('/api/v1/imports').json()==[]
    assert client.get(draft['image_url']).status_code==404
