"""Joint plan drafts; strict checks and one transactional approval for all session types."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from uuid import uuid4
from fastapi import Depends, HTTPException
from pydantic import Field, model_validator, ValidationError
from sqlalchemy import select, update
from sqlalchemy.exc import IntegrityError
from .models import User, Workout, Checkin, StrengthWorkout, PlanVersion, PlanProposal, PlanOutbox
from .schemas import StrictModel, StrengthInput, Profile
from .exercises import EXERCISES, BY_ID
from typing import Literal
from pathlib import Path

PROMPT_PATH=Path(__file__).parent/'prompts/coach_system.txt'

class Prescription(StrictModel):
    exercise_id: str
    sets: int = Field(ge=1,le=3)
    repetitions: int = Field(ge=1,le=12)
    rest_seconds: int = Field(ge=30,le=180)
    resistance: str = Field(default='自身体重，保持可控动作',max_length=100)
    weight_kg: float | None = None

class PlanItem(StrictModel):
    id: str = Field(min_length=1,max_length=80)
    date: date
    type: Literal['run','strength','recovery']
    title: str = Field(min_length=1,max_length=100)
    duration_minutes: int = Field(ge=0,le=180)
    purpose: str = Field(min_length=1,max_length=500)
    intensity: Literal['easy','rest']
    exercises: list[Prescription] = Field(default_factory=list,max_length=5)
    stop_condition: str = Field(min_length=1,max_length=500)
    @model_validator(mode='after')
    def type_fields(self):
        if self.type=='recovery':
            if self.duration_minutes or self.exercises or self.intensity!='rest':raise ValueError('休息安排不能含训练负荷')
        elif self.duration_minutes<1 or self.intensity!='easy':raise ValueError('当前一般训练草稿仅支持轻强度')
        if self.type=='strength' and not self.exercises:raise ValueError('力量卡需有具体动作')
        if self.type!='strength' and self.exercises:raise ValueError('非力量课不含力量动作')
        return self

class PlanData(StrictModel):
    summary: str = Field(min_length=1,max_length=2000)
    assessment: str = Field(min_length=1,max_length=2000)
    questions: list[str] = Field(default_factory=list,max_length=20)
    uncertainties: list[str] = Field(default_factory=list,max_length=20)
    evidence_ids: list[str] = Field(default_factory=list,max_length=100)
    sessions: list[PlanItem] = Field(max_length=28)

class Generate(StrictModel):
    start_date: date
    allow_external_ai: bool = False

class Approval(StrictModel):
    expected_plan_version: int = Field(ge=0)
    expected_facts_revision: int = Field(ge=1)

class Restore(Approval):
    source_version: int = Field(ge=0)
    start_date: date


def local_today(profile):return datetime.now(ZoneInfo(profile.timezone)).date()

def gather(session,user):
    profile=Profile.model_validate(user.profile)
    def records(model):
        rows=session.scalars(select(model).where(model.user_id==user.id)).all()
        return sorted([{'id':r.id,**r.payload,'created_at':r.created_at.replace(tzinfo=timezone.utc).isoformat()} for r in rows],key=lambda r:(r['date'],r['created_at']),reverse=True)
    workouts=records(Workout);strength=[];checks=records(Checkin)
    profile=profile.model_copy(update={'strength_experience':'unknown','strength_equipment':[],'strength_max_minutes':None,'familiar_exercises':[],'band_resistance':'','same_day_strength':False})
    from .feedback import checkin_key
    checks=sorted(checks,key=checkin_key,reverse=True)
    latest=checks[0] if checks else None
    old=session.scalar(select(PlanVersion).where(PlanVersion.user_id==user.id,PlanVersion.version==user.plan_version))
    return {'profile':profile,'workouts':workouts,'strength':strength,'checkins':checks,'latest':latest,'old':old,'days':user.availability,'running_only':True}

def prior_run_dates(context,start):
    """Respect yesterday's actual run and retained arrangements before the replaced window."""
    dates={date.fromisoformat(r['date']) for r in context['workouts'] if r['date']<start.isoformat()}
    old=context.get('old')
    if old:dates.update(date.fromisoformat(r['date']) for r in old.payload.get('sessions',[]) if r['type']=='run' and r['date']<start.isoformat())
    return dates

def validate_run_spacing(sessions,context,start):
    runs=[s.date for s in sessions if s.type=='run']
    if any(abs((a-b).days)<=1 for i,a in enumerate(runs) for b in runs[i+1:]) or any(abs((a-b).days)<=1 for a in runs for b in prior_run_dates(context,start)):
        raise ValueError('跑步至少隔天安排，跨周及已记录的前一天也不能连续跑')

