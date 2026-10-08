"""Compare frozen proposed schedules; these are durations, not physiological loads."""
from datetime import date, timedelta


def summarize_sessions(sessions):
    summary={kind:{'sessions':0,'minutes':0} for kind in ('run','strength','recovery')}
    summary['strength']['sets']=0
    for item in sessions:
        group=summary[item['type']]
        group['sessions']+=1
        group['minutes']+=item['duration_minutes']
        if item['type']=='strength':group['sets']+=sum(e['sets'] for e in item['exercises'])
    return {**summary,'total_minutes':sum(v['minutes'] for v in summary.values())}


def compare_plans(old,new,start,days):
    before=old.payload['sessions'] if old else []
    after=new['sessions']
    old_start=old.payload.get('start_date') if old else None
    end=(start+timedelta(days=6)).isoformat()
    slots={d['date']:d for d in days}
    return {'version':'runner-plan-comparison-1',
            'before_window':{'start':old_start,'end':(date.fromisoformat(old_start)+timedelta(days=6)).isoformat() if old_start else None},
            'after_window':{'start':start.isoformat(),'end':end},
            'same_window':old_start==start.isoformat() if old else False,
            'has_previous_plan':bool(old),
            'before':summarize_sessions(before),'after':summarize_sessions(after),
            'days':[{'date':day,'availability':slots.get(day),
                     'before':summarize_sessions([s for s in before if s['date']==day]),
                     'after':summarize_sessions([s for s in after if s['date']==day])}
                    for day in [(start+timedelta(days=i)).isoformat() for i in range(7)]],
            'limitations':['仅对比计划时长与项目数，不代表已完成训练或生理负荷。',
                           '日期范围变化时，两个窗口的差值不能直接解释为减量或进步。',
                           '未在新窗口中的原安排保留在历史版本；不认定漏练或要求补做。']}
