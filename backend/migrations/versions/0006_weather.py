"""Private, revisioned city selection and weather snapshot."""
from alembic import op
import sqlalchemy as sa
revision='0006'
down_revision='0005'
branch_labels=None
depends_on=None

def upgrade():
    op.create_table('weather_states',sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),primary_key=True),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('payload',sa.JSON(),nullable=False))

def downgrade():op.drop_table('weather_states')