def validate_run_week_counts(sessions,context,start):
    previous=prior_run_dates(context,start)
    future={s.date for s in sessions if s.type=='run'}
    for day in future:
        monday=day-timedelta(days=day.weekday())
        if sum(monday<=d<monday+timedelta(days=7) for d in previous|future)>context['profile'].runs_per_week:
            raise ValueError('每周跑步次数包含本周已记录或保留的先前跑步，不能额外补满三次')

def tolerated_soreness(context):
    c=context.get('latest') or {}
    return bool((c.get('soreness_level') or 0)>0 and c.get('soreness_tolerability')=='tolerable' and c.get('function_affected') is False and c.get('soreness_trend') in ('improving','stable') and c.get('soreness_location') and not any(word in c['soreness_location'] for word in ('膝','关节')) and (c.get('soreness_level') or 0)<7)

def reduced(context):
    c=context.get('latest') or {}
    return c.get('training_readiness')=='reduce' or tolerated_soreness(context) or c.get('fatigue',0)>=5

def available_exercises(context):
    if context.get('running_only'):return []
    p=context['profile']
    return [e for e in EXERCISES if e['id'] in p.familiar_exercises and e['equipment'] in p.strength_equipment and (e['equipment']!='resistance_band' or p.band_resistance.strip()) and not (tolerated_soreness(context) and e['region']=='lower')]

def constraints(context):
    profile=context['profile'];c=context['latest']
    reasons=[]
    if profile.doctor_restrictions or (profile.health_notes and profile.running_permission!='user_reports_doctor_allows'):reasons.append('档案有身体情况或医生限制；当前规则未经临床审阅，不生成医疗恢复处方')
    if not c or c['date']!=local_today(profile).isoformat():reasons.append('缺少今天的身体反馈')
    if c:
        if c.get('pain_location') or (c.get('pain_level') or 0)>0 or c.get('pain_level') is None:reasons.append('疼痛状态需核对，不能把关节痛判断为普通酸胀')
        if c.get('soreness_level') is None or ((c.get('soreness_level') or 0)>0 and not tolerated_soreness(context)):reasons.append('局部肌肉酸胀或其耐受情况需核对')
        if c.get('function_affected') is not False:reasons.append('日常活动是否受影响需核对')
        if c.get('illness_notes'):reasons.append('已反馈疾病情况，需要重新评估')
        if c.get('training_readiness','unknown') in ('unknown','rest'):reasons.append('用户尚未确认今天适合训练，或希望休息')
    if c and c.get('fatigue',0)>=7:reasons.append('自述高疲劳，暂缓跑步训练并核对恢复')
    # A later health update does not silently erase unassessed symptoms in a recent workout.
    recent_start=(local_today(profile)-timedelta(days=7)).isoformat()
    if any(r.get('pain_notes') for r in context['workouts']+context['strength'] if r['date']>=recent_start and not (profile.running_permission=='user_reports_doctor_allows' and profile.medical_review_date and r['date']<=profile.medical_review_date.isoformat())):reasons.append('近期实际训练有未评估疼痛反馈')
    return reasons

