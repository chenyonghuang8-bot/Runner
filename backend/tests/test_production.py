from pathlib import Path
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.owner import initialize
from app.models import User
from app.security import verify_password
from test_app import client
import pytest

@pytest.mark.parametrize('url',['http://runner.example.com','https://','https://runner.example.com/path','https://name:password@runner.example.com','https://runner.example.com?key=test','https://runner.example.com:bad'])
def test_production_bad_origin_rejected_without_secret(url):
    key='synthetic-secret-value-not-for-errors'
    with pytest.raises(ValueError) as exc:Settings(app_env='production',app_public_url=url,session_secret=key,_env_file=None)
    assert key not in str(exc.value)

def test_operator_owner_validation_and_refuses_overwrite(client):
    with Session(client.app.state.engine) as db:
        with pytest.raises(ValueError):initialize(db,'owner','short')
        initialize(db,'owner','synthetic-only-password')
        with pytest.raises(ValueError):initialize(db,'owner','different-synthetic-password')
        assert verify_password('synthetic-only-password',db.get(User,'owner').password_hash)

def test_production_cookie_origin_host_and_private_routes(client):
    with Session(client.app.state.engine) as db:initialize(db,'owner','synthetic-only-password')
    cfg=Settings(app_env='production',app_public_url='https://runner.example.com',session_secret='synthetic-session-configuration-32-characters',database_url=str(client.app.state.engine.url),private_storage_dir=client.app.state.settings.private_storage_dir,_env_file=None)
    app=create_app(cfg)
    try:
        with TestClient(app,base_url='https://runner.example.com') as c:
            assert c.get('/api/v1/auth/status').json()=={'initialized':True,'setup_allowed':False}
            credentials={'username':'owner','password':'synthetic-only-password'}
            assert c.post('/api/v1/auth/setup',json=credentials).status_code==403
            assert c.post('/api/v1/auth/login',json=credentials,headers={'Origin':'https://wrong.example.com'}).status_code==403
            r=c.post('/api/v1/auth/login',json=credentials,headers={'Origin':'https://runner.example.com'})
            assert r.status_code==200
            cookie=r.headers['set-cookie'].lower();assert all(x in cookie for x in ('secure','httponly','samesite=strict'))
            assert c.get('/api/v1/profile').status_code==200
            assert c.post('/api/v1/data/deletion-preview').status_code==403
            assert c.get('/api/v1/health',headers={'Host':'wrong.example.com'}).status_code==400
            for path in ('/docs','/redoc','/openapi.json','/private/test.png','/data/runner.db','/.env'):assert c.get(path).status_code==404
            assert c.get('/api/v1/health',headers={'Host':'127.0.0.1:8000'}).status_code==200
    finally:app.state.engine.dispose()
