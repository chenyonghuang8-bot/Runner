"""Durable coach exchanges with per-user request idempotency."""
from alembic import op
import sqlalchemy as sa
revision='0004'
down_revision='0003'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('coach_turns',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('request_id',sa.String(),nullable=False),sa.Column('message',sa.Text(),nullable=False),sa.Column('response',sa.JSON(),nullable=False),sa.Column('state',sa.String(),nullable=False),sa.Column('provider',sa.String(),nullable=False),sa.Column('facts_revision',sa.Integer(),nullable=False),sa.Column('error',sa.Text(),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('user_id','request_id'))

def downgrade():
    op.drop_table('coach_turns')
