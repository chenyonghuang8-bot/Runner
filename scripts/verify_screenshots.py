"""Authorized local screenshot acceptance; private outputs, no training confirmation."""
import argparse, hashlib, json, os, sys
from pathlib import Path
from types import SimpleNamespace
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
from PIL import Image
from alembic import command
from alembic.config import Config
from fastapi import HTTPException
from sqlalchemy.orm import Session
from fastapi.testclient import TestClient
from app.config import Settings
from app.main import create_app
from app.recognition import DeepSeekVision, tile_regions, crop_jpeg, validate_readings, reserve, settle, summarize, VERSION
from compare_recognition import compare


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--send-authorized-images',action='store_true',required=True)
    parser.add_argument('--sample',choices=['road','trail','all'],default='all')
    parser.add_argument('--retry-failed',action='store_true',help='显式重试失败段，仍受每段重试上限与预算约束')
    parser.add_argument('--refresh-tile',type=int,help='显式重新验收单个0起始分段，保留旧返回；需指定sample')
    args=parser.parse_args()
    if args.refresh_tile is not None and args.sample=='all':parser.error('refresh-tile 需要单个sample')
    cfg=Settings()
    if not cfg.deepseek_api_key or cfg.deepseek_base_url.rstrip('/')!='https://api.deepseek.com':
        print('需要本机密钥及官方 DeepSeek 地址；不输出配置值。');return 2
    folder=ROOT/'private/screenshot-acceptance';folder.mkdir(parents=True,exist_ok=True)
    url='sqlite:///'+str(folder/'budget.db');os.environ['DATABASE_URL']=url
    command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')
    cfg=cfg.model_copy(update={'app_env':'test','database_url':url,'private_storage_dir':Path('/Users/toby/Pictures'),'ai_provider':'deepseek','ai_monthly_budget_cny':min(.5,cfg.ai_monthly_budget_cny)})
    app=create_app(cfg)
    acceptance_passed=True
    with TestClient(app) as client:
        route='/api/v1/auth/login' if client.get('/api/v1/auth/status').json()['initialized'] else '/api/v1/auth/setup'
        auth=client.post(route,json={'username':'local-image-acceptance','password':'local-only-test-identity'});auth.raise_for_status()
        user_id=auth.json()['id']
        for tag in (['road','trail'] if args.sample=='all' else [args.sample]):
            expected=json.loads((ROOT/'private/screenshot-baselines'/f'{tag}-expected.json').read_text())
            source=Path(expected['source_path'])
            if hashlib.sha256(source.read_bytes()).hexdigest()!=expected['source_sha256']:raise ValueError('原图已变化，请重新核对基准')
            width,height=Image.open(source).size
            # Authorized full screenshot scope; skip account/header/map at top where no run metrics occur.
            region={'x':0,'y':1460,'width':width,'height':height-1460}
            result_file=folder/f'{tag}-{VERSION}.json'
            job=json.loads(result_file.read_text()) if result_file.exists() else {'cache_key':expected['source_sha256']+VERSION,'region':region,'tiles':tile_regions(region,2000,200),'lease_until':0}
            if job['cache_key']!=expected['source_sha256']+VERSION:raise ValueError('验收缓存不匹配')
            if args.refresh_tile is not None:
                index=args.refresh_tile
                if not 0<=index<len(job['tiles']):raise ValueError('分段超出范围')
                if job['tiles'][index].get('attempts',1)>=cfg.ai_max_retries+1:raise ValueError('该段达到重试上限')
                raw_file=folder/f'{tag}-{VERSION}-{index}-raw.json'
                if raw_file.exists():raw_file.rename(raw_file.with_name(raw_file.stem+f'-previous-{job["tiles"][index].get("attempts",1)}.json'))
                job['tiles'][index]['state']='pending'
            for index,tile in enumerate(job['tiles']):
                if tile['state']=='done':
                    cached=json.loads((folder/f'{tag}-{VERSION}-{index}-raw.json').read_text())
                    tile['readings']=validate_readings(cached['content'],tile['rect'],index)
                    continue
                raw_file=folder/f'{tag}-{VERSION}-{index}-raw.json'
                if raw_file.exists():
                    cached=json.loads(raw_file.read_text())
                    if cached['finish']=='stop':
                        try:
                            tile['readings']=validate_readings(cached['content'],tile['rect'],index);tile['state']='done'
                            result_file.write_text(json.dumps(job,ensure_ascii=False,indent=2))
                            print(tag,index+1,'复核已有返回，不重新外发',flush=True);continue
                        except Exception:
                            if not args.retry_failed or tile.get('attempts',1)>=cfg.ai_max_retries+1:
                                print(tag,index+1,'已有返回仍不合格；保留失败并处理其他段',flush=True);continue
                            backup=raw_file.with_name(raw_file.stem+f'-rejected-{tile.get("attempts",1)}.json')
                            raw_file.rename(backup)
                            print(tag,index+1,'显式重试，原返回已保留',flush=True)
                with Session(app.state.engine) as session:
                    try:call_id=reserve(session,cfg,user_id,None,task_kind='vision_acceptance')
                    except HTTPException:
                        print('已达到本次0.5元预算预留限制；保留进度，未确认训练。',flush=True);return 1
                    tile['attempts']=tile.get('attempts',0)+1
                    tile['state']='processing';result_file.write_text(json.dumps(job,ensure_ascii=False,indent=2))
                    try:
                        content,usage,finish=DeepSeekVision(cfg).read(crop_jpeg(cfg,SimpleNamespace(storage_name=source.name),tile['rect']))
                        settle(session,cfg,call_id,usage)
                        (folder/f'{tag}-{VERSION}-{index}-raw.json').write_text(json.dumps({'content':content,'finish':finish},ensure_ascii=False))
                        if finish!='stop':raise ValueError('输出截断')
                        tile['readings']=validate_readings(content,tile['rect'],index);tile['state']='done'
                    except Exception as exc:
                        tile['state']='failed';tile['error']='接口或结构校验失败；私有结果待核对'
                        result_file.write_text(json.dumps(job,ensure_ascii=False,indent=2))
                        print(tag,index+1,'未通过，不自动重试；继续其他段',flush=True);continue
                result_file.write_text(json.dumps(job,ensure_ascii=False,indent=2))
                print(tag,f'{index+1}/{len(job["tiles"])} 段完成',flush=True)
            result_file.write_text(json.dumps(job,ensure_ascii=False,indent=2))
            summary=summarize(SimpleNamespace(**job));report=compare(expected,summary['readings'])
            (folder/f'{tag}-summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2))
            (folder/f'{tag}-comparison.json').write_text(json.dumps(report,ensure_ascii=False,indent=2))
            acceptance_passed &= summary['complete'] and report['complete']
            print(tag,'人工基准通过',report['passed'],'/',report['count'],flush=True)
        print('本轮分段处理结束（报告保留失败/冲突）；结果需人工核对，未写入正式训练库。',flush=True)
    return 0 if acceptance_passed else 1

if __name__=='__main__':raise SystemExit(main())
