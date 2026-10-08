"""Bounded, source-transparent full-cycle drafts with deterministic shared-time allocation."""
import copy
import json
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4
from typing import Literal, Annotated
from fastapi import Depends, HTTPException
from pydantic import Field, model_validator, ValidationError
from sqlalchemy import select
from .schemas import StrictModel, Day, Availability
from .planning import PlanItem, Prescription, gather, local_today, constraints,available_exercises
from .models import PlanProposal, User
from .ability import assess, RULE_VERSION
from .exercises import EXERCISES, BY_ID

class CycleItem(PlanItem):
    intensity: Literal['easy','moderate','rest']
    @model_validator(mode='after')
    def type_fields(self):
        if self.type=='recovery':
            if self.duration_minutes or self.exercises or self.intensity!='rest':raise ValueError('恢复项不能包含负荷')
        elif self.duration_minutes<1 or self.intensity=='rest':raise ValueError('训练项类型与负荷不一致')
        if self.type=='strength' and (not self.exercises or self.intensity!='easy'):raise ValueError('力量需已熟悉的基础动作')
        if self.type!='strength' and self.exercises:raise ValueError('非力量课不能包含力量动作')
        if self.intensity=='moderate' and (self.type!='run' or self.run_kind!='controlled'):raise ValueError('中等用力仅用于已核对受控跑')
        return self
    run_kind: Literal['easy','long','controlled','none']='none'
    blocks: list[dict]=Field(default_factory=list,max_length=3)

class CycleData(StrictModel):
    summary: str=Field(min_length=1,max_length=2000)
    assessment: str=Field(min_length=1,max_length=2000)
    questions: list[str]=Field(default_factory=list,max_length=20)
    uncertainties: list[str]=Field(default_factory=list,max_length=20)
    evidence_ids: list[str]=Field(default_factory=list,max_length=2000)
    sessions: list[CycleItem]=Field(max_length=400)

class CycleGenerate(StrictModel):
    start_date: date
    allow_external_ai: bool=False
    include_weather: bool=False
    avoid_rain: bool=False
    expected_weather_revision: int|None=Field(default=None,ge=0)
    allow_controlled_work: bool=False
    expected_facts_revision: int=Field(ge=1)
    expected_plan_version: int=Field(ge=0)

class WeeklySlot(StrictModel):
    weekday: int=Field(ge=0,le=6)
    duration_minutes: int=Field(ge=10,le=480)

class CycleSchedule(StrictModel):
    start_date: date
    slots: list[WeeklySlot]=Field(max_length=7)
    preserve_existing: bool=True
    expected_facts_revision: int=Field(ge=1)
    @model_validator(mode='after')
    def unique(self):
        if len({s.weekday for s in self.slots})!=len(self.slots):raise ValueError('星期不能重复')
        return self

class WeekChoice(StrictModel):
    index: int=Field(ge=1,le=30)
    run_minutes: list[Annotated[int,Field(ge=10,le=132)]]=Field(max_length=7)
    controlled_count: int=Field(default=0,ge=0,le=1)
    strength_count: int=Field(ge=0,le=2)
    exercise_ids: list[str]=Field(max_length=4)
    sets: int=Field(ge=1,le=2)
    note: str=Field(max_length=160)
    @model_validator(mode='after')
    def positive(self):
        if any(n<10 or n>132 for n in self.run_minutes):raise ValueError('单次跑步时间超出支持范围')
        if len(set(self.exercise_ids))!=len(self.exercise_ids):raise ValueError('动作不能重复')
        return self

class Blueprint(StrictModel):
    summary: str=Field(min_length=1,max_length=300)
    weeks: list[WeekChoice]=Field(max_length=30)
    evidence_ids: list[str]=Field(default_factory=list,max_length=100)
    questions: list[str]=Field(default_factory=list,max_length=8)


def horizon(profile,start):
    today=local_today(profile)
    if not profile.goal_date or not today<=start<=profile.goal_date or (profile.goal_date-start).days>180:
        raise ValueError('周期开始日须在今天与比赛日之间，且比赛须在开始日后180天内')
    return profile.goal_date+timedelta(days=7)


