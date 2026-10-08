"""Evidence-based readiness and observed training capacity, never a race guarantee."""
from datetime import timedelta
from statistics import median

RULE_VERSION='runner-cycle-rules-1'
SOURCES=[
 {'title':'CDC：相对用力与谈话测试','url':'https://www.cdc.gov/physical-activity-basics/measuring/index.html','scope':'强度反馈的一般背景；不是个体配速处方'},
 {'title':'B.A.A.：半马训练结构','url':'https://www.baa.org/races/boston-half/info-for-athletes/boston-half-training/','scope':'阶段、长跑与赛前调整的结构参考；不直接套用其跑量'},
 {'title':'NHS：一般家庭力量动作','url':'https://www.nhs.uk/live-well/exercise/strength-exercises/','scope':'动作说明；不代表膝痛康复适用性'},
 {'title':'NHS：跑步伤痛','url':'https://www.nhs.uk/live-well/exercise/knee-pain-and-other-running-injuries/','scope':'疼痛需评估；不由本应用诊断'}]


def assess(context,today):
    from .planning import constraints,reduced
    rows=[r for r in context['workouts'] if (today-timedelta(days=27)).isoformat()<=r['date']<=today.isoformat()]
    road=[r for r in rows if r['sport']=='road_run']
    weeks=[]
    for i in range(4):
        begin=today-timedelta(days=27-7*i);end=begin+timedelta(days=6)
        week=[r for r in road if begin.isoformat()<=r['date']<=end.isoformat()]
        weeks.append({'start':begin.isoformat(),'end':end.isoformat(),'minutes':sum(r['duration_seconds'] for r in week)//60,'count':len(week)})
    occupied=sum(w['count']>0 for w in weeks)
    usable=[r for r in road if r.get('effort') is not None and 1<=r['effort']<=4 and not r.get('pain_notes')]
    durations=sorted((r['duration_seconds']//60 for r in usable),reverse=True)
    longest=durations[0] if durations else 0
    repeat_supported=durations[1] if len(durations)>=2 else 0
    baseline=int(median(w['minutes'] for w in weeks))
    status='established' if len(road)>=6 and occupied>=3 and len(usable)>=3 else 'limited' if road else 'insufficient'
    blocked=constraints(context)
    latest=context.get('latest') or {}
    recent_checks=[c for c in context['checkins'] if (today-timedelta(days=6)).isoformat()<=c['date']<=today.isoformat()]
    fatigue_high=latest.get('fatigue',0)>=7
    if fatigue_high:blocked.append('当前自述高疲劳；周期规则暂缓训练，先核对恢复')
    repeated=len({c['date'] for c in recent_checks if c.get('fatigue',0)>=5})>=2
    reduce=bool(reduced(context) or repeated)
    # Explicit product ceilings; these are not injury-risk thresholds or medical clearance.
    week_cap=min(300,baseline) if status=='established' else min(75,sum(r['duration_seconds'] for r in usable)//60)
    if blocked:week_cap=0
    if reduce:week_cap=week_cap//2
    run_cap=min(120,repeat_supported) if status=='established' else min(25,repeat_supported)
    pace_rows=[r for r in usable if r['distance_m']>=1000 and r['duration_seconds']>=600]
    observed=[round(r['duration_seconds']*1000/r['distance_m']) for r in pace_rows]
    compare=sorted(pace_rows,key=lambda r:(r['date'],r.get('created_at','')))
    trend='insufficient_comparable_evidence';pairs=[]
    if len(compare)>=4:
        for a,b in zip(compare[:2],compare[-2:]):
            same_distance=.8<=b['distance_m']/a['distance_m']<=1.2
            terrain=a.get('ascent_m') is not None and b.get('ascent_m') is not None and abs(a['ascent_m']/a['distance_m']-b['ascent_m']/b['distance_m'])<=.005
            if same_distance and terrain and a.get('effort')==b.get('effort') and a['date']<b['date']:
                pairs.append({'before_id':a['id'],'after_id':b['id'],'pace_change_seconds_per_km':round(b['duration_seconds']*1000/b['distance_m']-a['duration_seconds']*1000/a['distance_m'])})
        if len(pairs)==2:trend='repeated_faster_observation' if all(p['pace_change_seconds_per_km']<0 for p in pairs) else 'mixed_observation'
    controlled_evidence=[r for r in road if r.get('effort') in (5,6) and r['duration_seconds']>=1200 and not r.get('pain_notes')]
    strength=[r for r in context['strength'] if (today-timedelta(days=27)).isoformat()<=r['date']<=today.isoformat()]
    race_rows=[r for r in road if r.get('session_context')=='race' and 21000<=r['distance_m']<=21300]
    race_record=max(race_rows,key=lambda r:r['date'],default=None)
    target=context['profile'].goal_duration_seconds
    goal_status='recorded_at_target' if race_record and target and race_record['duration_seconds']<=target else 'recorded_slower_than_target' if race_record and target else 'insufficient_race_specific_evidence'
    goal_note='最近已保存的公路半马比赛用时达到过当前目标；仍不能保证下次成绩。' if goal_status=='recorded_at_target' else '最近已保存的公路半马比赛尚未达到当前目标；不据此安排追速或保证突破。' if goal_status=='recorded_slower_than_target' else '训练表现不能保证比赛结果；越野、轻松跑与设备估计不换算为半马达标承诺。'
    return {'rule_version':RULE_VERSION,'as_of':today.isoformat(),'status':status,'road_records':len(road),'occupied_weeks':occupied,
            'evidence_ids':[r['id'] for r in rows+strength+recent_checks],'weeks':weeks,'baseline_week_minutes':baseline,
            'longest_easy_minutes':longest,'repeat_supported_run_minutes':repeat_supported,'observed_easy_pace':{'median_seconds_per_km':round(median(observed)) if observed else None,'min_seconds_per_km':min(observed) if observed else None,'max_seconds_per_km':max(observed) if observed else None,'sample_count':len(observed),'is_training_prescription':False},
            'trend':trend,'comparisons':pairs,'blocked_reasons':blocked,'reduce':reduce,
            'envelope':{'week_run_minutes_cap':week_cap,'single_run_minutes_cap':run_cap,'strength_sessions_cap':0 if context.get('running_only') else 2,'strength_sets_cap':1 if reduce else 2,'repetitions_cap':5,'controlled_eligible':status=='established' and len(controlled_evidence)>=2 and not blocked and not reduce},
            'goal':{'date':context['profile'].goal_date.isoformat() if context['profile'].goal_date else None,'feasibility':goal_status,'record_id':race_record['id'] if race_record else None,'note':goal_note},
            'strength':{'completed':sum(r['status']=='completed' for r in strength),'partial':sum(r['status']=='partial' for r in strength),'skipped':sum(r['status']=='skipped' for r in strength)},
            'sources':[s for s in SOURCES if not context.get('running_only') or '力量' not in s['title']],'professional_review':'source_checked_not_clinically_reviewed',
            'limitations':['这里评估的是已记录的训练基础，不是生理测试或医学许可。','28天记录缺失会压低基线，零记录不代表实际没有训练。','数值上限、疲劳分支和阶段比例是保守产品规则，不宣称可预防受伤。','单次或重复配速改善均不会自动提高跑量或速度。']}
