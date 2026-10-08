from datetime import date, timedelta
from types import SimpleNamespace
from app.race_cycle import summarize_cycle


def cycle(race, today=date(2026,10,8), reasons=None):
    return summarize_cycle({'profile':SimpleNamespace(goal_date=race,goal_duration_seconds=6600)},today,reasons or [])


def test_calendar_partition_and_joint_review():
    result=cycle(date(2026,11,29),reasons=['疼痛尚待评估'])
    assert result['days_remaining']==52 and len(result['weeks'])==9
    assert result['blocked_reasons']==['疼痛尚待评估']
    weeks=result['weeks']
    assert weeks[0]['start']=='2026-10-08' and weeks[0]['end']=='2026-10-11'
    assert weeks[-3]['phase']=='pre_race' and weeks[-2]['phase']=='race_week'
    assert weeks[-2]['race_day']=='2026-11-29' and weeks[-1]['phase']=='post_race'
    assert all(w['state']=='needs_review' and set(w['focus'])=={'run','strength','recovery'} for w in weeks)
    assert all(date.fromisoformat(b['start'])==date.fromisoformat(a['end'])+timedelta(days=1) for a,b in zip(weeks,weeks[1:]))
    assert 'sessions' not in result


def test_missing_past_and_race_today():
    assert cycle(None)['status']=='missing_date' and cycle(None)['weeks']==[]
    assert cycle(date(2026,10,7))['status']=='past_goal' and cycle(date(2026,10,7))['weeks']==[]
    today=cycle(date(2026,10,8))
    assert today['days_remaining']==0 and len(today['weeks'])==2
    assert today['weeks'][0]['start']==today['weeks'][0]['end']=='2026-10-08'
    assert today['weeks'][0]['phase']=='race_week'


def test_horizon_and_leap_day():
    far=cycle(date(2028,1,1))
    assert far['truncated'] and far['weeks'][-1]['end']=='2027-10-07'
    assert not any(w['race_day'] or w['phase']=='post_race' for w in far['weeks'])
    leap=cycle(date(2028,2,29),today=date(2028,2,28))
    assert leap['days_remaining']==1 and leap['weeks'][0]['end']=='2028-02-29'
    assert leap['weeks'][1]['start']=='2028-03-01'
