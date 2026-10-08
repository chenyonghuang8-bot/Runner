"""Synthetic screenshot metrics; no real screenshots or external services."""
import copy
from sqlalchemy import select
from sqlalchemy.orm import Session
from app.models import Recognition, ImportMetricReview, User
from app.planning import gather
from app.coach_provider import messages_for
from test_app import client, account, image, workout


def fixture(client):
    h=account(client)
    draft=client.post('/api/v1/imports',headers=h,files={'file':('synthetic.png',image(),'image/png')}).json()
    base='/api/v1/imports/'+draft['id']
    reading={'field':'average_pace_seconds_per_km','value':360,'raw':'6′00″','unit':'s/km','source_tile_id':0,'source_rect':[0,0,20,20],'origin':'device','issue':'','review_status':'needs_review'}
    with Session(client.app.state.engine) as db:
        db.add(Recognition(import_id=draft['id'],cache_key='x'*64,region={'x':0,'y':0,'width':32,'height':64},tiles=[{'state':'done','readings':[reading],'rect':[0,0,32,64],'attempts':1,'error':'','retryable':True}],lease_until=0));db.commit()
    return h,draft,base,reading


def review(**changes):
    return {'expected_revision':1,'cache_key':'x'*64,'field':'average_pace_seconds_per_km','status':'confirmed','value':360,'unit':'s/km','raw':'6′00″','note':'参照原图核对','source_reading_index':0,**changes}


def test_metric_review_history_confirm_and_coach_context(client):
    h,draft,base,original=fixture(client)
    result=client.post(base+'/metric-reviews',headers=h,json=review())
    assert result.status_code==201
    d=result.json();assert d['revision']==2 and d['fields']=={} and d['status']=='needs_review'
    assert d['metric_reviews'][0]['current'] and client.get('/api/v1/workouts').json()==[]
    assert client.post(base+'/metric-reviews',headers=h,json=review()).status_code==409
    excluded=client.post(base+'/metric-reviews',headers=h,json=review(expected_revision=2,status='excluded',value=None,note='不能确定')).json()
    assert excluded['revision']==3 and len(excluded['metric_reviews'])==2
    assert excluded['metric_reviews'][0]['status']=='excluded' and not excluded['metric_reviews'][1]['current']
    confirmed=client.post(base+'/metric-reviews',headers=h,json=review(expected_revision=3)).json()
    saved=client.patch(base+'/fields',headers=h,json={'expected_revision':4,'workout':workout()}).json()
    result=client.post(base+'/confirm',headers=h,json={'expected_revision':saved['revision']})
    assert result.status_code==200
    run=result.json();metric=run['confirmed_metrics']['average_pace_seconds_per_km']
    assert metric['value']==360 and metric['review_source']=='human_confirmed'
    assert metric['review_id']==confirmed['metric_reviews'][0]['id']
    again=client.post(base+'/confirm',headers=h,json={'expected_revision':saved['revision']}).json()
    assert again['id']==run['id'] and len(client.get('/api/v1/workouts').json())==1
    assert client.post(base+'/metric-reviews',headers=h,json=review(expected_revision=6)).status_code==409
    with Session(client.app.state.engine) as db:
        assert db.get(Recognition,draft['id']).tiles[0]['readings']==[original]
        user=db.scalar(select(User));context=gather(db,user)
        assert context['workouts'][0]['confirmed_metrics']['average_pace_seconds_per_km']['value']==360
        assert db.query(ImportMetricReview).count()==3
        from datetime import date
        import json
        messages,_,ids=messages_for(context,date(2026,10,8),'合成角色',{})
        encoded=json.dumps(messages,ensure_ascii=False)
        assert 'confirmed_metrics' in encoded and 'human_confirmed' in encoded
        assert 'source_snapshot' not in encoded and 'metric_reviews' not in encoded
        assert run['id'] in ids


def test_metric_review_evidence_changes_exclude_old_confirmation(client):
    h,draft,base,original=fixture(client)
    assert client.post(base+'/metric-reviews',headers=h,json=review()).status_code==201
    with Session(client.app.state.engine) as db:
        job=db.get(Recognition,draft['id']);tiles=copy.deepcopy(job.tiles);tiles[0]['readings'][0]['value']=365;job.tiles=tiles;db.commit()
    d=client.get(base).json()
    assert not d['metric_reviews'][0]['current']
    assert d['metric_reviews'][0]['source_snapshot'][0]['value']==360
    saved=client.patch(base+'/fields',headers=h,json={'expected_revision':2,'workout':workout()}).json()
    run=client.post(base+'/confirm',headers=h,json={'expected_revision':saved['revision']}).json()
    assert 'confirmed_metrics' not in run


def test_metric_review_rejects_bad_source_unit_unknown_and_unconfirmed(client):
    h,draft,base,_=fixture(client)
    for changes in [dict(source_reading_index=1),dict(unit='min/km'),dict(field='distance_m'),dict(value=None),dict(value=300,raw='6′00″'),dict(value=1,raw='<1分钟'),dict(status='excluded',value=None,note=''),dict(status='excluded',value=360),dict(value=-1),dict(value=True)]:
        assert client.post(base+'/metric-reviews',headers=h,json=review(**changes)).status_code==422
    assert client.post(base+'/metric-reviews',headers=h,json=review(cache_key='y'*64)).status_code==409
    assert client.post(base+'/metric-reviews',json=review()).status_code==403
    with Session(client.app.state.engine) as db:
        job=db.get(Recognition,draft['id']);tiles=copy.deepcopy(job.tiles);tiles[0]['state']='pending';job.tiles=tiles;db.commit()
    assert client.post(base+'/metric-reviews',headers=h,json=review()).status_code==409
    assert client.get(base).json()['revision']==1


def test_excluded_metric_never_enters_training_facts(client):
    h,draft,base,_=fixture(client)
    d=client.post(base+'/metric-reviews',headers=h,json=review(status='excluded',value=None,note='不采用')).json()
    saved=client.patch(base+'/fields',headers=h,json={'expected_revision':d['revision'],'workout':workout()}).json()
    run=client.post(base+'/confirm',headers=h,json={'expected_revision':saved['revision']}).json()
    assert 'confirmed_metrics' not in run


def test_metric_review_concurrent_save_has_one_winner(client):
    from concurrent.futures import ThreadPoolExecutor
    h,draft,base,_=fixture(client)
    with ThreadPoolExecutor(max_workers=2) as pool:
        statuses=list(pool.map(lambda _:client.post(base+'/metric-reviews',headers=h,json=review()).status_code,range(2)))
    assert sorted(statuses)==[201,409]
    with Session(client.app.state.engine) as db:
        assert db.query(ImportMetricReview).count()==1
        user=db.scalar(select(User));assert user.facts_revision==1
        assert not gather(db,user)['workouts']


def test_metric_review_ownership(client):
    h,draft,base,_=fixture(client)
    with Session(client.app.state.engine) as db:
        from app.models import ImportDraft
        other=User(username='synthetic-other',password_hash='not-used');db.add(other);db.flush()
        db.get(ImportDraft,draft['id']).user_id=other.id;db.commit()
    assert client.post(base+'/metric-reviews',headers=h,json=review()).status_code==404
    assert client.get(base).status_code==404
