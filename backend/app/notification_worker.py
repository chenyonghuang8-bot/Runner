"""Explicit local simulation worker; cannot send network notifications."""
import argparse,time
from sqlalchemy import select
from .config import Settings
from .database import create_database
from .models import User
from .notifications import simulate,Simulator

def main():
    parser=argparse.ArgumentParser(description='Runner 本地模拟提醒，不发送微信')
    parser.add_argument('--simulate',required=True,action='store_true',help='明确仅使用本地模拟')
    parser.add_argument('--once',action='store_true',help='核对并处理一次后退出')
    args=parser.parse_args();engine,factory=create_database(Settings().database_url)
    print('本地模拟提醒进程；没有连接微信渠道。',flush=True)
    try:
        while True:
            with factory() as session:
                for user in session.scalars(select(User)).all():
                    result=simulate(session,user,Simulator())
                    if result['simulated_count']:print('已记录本地模拟提醒：',result['simulated_count'],flush=True)
            if args.once:break
            time.sleep(30)
    except KeyboardInterrupt:pass
    finally:engine.dispose()

if __name__=='__main__':main()
