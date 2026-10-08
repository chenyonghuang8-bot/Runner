"""Arithmetic evidence summary, not a fitness estimate or medical assessment."""
from datetime import timedelta


def summarize(context, today):
    start = today - timedelta(days=27)
    def in_window(row):
        return start.isoformat() <= row['date'] <= today.isoformat()
    runs = [r for r in context['workouts'] if in_window(r)]
    strength = [r for r in context['strength'] if in_window(r)]
    checks = [r for r in context['checkins'] if in_window(r)]
    groups = []
    for sport in ('road_run', 'trail_run', 'treadmill'):
        rows = [r for r in runs if r['sport'] == sport]
        longest = max(rows, key=lambda r: r['distance_m'], default=None)
        latest = max(rows,key=lambda r:(r['date'],r.get('created_at',''),r['id']),default=None)
        from .run_evidence import summarize_run
        groups.append({'sport': sport, 'count': len(rows),
                       'distance_m': sum(r['distance_m'] for r in rows),
                       'duration_seconds': sum(r['duration_seconds'] for r in rows),
                       'missing_effort_count': sum(r.get('effort') is None for r in rows),
                       'longest': {k: longest.get(k) for k in ('id','date','title','distance_m','duration_seconds','effort','pain_notes')} if longest else None,
                       'latest_evidence': summarize_run(latest) if latest else None,
                       'evidence_ids': [r['id'] for r in rows]})
    weeks = []
    for i in range(4):
        begin = start + timedelta(days=i*7)
        end = begin + timedelta(days=6)
        rows = [r for r in runs if begin.isoformat() <= r['date'] <= end.isoformat()]
        weeks.append({'start': begin.isoformat(), 'end': end.isoformat(), 'count': len(rows), 'distance_m': sum(r['distance_m'] for r in rows)})
    profile = context['profile']
    return {'as_of': today.isoformat(), 'window_start': start.isoformat(),
            'goal': {'date': profile.goal_date.isoformat() if profile.goal_date else None,
                     'duration_seconds': profile.goal_duration_seconds,
                     'pace_seconds_per_km': round(profile.goal_duration_seconds / 21.0975) if profile.goal_duration_seconds else None,
                     'feasibility': '未评估；目标配速不是训练配速'},
            'runs': groups, 'weeks': weeks,
            'strength': {'completed': sum(r['status']=='completed' for r in strength),
                         'partial': sum(r['status']=='partial' for r in strength),
                         'skipped': sum(r['status']=='skipped' for r in strength),
                         'duration_seconds': sum(r['duration_seconds'] for r in strength if r['status']!='skipped'),
                         'evidence_ids': [r['id'] for r in strength]},
            'feedback_count': len(checks),
            'limitations': ['仅汇总已保存且在日期窗口内的记录；零条不代表没有训练。',
                            '公路、越野与跑步机分开；不从越野成绩推算公路半马能力。',
                            '未计算比赛预测、最大心率或医学诊断；存在未评估疼痛时不安排全力测试。']}


