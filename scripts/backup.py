"""Explicit local backup/verify/restore-to-new-directory CLI."""
import argparse,sys,sqlite3
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'backend'))
from app.backup import create,verify,restore

def main():
    parser=argparse.ArgumentParser(description='Runner 本机备份；不读取.env，不覆盖现有目录')
    sub=parser.add_subparsers(dest='action',required=True)
    c=sub.add_parser('create');c.add_argument('--database',required=True);c.add_argument('--images',required=True);c.add_argument('--output',required=True)
    v=sub.add_parser('verify');v.add_argument('folder')
    r=sub.add_parser('restore-copy');r.add_argument('folder');r.add_argument('--output',required=True)
    a=parser.parse_args()
    try:
        if a.action=='create':print('备份完成，原图数量：',create(a.database,a.images,a.output))
        elif a.action=='verify':verify(a.folder);print('文件散列、原图清单和数据库完整性通过')
        else:restore(a.folder,a.output);print('已恢复到新目录；会话与识别租约已清除，提醒关闭；未改原数据库')
        return 0
    except (ValueError,OSError,sqlite3.Error) as e:print('操作未完成：',str(e));return 1

if __name__=='__main__':raise SystemExit(main())
