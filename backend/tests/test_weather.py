from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from sqlalchemy.orm import Session
from app.weather import OpenMeteo,VARIABLES,now,view
from app.models import WeatherState,User
from app.schemas import Profile
from test_app import client,account,workout
import copy
import pytest
from fastapi import HTTPException

CITY={'id':'synthetic-city','name':'合成城市','admin1':'示例省','country':'示例国','latitude':30.,'longitude':120.,'timezone':'Asia/Shanghai'}

def forecast():
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    t=datetime.combine(today,datetime.strptime('20:00','%H:%M').time(),ZoneInfo('Asia/Shanghai')).astimezone(timezone.utc).timestamp()
    return {'hourly_units':{'time':'unixtime',**VARIABLES},'hourly':{'time':[t],**{k:[95 if k=='weather_code' else 1 if k=='precipitation' else 30] for k in VARIABLES}}}

def enabled(c):
    h=account(c);c.app.state.settings.weather_provider='open_meteo'
    c.app.state.weather.search=lambda name:[CITY.copy()]
    c.app.state.weather.read=lambda url,params:forecast()
    r=c.post('/api/v1/weather/search',headers=h,json={'name':'合成城市','allow_external_weather':True,'expected_revision':0});assert r.status_code==200
    r=c.put('/api/v1/weather/location',headers=h,json={'candidate_id':CITY['id'],'expected_revision':1});assert r.status_code==200
    return h

def refresh(c,h,revision=2):return c.post('/api/v1/weather/refresh',headers=h,json={'allow_external_weather':True,'expected_revision':revision})

def test_disabled_auth_and_consent(client):
    assert client.get('/api/v1/weather').status_code==401
    h=account(client);assert client.get('/api/v1/weather').json()['status']=='disabled'
    assert refresh(client,h,0).status_code==503
    client.app.state.settings.weather_provider='open_meteo'
    assert client.post('/api/v1/weather/search',headers=h,json={'name':'北京','expected_revision':0}).status_code==422
    assert client.post('/api/v1/weather/search',json={'name':'北京','expected_revision':0}).status_code==403


def test_forecast_hour_units_cache_and_no_plan_change(client):
    h=enabled(client);r=refresh(client,h);assert r.status_code==200,r.text
    v=r.json();assert v['status']=='ready' and v['selected_hour']['temperature_2m']==30 and len(v['advice'])==2
    assert v['official_alerts']=='not_connected' and v['air_quality']=='not_connected'
    before=client.get('/api/v1/plans/current').json()
    client.app.state.weather.forecast=lambda _:pytest.fail('15分钟内不得重复外发')
    assert refresh(client,h,3).json()['revision']==3
    assert client.get('/api/v1/plans/current').json()==before
    assert client.get('/api/v1/weather?target_date=2030-01-01').json()['status']=='missing_hour'
    assert client.post('/api/v1/workouts',headers=h,json=workout()).status_code==201


def test_city_selection_revision_and_clears_previous_forecast(client):
    h=enabled(client);assert refresh(client,h).status_code==200
    assert client.put('/api/v1/weather/location',headers=h,json={'candidate_id':'invented','expected_revision':3}).status_code==422
    r=client.post('/api/v1/weather/search',headers=h,json={'name':'新城市','allow_external_weather':True,'expected_revision':3});assert r.status_code==200
    assert client.put('/api/v1/weather/location',headers=h,json={'candidate_id':CITY['id'],'expected_revision':3}).status_code==409
    r=client.put('/api/v1/weather/location',headers=h,json={'candidate_id':CITY['id'],'expected_revision':4})
    assert r.status_code==200 and r.json()['forecast'] is None


@pytest.mark.parametrize('bad',['units','length','nan','percent','time'])
def test_malformed_provider_response_not_saved(client,bad):
    h=enabled(client);raw=forecast()
    if bad=='units':raw['hourly_units']['temperature_2m']='°F'
    elif bad=='length':raw['hourly']['precipitation']=[]
    elif bad=='nan':raw['hourly']['temperature_2m']=[float('nan')]
    elif bad=='percent':raw['hourly']['relative_humidity_2m']=[150]
    else:raw['hourly']['time']=[True]
    client.app.state.weather.read=lambda url,params:raw
    assert refresh(client,h).status_code==502
    assert client.get('/api/v1/weather').json()['forecast'] is None


def test_expired_unknown_and_failed_provider_preserve_snapshot(client):
    h=enabled(client);assert refresh(client,h).status_code==200
    with Session(client.app.state.engine) as db:
        row=db.get(WeatherState,'owner');p=copy.deepcopy(row.payload);p['forecast']['expires_at']=(now()-timedelta(seconds=1)).isoformat();p['forecast']['fetched_at']=(now()-timedelta(hours=3)).isoformat();row.payload={**p};db.commit()
    v=client.get('/api/v1/weather').json();assert v['status']=='expired' and v['advice']==[]
    def fail(_):raise HTTPException(502,'合成服务故障')
    client.app.state.weather.forecast=fail
    assert refresh(client,h,3).status_code==502
    assert client.get('/api/v1/weather').json()['forecast']==v['forecast']


