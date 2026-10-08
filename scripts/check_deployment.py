#!/usr/bin/env python3
"""Static deployment preflight. Does not start services, print secrets, or call APIs."""
import argparse,re,sys,shutil
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
import yaml
from app.config import Settings

def raw_env(path):
    result={}
    for line in path.read_text().splitlines():
        if not line.strip() or line.lstrip().startswith('#'):continue
        if '=' not in line:raise ValueError('环境文件需使用 KEY=value 格式')
        key,value=line.split('=',1)
        if not re.fullmatch(r'[A-Z][A-Z0-9_]*',key) or key in result:raise ValueError('环境文件含重复或不合法变量名')
        result[key.lower()]=value
    return result

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--templates',action='store_true');args=parser.parse_args()
    try:
        suffix='.example' if args.templates else ''
        site=raw_env(ROOT/'deploy'/('site.env'+suffix));runtime=raw_env(ROOT/'deploy'/('runtime.env'+suffix))
        domain=site.get('runner_domain','')
        if not re.fullmatch(r'(?=.{1,253}$)(?:[a-zA-Z0-9](?:[a-zA-Z0-9-]{0,61}[a-zA-Z0-9])?\.)+[a-zA-Z]{2,63}',domain):raise ValueError('RUNNER_DOMAIN需为域名，不含协议、路径或端口')
        if not args.templates and (domain=='example.com' or domain.endswith('.example.com')):raise ValueError('请替换示例域名')
        forbidden=('app_env','app_public_url','database_url','private_storage_dir')
        if any(k in runtime for k in forbidden):raise ValueError('运行环境文件不要覆盖Compose固定的生产环境/地址/数据路径')
        if args.templates:runtime['session_secret']='synthetic-template-validation-only-32-characters'
        Settings(_env_file=None,**runtime,app_env='production',app_public_url='https://'+domain,database_url='sqlite:////data/runner.db',private_storage_dir='/data/private')
        compose=yaml.safe_load((ROOT/'deploy/compose.yaml').read_text())
        if compose['services']['backend'].get('ports'):raise ValueError('后端不能发布公网端口')
        for service in compose['services'].values():
            build=service['build'];path=(ROOT/'deploy'/build['context']/build['dockerfile']).resolve()
            if not path.is_file():raise ValueError('缺少构建文件')
        print('模板静态检查通过。' if args.templates else '部署环境静态检查通过（未输出密钥）。')
        print('Docker工具：'+('已找到，仍需实际构建/启动验收。' if shutil.which('docker') else '未安装，尚未验证Compose、容器与HTTPS。'))
        print('本检查未启动服务、申请证书、迁移数据库或外发资料。')
    except (OSError,ValueError,KeyError,yaml.YAMLError):
        print('静态检查失败：请检查配置文件存在、域名、会话配置、AI密钥/模型及构建路径。未输出变量值。',file=sys.stderr);return 1
    return 0

if __name__=='__main__':raise SystemExit(main())
