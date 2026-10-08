"""Immutable opt-in forecast evidence for cycle drafts, with transactional approval guard."""
import copy
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from sqlalchemy import update
from .models import WeatherState
from .weather import view,now

def snapshot(session,user,profile,start,revision,avoid_rain):
    row=session.get(WeatherState,user.id)
    if not row or row.revision!=revision:raise HTTPException(409,'天气版本已变化，请刷新天气后重试')
    forecast=row.payload.get('forecast')
    if not row.payload.get('location') or not forecast or datetime.fromisoformat(forecast['expires_at'])<=now():raise HTTPException(422,'请先获取未过期天气，再选择天气联动')
    days=[]
    for i in range(7):
        v=view(row,profile,True,start+timedelta(days=i));hour=v['selected_hour']
        # Cover the supported maximum 132-minute run, including a partial starting hour.
        local_start=datetime.combine(start+timedelta(days=i),datetime.strptime(profile.preferred_time,'%H:%M').time(),ZoneInfo(profile.timezone)).astimezone(timezone.utc)
        first=local_start.replace(minute=0);last=local_start+timedelta(minutes=132)
        interval=[copy.deepcopy(h) for h in forecast['hours'] if first<=datetime.fromisoformat(h['time'])<last]
        expected=int(((last-first).total_seconds()+3599)//3600)
        reason='训练窗口内含雷暴预报，避开户外跑' if any(h.get('weather_code') in (95,96,99) for h in interval) else '按本人选择避开降水时段' if avoid_rain and any((h.get('precipitation') or 0)>0 for h in interval) else ''
        days.append({'date':v['target_date'],'hour':copy.deepcopy(hour),'window_hours':interval,'window_complete':len(interval)==expected,'blocked_reason':reason})
    return {'rule_version':'runner-weather-cycle-1','units':copy.deepcopy(forecast.get('units',{})),'revision':row.revision,'city':row.payload['location']['name'],'source':forecast['source'],'fetched_at':forecast['fetched_at'],'expires_at':forecast['expires_at'],'timezone':profile.timezone,'training_time':profile.preferred_time,'avoid_rain':avoid_rain,'days':days,'limitations':['采用开始日起七天内已有小时预报，保守检查从训练时间起132分钟窗口；缺失时段及其他日期天气未知。','没有接入官方预警或空气质量；出发前核对现场，预报不等于适跑许可。','避开雷暴与可选避雨为产品排期规则，不是医疗或气象安全认证。']}

def blocked(context):return {d['date']:d['blocked_reason'] for d in (context.get('weather_snapshot') or {}).get('days',[]) if d['blocked_reason']}

def validate(sessions,context):
    excluded=blocked(context)
    if any(s.type=='run' and s.date.isoformat() in excluded for s in sessions):raise ValueError('户外跑安排与已选择天气限制冲突')

def stale(session,user,payload):
    evidence=payload.get('weather_snapshot')
    if not evidence:return False
    row=session.get(WeatherState,user.id)
    if row:session.refresh(row)
    forecast=row.payload.get('forecast') if row else None
    return not row or row.revision!=evidence['revision'] or not row.payload.get('location') or not forecast or datetime.fromisoformat(forecast['expires_at'])<=now() or datetime.fromisoformat(evidence['expires_at'])<=now()

def claim(session,user,payload):
    evidence=payload.get('weather_snapshot')
    if not evidence:return
    if stale(session,user,payload):raise HTTPException(409,'天气已变化或过期，请刷新天气并重新生成草稿')
    # Acquire the same SQLite write lock as a weather refresh, without changing its revision.
    result=session.execute(update(WeatherState).where(WeatherState.user_id==user.id,WeatherState.revision==evidence['revision']).values(revision=evidence['revision']))
    if not result.rowcount or datetime.fromisoformat(evidence['expires_at'])<=now():session.rollback();raise HTTPException(409,'天气已变化或过期，请重新生成草稿')
