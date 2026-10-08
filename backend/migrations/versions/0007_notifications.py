"""Local notification preferences and durable delivery jobs."""
from alembic import op
import sqlalchemy as sa
revision='0007'
down_revision='0006'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('notification_settings',sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),primary_key=True),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False))
    op.create_table('notification_jobs',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('dedupe_key',sa.String(),nullable=False),sa.Column('plan_version',sa.Integer(),nullable=False),sa.Column('facts_revision',sa.Integer(),nullable=False),sa.Column('settings_revision',sa.Integer(),nullable=False),sa.Column('due_at',sa.DateTime(timezone=True),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('state',sa.String(),nullable=False),sa.Column('attempts',sa.Integer(),nullable=False),sa.Column('processed_at',sa.DateTime(timezone=True)),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('reason',sa.String(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('user_id','dedupe_key'))
    op.create_index('ix_notification_due','notification_jobs',['user_id','state','due_at'])

def downgrade():
    op.drop_table('notification_jobs');op.drop_table('notification_settings')