def mock_plan(context,start):
    """Explicit workflow sample. No target-derived paces or claimed clinical assessment."""
    profile=context['profile'];c=context['latest'];reasons=constraints(context);questions=list(reasons)
    if not context.get('running_only') and profile.strength_experience=='unknown':questions.append('请在档案填写力量训练经验')
    if not context.get('running_only') and (not profile.strength_equipment or not profile.strength_max_minutes):questions.append('请填写力量器械和单次可用时长')
    known=available_exercises(context)
    if 'resistance_band' in profile.strength_equipment and not profile.band_resistance.strip():questions.append('请填写已经熟悉、可控且不会引发不适的弹力带阻力；不根据颜色推测公斤或自动加阻力')
    if not context.get('running_only') and not known:questions.append('请核对熟悉且不会引发不适的基础动作；不自动假定技术水平或器械阻力')
    sessions=[];runs=0;strengths=0
    is_reduced=reduced(context)
    stop='出现疼痛、异常不适或无法保持可控动作时停止；更新身体反馈后重新评估。'
    def item(day,kind,minutes,title,exercises=None):
        return {'id':str(uuid4()),'date':day,'type':kind,'title':title,'duration_minutes':minutes,'purpose':'模拟流程示例，需核对个人适用性；不会承诺比赛成绩。','intensity':'rest' if kind=='recovery' else 'easy','exercises':exercises or [],'stop_condition':stop}
    slots={d['date']:d for d in context['days']}
    for i in range(7):
        day=(start+timedelta(days=i)).isoformat();slot=slots.get(day)
        parsed=date.fromisoformat(day);monday=parsed-timedelta(days=parsed.weekday())
        already=sum(monday<=d<monday+timedelta(days=7) for d in prior_run_dates(context,start)|{date.fromisoformat(r['date']) for r in sessions if r['type']=='run'})
        if profile.goal_date and day==profile.goal_date.isoformat():
            sessions.append(item(day,'recovery',0,'比赛安排 · 等待单独核对'));continue
        if reasons or not slot or not slot['available']:
            sessions.append(item(day,'recovery',0,'休息 / 等待评估' if reasons else '休息 / 无训练窗口'));continue
        available=slot['duration_minutes']
        # Strength and runs share available minutes. Missed sessions never become a debt.
        is_strength=bool(known and profile.strength_max_minutes and profile.strength_experience!='unknown' and strengths<2 and i%2==1)
        if is_strength:
            total=min(available,profile.strength_max_minutes,10 if is_reduced else 15)
            if total>=10:
                exercise={'exercise_id':known[0]['id'],'sets':1 if is_reduced else 2,'repetitions':5,'rest_seconds':60,'resistance':profile.band_resistance if known[0]['equipment']=='resistance_band' else '自身体重，保持可控动作','weight_kg':None}
                sessions.append(item(day,'strength',total,'基础力量 · 模拟示例',[exercise]));strengths+=1;continue
        if runs<profile.runs_per_week and already<profile.runs_per_week and (i%2==0 or not known) and all(abs((date.fromisoformat(day)-d).days)>1 for d in prior_run_dates(context,start)|{date.fromisoformat(r['date']) for r in sessions if r['type']=='run'}):
            total=min(available,15 if is_reduced else 25)
            sessions.append(item(day,'run',total,'轻松跑 · 模拟示例'));runs+=1
        else:sessions.append(item(day,'recovery',0,'休息 / 恢复'))
    return {'summary':'跑步与恢复共同排期；这是模拟草稿，确认前不会生效。','assessment':f"已有 {len(context['workouts'])} 次跑步记录。目标与现有能力分开；未做达标预测。",'questions':questions,'uncertainties':['当前为模拟服务，不是 DeepSeek 推理结果。','训练规则未经人工专业审阅；目标成绩不是当前能力。'],'evidence_ids':[r['id'] for r in (context['workouts'][:10]+context['strength'][:10]+context['checkins'][:3])],'sessions':sessions}

