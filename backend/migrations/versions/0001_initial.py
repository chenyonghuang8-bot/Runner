"""Initial M1 schema."""
from alembic import op
import sqlalchemy as sa

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None

def upgrade():
    op.create_table('users',sa.Column('id',sa.String(),primary_key=True),sa.Column('username',sa.String(),unique=True,nullable=False),sa.Column('password_hash',sa.Text(),nullable=False),sa.Column('profile',sa.JSON(),nullable=False),sa.Column('availability',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_table('sessions',sa.Column('token_hash',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id',ondelete='CASCADE'),nullable=False),sa.Column('csrf_token',sa.String(),nullable=False),sa.Column('expires_at',sa.DateTime(timezone=True),nullable=False))
    for name in ('workouts','checkins'):
        columns=[sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('payload',sa.JSON(),nullable=False),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False)]
        if name=='workouts':columns.append(sa.Column('revision',sa.Integer(),nullable=False))
        op.create_table(name,*columns)
    op.create_table('imports',sa.Column('id',sa.String(),primary_key=True),sa.Column('user_id',sa.String(),sa.ForeignKey('users.id'),nullable=False),sa.Column('file_hash',sa.String(),nullable=False),sa.Column('original_name',sa.String(),nullable=False),sa.Column('storage_name',sa.String(),unique=True,nullable=False),sa.Column('mime',sa.String(),nullable=False),sa.Column('width',sa.Integer(),nullable=False),sa.Column('height',sa.Integer(),nullable=False),sa.Column('status',sa.String(),nullable=False),sa.Column('fields',sa.JSON(),nullable=False),sa.Column('revision',sa.Integer(),nullable=False),sa.Column('workout_id',sa.String(),sa.ForeignKey('workouts.id')),sa.Column('created_at',sa.DateTime(timezone=True),nullable=False))
    op.create_index('ix_imports_file_hash','imports',['file_hash'])

def downgrade():
    for name in ('imports','checkins','workouts','sessions','users'):
        op.drop_table(name)
