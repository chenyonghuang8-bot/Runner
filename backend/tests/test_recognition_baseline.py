"""Synthetic regression samples only; personal baseline files are not loaded."""
import importlib.util
from pathlib import Path
import json
from app.recognition import validate_readings

spec=importlib.util.spec_from_file_location('recognition_comparison',Path(__file__).resolve().parents[2]/'scripts/compare_recognition.py')
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)


def test_quote_variants_do_not_bypass_time_conversion():
    for raw in ['7’12”','7′12″',"7'12\"",'7：12']:
        readings=validate_readings(json.dumps({'readings':[{'field':'average_pace_seconds_per_km','value':300,'raw':raw,'unit':'s/km','rect':[0,0,1,1]}]}),[0,0,100,100],0)
        assert '转换不一致' in readings[0]['issue']


def test_comparison_missing_conflicts_units_and_evidence():
    expected={'fields':[{'field':'distance_m','value':4560,'unit':'m','origin':'device','source_rect':[0,100,200,200]}]}
    good={'field':'distance_m','value':4560,'unit':'m','origin':'device','source_rect':[10,110,40,30],'raw':'4.56公里','issue':''}
    assert module.compare(expected,[good,good])['complete']
    assert module.compare(expected,[])['fields'][0]['errors']==['missing']
    for key,value,error in [('value',4500,'value_mismatch'),('unit','km','unit_mismatch'),('source_rect',[0,0,10,10],'evidence_mismatch'),('origin','derived','origin_mismatch'),('issue','冲突','validation_issue')]:
        result=module.compare(expected,[good,{**good,key:value}])
        assert not result['complete'] and error in result['fields'][0]['errors']
    assert not module.compare({'fields':[]},[])['complete']


def test_unitless_null_compatibility_does_not_allow_missing_distance_unit():
    import pytest
    from pydantic import ValidationError
    row={'field':'title','value':'合成标题','raw':'合成标题','unit':None,'rect':[0,0,1,1]}
    assert validate_readings(json.dumps({'readings':[row]}),[0,0,100,100],0)[0]['unit']==''
    with pytest.raises(ValidationError):
        validate_readings(json.dumps({'readings':[{**row,'field':'distance_m','value':4500}]}),[0,0,100,100],0)


def test_detailed_fields_preserve_device_labels_and_nonexact_values():
    rows=[{'field':'device_pace_zone_z5_minutes','value':None,'raw':'Z5 小于1分钟','unit':'min','rect':[0,0,1,1]},
          {'field':'recovery_hr_drop_bpm','value':15,'raw':'降低15','unit':'bpm','rect':[0,0,1,1]},
          {'field':'recovery_hr_start_bpm','value':160,'raw':'开始160','unit':'bpm','rect':[0,0,1,1]},
          {'field':'recovery_hr_end_bpm','value':120,'raw':'结束120','unit':'bpm','rect':[0,0,1,1]}]
    result=validate_readings(json.dumps({'readings':rows}),[0,0,100,100],0)
    assert result[0]['value'] is None and result[0]['origin']=='device_estimate'
    assert result[1]['value']==15  # do not replace displayed drop with 160-120


def test_metric_unit_aliases_keep_raw_units():
    rows=[{'field':'avg_cadence','value':170,'raw':'平均步频170','unit':'spm','rect':[0,0,1,1]},
          {'field':'avg_power_w','value':150,'raw':'平均功率150','unit':'w','rect':[0,0,1,1]}]
    result=validate_readings(json.dumps({'readings':rows}),[0,0,100,100],0)
    assert result[0]['unit']=='steps/min' and result[0]['raw_unit']=='spm'
    assert result[1]['unit']=='W' and result[1]['raw_unit']=='w'


def test_heart_rate_and_step_unit_aliases_are_explicit():
    rows=[{'field':'avg_heart_rate','value':135,'raw':'平均心率135次/分钟','unit':'次/分钟','rect':[0,0,1,1]},
          {'field':'steps','value':4000,'raw':'步数4000步','unit':'步','rect':[0,0,1,1]}]
    result=validate_readings(json.dumps({'readings':rows}),[0,0,100,100],0)
    assert result[0]['unit']=='bpm' and result[0]['raw_unit']=='次/分钟' and result[0]['issue']==''
    assert result[1]['unit']=='steps' and result[1]['raw_unit']=='步'


def test_review_units_cover_all_supported_fields():
    from app.recognition import CORE, METRICS, FIELD_UNITS
    assert CORE | METRICS == set(FIELD_UNITS)


import pytest
@pytest.mark.parametrize('field,unit,canonical,value',[
    ('device_heart_zone_threshold_minutes','分钟','minutes',3),
    ('device_pace_zone_z1_minutes','min','minutes',0),
    ('contact_avg_ms','毫秒','ms',237),
    ('vertical_avg_cm','厘米','cm',7.3),
    ('balance_left_percent','％','%',50.3),
    ('calories_total','千卡','kcal',551),
    ('device_recovery_hours','小时','h',14),
    ('average_pace_seconds_per_km','秒/公里','s/km',382),
    ('split_1_seconds','秒','s',448),
])
def test_review_aliases_do_not_change_values_or_cached_evidence(field,unit,canonical,value):
    from app.recognition import summarize
    from app.models import Recognition
    row={'field':field,'value':value,'raw':'合成读数','unit':unit,'source_tile_id':0,'source_rect':[0,0,20,20],'origin':'device','issue':'','review_status':'needs_review'}
    original=json.loads(json.dumps(row))
    job=Recognition(cache_key='x'*64,region={},tiles=[{'state':'done','readings':[row,{**row,'unit':canonical}]}],lease_until=0)
    result=summarize(job)
    assert result['review_groups'][0]['status']=='needs_review'
    assert result['readings'][0]['unit']==canonical and result['readings'][0]['raw_unit']==unit
    assert result['readings'][0]['value']==value and row==original


@pytest.mark.parametrize('field,unit',[
    ('average_pace_seconds_per_km','s'),
    ('average_pace_seconds_per_km','min/km'),
    ('contact_avg_ms','s'),
    ('device_training_load','kcal'),
    ('device_heart_zone_threshold_minutes','h'),
])
def test_review_rejects_different_units_even_for_single_reading(field,unit):
    from app.recognition import summarize
    from app.models import Recognition
    row={'field':field,'value':300,'raw':'合成读数','unit':unit,'source_tile_id':0,'source_rect':[0,0,20,20],'origin':'device','issue':'','review_status':'needs_review'}
    result=summarize(Recognition(cache_key='x'*64,region={},tiles=[{'state':'done','readings':[row]}],lease_until=0))
    assert result['review_groups'][0]['status']=='invalid'
    assert result['readings'][0]['value']==300 and result['readings'][0]['unit']==unit
    assert result['issues'] and not result['suggested_fields']