def week_envelopes(context,start):
    p=context['profile'];end=horizon(p,start);ability=assess(context,local_today(p));env=ability['envelope'];race=p.goal_date
    known=available_exercises(context)
    weeks=[];cursor=start;index=1
    while cursor<=end:
        finish=min(cursor+timedelta(days=6-cursor.weekday()),end)
        days_to_race=(race-cursor).days
        phase='post_race' if cursor>race else 'race_week' if cursor>=race-timedelta(days=race.weekday()) else 'taper' if days_to_race<=14 else 'foundation' if index<=2 else 'consolidate' if index%4==0 else 'build'
        # Numeric ratios are product ceilings, not copied source prescriptions.
        factor={'foundation':.8,'build':1.,'consolidate':.8,'taper':.65,'race_week':.35,'post_race':0}[phase]
        cap=int(env['week_run_minutes_cap']*factor*(finish-cursor+timedelta(days=1)).days/7)
        strength_cap=0 if ability['blocked_reasons'] or phase=='post_race' or not known or not p.strength_max_minutes or p.strength_experience=='unknown' else 1 if phase in ('race_week','taper') or ability['reduce'] else 2
        weeks.append({'index':index,'start':cursor.isoformat(),'end':finish.isoformat(),'phase':phase,
                      'run_minutes_cap':cap,'single_run_minutes_cap':env['single_run_minutes_cap'],
                      'run_count_cap':p.runs_per_week,'controlled_count_cap':1 if env['controlled_eligible'] and context.get('allow_controlled_work') and phase=='build' else 0,'strength_count_cap':strength_cap,
                      'sets_cap':1 if phase in ('race_week','taper') or ability['reduce'] else 2,
                      'allowed_exercise_ids':[e['id'] for e in known]})
        cursor=finish+timedelta(days=1);index+=1
    return ability,weeks


