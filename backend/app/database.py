from pathlib import Path
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

def create_database(url: str):
    if url.startswith('sqlite:///'):
        Path(url[len('sqlite:///'):]).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, connect_args={'check_same_thread': False} if url.startswith('sqlite') else {})
    if url.startswith('sqlite'):
        @event.listens_for(engine, 'connect')
        def sqlite_settings(connection, _):
            connection.execute('PRAGMA foreign_keys=ON')
            connection.execute('PRAGMA busy_timeout=5000')
            connection.execute('PRAGMA journal_mode=WAL')
    return engine, sessionmaker(engine, expire_on_commit=False)
