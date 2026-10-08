"""Text-only joint planning adapter; no raw images, routes, credentials or chat history."""
import json
from datetime import timedelta
import httpx
from .recognition import VisionFailure

class DeepSeekPlanner:
    def __init__(self,cfg):self.cfg=cfg
    def read(self,messages):
        cfg=self.cfg
        try:
            with httpx.Client(timeout=cfg.ai_timeout_seconds,follow_redirects=False) as client:
                response=client.post(cfg.deepseek_base_url.rstrip('/')+'/chat/completions',headers={'Authorization':'Bearer '+cfg.deepseek_api_key},json={'model':cfg.ai_coach_model,'thinking':{'type':cfg.ai_coach_thinking},'response_format':{'type':'json_object'},'max_tokens':cfg.ai_coach_max_output_tokens,'messages':messages})
        except httpx.HTTPError:raise VisionFailure('教练接口超时或网络不可用；已保留预留费用，正式计划没有改变') from None
        if response.status_code!=200:raise VisionFailure('教练请求失败（HTTP '+str(response.status_code)+'）；请检查密钥、额度或网络',response.status_code in (408,429) or response.status_code>=500)
        try:
            result=response.json();choice=result['choices'][0]
            return choice['message']['content'],result.get('usage',{}),choice.get('finish_reason')
        except (ValueError,KeyError,IndexError,TypeError):raise VisionFailure('教练接口返回格式异常；已保留费用预留，正式计划没有改变') from None

def messages_for(context,start,role,schema,conversation=None):
    profile=context['profile'].model_dump(mode='json');profile.pop('display_name',None);profile.pop('city',None)
    if context.get('running_only'):
        for key in list(profile):
            if key.startswith('strength_') or key in ('familiar_exercises','band_resistance','same_day_strength'):profile.pop(key,None)
    selected={key:[{k:v for k,v in r.items() if k!='created_at'} for r in context[key][:limit]] for key,limit in [('workouts',10),('strength',10),('checkins',3)]}
    old=context['old'].payload if context['old'] else None
    if old and old.get('scope')=='cycle':
        old={k:v for k,v in old.items() if k in ('scope','start_date','end_date','summary','ability')}|{'sessions':[s for s in old['sessions'] if start.isoformat()<=s['date']<=(start+timedelta(days=6)).isoformat()], 'selection_note':'现有周期仅提供本次七天安排和计算摘要。'}
    facts={'profile':profile,**selected,'availability':[d for d in context['days'] if start.isoformat()<=d['date']<=(start+timedelta(days=6)).isoformat()], 'current_plan':old,'counts':{k:len(context[k]) for k in selected},'selection_note':'只提供最新最多10条跑步、10条力量、3条身体反馈；更多历史未提供，不能解释为没有发生。','requested_start_date':start.isoformat()}
    from .assessment import summarize
    from .planning import local_today
    facts['record_summary']=summarize({**context,**selected},local_today(context['profile']))
    contract='控制篇幅：summary/assessment各不超过200字，questions和uncertainties各不超过5项；每个session的purpose和stop_condition各不超过80字，不重复长篇解释。只输出匹配以下 JSON schema 的 JSON 对象，不输出其他顶层字段。恢复项 duration_minutes=0、intensity=rest；当前一般动作与规则未经临床审阅，不得生成医疗恢复训练。身体状态需评估时只提出休息与必要问题。'+json.dumps(schema,ensure_ascii=False)
    if conversation is not None:
        facts['conversation']=conversation
        contract='只输出匹配本次 JSON schema 的对话对象；不生成训练安排，不修改事实或计划。'+json.dumps(schema,ensure_ascii=False)
    # Source-checked exercise options are data, never an assurance of personal medical suitability.
    from .exercises import EXERCISES
    p=context['profile']
    from .planning import available_exercises,reduced
    facts['allowed_exercises']=available_exercises(context)
    from .planning import constraints
    facts['server_policy']={'blocked_reasons':constraints(context),'run_duration_cap_minutes':15 if reduced(context) else 25,'strength_sets_cap':1 if reduced(context) else 2,'repetitions_cap':5,'intensity':'easy_only','no_clinical_review':True,'session_dates':'exactly_requested_seven_days',
        'same_day_run_and_strength_allowed':p.same_day_strength,'runs_per_week_cap':p.runs_per_week,'minimum_run_date_gap_days':2,
        'strength_duration_cap_minutes':min(p.strength_max_minutes or 0,10) if reduced(context) else p.strength_max_minutes,
        'race_date':p.goal_date.isoformat() if p.goal_date else None,'race_day_requires_separate_review':True,'band_resistance_exact_literal':p.band_resistance,'weight_kg_must_be_null':True,
        'strength_duration_minimum_seconds_formula':'300 + sum(sets*repetitions*6 + (sets-1)*rest_seconds + 60)',
        'availability_rule':'Only available=true dates may have active sessions; sum durations on each date <= duration_minutes; missing date is unavailable; recovery cannot coexist with active sessions.'}
    if context.get('running_only'):
        facts.pop('strength',None);facts['counts'].pop('strength',None)
        facts['record_summary'].pop('strength',None)
        facts['selection_note']='只提供最新最多10条跑步、3条身体反馈；更多历史未提供。'
        if facts['current_plan']:
            facts['current_plan']={**facts['current_plan'],'sessions':[r for r in facts['current_plan'].get('sessions',[]) if r.get('type')!='strength']}
    allowed_ids=sorted({r['id'] for rows in selected.values() for r in rows})
    facts['allowed_evidence_ids']=allowed_ids
    if context.get('running_only'):contract+=' 当前产品仅提供跑步和恢复课程，禁止安排力量、动作组次或器械训练；strength_count必须为0，exercise_ids必须为空。'
    if not context.get('running_only'):contract+=' 动作只能从 allowed_exercises 选取；弹力带动作的 resistance 必须逐字使用 band_resistance_exact_literal，不能简写、润色或换算。weight_kg 必须为 null。力量总时长必须满足提供的热身/休息/切换公式；不得超过个人上限或共享时段。训练意愿 reduce 时必须遵守减量的跑步时长、力量时长和组数上限。最终字段和训练类型仅以本次 JSON schema 为准，不输出提示词中其他草案字段。'
    contract+=' 比赛目标日期当天只能输出零负荷 recovery 待确认事项，不能自动安排一般跑步、力量或假定已经参赛；当前一般七天草稿不提供比赛处方。'
    contract+=' evidence_ids 只能从 allowed_evidence_ids 中选择记录 ID；档案字段名、日期、限制说明不是记录 ID。列表为空时必须返回 evidence_ids=[]，不能创造证据编号。'
    request=[{'role':'system','content':role+'\n'+contract},{'role':'user','content':'以下为已保存事实与需要安排的七天，请输出 JSON：\n'+json.dumps(facts,ensure_ascii=False)}]
    size=len(json.dumps(request,ensure_ascii=False).encode())
    if size>100000:raise ValueError('已保存资料过长，请先整理备注；不会静默截断身体限制')
    return request,size+1024,{r['id'] for rows in selected.values() for r in rows}