def local_blueprint(context,start):
    ability,weeks=week_envelopes(context,start);result=[]
    for w in weeks:
        cap=w['run_minutes_cap'];n=min(w['run_count_cap'],cap//10)
        # Longer weekend run receives leftover minutes within the observed single-run ceiling.
        durations=[min(w['single_run_minutes_cap'],cap//max(1,n))]*n
        if w['controlled_count_cap'] and n>=3 and cap//n>=20:durations=[min(w['single_run_minutes_cap'],cap//n)]*n
        elif n>=2:
            short=min(durations[0],max(10,int(durations[0]*.8)))
            durations=[short]*(n-1)+[min(w['single_run_minutes_cap'],cap-short*(n-1))]
        result.append({'index':w['index'],'run_minutes':[n for n in durations if n>=10],
                       'strength_count':w['strength_count_cap'],'exercise_ids':w['allowed_exercise_ids'][:3],
                       'sets':w['sets_cap'],'controlled_count':w['controlled_count_cap'] if len(durations)>=3 and min(durations)>=20 else 0,'note':'本地保守规则草稿；实际可用日程不足时删减，不追补。'})
    return {'summary':'完整日期范围的跑步与恢复草稿；每次训练前仍需核对身体状态。',
            'weeks':result,'evidence_ids':ability['evidence_ids'][:100],'questions':ability['blocked_reasons'][:8]}


def validate_blueprint(value,context,start,allowed_ids=None):
    data=Blueprint.model_validate(value);ability,envelopes=week_envelopes(context,start)
    if [w.index for w in data.weeks]!=[w['index'] for w in envelopes]:raise ValueError('周期周次缺失、重复或顺序错误')
    valid_ids=set(ability['evidence_ids']) if allowed_ids is None else allowed_ids
    if any(i not in valid_ids for i in data.evidence_ids):raise ValueError('周期证据未提供或不属于保存事实')
    for choice,env in zip(data.weeks,envelopes):
        if sum(choice.run_minutes)>env['run_minutes_cap'] or len(choice.run_minutes)>env['run_count_cap'] or any(n>env['single_run_minutes_cap'] for n in choice.run_minutes):raise ValueError('跑步超出事实评估的周期上限')
        if choice.controlled_count>env['controlled_count_cap'] or (choice.controlled_count and (len(choice.run_minutes)<3 or min(choice.run_minutes)<20)):raise ValueError('受控跑缺少用户选择、连续依据或满足时长的三次跑步安排')
        if choice.strength_count>env['strength_count_cap'] or choice.sets>env['sets_cap']:raise ValueError('力量超出阶段或身体限制')
        if any(e not in env['allowed_exercise_ids'] for e in choice.exercise_ids):raise ValueError('动作不是已确认可用动作')
        if choice.strength_count and not choice.exercise_ids:raise ValueError('力量安排缺少已熟悉动作')
    return data.model_dump(mode='json'),ability,envelopes


def expand(blueprint,context,start):
    clean,ability,weeks=validate_blueprint(blueprint,context,start)
    p=context['profile'];slots={d['date']:d for d in context['days']};sessions=[];notes=[]
    stop='疼痛、疾病或异常不适时停止并更新反馈；身体状态变化后重新评估跑步与恢复，不追补漏练。'
    def item(day,kind,minutes,title,exercises=None,run_kind='none'):
        blocks=[]
        if kind=='run':blocks=[{'label':'逐渐进入轻松状态','minutes':5},{'label':'可完整说话的轻松跑；不以目标配速追速','minutes':minutes-10},{'label':'逐渐放缓','minutes':5}]
        if run_kind=='controlled':blocks[1]={'label':'受控稳态：自述用力5/10、能完整说话，不追目标配速；不适立即改轻松或停止','minutes':minutes-10}
        return {'id':str(uuid4()),'date':day,'type':kind,'title':title,'duration_minutes':minutes,'purpose':'以已保存训练基础与共同日程为依据；未来安排需持续复核。','intensity':'rest' if kind=='recovery' else 'moderate' if run_kind=='controlled' else 'easy','exercises':exercises or [],'stop_condition':stop,'run_kind':run_kind,'blocks':blocks}
    from .planning import prior_run_dates
    from .weather_plan import blocked
    weather_blocked=blocked(context)
    prior_runs=prior_run_dates(context,start)
    for choice,week in zip(clean['weeks'],weeks):
        dates=[(date.fromisoformat(week['start'])+timedelta(days=i)).isoformat() for i in range((date.fromisoformat(week['end'])-date.fromisoformat(week['start'])).days+1)]
        available=[d for d in dates if slots.get(d,{}).get('available') and d!=p.goal_date.isoformat() and d<=p.goal_date.isoformat()]
        used={};runs=[];strengths=[]
        # Put the longest requested easy run in an available weekend slot; avoid adjacent run dates.
        candidates=sorted(available,key=lambda d:(date.fromisoformat(d).weekday()>=5,slots[d]['duration_minutes'],d),reverse=True)
        monday=date.fromisoformat(week['start']);monday-=timedelta(days=monday.weekday())
        count_before=sum(monday<=d<monday+timedelta(days=7) for d in prior_runs)
        for number,minutes in enumerate(sorted(choice['run_minutes'],reverse=True)[:max(0,p.runs_per_week-count_before)]):
            possible=[d for d in candidates if d not in used and d not in weather_blocked and all(abs((date.fromisoformat(d)-previous).days)>1 for previous in prior_runs) and all(abs((date.fromisoformat(d)-date.fromisoformat(s['date'])).days)>1 for s in sessions if s['type']=='run')]
            if not possible:notes.append(f"{week['start']}：跑步日程不足，删减而不补债");continue
            day=possible[0];total=min(minutes,slots[day]['duration_minutes'])
            if total<10:continue
            sessions.append(item(day,'run',total,'较长轻松跑' if number==0 and len(choice['run_minutes'])>=2 else '轻松跑',run_kind='long' if number==0 and len(choice['run_minutes'])>=2 else 'easy'));used[day]=total;runs.append(day)
        if choice['controlled_count'] and len(runs)>=3:
            short_runs=[s for s in sessions if s['date'] in runs and s['run_kind']=='easy' and s['duration_minutes']>=20]
            if short_runs:
                target=min(short_runs,key=lambda s:s['date']);target['run_kind']='controlled';target['title']='受控稳态跑';target['intensity']='moderate';target['blocks'][1]={'label':'受控稳态：自述用力5/10、能完整说话，不追目标配速；不适立即改轻松或停止','minutes':target['duration_minutes']-10}
        # Shared minutes count both disciplines. Lower-body work is not forced before the long run.
        protected_days=[s['date'] for s in sessions if s['date'] in dates and s['run_kind'] in ('long','controlled')]
        long_day=next((s['date'] for s in sessions if s['date'] in dates and s['run_kind']=='long'),None)
        for _ in range(choice['strength_count']):
            possible=[d for d in available if d not in strengths and (d not in used or p.same_day_strength)
                      and all(date.fromisoformat(d)!=date.fromisoformat(protected)-timedelta(days=1) for protected in protected_days)
                      and all(abs((date.fromisoformat(d)-date.fromisoformat(s['date'])).days)>1 for s in sessions if s['type']=='strength')]
            possible.sort(key=lambda d:(d in used,d))
            done=False
            for day in possible:
                remaining=min(p.strength_max_minutes or 0,slots[day]['duration_minutes']-used.get(day,0))
                exercises=[]
                for exercise_id in choice['exercise_ids']:
                    e={'exercise_id':exercise_id,'sets':choice['sets'],'repetitions':5,'rest_seconds':60,'resistance':p.band_resistance if BY_ID[exercise_id]['equipment']=='resistance_band' else '自身体重，保持可控动作','weight_kg':None}
                    cost=300+sum(x['sets']*x['repetitions']*6+(x['sets']-1)*x['rest_seconds']+60 for x in exercises+[e])
                    if cost<=remaining*60:exercises.append(e)
                if not exercises:continue
                minutes=(300+sum(x['sets']*x['repetitions']*6+(x['sets']-1)*x['rest_seconds']+60 for x in exercises)+59)//60
                sessions.append(item(day,'strength',minutes,'家庭基础力量',exercises));used[day]=used.get(day,0)+minutes;strengths.append(day);done=True;break
            if not done:notes.append(f"{week['start']}：力量共享时间不足，删减而不压缩休息")
        for d in dates:
            if d not in used:
                title='比赛安排 · 单独核对参赛与身体状态' if d==p.goal_date.isoformat() else '赛后恢复 · 记录实际参赛与身体感受' if d>p.goal_date.isoformat() else '休息 / 等待评估' if ability['blocked_reasons'] else '休息 / 无可用训练窗口'
                sessions.append(item(d,'recovery',0,title))
    notes.extend(f'{d}：{reason}；删减或另选可用日，不补债。' for d,reason in weather_blocked.items())
    if context.get('weather_snapshot'):notes.extend(context['weather_snapshot']['limitations'])
    sessions.sort(key=lambda s:(s['date'],s['type']))
    return {'summary':clean['summary'],'assessment':f"公路记录 {ability['road_records']} 条，{'已有连续训练依据' if ability['status']=='established' else '资料不足' if ability['status']=='insufficient' else '只有初步依据'}；目标可行性仍需比赛专项依据。",
            'questions':clean['questions'],'uncertainties':(notes+ability['limitations'])[:20],'evidence_ids':clean['evidence_ids'],'sessions':sessions},ability,weeks


def validate_cycle(payload,context,start):
    data=CycleData.model_validate(payload)
    if context.get('running_only') and any(s.type=='strength' for s in data.sessions):raise ValueError('当前仅提供跑步与恢复课程')
    from .weather_plan import validate as validate_weather
    validate_weather(data.sessions,context)
    ability,weeks=week_envelopes(context,start);p=context['profile']
    days={d['date']:d for d in context['days']};end=horizon(p,start);expected={(start+timedelta(days=i)).isoformat() for i in range((end-start).days+1)}
    if {s.date.isoformat() for s in data.sessions}!=expected:raise ValueError('周期必须覆盖开始日至比赛后七天，不能少日期')
    if len({s.id for s in data.sessions})!=len(data.sessions):raise ValueError('项目ID重复')
    if any(s.type=='recovery' and (s.blocks or s.run_kind!='none') for s in data.sessions):raise ValueError('恢复待核对项不能夹带跑步分段')
    from .planning import validate_run_spacing,validate_run_week_counts
    validate_run_spacing(data.sessions,context,start)
    validate_run_week_counts(data.sessions,context,start)
    all_runs=[s for s in data.sessions if s.type=='run']
    if any(abs((a.date-b.date).days)<=1 for i,a in enumerate(all_runs) for b in all_runs[i+1:]):raise ValueError('相邻周次也不能连续两天挤入跑步')
    all_strength=[s for s in data.sessions if s.type=='strength']
    if any(abs((a.date-b.date).days)<=1 for i,a in enumerate(all_strength) for b in all_strength[i+1:]):raise ValueError('当前周期不连续两天追加力量')
    if any(s.date==r.date-timedelta(days=1) for s in all_strength for r in all_runs if r.run_kind in ('long','controlled')):raise ValueError('较长或受控跑前一天不能自动追加力量')
    valid={r['id'] for key in ('workouts','strength','checkins') for r in context[key]}
    if any(i not in valid for i in data.evidence_ids):raise ValueError('证据不属于保存事实')
    for week in weeks:
        active=[s for s in data.sessions if week['start']<=s.date.isoformat()<=week['end'] and s.type!='recovery']
        runs=[s for s in active if s.type=='run'];strength=[s for s in active if s.type=='strength']
        if sum(s.duration_minutes for s in runs)>week['run_minutes_cap'] or len(runs)>p.runs_per_week:raise ValueError('周期跑步总量超出评估')
        if sum(s.run_kind=='controlled' for s in runs)>week['controlled_count_cap']:raise ValueError('受控跑超出当前身体、依据或用户选择')
        if any(s.run_kind=='controlled' for s in runs) and len(runs)<3:raise ValueError('日程不足三次跑步时不挤入受控跑')
        if len(strength)>week['strength_count_cap']:raise ValueError('力量次数超出阶段约束')
        for s in active:
            day=s.date.isoformat();slot=days.get(day)
            if ability['blocked_reasons'] or s.date>=p.goal_date:raise ValueError('当前身体限制、比赛日或赛后不能自动生成训练负荷')
            if not slot or not slot['available']:raise ValueError('缺少已保存的可用日程')
            if s.type=='run':
                if s.run_kind not in ('easy','long','controlled') or not 10<=s.duration_minutes<=week['single_run_minutes_cap']:raise ValueError('跑步类型或单次时间超出评估')
                middle='受控稳态：自述用力5/10、能完整说话，不追目标配速；不适立即改轻松或停止' if s.run_kind=='controlled' else '可完整说话的轻松跑；不以目标配速追速'
                if s.run_kind=='controlled' and (s.duration_minutes<20 or s.intensity!='moderate'):raise ValueError('受控跑需满足时长及用力约束')
                if s.run_kind!='controlled' and s.intensity!='easy':raise ValueError('轻松跑不能提高强度')
                if s.blocks!=[{'label':'逐渐进入轻松状态','minutes':5},{'label':middle,'minutes':s.duration_minutes-10},{'label':'逐渐放缓','minutes':5}]:raise ValueError('跑步分段不符合总时长与强度约束')
            else:
                if s.run_kind!='none' or s.blocks or s.duration_minutes>(p.strength_max_minutes or 0):raise ValueError('力量卡类型或时长不符合档案')
                if len({e.exercise_id for e in s.exercises})!=len(s.exercises):raise ValueError('力量动作重复')
                for e in s.exercises:
                    if e.exercise_id not in week['allowed_exercise_ids'] or e.sets>week['sets_cap'] or e.repetitions>5 or e.rest_seconds!=60 or e.weight_kg is not None:raise ValueError('动作、组次或阻力规则不符合周期约束')
                    literal=p.band_resistance if BY_ID[e.exercise_id]['equipment']=='resistance_band' else '自身体重，保持可控动作'
                    if e.resistance!=literal:raise ValueError('不能改写已确认阻力')
                minimum=300+sum(e.sets*e.repetitions*6+(e.sets-1)*e.rest_seconds+60 for e in s.exercises)
                if s.duration_minutes*60<minimum:raise ValueError('力量时长未含热身、休息和切换')
        for i,a in enumerate(runs):
            if any(abs((a.date-b.date).days)<=1 for b in runs[i+1:]):raise ValueError('当前周期规则不把跑步挤到连续两天')
        for long in [s for s in runs if s.run_kind=='long']:
            if any(s.date==long.date-timedelta(days=1) for s in strength):raise ValueError('较长跑前一天不自动追加力量')
    for day in expected:
        rows=[s for s in data.sessions if s.date.isoformat()==day];types={s.type for s in rows}
        if len(rows)>2 or ('recovery' in types and len(rows)>1) or len(types)!=len(rows):raise ValueError('同日重复或恢复与训练冲突')
        if {'run','strength'}<=types and not p.same_day_strength:raise ValueError('未同意同日跑步和力量')
        if sum(s.duration_minutes for s in rows)>days.get(day,{}).get('duration_minutes',0):raise ValueError('两类训练总时间超过日程')
    return data.model_dump(mode='json')


def install_routes(app,cfg,current,db,bump_facts):
    from .coach_provider import DeepSeekPlanner,messages_for
    from .recognition import reserve,settle,VisionFailure
    from .planning import diffs
    from .plan_comparison import summarize_sessions
    @app.put('/api/v1/cycle-schedule')
    def save_schedule(data:CycleSchedule,user=Depends(current),session=Depends(db)):
        if data.expected_facts_revision!=user.facts_revision:raise HTTPException(409,'资料版本已变化，请刷新再保存日程')
        context=gather(session,user)
        try:end=horizon(context['profile'],data.start_date)
        except ValueError as e:raise HTTPException(422,str(e)) from None
        slots={s.weekday:s.duration_minutes for s in data.slots};existing={d['date']:d for d in user.availability}
        new=[]
        for i in range((end-data.start_date).days+1):
            day=data.start_date+timedelta(days=i);key=day.isoformat()
            new.append(existing[key] if data.preserve_existing and key in existing else {'date':key,'available':day.weekday() in slots,'duration_minutes':slots.get(day.weekday(),30)})
        # Keep exceptions outside this cycle as well, within the existing 366-date API limit.
        merged={**existing,**{d['date']:d for d in new}}
        if len(merged)>366:raise HTTPException(422,'保存后日程超过366天，请先整理较早日程')
        user.availability=Availability.model_validate({'days':list(merged.values())}).model_dump(mode='json')['days']
        session.add(user);bump_facts(user,session);session.commit()
        return {'days':user.availability}

    @app.post('/api/v1/cycle-proposals',status_code=201)
    def generate_cycle(data:CycleGenerate,user=Depends(current),session=Depends(db)):
        if data.expected_facts_revision!=user.facts_revision or data.expected_plan_version!=user.plan_version:raise HTTPException(409,'事实或计划版本已变化，请刷新')
        context=gather(session,user);context['allow_controlled_work']=data.allow_controlled_work
        from .weather_plan import snapshot,stale
        if data.avoid_rain and not data.include_weather:raise HTTPException(422,'避雨须同时选择天气联动')
        if data.include_weather:
            if cfg.weather_provider!='open_meteo':raise HTTPException(503,'天气服务尚未启用')
            context['weather_snapshot']=snapshot(session,user,context['profile'],data.start_date,data.expected_weather_revision,data.avoid_rain)
        from .planning import PROMPT_PATH
        app.state.coach_role=PROMPT_PATH.read_text()
        try:ability,envelopes=week_envelopes(context,data.start_date)
        except ValueError as e:raise HTTPException(422,str(e)) from None
        source='local_rules';blueprint=local_blueprint(context,data.start_date)
        if cfg.ai_provider=='deepseek':
            if not data.allow_external_ai:raise HTTPException(422,'请同意发送已保存资料与周期范围日程至DeepSeek')
            # This consent explicitly covers cycle availability; history selection remains 10/10/3.
            messages,bound,allowed=messages_for(context,data.start_date,app.state.coach_role,Blueprint.model_json_schema())
            facts=json.loads(messages[1]['content'].split('\n',1)[1]);facts.pop('record_summary',None);facts.pop('server_policy',None)
            selected_context={**context,**{k:facts.get(k,[]) for k in ('workouts','strength','checkins')}}
            facts['ability']=assess(selected_context,local_today(context['profile']))
            facts['availability']=[d for d in context['days'] if data.start_date.isoformat()<=d['date']<=horizon(context['profile'],data.start_date).isoformat()]
            # Full-history numeric caps stay on the server. The model gets stricter selected-history caps.
            _,selected_envs=week_envelopes(selected_context,data.start_date)
            for full,small in zip(envelopes,selected_envs):
                small['run_minutes_cap']=min(full['run_minutes_cap'],small['run_minutes_cap']);small['single_run_minutes_cap']=min(full['single_run_minutes_cap'],small['single_run_minutes_cap'])
            facts['week_envelopes']=selected_envs
            if context.get('weather_snapshot'):facts['weather_snapshot']=context['weather_snapshot']
            facts['cycle_policy']={'same_day_run_and_strength_allowed':context['profile'].same_day_strength,'band_resistance_exact_literal':context['profile'].band_resistance,'no_clinical_review':True,'future_weeks_require_daily_feedback':True,'no_make_up_debt':True,'controlled_work_opt_in':data.allow_controlled_work}
            messages=[{'role':'system','content':app.state.coach_role+'\n本次是完整周期蓝图，严格输出本次schema，不输出sessions或每日安排。每周必须对应week_envelopes全部index，跑步分钟数/次数、力量次数/组数不得超过该周上限。每个run_minutes元素必须至少10分钟且不超过single_run_minutes_cap；周预算不足10分钟时必须run_minutes=[]，不能填0、5或8分钟来凑训练。总和不得超过run_minutes_cap。数据不足、症状限制或赛后上限为0时run_minutes=[]、strength_count=0。动作仅从allowed_exercise_ids选，note说明删减或核对原因，不能提议全力测试、超额或欠账。目标成绩不能变成训练配速。后端负责按保存日程分配日期，未来周是需复核草稿。\n'+json.dumps(Blueprint.model_json_schema(),ensure_ascii=False)},
                      {'role':'user','content':'以下为已保存事实和逐周上限：\n'+json.dumps(facts,ensure_ascii=False)}]
            bound=len(json.dumps(messages,ensure_ascii=False).encode())+1024
            if bound>100000:raise HTTPException(422,'周期资料过长；不会静默截断限制')
            call_id=reserve(session,cfg,user.id,None,task_kind='coach_cycle',input_bound=bound,output_bound=cfg.ai_coach_max_output_tokens)
            try:
                text,usage,finish=app.state.planner.read(messages);settle(session,cfg,call_id,usage)
                if finish!='stop':raise VisionFailure('周期输出截断，未创建提案')
                blueprint=json.loads(text)
                # Enforce selected-context caps in addition to complete saved-fact caps.
                validate_blueprint(blueprint,selected_context,data.start_date,allowed)
                source='deepseek'
            except VisionFailure as e:raise HTTPException(502,e.message) from None
            except (ValueError,ValidationError,TypeError):raise HTTPException(422,'周期蓝图未通过结构、证据或训练上限校验，正式计划未修改') from None
        try:
            payload,ability,weeks=expand(blueprint,context,data.start_date)
            clean=validate_cycle(payload,context,data.start_date)
        except (ValueError,ValidationError) as e:raise HTTPException(422,'周期未通过整体约束检查：'+str(e)) from None
        if context.get('weather_snapshot') and stale(session,user,{'weather_snapshot':context['weather_snapshot']}):raise HTTPException(409,'生成期间天气已变化或过期，请刷新后重试')
        before=context['old'].payload['sessions'] if context['old'] else []
        weather_evidence=context.get('weather_snapshot')
        expires=min(datetime.now(timezone.utc)+timedelta(hours=24),datetime.fromisoformat(weather_evidence['expires_at'])) if weather_evidence else datetime.now(timezone.utc)+timedelta(hours=24)
        row=PlanProposal(user_id=user.id,base_version=user.plan_version,facts_revision=user.facts_revision,
            expires_at=expires,payload={**clean,**({'weather_snapshot':weather_evidence} if weather_evidence else {}),'scope':'cycle','allow_controlled_work':data.allow_controlled_work,'is_mock':source!='deepseek','provider':source,'prompt_version':RULE_VERSION,'professional_review':'source_checked_not_clinically_reviewed',
            'start_date':data.start_date.isoformat(),'end_date':horizon(context['profile'],data.start_date).isoformat(),
            'ability':ability,'cycle_weeks':weeks,'blueprint':blueprint,'sources':ability['sources'],
            'changes':diffs(context['old'],clean,context),'cycle_totals':{'before':summarize_sessions(before),'after':summarize_sessions(clean['sessions'])},
            'constraints_checked':['当前身体反馈','全部周期日期','已保存日程','共享时间','动作与阻力','训练基础上限','阶段上限','比赛与赛后待核对','证据与事实版本']+(['天气快照与时段限制'] if weather_evidence else [])})
        session.add(row);session.commit();session.refresh(user)
        return {'id':row.id,'state':'pending' if row.facts_revision==user.facts_revision and row.base_version==user.plan_version and not stale(session,user,row.payload) else 'expired','base_version':row.base_version,'facts_revision':row.facts_revision,'expires_at':row.expires_at.replace(tzinfo=timezone.utc).isoformat(),**row.payload}
