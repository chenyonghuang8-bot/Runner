"""Local SQLite+immutable-original backup; restores only into a new directory."""
import json,sqlite3,shutil
from contextlib import closing
from pathlib import Path
from .data_management import original_path,digest

FORMAT='runner-local-backup-1'

def verify(folder):
    folder=Path(folder);manifest=json.loads((folder/'manifest.json').read_text())
    if not isinstance(manifest,dict) or manifest.get('format')!=FORMAT:raise ValueError('不支持的备份格式')
    if not isinstance(manifest.get('files'),dict) or not all(isinstance(k,str) and isinstance(v,str) and len(v)==64 for k,v in manifest['files'].items()):raise ValueError('备份清单不完整')
    for name,expected in manifest['files'].items():
        p=Path(name)
        if p.is_absolute() or '..' in p.parts or not p.parts or p.parts[0] not in ('database.sqlite3','originals'):raise ValueError('备份路径不合法')
        path=folder/p
        if any(x.is_symlink() for x in [path,*path.parents[:len(p.parts)]]) or not path.is_file() or digest(path)!=expected:raise ValueError('备份文件缺失、链接或散列不符')
    entries=list(folder.rglob('*'))
    if any(p.is_symlink() for p in entries) or {p.relative_to(folder).as_posix() for p in entries if p.is_file()}!=set(manifest['files'])|{'manifest.json'}:raise ValueError('备份有未列出的文件或链接')
    if 'database.sqlite3' not in manifest['files']:raise ValueError('备份缺少数据库')
    with closing(sqlite3.connect((folder/'database.sqlite3').resolve().as_uri()+'?mode=ro&immutable=1',uri=True)) as c:
        if c.execute('PRAGMA quick_check').fetchall()!=[('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall():raise ValueError('数据库完整性检查失败')
        if c.execute('SELECT version_num FROM alembic_version').fetchone()[0]!=manifest.get('migration'):raise ValueError('数据库版本与清单不一致')
        rows=c.execute('SELECT storage_name,file_hash FROM imports').fetchall()
        if {n for n in manifest['files'] if n.startswith('originals/')}!={'originals/'+name for name,sha in rows} or any(manifest['files'].get('originals/'+name)!=sha for name,sha in rows):raise ValueError('原图清单与数据库不一致')
    return manifest

def create(database,images,output):
    output=Path(output)
    if output.exists():raise ValueError('目标目录已存在，不覆盖备份')
    database=Path(database).resolve()
    if not database.is_file():raise ValueError('找不到数据库')
    output.mkdir(parents=True);(output/'originals').mkdir()
    try:
        with closing(sqlite3.connect(database.as_uri()+'?mode=ro',uri=True)) as src,closing(sqlite3.connect(output/'database.sqlite3')) as dst:
            src.backup(dst)
            dst.execute('PRAGMA journal_mode=DELETE')
        with closing(sqlite3.connect(output/'database.sqlite3')) as db:
            originals=db.execute('SELECT storage_name,file_hash FROM imports').fetchall()
            version=db.execute('SELECT version_num FROM alembic_version').fetchone()[0]
        files={'database.sqlite3':digest(output/'database.sqlite3')}
        for name,expected in originals:
            p=original_path(images,name)
            if digest(p)!=expected:raise ValueError('原图散列与数据库不一致')
            shutil.copyfile(p,output/'originals'/name);files['originals/'+name]=digest(output/'originals'/name)
        (output/'manifest.json').write_text(json.dumps({'format':FORMAT,'migration':version,'files':files,'contains_credentials':True,'notes':['包含账户密码散列、原图和个人健康资料；妥善保存，勿提交或公开。','不包含.env或第三方密钥；恢复后须另外配置环境。']},ensure_ascii=False,indent=2))
        verify(output);return len(originals)
    except BaseException:
        shutil.rmtree(output);raise

def restore(backup,output):
    verify(backup);output=Path(output)
    if output.exists():raise ValueError('恢复目标已存在，只能恢复到新目录')
    output.mkdir(parents=True)
    try:
        shutil.copyfile(Path(backup)/'database.sqlite3',output/'runner.db')
        shutil.copytree(Path(backup)/'originals',output/'private')
        # Recovered copies do not preserve authenticated sessions or active request leases.
        with closing(sqlite3.connect(output/'runner.db')) as c:
            c.execute('DELETE FROM sessions');c.execute('UPDATE recognitions SET lease_token=NULL,lease_until=0')
            c.execute("UPDATE notification_jobs SET state='cancelled',reason='恢复副本，重新核对提醒' WHERE state='pending'")
            c.execute("UPDATE notification_settings SET payload=json_set(payload,'$.enabled',json('false'))")
            c.commit()
        return output
    except BaseException:
        shutil.rmtree(output);raise
