#!/usr/bin/env python3
"""Home HTTPS endpoint; bind only an explicitly selected RFC1918 IPv4 address."""
import argparse,ipaddress,json,os,secrets,subprocess,sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'backend'))
TLS=ROOT/'private/home-tls'

def validate_ip(value):
    ip=ipaddress.IPv4Address(value)
    if not any(ip in ipaddress.ip_network(n) for n in ('10.0.0.0/8','172.16.0.0/12','192.168.0.0/16')):raise ValueError('只允许家中局域网IPv4地址')
    return str(ip)

def prepare(ip):
    ip=validate_ip(ip);TLS.mkdir(parents=True,exist_ok=True);TLS.chmod(0o700)
    def openssl(*args):subprocess.run(['openssl',*args],check=True,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    if not (TLS/'ca.key').exists() and not (TLS/'ca.crt').exists():
        openssl('req','-x509','-newkey','rsa:3072','-nodes','-keyout',str(TLS/'ca.key'),'-out',str(TLS/'ca.crt'),'-days','3650','-subj','/CN=Runner Home Local CA','-addext','basicConstraints=critical,CA:TRUE,pathlen:0','-addext','keyUsage=critical,keyCertSign,cRLSign')
    if not (TLS/'ca.key').exists() or not (TLS/'ca.crt').exists():raise ValueError('CA文件不完整，请勿自动替换已有受信证书')
    folder=TLS/ip;folder.mkdir(exist_ok=True);folder.chmod(0o700)
    # Existing server keys stay stable; expired certificates must be renewed explicitly.
    if not (folder/'server.key').exists():
        openssl('req','-newkey','rsa:2048','-nodes','-keyout',str(folder/'server.key'),'-out',str(folder/'server.csr'),'-subj','/CN=Runner Home')
        (folder/'extensions.cnf').write_text(f'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature,keyEncipherment\nextendedKeyUsage=serverAuth\nsubjectAltName=IP:{ip}\n')
        openssl('x509','-req','-in',str(folder/'server.csr'),'-CA',str(TLS/'ca.crt'),'-CAkey',str(TLS/'ca.key'),'-set_serial',str(secrets.randbits(128)),'-out',str(folder/'server.crt'),'-days','365','-sha256','-extfile',str(folder/'extensions.cnf'))
    openssl('verify','-CAfile',str(TLS/'ca.crt'),'-verify_ip',ip,str(folder/'server.crt'))
    openssl('x509','-in',str(folder/'server.crt'),'-checkend','86400','-noout')
    openssl('x509','-in',str(TLS/'ca.crt'),'-outform','DER','-out',str(TLS/'runner-home-ca.cer'))
    if not (TLS/'session-secret').exists():(TLS/'session-secret').write_text(secrets.token_urlsafe(48))
    for key in TLS.rglob('*'):
        if key.is_file():key.chmod(0o600)
    return folder

def home_app(settings,dist):
    from app.main import create_app
    from starlette.staticfiles import StaticFiles
    dist=Path(dist)
    if not (dist/'index.html').is_file():raise ValueError('请先构建前端')
    app=create_app(settings)
    app.mount('/',StaticFiles(directory=dist,html=True,follow_symlink=False),name='home-ui')
    return app

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','serve']);parser.add_argument('--ip',required=True);parser.add_argument('--port',type=int,default=8443);args=parser.parse_args()
    try:
        if not 1024<=args.port<=65535:raise ValueError('端口需1024–65535')
        os.umask(0o077);ip=validate_ip(args.ip);folder=prepare(ip)
        if args.action=='prepare':
            print('本地HTTPS证书已准备，私钥未外发。');print('安卓需手动安装公开CA证书：'+str(TLS/'runner-home-ca.cer'));return
        from app.config import Settings
        import uvicorn
        cfg=Settings(app_env='production',app_public_url=f'https://{ip}:{args.port}',session_secret=(TLS/'session-secret').read_text())
        # Migrations use the same environment-derived data/AI settings, without changing .env.
        from alembic import command
        from alembic.config import Config
        command.upgrade(Config(str(ROOT/'backend/alembic.ini')),'head')
        app=home_app(cfg,ROOT/'frontend/dist')
        print(f'家中HTTPS入口：https://{ip}:{args.port}；Ctrl+C停止。')
        uvicorn.run(app,host=ip,port=args.port,ssl_keyfile=str(folder/'server.key'),ssl_certfile=str(folder/'server.crt'),proxy_headers=False,access_log=False)
    except (ValueError,OSError,subprocess.CalledProcessError):
        parser.exit(1,'启动失败：检查局域网IP、前端构建、端口及本地证书完整性；未输出密钥。\n')

if __name__=='__main__':main()
