"""Read-only arithmetic from confirmed workout facts, never a race prediction."""
import math
import re
from .recognition import FIELD_UNITS, METRICS


def summarize_run(row):
    metrics={};ignored=[]
    for field,m in row.get('confirmed_metrics',{}).items():
        split=re.fullmatch(r'split_([1-9]|[1-4][0-9]|50)_seconds',field)
        expected='s' if split else FIELD_UNITS.get(field)
        if field not in METRICS and not split:
            ignored.append(field);continue
        value=m.get('value')
        valid=(m.get('review_source')=='human_confirmed' and bool(m.get('review_id'))
               and m.get('unit')==expected and isinstance(value,(int,float)) and not isinstance(value,bool)
               and math.isfinite(value) and value>=0
               and m.get('origin')==('device_estimate' if field.startswith('device_') else 'device'))
        if valid and (split or field in {'average_pace_seconds_per_km','fastest_instant_pace_seconds_per_km','fastest_km_seconds','elapsed_seconds'}):valid=value>0
        if not valid:
            ignored.append(field);continue
        metrics[field]={k:m[k] for k in ('value','unit','origin','review_id','review_source')}
    duration=row['duration_seconds'];distance=row['distance_m']
    calculated=duration*1000/distance
    device=metrics.get('average_pace_seconds_per_km',{}).get('value')
    elapsed=metrics.get('elapsed_seconds',{}).get('value')
    notices=[]
    if device is not None and abs(calculated-device)>max(3,calculated*.02):
        notices.append('计算配速与设备平均配速存在差异，请核对运动时间和设备口径；不自动归因为暂停。')
    difference=None
    if elapsed is not None:
        if elapsed<duration:notices.append('已核对总时间小于运动时间，请再次核对时间口径。')
        else:difference=elapsed-duration
    expected=distance//1000
    splits=[]
    for key,m in metrics.items():
        match=re.fullmatch(r'split_(\d+)_seconds',key)
        if not match:continue
        number=int(match[1])
        if number>expected:
            notices.append(f'第{number}公里超出记录距离中的完整公里数，未参与分段统计。');continue
        splits.append({'kilometer':number,'seconds':m['value'],'review_id':m['review_id']})
    splits.sort(key=lambda s:s['kilometer'])
    seen={s['kilometer'] for s in splits}
    missing=[i for i in range(1,min(expected,50)+1) if i not in seen]
    complete=0<expected<=50 and len(splits)==expected
    total=sum(s['seconds'] for s in splits)
    if complete and total>duration+1:
        notices.append('完整公里耗时合计超过运动时间，请核对分段及时间口径。')
    fastest=min((s['seconds'] for s in splits),default=None)
    device_fastest=metrics.get('fastest_km_seconds',{}).get('value')
    if complete and device_fastest is not None and abs(device_fastest-fastest)>1:
        notices.append('设备最快整公里与已核对完整分段最小值不同，请核对分段与设备标签。')
    if ignored:notices.append('部分详细指标缺少有效核对来源或标准单位，未参与摘要。')
    return {k:row.get(k) for k in ('id','date','sport','title','distance_m','duration_seconds','effort','pain_notes')} | {
        'calculated_pace_seconds_per_km':round(calculated),
        'device_average_pace_seconds_per_km':device,
        'elapsed_minus_duration_seconds':difference,
        'confirmed_metric_count':len(metrics),'ignored_metric_fields':ignored,
        'device_metrics':{key:m for key,m in metrics.items() if not key.startswith('split_')},
        'splits':{'expected_full_kilometers':expected,'confirmed_count':len(splits),
                  'complete':complete,'missing_kilometers':missing,'supported_kilometers_limit':50,
                  'fastest_confirmed_seconds':fastest,
                  'mean_full_kilometer_seconds':round(total/expected) if complete else None,
                  'rows':splits},
        'notices':notices,
        'limitations':['仅描述本次已保存表现，不是训练配速或比赛能力。',
                       '完整公里分段不包括最后不足一公里；缺失分段不补齐，不从曲线推算。',
                       '总时间与运动时间之差只是两种读数之差，不能直接当作暂停时间。']}