def validate_plan(payload,context,start):
    data=PlanData.model_validate(payload)
    if context.get('running_only') and any(s.type=='strength' for s in data.sessions):raise ValueError('当前仅提供跑步与恢复课程，不接受力量安排')
    reasons=constraints(context);p=context['profile'];slots={d['date']:d for d in context['days']};totals={};types={};active=[]
    if len({s.id for s in data.sessions})!=len(data.sessions):raise ValueError('训练项 ID 重复')
    if {s.date for s in data.sessions}!={start+timedelta(days=i) for i in range(7)}:raise ValueError('必须覆盖所选七天，包括休息安排')
    valid_ids={r['id'] for r in context['workouts']+context['strength']+context['checkins']}
    if any(i not in valid_ids for i in data.evidence_ids):raise ValueError('证据引用不属于已保存事实')
    for s in data.sessions:
        day=s.date.isoformat();totals[day]=totals.get(day,0)+s.duration_minutes;types.setdefault(day,set()).add(s.type)
        if s.type=='recovery':continue
        active.append(s)
        if p.goal_date and s.date==p.goal_date:raise ValueError('比赛日期须单独核对，不能自动安排一般跑步或力量')
        if reasons:raise ValueError('身体状态需评估时，不接受带训练负荷的草稿')
        slot=slots.get(day)
        if not slot or not slot['available']:raise ValueError('训练没有可用时间窗口')
        if s.type=='run' and s.duration_minutes>25:raise ValueError('当前一般训练草稿不支持自动升级跑步时长')
        if s.type=='strength':
            if not p.strength_max_minutes or p.strength_experience=='unknown':raise ValueError('力量档案不完整')
            if s.duration_minutes>p.strength_max_minutes:raise ValueError('力量课超过单次时长上限')
            estimate=5*60
            for e in s.exercises:
                definition=BY_ID.get(e.exercise_id)
                if not definition or e.exercise_id not in p.familiar_exercises or definition['equipment'] not in p.strength_equipment:raise ValueError('动作或器械不在已确认范围')
                if tolerated_soreness(context) and definition['region']=='lower':raise ValueError('腿部酸胀减量时不追加下肢力量')
                if definition['equipment']=='resistance_band' and (not p.band_resistance.strip() or e.resistance!=p.band_resistance):raise ValueError('弹力带只能使用用户已确认的阻力描述，不推测或升级')
                if e.weight_kg is not None:raise ValueError('当前动作库不能推测负重')
                estimate+=e.sets*e.repetitions*6+(e.sets-1)*e.rest_seconds+60
            if any(e.sets>2 or e.repetitions>5 for e in s.exercises):raise ValueError('当前未审阅的一般力量草稿不支持自动升级组次')
            if s.duration_minutes*60<estimate:raise ValueError('力量课时长未包含热身、休息与切换')
    for day,total in totals.items():
        if total>slots.get(day,{}).get('duration_minutes',0):raise ValueError('同日总训练时间超过可用时间')
        if 'run' in types[day] and 'strength' in types[day] and not p.same_day_strength:raise ValueError('未同意同日安排跑步与力量')
        if 'recovery' in types[day] and len(types[day])>1:raise ValueError('休息日不能同时安排训练')
    if sum(s.type=='run' for s in active)>p.runs_per_week:raise ValueError('跑步次数超过档案约束')
    if reduced(context):
        if any(s.type=='run' and s.duration_minutes>15 or s.type=='strength' and (s.duration_minutes>10 or any(e.sets>1 for e in s.exercises)) for s in active):raise ValueError('用户请求减量时不接受正常负荷示例')
    validate_run_spacing(data.sessions,context,start)
    validate_run_week_counts(data.sessions,context,start)
    return data.model_dump(mode='json')

def diffs(old,new,context):
    a=old.payload['sessions'] if old else []
    days=sorted({s['date'] for s in a+new['sessions']})
    reason='；'.join(constraints(context)) or ('用户希望减量，缩短跑步并保留恢复。' if context['latest'] and context['latest'].get('training_readiness')=='reduce' else '结合已保存的跑步、身体反馈与可用日程重新排期。')
    return [{'date':d,'before':[s for s in a if s['date']==d],'after':[s for s in new['sessions'] if s['date']==d],'reason':reason+' 未完成训练不追补。'} for d in days if [{k:v for k,v in s.items() if k!='id'} for s in a if s['date']==d]!=[{k:v for k,v in s.items() if k!='id'} for s in new['sessions'] if s['date']==d]]

