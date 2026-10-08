"""Synthetic confirmed facts only; no screenshot requests or race predictions."""
import copy
from datetime import date
import json
import pytest
from app.run_evidence import summarize_run
from app.assessment import summarize
from app.schemas import Profile
from app.coach_provider import messages_for
from test_app import workout


def metric(value,unit='s',origin='device',**changes):
    return {'value':value,'unit':unit,'origin':origin,'review_id':'synthetic-review','review_source':'human_confirmed',**changes}

def run(**changes):
    return {'id':'run','effort':2,'pain_notes':'',**workout(),**changes}


def test_complete_splits_exclude_tail_and_do_not_infer_pause():
    row=run(distance_m=2500,duration_seconds=900,confirmed_metrics={
        'split_1_seconds':metric(350),'split_2_seconds':metric(370),
        'average_pace_seconds_per_km':metric(360,'s/km'),
        'elapsed_seconds':metric(960),'fastest_km_seconds':metric(350)})
    original=copy.deepcopy(row)
    result=summarize_run(row);s=result['splits']
    assert s['expected_full_kilometers']==2 and s['complete']
    assert s['mean_full_kilometer_seconds']==360 and s['fastest_confirmed_seconds']==350
    assert len(s['rows'])==2 and result['elapsed_minus_duration_seconds']==60
    assert not result['notices'] and row==original
    assert 'pause_seconds' not in result


def test_missing_and_outside_splits_never_fill_or_claim_full_mean():
    result=summarize_run(run(distance_m=3050,confirmed_metrics={
        'split_1_seconds':metric(350),'split_3_seconds':metric(370),'split_4_seconds':metric(300)}))
    s=result['splits']
    assert not s['complete'] and s['missing_kilometers']==[2]
    assert s['mean_full_kilometer_seconds'] is None
    assert [r['kilometer'] for r in s['rows']]==[1,3]
    assert s['fastest_confirmed_seconds']==350 and result['notices']
    result=summarize_run(run(distance_m=900,confirmed_metrics={'split_1_seconds':metric(300)}))
    assert result['splits']['confirmed_count']==0 and not result['splits']['complete']


def test_inconsistent_times_and_fastest_only_warn():
    result=summarize_run(run(distance_m=2000,duration_seconds=600,confirmed_metrics={
        'split_1_seconds':metric(350),'split_2_seconds':metric(370),
        'elapsed_seconds':metric(500),'average_pace_seconds_per_km':metric(350,'s/km'),
        'fastest_km_seconds':metric(300)}))
    assert result['elapsed_minus_duration_seconds'] is None
    assert result['device_average_pace_seconds_per_km']==350
    assert len(result['notices'])==4
    assert result['splits']['rows'][0]['seconds']==350


@pytest.mark.parametrize('changes',[
    {'review_source':'needs_review'},{'review_id':''},{'value':None},
    {'value':True},{'value':float('nan')},{'value':float('inf')},
    {'unit':'min/km'},{'origin':'device_estimate'},{'value':0},
])
def test_only_confirmed_valid_units_and_provenance_participate(changes):
    result=summarize_run(run(confirmed_metrics={'average_pace_seconds_per_km':{**metric(360,'s/km'),**changes}}))
    assert result['device_average_pace_seconds_per_km'] is None
    assert result['ignored_metric_fields']==['average_pace_seconds_per_km']


def test_zero_device_estimate_is_preserved_and_limit_not_hidden():
    result=summarize_run(run(distance_m=51000,confirmed_metrics={
        'device_pace_zone_z1_minutes':metric(0,'minutes','device_estimate'),
        **{f'split_{i}_seconds':metric(360) for i in range(1,51)}}))
    assert result['device_metrics']['device_pace_zone_z1_minutes']['value']==0
    assert result['device_metrics']['device_pace_zone_z1_minutes']['origin']=='device_estimate'
    assert result['splits']['confirmed_count']==50 and not result['splits']['complete']
    assert result['splits']['expected_full_kilometers']==51


def test_latest_evidence_keeps_sports_and_window_separate():
    context={'profile':Profile(),'strength':[],'checkins':[],'workouts':[
        run(id='road-old',date='2026-10-01',distance_m=10000),
        run(id='road-new',date='2026-10-08'),
        run(id='trail',sport='trail_run',date='2026-10-07',distance_m=20000),
        run(id='future',date='2026-10-09'),run(id='expired',date='2026-09-10')]}
    result=summarize(context,date(2026,10,8))
    assert result['runs'][0]['longest']['id']=='road-old'
    assert result['runs'][0]['latest_evidence']['id']=='road-new'
    assert result['runs'][1]['latest_evidence']['id']=='trail'
    assert result['runs'][2]['latest_evidence'] is None


def test_coach_detailed_summary_does_not_expand_selected_history():
    context={'profile':Profile(),'strength':[],'checkins':[],'latest':None,'old':None,'days':[],
             'workouts':[run(id=f'road-{i}') for i in range(10)]+[run(id='unselected-trail',sport='trail_run')]}
    messages,_,ids=messages_for(context,date(2026,10,8),'合成角色',{})
    text=json.dumps(messages,ensure_ascii=False)
    assert 'unselected-trail' not in text and 'unselected-trail' not in ids
    assert 'latest_evidence' in text and len(ids)==10
