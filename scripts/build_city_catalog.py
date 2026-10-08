"""Explicit maintenance command for the bundled public GeoNames city directory."""
import argparse,hashlib,io,json,re,zipfile,sys
from pathlib import Path
import httpx
from zoneinfo import ZoneInfo
parser=argparse.ArgumentParser();parser.add_argument('--download-public-data',action='store_true',required=True);parser.add_argument('--snapshot-date',required=True);args=parser.parse_args()
from datetime import date
date.fromisoformat(args.snapshot_date)
root=Path(__file__).resolve().parents[1];url='https://download.geonames.org/export/dump/cities15000.zip';admin_url='https://download.geonames.org/export/dump/admin1CodesASCII.txt'
with httpx.Client(timeout=45,follow_redirects=False) as client:
    raw=client.get(url);raw.raise_for_status();assert len(raw.content)<10000000
    admin=client.get(admin_url);admin.raise_for_status();assert len(admin.content)<1000000
admin_names={f[0]:f[1] for line in admin.text.splitlines() if len(f:=line.split('\t'))==4}
z=zipfile.ZipFile(io.BytesIO(raw.content));assert z.getinfo('cities15000.txt').file_size<30000000
rows=[]
for line in z.read('cities15000.txt').decode('utf-8').splitlines():
    f=line.split('\t')
    if len(f)!=19 or f[8] not in ('CN','HK','MO','TW'):continue
    names=list(dict.fromkeys([f[1],f[2],*f[3].split(',')]))
    han=[n for n in names if re.fullmatch('[\u3400-\u9fff]{2,12}',n)]
    name=next((n for n in han if n.endswith('市')),han[0] if han else f[1])
    aliases=list(dict.fromkeys([name,f[1],f[2],*han]))[:30]
    ZoneInfo(f[17]);lat=float(f[4]);lon=float(f[5]);assert -90<=lat<=90 and -180<=lon<=180
    rows.append({'id':'geonames-'+f[0],'name':name,'ascii_name':f[2],'aliases':aliases,'admin1':admin_names.get(f[8]+'.'+f[10],f[10]),'country':f[8],'latitude':lat,'longitude':lon,'timezone':f[17],'population':int(f[14]),'source':'https://www.geonames.org/'+f[0],'location_source':'geonames_local'})
rows.sort(key=lambda r:(-r['population'],r['id']))
metadata={'source_url':url,'admin_source_url':admin_url,'attribution':'GeoNames (www.geonames.org), CC BY 4.0; filtered and transformed city-reference records.','license_url':'https://creativecommons.org/licenses/by/4.0/','retrieved_on':args.snapshot_date,'source_sha256':hashlib.sha256(raw.content).hexdigest(),'admin_source_sha256':hashlib.sha256(admin.content).hexdigest(),'selection':'cities15000 entries with country CN/HK/MO/TW; reference settlements, not a full administrative-city list','cities':rows}
path=root/'backend/app/data/weather-cities.json';path.write_text(json.dumps(metadata,ensure_ascii=False,separators=(',',':'))+'\n')
print(json.dumps({'cities':len(rows),'file_bytes':path.stat().st_size,'snapshot_date':args.snapshot_date}))
