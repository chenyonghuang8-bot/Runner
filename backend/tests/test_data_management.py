import io,json,zipfile,hashlib,sqlite3,copy
from pathlib import Path
from sqlalchemy.orm import Session
from app.models import User,Workout,Recognition
from app.backup import create,verify,restore
from test_app import client,account,workout,image
import pytest

def uploaded(c):
    h=account(c);assert c.post('/api/v1/workouts',headers=h,json=workout()).status_code==201
    r=c.post('/api/v1/imports',headers=h,files={'file':('private-map.png',image(),'image/png')});assert r.status_code==201
    return h,r.json()['id']

def unpack(r):
    assert r.status_code==200,r.text
    z=zipfile.ZipFile(io.BytesIO(r.content));return z,json.loads(z.read('data.json')),json.loads(z.read('manifest.json'))

def test_export_auth_no_credentials_raw_evidence_and_scope(client):
    assert client.get('/api/v1/data/export').status_code==401
    h,identifier=uploaded(client)
    with Session(client.app.state.engine) as db:
        db.add(User(id='other',username='other',password_hash='not-for-export',profile={},availability=[]));db.flush()
        db.add(Workout(user_id='other',payload={**workout(),'notes':'other-secret'}))
        db.add(Recognition(import_id=identifier,cache_key='hash',region={},tiles=[{'state':'done','readings':[{'raw_text':'设备读数','unit':'s','value':448,'evidence_rect':[1,2,3,4]}]}],lease_token='secret-lease',lease_until=999));db.commit()
    z,data,manifest=unpack(client.get('/api/v1/data/export'))
    assert z.namelist()==['data.json','manifest.json'] and not manifest['includes_originals']
    assert len(data['tables']['workouts'])==1 and len(data['tables']['imports'])==1
    assert data['tables']['imports'][0]['status']=='needs_review'
    assert data['tables']['recognitions'][0]['tiles'][0]['readings'][0]['value']==448
    assert all(s not in str(data) for s in ('password_hash','token_hash','secret-lease','other-secret','not-for-export','deepseek_api_key','storage_name'))
    assert hashlib.sha256(z.read('data.json')).hexdigest()==manifest['data_sha256']
    assert 'attachment' in client.get('/api/v1/data/export').headers['content-disposition']
    assert client.get('/api/v1/workouts').json()[0]['distance_m']==5000


def test_opt_in_originals_hash_and_missing_failure_preserves_manual_export(client):
    uploaded(client);z,data,m=unpack(client.get('/api/v1/data/export?include_originals=true'))
    item=m['originals'][0];assert item['included'] and hashlib.sha256(z.read(item['path'])).hexdigest()==item['sha256']
    next(client.app.state.settings.private_storage_dir.iterdir()).unlink()
    assert client.get('/api/v1/data/export?include_originals=true').status_code==409
    assert client.get('/api/v1/data/export').status_code==200


def paths(c):return Path(c.app.state.engine.url.database),c.app.state.settings.private_storage_dir

def test_backup_roundtrip_new_copy_sessions_disabled_and_no_overwrite(client,tmp_path):
    uploaded(client);db,images=paths(client);backup=tmp_path/'backup';assert create(db,images,backup)==1
    assert verify(backup)['migration']=='0008'
    restored=restore(backup,tmp_path/'restored')
    with sqlite3.connect(restored/'runner.db') as c:
        assert c.execute('SELECT COUNT(*) FROM workouts').fetchone()[0]==1 and c.execute('SELECT COUNT(*) FROM imports').fetchone()[0]==1
        assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==0
    with sqlite3.connect(db) as c:assert c.execute('SELECT COUNT(*) FROM sessions').fetchone()[0]==1
    with pytest.raises(ValueError,match='存在'):restore(backup,restored)
    with pytest.raises(ValueError,match='存在'):create(db,images,backup)


@pytest.mark.parametrize('kind',['extra','hash','traversal','missing','symlink'])
def test_corrupt_backups_refused_before_restore(client,tmp_path,kind):
    uploaded(client);db,images=paths(client);backup=tmp_path/'backup';create(db,images,backup)
    if kind=='extra':(backup/'originals'/'unexpected').write_text('extra')
    elif kind=='hash':(backup/'database.sqlite3').write_bytes(b'corrupt')
    elif kind=='missing':next((backup/'originals').iterdir()).unlink()
    elif kind=='symlink':p=next((backup/'originals').iterdir());p.unlink();p.symlink_to(db)
    else:
        m=json.loads((backup/'manifest.json').read_text());m['files']['../escape']='bad';(backup/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError):restore(backup,tmp_path/'restored')
    assert not (tmp_path/'restored').exists()


def test_export_is_one_snapshot_during_concurrent_write(client,monkeypatch):
    from app import data_management as dm
    from app.models import Checkin
    uploaded(client);original=dm.record;written=False
    def record(row,exclude=()):
        nonlocal written
        if isinstance(row,Workout) and not written:
            written=True
            with Session(client.app.state.engine) as db:
                db.add(Checkin(user_id='owner',payload={'date':'2026-10-08','fatigue':2,'notes':'added-during-export'}));u=db.get(User,'owner');u.facts_revision+=1;db.commit()
        return original(row,exclude)
    monkeypatch.setattr(dm,'record',record)
    z,data,m=unpack(client.get('/api/v1/data/export'))
    assert data['tables']['checkins']==[]
    assert len(client.get('/api/v1/checkins').json())==1


def test_restore_disables_pending_jobs_and_lease_without_touching_source(client,tmp_path):
    from app.models import NotificationSettings,NotificationJob
    from datetime import datetime,timedelta,timezone
    h,identifier=uploaded(client);t=datetime.now(timezone.utc)
    with Session(client.app.state.engine) as db:
        db.add(NotificationSettings(user_id='owner',revision=1,payload={'enabled':True}))
        db.add(NotificationJob(user_id='owner',dedupe_key='synthetic',plan_version=0,facts_revision=1,settings_revision=1,due_at=t,expires_at=t+timedelta(hours=1),payload={},state='pending',attempts=0,reason=''))
        db.add(Recognition(import_id=identifier,cache_key='synthetic',region={},tiles=[],lease_token='active-lease',lease_until=999999));db.commit()
    database,images=paths(client);create(database,images,tmp_path/'backup');restore(tmp_path/'backup',tmp_path/'copy')
    with sqlite3.connect(tmp_path/'copy'/'runner.db') as c:
        assert json.loads(c.execute('SELECT payload FROM notification_settings').fetchone()[0])['enabled'] is False
        assert c.execute('SELECT state FROM notification_jobs').fetchone()[0]=='cancelled'
        assert c.execute('SELECT lease_token,lease_until FROM recognitions').fetchone()==(None,0)
    with Session(client.app.state.engine) as db:assert db.get(NotificationSettings,'owner').payload['enabled']
