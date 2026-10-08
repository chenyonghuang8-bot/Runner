"""Explicit reset of current application data, with durable file cleanup."""
import hashlib,json,time
from collections import defaultdict,deque
from datetime import datetime,timedelta,timezone
from pathlib import Path
from fastapi import Depends,HTTPException
from pydantic import Field
from sqlalchemy import select,delete,update
from . import models as m
from .data_management import record
from .schemas import StrictModel,Profile
from .security import verify_password

# Order is significant: foreign-key dependents are removed first.
TABLES=(m.NotificationJob,m.NotificationSettings,m.PlanOutbox,m.PlanProposal,m.PlanVersion,m.CoachTurn,m.ImportMetricReview,m.ImageTileCache,m.ImportDraft,m.Workout,m.StrengthWorkout,m.Checkin,m.WeatherState)
LABELS={'notification_jobs':'提醒记录','notification_settings':'提醒偏好','plan_outbox':'计划事件','plan_proposals':'计划草稿','plan_versions':'正式计划版本','coach_turns':'教练聊天','import_metric_reviews':'指标核对历史','image_tile_cache':'识别缓存','imports':'截图与原图','workouts':'跑步记录','strength_workouts':'力量记录','checkins':'身体反馈','weather_states':'天气位置与预报','recognitions':'截图识别结果'}
PHRASE='清空我的训练数据'

class Confirmation(StrictModel):
    password:str=Field(min_length=1,max_length=128)
    confirmation:str=Field(max_length=40)

def lock(session,user):
    session.execute(update(m.User).where(m.User.id==user.id).values(facts_revision=m.User.facts_revision))
    session.refresh(user)

def state(session,user):
    rows={};imports=list(session.scalars(select(m.ImportDraft).where(m.ImportDraft.user_id==user.id)))
    for model in TABLES:
        rows[model.__tablename__]=[record(r) for r in session.scalars(select(model).where(model.user_id==user.id))]
    rows['recognitions']=[record(r) for r in session.scalars(select(m.Recognition).where(m.Recognition.import_id.in_([r.id for r in imports])))]
    # Fees are preserved, but new/reserved calls must invalidate an old preview.
    calls=[record(r) for r in session.scalars(select(m.AICall).where(m.AICall.user_id==user.id))]
    if any(r['status']=='reserved' for r in calls) or any(r['lease_until']>time.time() for r in rows['recognitions']) or any(r['state']=='processing' and datetime.fromisoformat(r['expires_at']).timestamp()>time.time() for r in rows['coach_turns']):
        raise HTTPException(409,'有AI请求或截图识别正在进行，请完成后再预览清空范围')
    canonical={'account':record(user,('username','password_hash','created_at')),'tables':rows,'calls':calls}
    for values in rows.values():values.sort(key=lambda r:json.dumps(r,sort_keys=True,ensure_ascii=False))
    calls.sort(key=lambda r:r['id'])
    fingerprint=hashlib.sha256(json.dumps(canonical,sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    return fingerprint,rows,imports

def clean_files(session,job,root):
    root=Path(root).resolve();remaining=[]
    for name in job.pending_files:
        try:
            if not name or Path(name).name!=name or name in ('.','..'):raise ValueError()
            p=root/name
            # Never follow links or remove directories. Missing files are already cleaned.
            if p.is_symlink():raise ValueError()
            if p.exists():
                if not p.is_file() or p.resolve().parent!=root:raise ValueError()
                if session.scalar(select(m.ImportDraft.id).where(m.ImportDraft.storage_name==name)):raise ValueError()
                p.unlink()
        except (OSError,ValueError):remaining.append(name)
    job.pending_files=remaining;job.state='cleanup_pending' if remaining else 'done';session.commit()
    return {'id':job.id,'state':job.state,'remaining_originals':len(remaining),'message':'应用数据已清空；部分原图清理失败，请重试。' if remaining else '当前应用数据与登记原图已清空。账户和AI费用保留；备份与导出文件未删除。'}

def install_routes(app,cfg,current,db):
    attempts=defaultdict(deque)
    def reauthenticate(session,user,body):
        now=time.time();queue=attempts[user.id]
        while queue and queue[0]<now-600:queue.popleft()
        if len(queue)>=5:raise HTTPException(429,'密码验证尝试过多，请十分钟后再试')
        queue.append(now)
        session.refresh(user)
        if not verify_password(body.password,user.password_hash):raise HTTPException(403,'密码不正确，未清空数据')
        if body.confirmation!=PHRASE:raise HTTPException(422,'请输入完整确认文字：'+PHRASE)

    @app.post('/api/v1/data/deletion-preview')
    def preview(user=Depends(current),session=Depends(db)):
        lock(session,user);fp,rows,_=state(session,user)
        session.execute(delete(m.DataDeletion).where(m.DataDeletion.user_id==user.id,m.DataDeletion.state=='preview'))
        job=m.DataDeletion(user_id=user.id,fingerprint=fp,expires_at=datetime.now(timezone.utc)+timedelta(minutes=10),state='preview',pending_files=[])
        session.add(job);session.commit()
        return {'id':job.id,'expires_at':job.expires_at.replace(tzinfo=timezone.utc).isoformat(),'counts':[{'label':LABELS[k],'count':len(v)} for k,v in rows.items()],'confirmation':PHRASE}

    @app.get('/api/v1/data/deletions')
    def pending(user=Depends(current),session=Depends(db)):
        return [{'id':j.id,'remaining_originals':len(j.pending_files)} for j in session.scalars(select(m.DataDeletion).where(m.DataDeletion.user_id==user.id,m.DataDeletion.state=='cleanup_pending'))]

    @app.post('/api/v1/data/deletions/{identifier}/confirm')
    def confirm(identifier:str,body:Confirmation,user=Depends(current),session=Depends(db)):
        reauthenticate(session,user,body);lock(session,user)
        job=session.scalar(select(m.DataDeletion).where(m.DataDeletion.id==identifier,m.DataDeletion.user_id==user.id))
        if not job:raise HTTPException(404,'清空预览不存在，请重新预览')
        if job.state!='preview':return clean_files(session,job,cfg.private_storage_dir)
        if job.expires_at.replace(tzinfo=timezone.utc)<=datetime.now(timezone.utc):raise HTTPException(409,'清空预览已过期，请重新预览')
        fp,_,imports=state(session,user)
        if fp!=job.fingerprint:raise HTTPException(409,'数据已变化，请重新预览清空范围')
        job.pending_files=[r.storage_name for r in imports];job.state='cleanup_pending'
        # Budget and call totals are not reset; detach deleted imports and remove ancillary usage.
        session.execute(update(m.AICall).where(m.AICall.user_id==user.id).values(import_id=None,usage={}))
        session.execute(delete(m.Recognition).where(m.Recognition.import_id.in_([r.id for r in imports])))
        for model in TABLES:session.execute(delete(model).where(model.user_id==user.id))
        user.profile=Profile().model_dump(mode='json');user.availability=[]
        user.facts_revision+=1;user.plan_version+=1
        session.commit() # Original cleanup is durable and retryable if filesystem fails.
        lock(session,user)
        return clean_files(session,job,cfg.private_storage_dir)
