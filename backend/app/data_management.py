"""Authenticated portable data exports; never an executable import format."""
import hashlib,json,tempfile,zipfile
from pathlib import Path
from datetime import datetime,timezone
from fastapi import Depends,HTTPException
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask
from sqlalchemy import select
from . import models as m

SCHEMA='runner-data-export-1'

def original_path(root,name):
    root=Path(root).resolve()
    if not name or Path(name).name!=name or name in ('.','..'):raise ValueError('原图存储路径不合法')
    p=root/name
    if p.is_symlink() or not p.is_file() or p.resolve().parent!=root:raise ValueError('原图缺失或不是本地常规文件')
    return p

def digest(path):
    h=hashlib.sha256()
    with Path(path).open('rb') as f:
        for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
    return h.hexdigest()

def record(row,exclude=()):
    result={}
    for c in row.__table__.columns:
        if c.name in exclude:continue
        v=getattr(row,c.name)
        if isinstance(v,datetime):v=(v.replace(tzinfo=timezone.utc) if v.tzinfo is None else v.astimezone(timezone.utc)).isoformat()
        result[c.name]=v
    return result

def snapshot(session,user):
    # sqlite3 legacy transaction mode does not begin a snapshot for SELECT automatically.
    connection=session.connection()
    if not connection.connection.driver_connection.in_transaction:connection.exec_driver_sql('BEGIN')
    session.refresh(user)
    data={'schema_version':SCHEMA,'exported_at':datetime.now(timezone.utc).isoformat(),'account':record(user,('username','password_hash')),'tables':{}}
    for model in (m.Workout,m.StrengthWorkout,m.Checkin,m.ImportDraft,m.ImportMetricReview,m.PlanVersion,m.PlanProposal,m.PlanOutbox,m.CoachTurn,m.WeatherState,m.NotificationSettings,m.NotificationJob,m.AICall):
        data['tables'][model.__tablename__]=[record(r,('storage_name',)) for r in session.scalars(select(model).where(model.user_id==user.id))]
    imports=list(session.scalars(select(m.ImportDraft).where(m.ImportDraft.user_id==user.id)))
    data['tables']['recognitions']=[record(r,('lease_token','lease_until')) for r in session.scalars(select(m.Recognition).join(m.ImportDraft,m.Recognition.import_id==m.ImportDraft.id).where(m.ImportDraft.user_id==user.id))]
    return data,imports

def install_routes(app,cfg,current,db):
    @app.get('/api/v1/data/export')
    def export(include_originals:bool=False,user=Depends(current),session=Depends(db)):
        tmp=tempfile.TemporaryDirectory(prefix='runner-export-')
        try:
            data,imports=snapshot(session,user);files=[];size=0
            archive=Path(tmp.name)/'runner-data.zip'
            with zipfile.ZipFile(archive,'w',compression=zipfile.ZIP_DEFLATED) as z:
                for draft in imports:
                    entry={'import_id':draft.id,'sha256':draft.file_hash,'included':False,'path':None}
                    if include_originals:
                        try:p=original_path(cfg.private_storage_dir,draft.storage_name)
                        except ValueError:raise HTTPException(409,'有原图缺失或路径异常；请先核对，或取消包含原图后导出') from None
                        size+=p.stat().st_size
                        if size>512*1024*1024:raise HTTPException(413,'原图合计超过512MB，请使用本机备份工具')
                        if digest(p)!=draft.file_hash:raise HTTPException(409,'原图散列与数据库不一致，未生成导出包')
                        path='originals/'+draft.id+Path(draft.storage_name).suffix
                        z.write(p,path);entry.update(included=True,path=path)
                    files.append(entry)
                raw=json.dumps(data,ensure_ascii=False,indent=2).encode()
                if len(raw)>64*1024*1024:raise HTTPException(413,'数据超过64MB，请使用本机备份工具')
                z.writestr('data.json',raw)
                z.writestr('manifest.json',json.dumps({'schema_version':SCHEMA,'data_sha256':hashlib.sha256(raw).hexdigest(),'originals':files,'includes_originals':include_originals,'limitations':['个人数据导出，不是数据库备份，不支持直接导入执行。','待核对截图与模型草稿保留状态，不自动转成训练事实。','不含密码、登录会话、应用密钥、内部识别缓存。','天气和计划可能已过期；保留原始版本，不视为当前有效。']},ensure_ascii=False,indent=2))
            session.rollback()
            return FileResponse(archive,media_type='application/zip',filename='runner-data.zip',background=BackgroundTask(tmp.cleanup))
        except BaseException:
            tmp.cleanup();raise
