import importlib.util
from pathlib import Path
from fastapi.testclient import TestClient
from app.config import Settings
from test_app import client
import pytest
spec=importlib.util.spec_from_file_location('runner_home',Path(__file__).resolve().parents[2]/'scripts/home.py');home=importlib.util.module_from_spec(spec);spec.loader.exec_module(home)

@pytest.mark.parametrize('ip',['0.0.0.0','127.0.0.1','8.8.8.8','198.18.0.1','::1'])
def test_home_private_bind_only(ip):
    with pytest.raises(ValueError):home.validate_ip(ip)

def test_home_static_only_build_and_api_auth(client,tmp_path):
    dist=tmp_path/'dist';dist.mkdir();(dist/'index.html').write_text('<html>synthetic UI</html>');(dist/'assets').mkdir();(dist/'assets/app.js').write_text('synthetic')
    outside=tmp_path/'secret';outside.write_text('private');(dist/'secret.txt').symlink_to(outside)
    cfg=Settings(app_env='production',app_public_url='https://192.168.1.6:8443',session_secret='synthetic-session-configuration-over-32',database_url=str(client.app.state.engine.url),private_storage_dir=tmp_path/'images',_env_file=None)
    app=home.home_app(cfg,dist)
    try:
        with TestClient(app,base_url=cfg.app_public_url) as c:
            assert c.get('/').status_code==200 and c.get('/assets/app.js').status_code==200
            assert c.get('/api/v1/profile').status_code==401
            for path in ('/secret.txt','/.env','/private/home-tls/ca.key','/data/runner.db','/docs','/api/not-real'):assert c.get(path).status_code==404
            assert c.get('/api/v1/auth/status').json()['setup_allowed'] is False
            assert c.get('/',headers={'Host':'evil.example.com'}).status_code==400
    finally:app.state.engine.dispose()
