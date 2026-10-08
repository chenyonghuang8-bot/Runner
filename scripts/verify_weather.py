"""Read-only official weather smoke test with a fixed public example city."""
import argparse,json,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'backend'))
from app.weather import OpenMeteo,view
from app.schemas import Profile
from app.models import WeatherState
from fastapi import HTTPException
parser=argparse.ArgumentParser(description='只读取公开示例城市北京，不使用个人数据库或健康资料')
parser.add_argument('--live-public-example',action='store_true',required=True)
parser.add_argument('--city-source',choices=['local','online'],default='local')
args=parser.parse_args()
provider=OpenMeteo();search_passed=False
if args.city_source=='local':
    from app.city_catalog import search
    cities=search('北京')['candidates']
    city=next(c for c in cities if c['ascii_name']=='Beijing');search_passed=True
else:
    try:
        cities=provider.search('Beijing')
        city=next(c for c in cities if 39<=c['latitude']<=41 and 115<=c['longitude']<=118);search_passed=True
    except HTTPException:
        # Independent forecast diagnostic only; the application never guesses a fallback city.
        city={'id':'public-coordinate-example','name':'北京公开近似坐标示例','latitude':39.9,'longitude':116.4,'timezone':'Asia/Shanghai','country':'China','admin1':''}
forecast=provider.forecast(city)
state=view(WeatherState(user_id='public-example',revision=1,payload={'location':city,'forecast':forecast}),Profile(timezone='Asia/Shanghai',preferred_time='20:00'),True)
report={'city_source':args.city_source,'data_kind':'fixed_public_city_only','passed':search_passed and state['status']=='ready','city_search_passed':search_passed,'forecast_passed':state['status']=='ready','city':city,'hour_count':len(forecast['hours']),'status':state['status'],'selected_hour':state['selected_hour'],'source':forecast['source'],'units':forecast['units'],'fetched_at':forecast['fetched_at'],'expires_at':forecast['expires_at'],'personal_database_used':False,'personal_service_enabled':False}
path=ROOT/('private/weather-public-smoke-'+args.city_source+'.json');path.parent.mkdir(exist_ok=True);path.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
print(json.dumps({k:report[k] for k in ('passed','city_search_passed','forecast_passed','data_kind','hour_count','status','personal_database_used','personal_service_enabled')},ensure_ascii=False))
if not report['passed']:raise SystemExit(1)
