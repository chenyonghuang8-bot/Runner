"""Opt-in city-centre forecasts; never treats weather as permission to train."""
from datetime import datetime,timedelta,timezone,date
from zoneinfo import ZoneInfo
from uuid import uuid4
import math
import httpx
from fastapi import Depends,HTTPException,Query
from pydantic import Field
from sqlalchemy import update
from .schemas import StrictModel,Profile
from .models import WeatherState

SOURCE='https://open-meteo.com/en/docs'
VARIABLES={'temperature_2m':'°C','apparent_temperature':'°C','relative_humidity_2m':'%','precipitation_probability':'%','precipitation':'mm','wind_speed_10m':'km/h','weather_code':'wmo code'}

def now():return datetime.now(timezone.utc)

class Revision(StrictModel):
    expected_revision:int=Field(ge=0)
class External(Revision):
    allow_external_weather:bool=False
class Search(External):
    name:str=Field(min_length=2,max_length=80)
class Choose(Revision):
    candidate_id:str=Field(max_length=40)

class OpenMeteo:
    def read(self,url,params):
        try:
            with httpx.Client(timeout=15,follow_redirects=False) as client:
                response=client.get(url,params=params)
            if response.status_code!=200 or len(response.content)>1000000:raise ValueError()
            return response.json()
        except (httpx.HTTPError,ValueError):raise HTTPException(502,'天气服务不可用；保留已保存资料，可继续手工记录和调整日程') from None
    def search(self,name):
        data=self.read('https://geocoding-api.open-meteo.com/v1/search',{'name':name,'count':5,'language':'zh','format':'json'})
        if not isinstance(data,dict) or not isinstance(data.get('results',[]),list):raise ValueError('城市返回格式错误')
        result=[]
        for r in data.get('results',[])[:5]:
            lat=r['latitude'];lon=r['longitude'];zone=r['timezone'];ZoneInfo(zone)
            if isinstance(lat,bool) or isinstance(lon,bool) or not -90<=lat<=90 or not -180<=lon<=180:raise ValueError('坐标无效')
            result.append({'id':str(uuid4()),'name':str(r['name'])[:80],'admin1':str(r.get('admin1',''))[:80],'country':str(r.get('country',''))[:80],'latitude':lat,'longitude':lon,'timezone':zone,'location_source':'open_meteo_geocoding','source':'https://open-meteo.com/en/docs/geocoding-api'})
        return result
    def forecast(self,location):
        raw=self.read('https://api.open-meteo.com/v1/forecast',{'latitude':location['latitude'],'longitude':location['longitude'],'hourly':','.join(VARIABLES),'timezone':'UTC','timeformat':'unixtime','forecast_days':7,'temperature_unit':'celsius','wind_speed_unit':'kmh','precipitation_unit':'mm'})
        units=raw['hourly_units'];hours=raw['hourly'];times=hours['time']
        if units.get('time')!='unixtime' or not isinstance(times,list) or not 1<=len(times)<=200:raise ValueError('预报时间格式错误')
        if any(units.get(k)!=u or not isinstance(hours.get(k),list) or len(hours[k])!=len(times) for k,u in VARIABLES.items()):raise ValueError('预报单位或字段长度错误')
        out=[]
        for i,t in enumerate(times):
            if isinstance(t,bool) or not isinstance(t,(int,float)) or not math.isfinite(t):raise ValueError('时间无效')
            row={'time':datetime.fromtimestamp(t,timezone.utc).isoformat()}
            for key in VARIABLES:
                value=hours[key][i]
                if value is not None and (isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value)):raise ValueError('数值无效')
                if key in ('relative_humidity_2m','precipitation_probability') and value is not None and not 0<=value<=100:raise ValueError('百分比无效')
                if key in ('precipitation','wind_speed_10m') and value is not None and value<0:raise ValueError('负数无效')
                row[key]=value
            out.append(row)
        if any(a['time']>=b['time'] for a,b in zip(out,out[1:])):raise ValueError('预报日期重复或乱序')
        return {'hours':out,'units':units,'fetched_at':now().isoformat(),'expires_at':(now()+timedelta(hours=2)).isoformat(),'source':SOURCE,'provider':'open_meteo'}

