"""Recorded observation time differs from ingestion time; references are owner-scoped."""
from datetime import datetime, timezone, date, time
from zoneinfo import ZoneInfo
from fastapi import HTTPException
from .models import Workout, StrengthWorkout


def checkin_key(row):
    # Dates with no observation time remain explicitly unknown in responses.
    stamp=datetime.fromisoformat(row.get('observed_at') or row['created_at'])
    if stamp.tzinfo is None:stamp=stamp.replace(tzinfo=timezone.utc)
    return (row['date'],stamp.timestamp(),row['created_at'],row['id'])


def validate_feedback(data,user,session):
    zone=ZoneInfo(user.profile.get('timezone','Asia/Shanghai'))
    if data.observed_time:
        wall=datetime.combine(data.date,time.fromisoformat(data.observed_time))
        candidates={wall.replace(tzinfo=zone,fold=f).astimezone(timezone.utc) for f in (0,1) if wall.replace(tzinfo=zone,fold=f).astimezone(timezone.utc).astimezone(zone).replace(tzinfo=None)==wall}
        if len(candidates)!=1:raise HTTPException(422,'档案时区中该本地时刻不存在或有歧义；请留空或通过 API 提供明确的时区偏移')
        data.observed_at=candidates.pop()
    if data.observed_at:
        if data.observed_at.astimezone(zone).date()!=data.date:raise HTTPException(422,'发生时间与档案时区中的反馈日期不一致')
        if data.observed_at>datetime.now(timezone.utc):raise HTTPException(422,'身体反馈发生时间不能在未来')
    if not data.related_training_id:return
    model=Workout if data.related_training_type=='run' else StrengthWorkout
    row=session.get(model,data.related_training_id)
    if row is None or row.user_id!=user.id:raise HTTPException(404,'找不到可关联的实际训练记录')
    training_date=date.fromisoformat(row.payload['date'])
    if data.feedback_phase in ('during','after','next_day'):
        if data.date<training_date:raise HTTPException(422,'训练中或训练后的反馈不能早于训练日期')
        if row.payload.get('status')=='skipped':raise HTTPException(422,'跳过的训练不能关联训练中或训练后反馈')
    if data.feedback_phase=='next_day' and (data.date-training_date).days!=1:raise HTTPException(422,'次日反馈应在训练日期的下一天')
    if data.feedback_phase=='before' and data.date>training_date:raise HTTPException(422,'训练前反馈不能晚于训练日期')
