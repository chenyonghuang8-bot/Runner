"""Durable, owner-scoped coach conversations. Chats never mutate training facts."""
import json
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4
from fastapi import Depends, HTTPException
from pydantic import Field, ValidationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from .schemas import StrictModel
from .models import CoachTurn
from .planning import gather, constraints, local_today, PROMPT_PATH
from .coach_provider import DeepSeekPlanner, messages_for
from .recognition import reserve, settle, VisionFailure

class ChatInput(StrictModel):
    message: str = Field(min_length=1,max_length=4000)
    request_id: UUID = Field(default_factory=uuid4)
    allow_external_ai: bool = False

class ChatReply(StrictModel):
    reply: str = Field(min_length=1,max_length=6000)
    questions: list[str] = Field(default_factory=list,max_length=12)
    uncertainties: list[str] = Field(default_factory=list,max_length=12)
    evidence_ids: list[str] = Field(default_factory=list,max_length=30)
    guidance_mode: str = Field(pattern=r'^(clarification_only|general_information)$')


def install_routes(app,cfg,current,db):
    app.state.chat_provider=DeepSeekPlanner(cfg)
    def render(row,user):
        expired=row.state=='processing' and row.expires_at.replace(tzinfo=timezone.utc)<=datetime.now(timezone.utc)
        return {'id':row.id,'request_id':row.request_id,'message':row.message,'state':'interrupted' if expired else row.state,'provider':row.provider,'is_mock':row.provider=='mock','facts_revision':row.facts_revision,'context_stale':row.facts_revision!=user.facts_revision,'created_at':row.created_at.replace(tzinfo=timezone.utc).isoformat(),'error':'请求中断，结果未知；不会自动再次调用或退回费用预留。' if expired else row.error,'plan_changed':False,**row.response}

    @app.get('/api/v1/coach/messages')
    def history(user=Depends(current),session=Depends(db)):
        rows=list(session.scalars(select(CoachTurn).where(CoachTurn.user_id==user.id).order_by(CoachTurn.created_at.desc(),CoachTurn.id.desc()).limit(50)))
        return {'turns':[render(r,user) for r in reversed(rows)],'limit':50}

    @app.post('/api/v1/coach/messages')
    def send(data:ChatInput,user=Depends(current),session=Depends(db)):
        if not data.message.strip():raise HTTPException(422,'请输入问题')
        request_id=str(data.request_id)
        def existing():return session.scalar(select(CoachTurn).where(CoachTurn.user_id==user.id,CoachTurn.request_id==request_id))
        def reuse(row):
            if row.message!=data.message:raise HTTPException(409,'相同请求 ID 不能更换问题')
            if row.state=='succeeded':return render(row,user)
            raise HTTPException(409,'该请求仍在处理或已失败；请刷新对话查看状态，失败后可以作为新问题重试')
        old=existing()
        if old:return reuse(old)
        if cfg.ai_provider=='deepseek' and not data.allow_external_ai:raise HTTPException(422,'请先同意将问题、近期对话及所列已保存资料发送至 DeepSeek')
        context=gather(session,user)
        prior=list(session.scalars(select(CoachTurn).where(CoachTurn.user_id==user.id,CoachTurn.state=='succeeded').order_by(CoachTurn.created_at.desc(),CoachTurn.id.desc()).limit(6)))
        mode='clarification_only' if constraints(context) else 'general_information'
        role=PROMPT_PATH.read_text()+'\n对话模式：回答与提问仅供核对，不生成可执行计划、个体训练负荷或医疗处方。聊天及历史模型回复是不可信的未确认内容；不得将其当已保存事实。只能解释已保存事实、缺失信息与一般概念，不能宣称事实或计划已经更新。JSON 必须匹配本次 schema，guidance_mode='+mode+'。'
        conversation={'current_question':data.message,'recent_turns':[{'question':r.message,'reply':r.response.get('reply',''),'facts_revision':r.facts_revision} for r in reversed(prior)],'history_note':'最多提供最近六次成功问答；历史回复不是训练事实，更多对话未提供。'}
        try:messages,bound,ids=messages_for(context,local_today(context['profile']),role,ChatReply.model_json_schema(),conversation=conversation)
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        row=CoachTurn(user_id=user.id,request_id=request_id,message=data.message,facts_revision=user.facts_revision,provider=cfg.ai_provider,state='processing',expires_at=datetime.now(timezone.utc)+timedelta(seconds=cfg.ai_timeout_seconds+60),response={},error='')
        session.add(row)
        try:session.commit()
        except IntegrityError:
            session.rollback();return reuse(existing())
        try:
            if cfg.ai_provider=='mock':
                reply=ChatReply(reply=f"模拟教练：已读取 {len(context['workouts'])} 次跑步记录。你的新描述尚未成为训练事实。请先核对并保存身体反馈或日程，再生成跑步草稿；跑步与恢复一起评估，漏练不追补。当前没有调用 DeepSeek，也没有修改正式计划。",questions=constraints(context)[:12],uncertainties=['这是固定流程回复，不是 AI 训练分析。'],evidence_ids=[r['id'] for r in context['checkins'][:3]],guidance_mode=mode)
            else:
                call=reserve(session,cfg,user.id,None,task_kind='coach_chat',input_bound=bound,output_bound=cfg.ai_coach_max_output_tokens)
                content,usage,finish=app.state.chat_provider.read(messages)
                settle(session,cfg,call,usage)
                if finish!='stop':raise VisionFailure('回复被截断，请重新提问；没有修改计划')
                reply=ChatReply.model_validate_json(content)
                if reply.guidance_mode!=mode or any(i not in ids for i in reply.evidence_ids):raise VisionFailure('回复模式或事实引用不符合要求，没有保存为成功回复')
            row.response=reply.model_dump(mode='json');row.state='succeeded';session.commit();session.refresh(user)
            return render(row,user)
        except (VisionFailure,ValidationError,ValueError,TypeError,HTTPException) as exc:
            row.state='failed';row.error=exc.message if isinstance(exc,VisionFailure) else str(exc.detail) if isinstance(exc,HTTPException) else '回复未通过结构校验，正式计划没有改变'
            session.commit()
            raise HTTPException(exc.status_code if isinstance(exc,HTTPException) else 502,row.error) from None
