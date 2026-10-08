"""Bundled public coordinates, not live geolocation or complete administrative coverage."""
import json,unicodedata
from pathlib import Path
from functools import lru_cache

@lru_cache(maxsize=1)
def catalog():return json.loads((Path(__file__).parent/'data/weather-cities.json').read_text())

def normalize(value):
    text=''.join(c for c in unicodedata.normalize('NFKD',value.casefold()) if not unicodedata.combining(c)).strip()
    return text[:-1] if len(text)>2 and text.endswith('市') else text

def public(row):return {k:v for k,v in row.items() if k not in ('aliases','population')}

def search(query):
    q=normalize(query);rows=catalog()['cities']
    matches=[]
    for r in rows:
        names=[normalize(n) for n in r['aliases']]
        if not q or any(q in n for n in names):matches.append((0 if q and q in names else 1,-r['population'],r['id'],r))
    matches.sort(key=lambda v:v[:3]);data=catalog()
    return {'candidates':[public(r[-1]) for r in matches[:10]],'count':len(matches),'snapshot_date':data['retrieved_on'],'source':data['source_url'],'license':data['license_url'],'attribution':data['attribution'],'scope':'城市/城镇参考点，不是完整行政区目录；请核对地区和坐标，未找到时可使用在线搜索。','external_request':False}

def lookup(id):return next((public(r) for r in catalog()['cities'] if r['id']==id),None)