def test_city_changes_while_fetching_cannot_overwrite_new_selection(client):
    h=enabled(client);reader=client.app.state.weather.forecast
    def changed(location):
        result=reader(location)
        with Session(client.app.state.engine) as db:
            row=db.get(WeatherState,'owner');row.revision+=1;row.payload={'location':{**CITY,'name':'新城市'}};db.commit()
        return result
    client.app.state.weather.forecast=changed
    assert refresh(client,h).status_code==409
    v=client.get('/api/v1/weather').json();assert v['location']['name']=='新城市' and v['forecast'] is None


def test_nullable_fields_and_fractional_timezone_not_invented():
    reader=OpenMeteo();raw=forecast();raw['hourly']['temperature_2m']=[None];reader.read=lambda url,params:raw
    f=reader.forecast(CITY);assert f['hours'][0]['temperature_2m'] is None
    p=Profile(timezone='Asia/Kolkata',preferred_time='17:45')
    today=datetime.now(ZoneInfo('Asia/Shanghai')).date()
    row=WeatherState(user_id='example',revision=1,payload={'location':CITY,'forecast':f})
    assert view(row,p,True,today)['selected_hour'] is not None


def test_local_city_selection_works_without_external_consent_or_provider(client):
    assert client.get('/api/v1/weather/cities?q=杭州').status_code==401
    h=account(client)
    client.app.state.weather.search=lambda _:pytest.fail('本地查询不能联网')
    client.app.state.weather.forecast=lambda _:pytest.fail('选择本地城市不能自动取天气')
    result=client.get('/api/v1/weather/cities?q=杭州').json()
    assert result['external_request'] is False and result['candidates']
    city=next(r for r in result['candidates'] if r['ascii_name']=='Hangzhou')
    assert city['location_source']=='geonames_local' and 29<city['latitude']<31 and 119<city['longitude']<121
    pinyin=client.get('/api/v1/weather/cities?q=hangzhou').json()
    assert city['id'] in [r['id'] for r in pinyin['candidates']]
    assert client.get('/api/v1/weather/cities?q=杭州市').json()['candidates'][0]['id']==city['id']
    v=client.put('/api/v1/weather/location',headers=h,json={'candidate_id':city['id'],'expected_revision':0})
    assert v.status_code==200 and v.json()['status']=='disabled' and v.json()['forecast'] is None
    assert client.get('/api/v1/weather').json()['location']['id']==city['id']
    assert client.get('/api/v1/plans/current').json()['plan'] is None


def test_catalog_coverage_unknown_and_no_client_coordinate_override(client):
    h=account(client)
    assert client.get('/api/v1/weather/cities?q=完全不存在的合成测试城市').json()['candidates']==[]
    assert len(client.get('/api/v1/weather/cities').json()['candidates'])<=10
    assert client.get('/api/v1/weather/cities',params={'q':'x'*81}).status_code==422
    assert client.put('/api/v1/weather/location',headers=h,json={'candidate_id':'geonames-invented','expected_revision':0}).status_code==422
    assert client.put('/api/v1/weather/location',headers=h,json={'candidate_id':'geonames-1816670','expected_revision':0,'latitude':0}).status_code==422
    from app.city_catalog import catalog
    assert len({r['id'] for r in catalog()['cities']})==len(catalog()['cities'])
    for r in catalog()['cities']:
        assert -90<=r['latitude']<=90 and -180<=r['longitude']<=180
        ZoneInfo(r['timezone'])


def test_local_city_can_fetch_and_change_clears_old_snapshot(client):
    h=account(client);client.app.state.settings.weather_provider='open_meteo';client.app.state.weather.read=lambda url,params:forecast()
    beijing=client.get('/api/v1/weather/cities?q=北京').json()['candidates'][0]
    assert client.put('/api/v1/weather/location',headers=h,json={'candidate_id':beijing['id'],'expected_revision':0}).status_code==200
    assert refresh(client,h,1).json()['status']=='ready'
    hangzhou=client.get('/api/v1/weather/cities?q=杭州').json()['candidates'][0]
    assert client.put('/api/v1/weather/location',headers=h,json={'candidate_id':hangzhou['id'],'expected_revision':1}).status_code==409
    v=client.put('/api/v1/weather/location',headers=h,json={'candidate_id':hangzhou['id'],'expected_revision':2}).json()
    assert v['forecast'] is None and v['location']['id']==hangzhou['id']