def view(row,profile,enabled,target_date=None):
    p=row.payload if row else {};forecast=p.get('forecast');expired=not forecast or datetime.fromisoformat(forecast['expires_at'])<=now()
    target=target_date or datetime.now(ZoneInfo(profile.timezone)).date()
    instant=datetime.combine(target,datetime.strptime(profile.preferred_time,'%H:%M').time(),ZoneInfo(profile.timezone)).astimezone(timezone.utc).replace(minute=0)
    selected=next((h for h in (forecast or {}).get('hours',[]) if datetime.fromisoformat(h['time'])==instant),None)
    status='disabled' if not enabled else 'no_location' if not p.get('location') else 'unavailable' if not forecast else 'expired' if expired else 'missing_hour' if not selected else 'ready'
    advice=[]
    if status=='ready':
        if selected.get('weather_code') in (95,96,99):advice.append('预报含雷暴：先核对当地官方预警，考虑取消户外训练或改时间。')
        if (selected.get('precipitation') or 0)>0:advice.append('预报有降水：核对路面和现场情况，可选择其他时段或调整日程。')
        if not advice:advice.append('预报不能判断身体是否适合训练；出发前仍需检查身体状态、现场天气和官方预警。')
    return {'provider':'open_meteo' if enabled else 'disabled','revision':row.revision if row else 0,'location':p.get('location'),'candidates':p.get('candidates',[]),'forecast':forecast,'status':status,'selected_hour':selected,'target_date':target.isoformat(),'target_time':profile.preferred_time,'display_timezone':profile.timezone,'advice':advice,'official_alerts':'not_connected','air_quality':'not_connected','source':SOURCE}

def install_routes(app,cfg,current,db):
    app.state.weather=OpenMeteo()
    def row_for(session,user):return session.get(WeatherState,user.id)
    def result(row,user,target=None):return view(row,Profile.model_validate(user.profile),cfg.weather_provider=='open_meteo',target)
    def check(row,revision):
        if (row.revision if row else 0)!=revision:raise HTTPException(409,'天气位置或预报已变化，请刷新后重试')
    def external(data):
        if cfg.weather_provider!='open_meteo':raise HTTPException(503,'天气服务尚未启用；可继续手工调整日程')
        if not data.allow_external_weather:raise HTTPException(422,'请先确认城市资料发送说明')
    def save(session,user,row,revision,payload):
        if row:
            changed=session.execute(update(WeatherState).where(WeatherState.user_id==user.id,WeatherState.revision==revision).values(revision=revision+1,payload=payload))
            if not changed.rowcount:session.rollback();raise HTTPException(409,'天气位置或预报已变化，请刷新后重试')
        else:
            from sqlalchemy.exc import IntegrityError
            session.add(WeatherState(user_id=user.id,revision=1,payload=payload))
            try:session.flush()
            except IntegrityError:session.rollback();raise HTTPException(409,'天气设置已变化，请刷新后重试') from None
        session.commit();session.expire_all();return row_for(session,user)
    @app.get('/api/v1/weather')
    def get_weather(target_date:date|None=None,user=Depends(current),session=Depends(db)):
        return result(row_for(session,user),user,target_date)
    @app.get('/api/v1/weather/cities')
    def cities(q:str=Query(default='',max_length=80),user=Depends(current)):
        from .city_catalog import search
        return search(q)
    @app.post('/api/v1/weather/search')
    def search(data:Search,user=Depends(current),session=Depends(db)):
        external(data);row=row_for(session,user);check(row,data.expected_revision)
        if len(data.name.strip())<2:raise HTTPException(422,'请填写至少两个字符的城市名')
        try:candidates=app.state.weather.search(data.name.strip())
        except (ValueError,KeyError,TypeError):raise HTTPException(502,'城市结果格式异常，未更改位置') from None
        row=save(session,user,row,data.expected_revision,{**(row.payload if row else {}),'candidates':candidates})
        return result(row,user)
    @app.put('/api/v1/weather/location')
    def location(data:Choose,user=Depends(current),session=Depends(db)):
        row=row_for(session,user);check(row,data.expected_revision)
        chosen=next((r for r in (row.payload if row else {}).get('candidates',[]) if r['id']==data.candidate_id),None)
        if not chosen:
            from .city_catalog import lookup
            chosen=lookup(data.candidate_id)
        if not chosen:raise HTTPException(422,'请从已搜索结果或本地城市目录选择位置')
        row=save(session,user,row,data.expected_revision,{'location':chosen,'candidates':[]})
        return result(row,user)
    @app.post('/api/v1/weather/refresh')
    def refresh(data:External,user=Depends(current),session=Depends(db)):
        external(data);row=row_for(session,user);check(row,data.expected_revision)
        if not row or not row.payload.get('location'):raise HTTPException(422,'请先选择城市')
        previous=row.payload.get('forecast')
        if previous and now()-datetime.fromisoformat(previous['fetched_at'])<timedelta(minutes=15):return result(row,user)
        try:forecast=app.state.weather.forecast(row.payload['location'])
        except (ValueError,KeyError,TypeError,OverflowError,OSError):raise HTTPException(502,'天气结果缺失或格式异常；保留旧预报并检查过期状态') from None
        row=save(session,user,row,data.expected_revision,{**row.payload,'forecast':forecast})
        return result(row,user)
