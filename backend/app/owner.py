"""Operator-only first-account initialization. No passwords in CLI arguments."""
import argparse,getpass,sys
from sqlalchemy import select,func
from sqlalchemy.exc import IntegrityError
from pydantic import ValidationError
from .config import Settings
from .database import create_database
from .models import User
from .schemas import Credentials,Profile
from .security import hash_password

def initialize(session,username,password):
    try:credentials=Credentials(username=username,password=password)
    except ValidationError:raise ValueError('用户名或密码格式无效（密码需8–128字符）') from None
    if session.scalar(select(func.count()).select_from(User)):raise ValueError('账户已初始化，不覆盖现有账户')
    session.add(User(id='owner',username=credentials.username,password_hash=hash_password(credentials.password),profile=Profile().model_dump(mode='json'),availability=[]))
    try:session.commit()
    except IntegrityError:
        session.rollback();raise ValueError('账户已初始化，不覆盖现有账户') from None

def main():
    parser=argparse.ArgumentParser(description='本机初始化唯一账户（先迁移数据库）')
    parser.add_argument('--username',required=True);args=parser.parse_args()
    if not sys.stdin.isatty():parser.exit(1,'需要交互终端，密码不能通过命令参数或管道提供。\n')
    password=getpass.getpass('初始密码：');again=getpass.getpass('再次输入：')
    if password!=again:parser.exit(1,'两次密码不一致，未创建账户。\n')
    engine,factory=create_database(Settings().database_url)
    try:
        with factory() as session:initialize(session,args.username,password)
    except ValueError as exc:parser.exit(1,str(exc)+'\n')
    finally:engine.dispose()
    print('账户已创建，请在页面登录。')

if __name__=='__main__':main()