def install_routes(app,cfg,current,db,bump_facts):
    from .coach_provider import DeepSeekPlanner, messages_for
    from .recognition import reserve, settle, VisionFailure
    app.state.planner=DeepSeekPlanner(cfg)
    @app.get('/api/v1/plan-evidence/{id}')
    def evidence(id:str,user=Depends(current),session=Depends(db)):
        for model,kind in ((Workout,'run'),(StrengthWorkout,'strength'),(Checkin,'checkin')):
            row=session.get(model,id)
            if row and row.user_id==user.id:return {'id':row.id,'type':kind,**row.payload}
        raise HTTPException(404,'找不到该已保存事实')

    @app.get('/api/v1/exercises')
    def exercises(user=Depends(current)):return []

    @app.get('/api/v1/strength-workouts')
    def strength(user=Depends(current),session=Depends(db)):
        return [] # Legacy data remains in export/backup, not active training features.
    @app.post('/api/v1/strength-workouts',status_code=201)
    def add_strength(data:StrengthInput,user=Depends(current),session=Depends(db)):
        raise HTTPException(410,'力量训练功能已移除，当前只提供跑步课程')
    def plan_json(row):return {'id':row.id,'version':row.version,'parent_version':row.parent_version,'facts_revision':row.facts_revision,'created_at':row.created_at.isoformat(),**row.payload}

    def proposal_json(row,user,session):
        from .weather_plan import stale
        state=row.state
        if state=='pending' and any(s.get('type')=='strength' for s in row.payload.get('sessions',[])):state='expired'
        if state=='pending' and (row.base_version!=user.plan_version or row.facts_revision!=user.facts_revision or row.expires_at.replace(tzinfo=timezone.utc)<=datetime.now(timezone.utc) or stale(session,user,row.payload)):state='expired'
        return {'id':row.id,'state':state,'base_version':row.base_version,'facts_revision':row.facts_revision,'expires_at':row.expires_at.replace(tzinfo=timezone.utc).isoformat(),**row.payload}

    def own_proposal(id,user,session):
        row=session.get(PlanProposal,id)
        if not row or row.user_id!=user.id:raise HTTPException(404,'找不到该计划提案')
        return row

    @app.get('/api/v1/plans/current')
    def get_plan(user=Depends(current),session=Depends(db)):
        from .weather_plan import stale
        context=gather(session,user);old=context['old']
        return {'plan':plan_json(old) if old else None,'facts_revision':user.facts_revision,'plan_version':user.plan_version,'needs_review':bool(old and (old.facts_revision!=user.facts_revision or stale(session,user,old.payload) or any(s.get('type')=='strength' for s in old.payload.get('sessions',[])))),'attention':constraints(context)+(['历史计划包含已停用的力量课程，请重新生成跑步计划并确认。'] if old and any(s.get('type')=='strength' for s in old.payload.get('sessions',[])) else []),'proposals':[proposal_json(r,user,session) for r in session.scalars(select(PlanProposal).where(PlanProposal.user_id==user.id).order_by(PlanProposal.created_at.desc()).limit(10))]}

    @app.get('/api/v1/plans/versions')
    def versions(user=Depends(current),session=Depends(db)):
        return [plan_json(r) for r in session.scalars(select(PlanVersion).where(PlanVersion.user_id==user.id).order_by(PlanVersion.version.desc()))]

    def save_proposal(payload,start,context,user,session,source='mock'):
        try:clean=validate_plan(payload,context,start)
        except (ValidationError,ValueError) as e:raise HTTPException(422,'计划未通过约束检查：'+str(e)) from None
        from .plan_comparison import compare_plans
        row=PlanProposal(user_id=user.id,base_version=user.plan_version,facts_revision=user.facts_revision,expires_at=datetime.now(timezone.utc)+timedelta(hours=24),payload={**clean,'is_mock':source=='mock','provider':source,'prompt_version':'runner-coach-4-running','start_date':start.isoformat(),'changes':diffs(context['old'],clean,context),'comparison':compare_plans(context['old'],clean,start,context['days']),'constraints_checked':['整体身体状态','七天范围','跑步间隔','当天可用时间','跑步与恢复类型','证据所有权'],'professional_review':'not_clinically_reviewed'})
        session.add(row);session.commit();return proposal_json(row,user,session)

    @app.post('/api/v1/plan-proposals',status_code=201)
    def generate(data:Generate,user=Depends(current),session=Depends(db)):
        context=gather(session,user);today=local_today(context['profile'])
        if not today<=data.start_date<=today+timedelta(days=30):raise HTTPException(422,'计划开始日应在今天至未来 30 天内')
        app.state.coach_role=PROMPT_PATH.read_text()
        if cfg.ai_provider=='mock':return save_proposal(mock_plan(context,data.start_date),data.start_date,context,user,session)
        if not data.allow_external_ai:raise HTTPException(422,'请先确认将已保存的档案、身体反馈、近期训练与日程发送至 DeepSeek 生成草稿')
        try:messages,bound,allowed_ids=messages_for(context,data.start_date,app.state.coach_role,PlanData.model_json_schema())
        except ValueError as exc:raise HTTPException(422,str(exc)) from None
        call_id=reserve(session,cfg,user.id,None,task_kind='coach_plan',input_bound=bound,output_bound=cfg.ai_coach_max_output_tokens)
        try:
            content,usage,finish=app.state.planner.read(messages)
            settle(session,cfg,call_id,usage)
            if finish!='stop':raise VisionFailure('教练输出被截断，未创建提案')
            candidate=PlanData.model_validate_json(content).model_dump(mode='json')
            if any(i not in allowed_ids for i in candidate['evidence_ids']):raise VisionFailure('教练引用了没有提供的证据，未创建提案')
        except VisionFailure as exc:raise HTTPException(502,exc.message) from None
        except (ValidationError,ValueError,TypeError):raise HTTPException(502,'教练结果没有通过 JSON 结构校验，未创建提案') from None
        return save_proposal(candidate,data.start_date,context,user,session,source='deepseek')

    @app.post('/api/v1/plan-proposals/{id}/approve')
    def approve(id:str,data:Approval,user=Depends(current),session=Depends(db)):
        row=own_proposal(id,user,session)
        if row.state=='applied':
            result=session.scalar(select(PlanVersion).where(PlanVersion.user_id==user.id,PlanVersion.version==row.applied_version));return plan_json(result)
        if proposal_json(row,user,session)['state']!='pending' or data.expected_plan_version!=row.base_version or data.expected_facts_revision!=row.facts_revision:raise HTTPException(409,'提案已失效或版本不匹配，请重新生成')
        from .weather_plan import claim
        claim(session,user,row.payload)
        context=gather(session,user)
        if row.payload.get('weather_snapshot'):context['weather_snapshot']=row.payload['weather_snapshot']
        clean={k:row.payload[k] for k in PlanData.model_fields}
        from .cycle_planning import validate_cycle
        try:
            if row.payload.get('scope')=='cycle':validate_cycle(clean,{**context,'allow_controlled_work':row.payload.get('allow_controlled_work',False)},date.fromisoformat(row.payload['start_date']))
            else:validate_plan(clean,context,date.fromisoformat(row.payload['start_date']))
        except (ValueError,ValidationError):raise HTTPException(409,'当前限制与草稿不一致，请重新生成') from None
        version=row.base_version+1
        result=session.execute(update(User).where(User.id==user.id,User.plan_version==row.base_version,User.facts_revision==row.facts_revision).values(plan_version=version))
        if not result.rowcount:session.rollback();raise HTTPException(409,'事实或正式计划已经变化')
        claimed=session.execute(update(PlanProposal).where(PlanProposal.id==id,PlanProposal.state=='pending').values(state='applied',applied_version=version))
        if not claimed.rowcount:session.rollback();raise HTTPException(409,'提案已经处理')
        payload=copy.deepcopy(row.payload);payload.pop('changes',None)
        plan=PlanVersion(user_id=user.id,version=version,parent_version=row.base_version,facts_revision=row.facts_revision,payload=payload)
        session.add(plan)
        from .models import NotificationJob
        session.execute(update(NotificationJob).where(NotificationJob.user_id==user.id,NotificationJob.state=='pending').values(state='cancelled',reason='正式计划已变化'))
        session.execute(update(PlanOutbox).where(PlanOutbox.user_id==user.id,PlanOutbox.state=='pending').values(state='cancelled'))
        session.add(PlanOutbox(user_id=user.id,plan_version=version,state='pending'))
        try:session.commit()
        except IntegrityError:session.rollback();raise HTTPException(409,'计划已被其他请求更新，请刷新') from None
        return plan_json(plan)

    @app.post('/api/v1/plan-proposals/{id}/reject')
    def reject(id:str,user=Depends(current),session=Depends(db)):
        row=own_proposal(id,user,session)
        if row.state=='rejected':return proposal_json(row,user,session)
        result=session.execute(update(PlanProposal).where(PlanProposal.id==id,PlanProposal.state=='pending').values(state='rejected'))
        if not result.rowcount:session.rollback();raise HTTPException(409,'提案已处理')
        session.commit();session.refresh(row);return proposal_json(row,user,session)

    @app.post('/api/v1/plans/restore-proposal',status_code=201)
    def restore(data:Restore,user=Depends(current),session=Depends(db)):
        if data.expected_plan_version!=user.plan_version or data.expected_facts_revision!=user.facts_revision:raise HTTPException(409,'版本已变化')
        context=gather(session,user)
        if data.source_version==0:
            payload=mock_plan(context,data.start_date)
            payload['sessions']=[{**s,'type':'recovery','duration_minutes':0,'intensity':'rest','exercises':[],'title':'撤回训练安排 / 休息'} for s in payload['sessions']]
            payload['summary']='撤回训练安排的草稿；确认后生成新版本，保留所有历史事实。'
        else:
            old=session.scalar(select(PlanVersion).where(PlanVersion.user_id==user.id,PlanVersion.version==data.source_version))
            if not old:raise HTTPException(404,'找不到历史版本')
            if old.payload.get('scope')=='cycle':raise HTTPException(422,'完整周期请依据当前事实重新生成剩余周期，不平移旧日期或恢复过期负荷')
            payload={k:copy.deepcopy(old.payload[k]) for k in PlanData.model_fields}
            if date.fromisoformat(old.payload['start_date'])!=data.start_date:raise HTTPException(422,'恢复历史版本需保持原日期；重新排期请生成新草稿')
            payload['summary']='恢复历史安排的提案；会重新核对当前事实与限制。'
        return save_proposal(payload,data.start_date,context,user,session)
