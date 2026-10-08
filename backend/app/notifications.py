"""Durable local simulation. No network sender and no delivery claims."""
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from fastapi import Depends,HTTPException
from pydantic import Field,model_validator
from sqlalchemy import select,update,func
from .schemas import StrictModel,Profile
from .models import User,NotificationSettings,NotificationJob,PlanOutbox
from .planning import gather,constraints
from .weather_plan import stale


def now():return datetime.now(timezone.utc)
def utc(t):return t.replace(tzinfo=timezone.utc) if t.tzinfo is None else t.astimezone(timezone.utc)

class Preferences(StrictModel):
    enabled:bool=False
    plan_updates:bool=True
    training_reminders:bool=True
    lead_minutes:int=Field(default=30,ge=5,le=180)
    daily_limit:int=Field(default=2,ge=1,le=5)
    quiet_enabled:bool=True
    quiet_start:str=Field(default='22:00',pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    quiet_end:str=Field(default='08:00',pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    @model_validator(mode='after')
    def window(self):
        if self.quiet_enabled and self.quiet_start==self.quiet_end:raise ValueError('免打扰起止不能相同；全天不提醒请关闭提醒')
        return self

class SavePreferences(Preferences):
    expected_revision:int=Field(ge=0)

class Simulator:
    def send(self,payload):return 'simulated'


def preferences(session,user):
    row=session.get(NotificationSettings,user.id)
    return Preferences.model_validate(row.payload if row else {}),row.revision if row else 0


def eligibility(session,user,context=None):
    context=context or gather(session,user);plan=context['old']
    if not plan:return '尚无正式计划'
    if any(s.get('type')=='strength' for s in plan.payload.get('sessions',[])):return '历史计划含已停用课程，请重新生成跑步计划'
    if plan.version!=user.plan_version or plan.facts_revision!=user.facts_revision:return '事实或正式计划已变化'
    if constraints(context):return '当前身体状态需核对'
    if stale(session,user,plan.payload):return '天气依据已变化或过期'
    return ''


def quiet_until(instant,prefs,zone):
    local=instant.astimezone(ZoneInfo(zone));clock=local.strftime('%H:%M');a=prefs.quiet_start;b=prefs.quiet_end
    inside=prefs.quiet_enabled and (a<=clock<b if a<b else clock>=a or clock<b)
    if not inside:return instant
    end=datetime.combine(local.date(),datetime.strptime(b,'%H:%M').time(),ZoneInfo(zone))
    if end<=local:end+=timedelta(days=1)
    return end.astimezone(timezone.utc)


def reconcile(session,user,instant):
    prefs,revision=preferences(session,user);context=gather(session,user);reason=eligibility(session,user,context)
    pending=list(session.scalars(select(NotificationJob).where(NotificationJob.user_id==user.id,NotificationJob.state=='pending')))
    for job in pending:
        why='提醒已关闭' if not prefs.enabled else reason or ('正式计划已变化' if job.plan_version!=user.plan_version or job.facts_revision!=user.facts_revision else '')
        if utc(job.expires_at)<=instant:why='提醒已过期'
        if why:job.state='cancelled';job.reason=why
    if not prefs.enabled or reason:return
    plan=context['old'];profile=context['profile'];zone=ZoneInfo(profile.timezone)
    def add(key,title,kind,due,expires):
        if expires<=instant:return
        job=session.scalar(select(NotificationJob).where(NotificationJob.user_id==user.id,NotificationJob.dedupe_key==key))
        if job and not (job.state=='pending' or job.state=='cancelled' and job.reason in ('提醒已关闭','该类提醒已关闭')):return
        if not job:
            job=NotificationJob(user_id=user.id,dedupe_key=key,plan_version=plan.version,facts_revision=plan.facts_revision,settings_revision=revision,due_at=due,expires_at=expires,payload={'title':title,'kind':kind,'body':'请打开 Runner，登录后核对当前安排与身体反馈。'},state='pending',attempts=0,reason='');session.add(job)
        elif job.settings_revision!=revision or job.state=='cancelled':
            job.settings_revision=revision;job.due_at=due;job.expires_at=expires;job.state='pending';job.reason=''
    outbox=session.scalar(select(PlanOutbox).where(PlanOutbox.user_id==user.id,PlanOutbox.plan_version==plan.version,PlanOutbox.state=='pending'))
    if prefs.plan_updates and outbox:
        add(f'plan:{plan.version}','Runner · 计划已更新','plan_updated',utc(outbox.created_at),utc(outbox.created_at)+timedelta(hours=24))
    if prefs.training_reminders:
        days=sorted({s['date'] for s in plan.payload['sessions'] if s['type']=='run'})
        for day in days:
            start=datetime.combine(datetime.fromisoformat(day).date(),datetime.strptime(profile.preferred_time,'%H:%M').time(),zone).astimezone(timezone.utc)
            add(f'training:{plan.version}:{day}','Runner · 训练前核对','training_reminder',start-timedelta(minutes=prefs.lead_minutes),start)
    for job in pending:
        if job.state=='pending' and (job.payload['kind']=='plan_updated' and not prefs.plan_updates or job.payload['kind']=='training_reminder' and not prefs.training_reminders):job.state='cancelled';job.reason='该类提醒已关闭'


def simulate(session,user,sender,instant=None):
    instant=instant or now()
    # Serialize policy checks, quota and state transition for this single-user SQLite worker.
    session.execute(update(User).where(User.id==user.id).values(plan_version=User.plan_version));session.refresh(user)
    reconcile(session,user,instant);session.flush();prefs,_=preferences(session,user)
    jobs=list(session.scalars(select(NotificationJob).where(NotificationJob.user_id==user.id,NotificationJob.state=='pending',NotificationJob.due_at<=instant).order_by(NotificationJob.due_at,NotificationJob.id)))
    processed=0
    for job in jobs:
        deferred=quiet_until(instant,prefs, Profile.model_validate(user.profile).timezone)
        if deferred>instant:
            if deferred>=utc(job.expires_at):job.state='cancelled';job.reason='免打扰结束时已过期'
            else:job.due_at=deferred;job.reason='免打扰延期'
            continue
        local=instant.astimezone(ZoneInfo(user.profile.get('timezone','Asia/Shanghai')))
        midnight=datetime.combine(local.date(),datetime.min.time(),local.tzinfo).astimezone(timezone.utc)
        end=datetime.combine(local.date()+timedelta(days=1),datetime.min.time(),local.tzinfo).astimezone(timezone.utc)
        count=session.scalar(select(func.count()).select_from(NotificationJob).where(NotificationJob.user_id==user.id,NotificationJob.state.in_(['simulated','unknown']),NotificationJob.processed_at>=midnight,NotificationJob.processed_at<end))
        if count>=prefs.daily_limit:
            if end>=utc(job.expires_at):job.state='cancelled';job.reason='每日额度内无法及时提醒'
            else:job.due_at=end;job.reason='每日额度延期'
            continue
        job.attempts+=1
        try:outcome=sender.send(job.payload)
        except Exception:outcome='unknown' # Never persist raw exceptions or retry ambiguous submission.
        if outcome=='simulated':job.state='simulated';job.reason='仅本地模拟，未发送微信';job.processed_at=instant;processed+=1
        elif outcome=='retryable_failure':
            retry=instant+timedelta(minutes=5)
            if job.attempts>=3 or retry>=utc(job.expires_at):job.state='failed';job.reason='已明确失败，达到重试上限或已无有效时间'
            else:job.due_at=retry;job.reason='已明确失败，五分钟后可重试'
        else:job.state='unknown';job.reason='结果未知，不自动重试';job.processed_at=instant
        session.flush()
    session.commit();return {'simulated_count':processed,'external_sent':False}


def install_routes(app,cfg,current,db):
    app.state.notification_simulator=Simulator()
    def settings_view(session,user):
        prefs,revision=preferences(session,user)
        return {**prefs.model_dump(),'revision':revision,'provider':'local_simulation','external_enabled':False,'scheduler_running':False,'timezone':user.profile.get('timezone','Asia/Shanghai'),'attention':eligibility(session,user)}
    @app.get('/api/v1/notification-settings')
    def settings(user=Depends(current),session=Depends(db)):return settings_view(session,user)
    @app.put('/api/v1/notification-settings')
    def save(data:SavePreferences,user=Depends(current),session=Depends(db)):
        row=session.get(NotificationSettings,user.id)
        if (row.revision if row else 0)!=data.expected_revision:raise HTTPException(409,'提醒设置已变化，请刷新')
        payload=data.model_dump(exclude={'expected_revision'})
        if row:
            changed=session.execute(update(NotificationSettings).where(NotificationSettings.user_id==user.id,NotificationSettings.revision==data.expected_revision).values(revision=data.expected_revision+1,payload=payload))
            if not changed.rowcount:session.rollback();raise HTTPException(409,'提醒设置已变化，请刷新')
        else:
            from sqlalchemy.exc import IntegrityError
            session.add(NotificationSettings(user_id=user.id,revision=1,payload=payload))
            try:session.flush()
            except IntegrityError:session.rollback();raise HTTPException(409,'提醒设置已变化，请刷新') from None
        if not data.enabled:session.execute(update(NotificationJob).where(NotificationJob.user_id==user.id,NotificationJob.state=='pending').values(state='cancelled',reason='提醒已关闭'))
        session.commit();session.expire_all();return settings_view(session,user)
    @app.get('/api/v1/notifications')
    def history(user=Depends(current),session=Depends(db)):
        return [{'id':j.id,'state':j.state,'attempts':j.attempts,'due_at':utc(j.due_at).isoformat(),'expires_at':utc(j.expires_at).isoformat(),'processed_at':utc(j.processed_at).isoformat() if j.processed_at else None,'reason':j.reason,'plan_version':j.plan_version,**j.payload} for j in session.scalars(select(NotificationJob).where(NotificationJob.user_id==user.id).order_by(NotificationJob.created_at.desc()).limit(50))]
    @app.post('/api/v1/notifications/preview')
    def preview(user=Depends(current),session=Depends(db)):
        session.execute(update(User).where(User.id==user.id).values(plan_version=User.plan_version));session.refresh(user)
        reconcile(session,user,now());session.commit();return {'external_sent':False,'message':'待发队列已按当前事实核对；未发送微信'}
    @app.post('/api/v1/notifications/simulate')
    def run(user=Depends(current),session=Depends(db)):return simulate(session,user,app.state.notification_simulator)
