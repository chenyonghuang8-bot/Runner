"""Human-confirmed metrics are append-only; raw screenshot evidence stays immutable."""
import hashlib
import json
import re
import time
from typing import Literal
from fastapi import Depends, HTTPException
from pydantic import Field, ConfigDict
from sqlalchemy import select, update
from .models import ImportDraft, ImportMetricReview, Recognition, now
from .recognition import METRICS, FIELD_UNITS, validate_readings
from .schemas import StrictModel

class MetricReviewInput(StrictModel):
    model_config=ConfigDict(extra='forbid',strict=True,allow_inf_nan=False)
    expected_revision:int=Field(ge=1)
    cache_key:str=Field(min_length=64,max_length=64)
    field:str
    status:Literal['confirmed','excluded']
    value:float|int|None
    unit:str=Field(max_length=30)
    raw:str=Field(min_length=1,max_length=200)
    note:str=Field(max_length=300)
    source_reading_index:int=Field(ge=0)

def evidence(job,field):
    rows=[r for t in job.tiles for r in t['readings']]
    group=[r for r in rows if r['field']==field]
    return rows,hashlib.sha256(json.dumps(group,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def history(session,draft,job):
    rows=session.scalars(select(ImportMetricReview).where(ImportMetricReview.import_id==draft.id,ImportMetricReview.user_id==draft.user_id).order_by(ImportMetricReview.draft_revision.desc())).all()
    latest=set();result=[]
    for row in rows:
        p=row.payload;field=p['field']
        matches=bool(job and p['cache_key']==job.cache_key and p['evidence_hash']==evidence(job,field)[1])
        current=field not in latest and matches
        latest.add(field)
        result.append({'id':row.id,'draft_revision':row.draft_revision,'created_at':row.created_at.isoformat(),**p,'current':current})
    return result

def confirmed_metrics(session,draft):
    job=session.get(Recognition,draft.id)
    return {r['field']:{'value':r['value'],'unit':r['unit'],'origin':'device_estimate' if r['field'].startswith('device_') else 'device','review_id':r['id'],'review_source':'human_confirmed'} for r in history(session,draft,job) if r['current'] and r['status']=='confirmed'}

def install_routes(app,current,db,own_import,import_json):
    @app.post('/api/v1/imports/{id}/metric-reviews',status_code=201)
    def save(id:str,data:MetricReviewInput,user=Depends(current),session=Depends(db)):
        draft=own_import(id,user,session)
        if draft.status=='confirmed':raise HTTPException(409,'截图已经确认，不能改动已保存指标')
        if data.field not in METRICS and not re.fullmatch(r'split_(?:[1-9]|[1-4][0-9]|50)_seconds',data.field):raise HTTPException(422,'只支持详细指标和整公里分段；基础记录请在表单填写')
        job=session.get(Recognition,id)
        if not job or job.cache_key!=data.cache_key or any(t['state']!='done' for t in job.tiles) or job.lease_until>time.time():raise HTTPException(409,'识别未完成或证据已变化，请刷新后核对')
        rows,digest=evidence(job,data.field)
        if data.source_reading_index>=len(rows) or rows[data.source_reading_index]['field']!=data.field:raise HTTPException(422,'证据读数与字段不对应')
        expected='s' if data.field.startswith('split_') else FIELD_UNITS[data.field]
        if data.unit!=expected:raise HTTPException(422,'请使用标准单位：'+(expected or '无单位'))
        if not data.raw.strip():raise HTTPException(422,'请填写核对后的原图文字')
        if data.status=='excluded':
            if data.value is not None:raise HTTPException(422,'不采用的指标必须留空')
            if not data.note.strip():raise HTTPException(422,'请说明不采用原因')
        else:
            if data.value is None:raise HTTPException(422,'确认指标需要明确数值；不可读时请选择不采用')
            if re.search(r'[<>＜＞≤≥]|小于|大于|少于|多于|约|模糊',data.raw):raise HTTPException(422,'非精确读数不能确认成精确数值，请选择不采用')
            source=rows[data.source_reading_index]['source_rect']
            x,y,w,h=source
            item={'field':data.field,'value':data.value,'raw':data.raw,'unit':data.unit,'rect':[x/draft.width,y/draft.height,w/draft.width,h/draft.height]}
            checked=validate_readings(json.dumps({'readings':[item]}),[0,0,draft.width,draft.height],0)[0]
            if checked['issue']:raise HTTPException(422,checked['issue'])
            if data.field.startswith('balance_') and data.value>100:raise HTTPException(422,'百分比不能超过100')
        changed=session.execute(update(ImportDraft).where(ImportDraft.id==id,ImportDraft.user_id==user.id,ImportDraft.revision==data.expected_revision,ImportDraft.status!='confirmed').values(revision=ImportDraft.revision+1))
        if not changed.rowcount:
            session.rollback();raise HTTPException(409,'草稿已变化，请刷新后重试')
        locked=session.execute(update(Recognition).where(Recognition.import_id==id,Recognition.cache_key==data.cache_key,Recognition.updated_at==job.updated_at,Recognition.lease_until<=int(time.time())).values(updated_at=now()))
        if not locked.rowcount:
            session.rollback();raise HTTPException(409,'识别证据已变化，请刷新后核对')
        session.add(ImportMetricReview(user_id=user.id,import_id=id,draft_revision=data.expected_revision+1,payload={**data.model_dump(exclude={'expected_revision'}),'evidence_hash':digest,'source_rect':rows[data.source_reading_index]['source_rect'],'source_snapshot':[r for r in rows if r['field']==data.field]}))
        session.commit();session.refresh(draft)
        return import_json(draft)
