"""Append-only human review of screenshot metrics, separate from raw extraction."""
from alembic import op
import sqlalchemy as sa
revision='0005'
down_revision='0004'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('import_metric_reviews',sa.Column('id',sa.String(),primary_key=True),sa.Column('import_id',sa.String(),sa.ForeignKey('imports.id'),nullable=False),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('draft_revision',sa.Integer(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('import_id','draft_revision'))

def downgrade():
    op.drop_table('import_metric_reviews')
