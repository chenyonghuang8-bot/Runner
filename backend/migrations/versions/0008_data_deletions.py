"""Deletion confirmations and retryable original-file cleanup."""
from alembic import op
import sqlalchemy as sa
revision='0008'
down_revision='0007'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('data_deletions',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('fingerprint',sa.String(),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False),sa.Column('state',sa.String(),nullable=False),sa.Column('pending_files',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    op.drop_table('data_deletions')
