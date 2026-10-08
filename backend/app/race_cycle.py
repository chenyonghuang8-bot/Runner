"""Calendar review windows, not a prescribed training cycle or medical clearance."""
from datetime import timedelta


def summarize_cycle(context,today,blocked_reasons):
    p=context['profile'];race=p.goal_date
    result={'as_of':today.isoformat(),'goal_date':race.isoformat() if race else None,
            'goal_duration_seconds':p.goal_duration_seconds,'days_remaining':(race-today).days if race else None,
            'status':'missing_date' if not race else 'past_goal' if race<today else 'review_framework',
            'blocked_reasons':list(blocked_reasons),'weeks':[],'horizon_days':365,'truncated':False,
            'limitations':['这是按日期生成的核对框架，不是已确认训练计划或能力判断。',
                           '阶段名称不会解除身体限制，不生成配速或跑量。',
                           '每周跑步与恢复需共同评估并通过正式提案确认；漏练不补债。']}
    if not race or race<today:return result
    end=min(race,today+timedelta(days=364));result['truncated']=end<race
    race_week_start=race-timedelta(days=race.weekday())
    cursor=today;index=1
    while cursor<=end:
        finish=min(cursor+timedelta(days=6-cursor.weekday()),end)
        if cursor>=race_week_start:phase='race_week';title='比赛周 · 安排核对'
        elif finish>=race_week_start-timedelta(days=7):phase='pre_race';title='赛前一周 · 负荷核对'
        elif index==1:phase='prepare';title='当前周 · 依据准备'
        else:phase='weekly_review';title='训练周 · 逐周评估'
        result['weeks'].append({'index':index,'start':cursor.isoformat(),'end':finish.isoformat(),
            'phase':phase,'title':title,'state':'needs_review','race_day':race.isoformat() if cursor<=race<=finish else None,
            'focus':{'run':'核对跑步依据、身体反馈与本周可用日程；不按目标成绩直接生成训练配速。',
                     'strength':'核对实际组次、已熟悉动作、酸胀耐受和与跑步共享的时间。',
                     'recovery':'核对疲劳、睡眠、疼痛及疾病；阶段推进不自动解除限制。'}})
        cursor=finish+timedelta(days=1);index+=1
    if not result['truncated']:
        result['weeks'].append({'index':index,'start':(race+timedelta(days=1)).isoformat(),'end':(race+timedelta(days=7)).isoformat(),
            'phase':'post_race','title':'赛后 · 实际情况与恢复反馈','state':'needs_review','race_day':None,
            'focus':{'run':'先确认是否实际参赛及记录，不预设已完成比赛或直接恢复训练。',
                     'strength':'根据实际疲劳、酸胀、疼痛和耐受重新评估，不自动补做力量。',
                     'recovery':'保存实际身体反馈，再评估后续跑步与恢复安排。'}})
    return result
