"""Durable screenshot recognition and conservative AI budget ledger."""
from alembic import op
import sqlalchemy as sa

revision = '0002'
down_revision = '0001'
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('recognitions', sa.Column('import_id',sa.String(),sa.ForeignKey('imports.id'),primary_key=True),sa.Column('cache_key',sa.String(),nullable=False),sa.Column('region',sa.JSON(),nullable=False),sa.Column('tiles',sa.JSON(),nullable=False),sa.Column('lease_token',sa.String()),sa.Column('lease_until',sa.Integer(),nullable=False),sa.Column('updated_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('ai_budgets',sa.Column('month',sa.String(),primary_key=True),sa.Column('committed_micro',sa.Integer(),nullable=False),sa.Column('calls',sa.Integer(),nullable=False))
    op.create_table('image_tile_cache',sa.Column('key',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('readings',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('ai_calls',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('import_id',sa.String(),sa.ForeignKey('imports.id'),nullable=False),sa.Column('month',sa.String(),nullable=False),sa.Column('reserved_micro',sa.Integer(),nullable=False),sa.Column('charged_micro',sa.Integer()),sa.Column('usage',sa.JSON(),nullable=False),sa.Column('status',sa.String(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))

def downgrade():
    for table in ('ai_calls','image_tile_cache','ai_budgets','recognitions'):
        op.drop_table(table)