def next_steps(context, today, blocked_reasons):
    """Data readiness only. Recorded never means medically cleared."""
    from .exercises import BY_ID
    profile = context['profile']
    checks = [r for r in context['checkins'] if r['date'] <= today.isoformat()]
    latest = checks[0] if checks else None
    runs = [r for r in context['workouts'] if
            (today-timedelta(days=27)).isoformat() <= r['date'] <= today.isoformat()]
    strength = [r for r in context['strength'] if
                (today-timedelta(days=27)).isoformat() <= r['date'] <= today.isoformat()]
    slots = [d for d in context['days'] if
             today.isoformat() <= d['date'] <= (today+timedelta(days=6)).isoformat()]
    known = [BY_ID[id] for id in profile.familiar_exercises if id in BY_ID
             and BY_ID[id]['equipment'] in profile.strength_equipment
             and (BY_ID[id]['equipment'] != 'resistance_band' or profile.band_resistance.strip())]
    strength_missing = []
    if profile.strength_experience == 'unknown': strength_missing.append('训练经验')
    if not profile.strength_equipment: strength_missing.append('可用器械')
    if profile.strength_max_minutes is None: strength_missing.append('单次总时长')
    if not known: strength_missing.append('与器械匹配的熟悉动作及已知阻力')
    goal_missing = profile.goal_date is None or profile.goal_duration_seconds is None
    goal_past = profile.goal_date is not None and profile.goal_date < today
    today_feedback = latest is not None and latest['date'] == today.isoformat()
    def step(id, title, state, detail, action, evidence_ids=None):
        return {'id':id, 'title':title, 'state':state, 'detail':detail,
                'action':action, 'evidence_ids':evidence_ids or []}
    items = [
        step('body', '先核对身体状态',
             'needs_info' if not today_feedback else 'needs_review' if blocked_reasons else 'recorded',
             '补充今天的疲劳、局部酸胀、疼痛、疾病、活动影响和训练意愿。' if not today_feedback else
             '有需要核对的身体信息；不会因目标、低疲劳或新成绩自动解除限制。' if blocked_reasons else
             '今天的身体反馈已保存；这不代表医学许可或专业恢复评估。',
             'feedback', [latest['id']] if latest else []),
        step('goal', '确认比赛目标', 'needs_info' if goal_missing else 'needs_review' if goal_past else 'recorded',
             '填写比赛日期与目标用时，目标可行性仍待评估。' if goal_missing else
             '比赛日期已过去，请核对是否需要更新目标。' if goal_past else
             '比赛目标已保存；不据此推算当前能力或训练配速。', 'settings'),
        step('schedule', '核对未来七天时间',
             'needs_info' if len(slots)<7 else 'needs_review' if not any(d['available'] for d in slots) else 'recorded',
             f'未来七天已保存 {len(slots)} 天日程、{sum(d["available"] for d in slots)} 天可用。未填写的日期不假定有空；跑步安排不得超过当天可用时长。', 'calendar'),
        step('running', '补齐已有跑步依据', 'needs_info' if not runs else 'recorded',
             '可以手工补录或核对截图，保留运动类型、距离、时长、主观用力与不适；无需做全力测试来补数据。' if not runs else
             f'最近28天有 {len(runs)} 条已保存跑步，其中 {sum(r.get("effort") is None for r in runs)} 条缺少主观用力；单次表现不等于比赛能力。',
             'records', [r['id'] for r in runs]),
        step('strength_profile', '核对力量条件', 'needs_info' if strength_missing else 'recorded',
             '仍需核对：'+'、'.join(strength_missing)+'。只选择自己熟悉的动作，不为填满档案试做引发不适的动作。' if strength_missing else
             '力量经验、器械、时长和可用熟悉动作已保存；动作适用性仍未经个体专业审阅。', 'settings'),
        step('strength_records', '记录实际力量与恢复反馈', 'needs_info' if not strength else 'recorded',
             '有已有力量训练时可记录实际组次、阻力、完整/部分/跳过和不适；没有历史记录时保留未知，不要求额外补练。' if not strength else
             f'最近28天有 {len(strength)} 条力量记录；少做或跳过保留为事实，不列为待补欠账。',
             'records', [r['id'] for r in strength]),
    ]
    return {'label':'身体状态需核对' if blocked_reasons else '可核对有限轻强度草稿',
            'note':'这是资料核对清单，不是能力评分；即使全部已记录，也不表示可做比赛强化或已达标。',
            'items': [i for i in items if not context.get('running_only') or not i['id'].startswith('strength_')]}


def install_routes(app, current, db):
    from fastapi import Depends
    from sqlalchemy.orm import Session
    from .models import User
    from .planning import gather, local_today, constraints

    @app.get('/api/v1/assessment')
    def assessment(user: User = Depends(current), session: Session = Depends(db)):
        context = gather(session, user)
        from .race_cycle import summarize_cycle
        from .ability import assess
        return {'ability':assess(context,local_today(context['profile'])),'race_cycle':summarize_cycle(context,local_today(context['profile']),constraints(context)),**summarize(context, local_today(context['profile'])),
                'facts_revision': user.facts_revision, 'attention': constraints(context),
                'next_steps': next_steps(context, local_today(context['profile']), constraints(context))}
