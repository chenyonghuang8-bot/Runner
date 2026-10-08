from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import String, Text, DateTime, ForeignKey, JSON, Integer, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

def uid():
    return str(uuid4())

def now():
    return datetime.now(timezone.utc)

class Base(DeclarativeBase):
    pass

class User(Base):
    __tablename__ = 'users'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    username: Mapped[str] = mapped_column(String, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    profile: Mapped[dict] = mapped_column(JSON, default=dict)
    availability: Mapped[list] = mapped_column(JSON, default=list)
    facts_revision: Mapped[int] = mapped_column(Integer, default=1)
    plan_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Session(Base):
    __tablename__ = 'sessions'
    token_hash: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id', ondelete='CASCADE'))
    csrf_token: Mapped[str] = mapped_column(String)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))

class Workout(Base):
    __tablename__ = 'workouts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    payload: Mapped[dict] = mapped_column(JSON)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Checkin(Base):
    __tablename__ = 'checkins'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class ImportDraft(Base):
    __tablename__ = 'imports'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    file_hash: Mapped[str] = mapped_column(String, index=True)
    original_name: Mapped[str] = mapped_column(String)
    storage_name: Mapped[str] = mapped_column(String, unique=True)
    mime: Mapped[str] = mapped_column(String)
    width: Mapped[int] = mapped_column(Integer)
    height: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String, default='needs_review')
    fields: Mapped[dict] = mapped_column(JSON, default=dict)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    workout_id: Mapped[str | None] = mapped_column(ForeignKey('workouts.id'), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class Recognition(Base):
    __tablename__ = 'recognitions'
    import_id: Mapped[str] = mapped_column(ForeignKey('imports.id'), primary_key=True)
    cache_key: Mapped[str] = mapped_column(String)
    region: Mapped[dict] = mapped_column(JSON)
    tiles: Mapped[list] = mapped_column(JSON)
    # Results stay separate from user-edited fields. The browser requests one durable step at a time.
    lease_token: Mapped[str | None] = mapped_column(String, nullable=True)
    lease_until: Mapped[int] = mapped_column(Integer, default=0)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AIBudget(Base):
    __tablename__ = 'ai_budgets'
    month: Mapped[str] = mapped_column(String, primary_key=True)
    # Micro-CNY integers avoid rounding and allow an atomic account-wide cap.
    committed_micro: Mapped[int] = mapped_column(Integer, default=0)
    calls: Mapped[int] = mapped_column(Integer, default=0)

class ImageTileCache(Base):
    __tablename__ = 'image_tile_cache'
    key: Mapped[str] = mapped_column(String, primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    readings: Mapped[list] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class AICall(Base):
    __tablename__ = 'ai_calls'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    import_id: Mapped[str | None] = mapped_column(ForeignKey('imports.id'), nullable=True)
    task_kind: Mapped[str] = mapped_column(String, default='vision')
    month: Mapped[str] = mapped_column(String)
    reserved_micro: Mapped[int] = mapped_column(Integer)
    charged_micro: Mapped[int | None] = mapped_column(Integer, nullable=True)
    usage: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String, default='reserved')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class StrengthWorkout(Base):
    __tablename__ = 'strength_workouts'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class PlanVersion(Base):
    __tablename__ = 'plan_versions'
    __table_args__ = (UniqueConstraint('user_id','version'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    version: Mapped[int] = mapped_column(Integer)
    facts_revision: Mapped[int] = mapped_column(Integer)
    parent_version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class PlanProposal(Base):
    __tablename__ = 'plan_proposals'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    base_version: Mapped[int] = mapped_column(Integer)
    facts_revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    state: Mapped[str] = mapped_column(String, default='pending')
    applied_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class PlanOutbox(Base):
    __tablename__ = 'plan_outbox'
    __table_args__ = (UniqueConstraint('user_id','plan_version'),)
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    plan_version: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String, default='pending')
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)

class CoachTurn(Base):
    __tablename__ = 'coach_turns'
    __table_args__ = (UniqueConstraint('user_id','request_id'),)
    id: Mapped[str] = mapped_column(String,primary_key=True,default=uid)
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    request_id: Mapped[str] = mapped_column(String)
    message: Mapped[str] = mapped_column(Text)
    response: Mapped[dict] = mapped_column(JSON,default=dict)
    state: Mapped[str] = mapped_column(String)
    provider: Mapped[str] = mapped_column(String)
    facts_revision: Mapped[int] = mapped_column(Integer)
    error: Mapped[str] = mapped_column(Text,default='')
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True),default=now)

class ImportMetricReview(Base):
    __tablename__ = 'import_metric_reviews'
    id: Mapped[str] = mapped_column(String, primary_key=True, default=uid)
    import_id: Mapped[str] = mapped_column(ForeignKey('imports.id'))
    user_id: Mapped[str] = mapped_column(ForeignKey('users.id'))
    draft_revision: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    __table_args__ = (UniqueConstraint('import_id','draft_revision'),)

class WeatherState(Base):
    __tablename__='weather_states'
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id'),primary_key=True)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    payload: Mapped[dict]=mapped_column(JSON,default=dict)

class NotificationSettings(Base):
    __tablename__='notification_settings'
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id'),primary_key=True)
    revision: Mapped[int]=mapped_column(Integer,default=1)
    payload: Mapped[dict]=mapped_column(JSON)

class NotificationJob(Base):
    __tablename__='notification_jobs'
    __table_args__=(UniqueConstraint('user_id','dedupe_key'),)
    id: Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id'))
    dedupe_key: Mapped[str]=mapped_column(String)
    plan_version: Mapped[int]=mapped_column(Integer)
    facts_revision: Mapped[int]=mapped_column(Integer)
    settings_revision: Mapped[int]=mapped_column(Integer)
    due_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    state: Mapped[str]=mapped_column(String,default='pending')
    attempts: Mapped[int]=mapped_column(Integer,default=0)
    processed_at: Mapped[datetime|None]=mapped_column(DateTime(timezone=True),nullable=True)
    payload: Mapped[dict]=mapped_column(JSON)
    reason: Mapped[str]=mapped_column(String,default='')
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)

class DataDeletion(Base):
    __tablename__='data_deletions'
    id: Mapped[str]=mapped_column(String,primary_key=True,default=uid)
    user_id: Mapped[str]=mapped_column(ForeignKey('users.id'))
    fingerprint: Mapped[str]=mapped_column(String)
    expires_at: Mapped[datetime]=mapped_column(DateTime(timezone=True))
    state: Mapped[str]=mapped_column(String,default='preview')
    pending_files: Mapped[list]=mapped_column(JSON,default=list)
    created_at: Mapped[datetime]=mapped_column(DateTime(timezone=True),default=now)
