from alembic import context
from app.config import Settings
from app.database import create_database
from app.models import Base

engine, _ = create_database(Settings().database_url)
with engine.connect() as connection:
    context.configure(connection=connection, target_metadata=Base.metadata, render_as_batch=True)
    with context.begin_transaction():
        context.run_migrations()
