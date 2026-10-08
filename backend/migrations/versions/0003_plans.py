"""Strength facts and atomic whole-plan versions."""
from alembic import op
import sqlalchemy as sa
revision='0003'
down_revision='0002'
branch_labels=None
depends_on=None

def upgrade():
    with op.batch_alter_table('ai_calls') as batch:
        batch.alter_column('import_id',existing_type=sa.String(),nullable=True)
        batch.add_column(sa.Column('task_kind',sa.String(),nullable=False,server_default='vision'))
    op.add_column('users',sa.Column('facts_revision',sa.Integer(),nullable=False,server_default='1'))
    op.add_column('users',sa.Column('plan_version',sa.Integer(),nullable=False,server_default='0'))
    op.create_table('strength_workouts',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    common=lambda:[sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('facts_revision',sa.Integer(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False)]
    op.create_table('plan_versions',*common(),sa.Column('version',sa.Integer(),nullable=False),sa.Column('parent_version',sa.Integer(),nullable=False),sa.UniqueConstraint('user_id','version'))
    op.create_table('plan_proposals',*common(),sa.Column('base_version',sa.Integer(),nullable=False),sa.Column('state',sa.String(),nullable=False),sa.Column('applied_version',sa.Integer()),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('plan_outbox',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('plan_version',sa.Integer(),nullable=False),sa.Column('state',sa.String(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False),sa.UniqueConstraint('user_id','plan_version'))

def downgrade():
    if op.get_bind().execute(sa.text('SELECT COUNT(*) FROM ai_calls WHERE import_id IS NULL')).scalar():
        raise RuntimeError('存在教练预算记录，拒绝会丢失该记录的降级；请先备份并人工处理。')
    for table in ('plan_outbox','plan_proposals','plan_versions','strength_workouts'):op.drop_table(table)
    with op.batch_alter_table('ai_calls') as batch:
        batch.drop_column('task_kind')
        batch.alter_column('import_id',existing_type=sa.String(),nullable=False)
    op.drop_column('users','plan_version');op.drop_column('users','facts_revision')
