from datetime import date, datetime, timezone
from typing import Literal
from zoneinfo import ZoneInfo
from pydantic import BaseModel, Field, ConfigDict, field_validator, model_validator

class StrictModel(BaseModel):
    model_config = ConfigDict(extra='forbid')

class Credentials(StrictModel):
    username: str = Field(min_length=1, max_length=64, pattern=r'^[\w.-]+$')
    password: str = Field(min_length=8, max_length=128)

class Profile(StrictModel):
    display_name: str = Field(default='跑者', min_length=1, max_length=40)
    age: int | None = Field(default=None, ge=1, le=120)
    sex: Literal['male', 'female', 'unspecified'] = 'unspecified'
    running_months: int | None = Field(default=None, ge=0, le=1200)
    runs_per_week: int = Field(default=3, ge=1, le=7)
    city: str = Field(default='', max_length=80)
    preferred_time: str = Field(default='20:00', pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    timezone: str = 'Asia/Shanghai'
    goal_date: date | None = None
    goal_duration_seconds: int | None = Field(default=None, ge=1800, le=86400)
    running_permission: Literal['unknown','user_reports_doctor_allows'] = 'unknown'
    medical_review_date: date | None = None
    health_notes: str = Field(default='', max_length=2000)
    doctor_restrictions: str = Field(default='', max_length=2000)
    strength_experience: Literal['unknown','none','some','experienced'] = 'unknown'
    strength_equipment: list[Literal['bodyweight','resistance_band','dumbbell','gym']] = Field(default_factory=list,max_length=4)
    strength_max_minutes: int | None = Field(default=None,ge=5,le=120)
    band_resistance: str = Field(default='',max_length=100)
    familiar_exercises: list[str] = Field(default_factory=list,max_length=30)
    same_day_strength: bool = False
    @model_validator(mode='after')
    def medical_review_valid(self):
        if self.medical_review_date and self.medical_review_date>datetime.now(ZoneInfo(self.timezone)).date():
            raise ValueError('不能用未来日期解除旧疼痛限制；具体就诊日期未知可留空')
        return self
    @field_validator('timezone')
    @classmethod
    def timezone_valid(cls, value):
        ZoneInfo(value)
        return value

class Day(StrictModel):
    date: date
    available: bool
    duration_minutes: int = Field(default=60, ge=10, le=480)

class Availability(StrictModel):
    days: list[Day] = Field(max_length=366)
    @model_validator(mode='after')
    def unique_days(self):
        if len({d.date for d in self.days}) != len(self.days):
            raise ValueError('日程日期不可重复')
        return self

class WorkoutInput(StrictModel):
    date: date
    started_time: str | None = Field(default=None, pattern=r'^([01]\d|2[0-3]):[0-5]\d$')
    sport: Literal['road_run', 'trail_run', 'treadmill'] = 'road_run'
    session_context: Literal['unknown','easy','long','steady','race'] = 'unknown'
    title: str = Field(default='户外跑步', min_length=1, max_length=100)
    distance_m: int = Field(gt=0, le=500000)
    duration_seconds: int = Field(gt=0, le=172800)
    avg_heart_rate: int | None = Field(default=None, ge=30, le=250)
    max_heart_rate: int | None = Field(default=None, ge=30, le=250)
    ascent_m: float | None = Field(default=None, ge=0, le=30000)
    effort: int | None = Field(default=None, ge=0, le=10)
    pain_notes: str = Field(default='', max_length=2000)
    notes: str = Field(default='', max_length=2000)
    @model_validator(mode='after')
    def heart_rate_order(self):
        if self.avg_heart_rate and self.max_heart_rate and self.avg_heart_rate > self.max_heart_rate:
            raise ValueError('平均心率不能大于最大心率')
        return self

class CheckinInput(StrictModel):
    date: date
    observed_at: datetime | None = None
    observed_time: str | None = Field(default=None,pattern=r'^([01]\d|2[0-3]):[0-5]\d$',exclude=True)
    related_training_type: Literal['run','strength'] | None = None
    related_training_id: str | None = Field(default=None,min_length=1,max_length=80)
    feedback_phase: Literal['unknown','before','during','after','next_day'] = 'unknown'
    @field_validator('observed_at')
    @classmethod
    def aware_observation(cls,value):
        if value is not None:
            if value.utcoffset() is None:raise ValueError('反馈发生时间必须包含时区偏移')
            return value.astimezone(timezone.utc)
        return value
    @model_validator(mode='after')
    def paired_training_reference(self):
        if self.observed_at is not None and self.observed_time is not None:raise ValueError('发生时间和本地时刻不能同时填写')
        if (self.related_training_type is None)!=(self.related_training_id is None):raise ValueError('关联训练类型与 ID 必须一起填写')
        return self
    training_readiness: Literal['unknown','ready','reduce','rest'] = 'unknown'
    soreness_level: int | None = Field(default=None,ge=0,le=10)
    soreness_location: str = Field(default='',max_length=100)
    soreness_timing: Literal['none','during','after','next_day','unknown'] = 'unknown'
    soreness_tolerability: Literal['unknown','tolerable','not_tolerable'] = 'unknown'
    soreness_trend: Literal['unknown','improving','stable','worsening'] = 'unknown'
    function_affected: bool | None = None
    illness_notes: str = Field(default='',max_length=1000)
    fatigue: int = Field(ge=0, le=10)
    sleep_hours: float | None = Field(default=None, ge=0, le=24)
    pain_level: int | None = Field(default=None, ge=0, le=10)
    pain_location: str = Field(default='', max_length=100)
    pain_timing: Literal['none', 'during', 'after', 'daily', 'unknown'] = 'none'
    notes: str = Field(default='', max_length=2000)

class CheckinRecord(CheckinInput):
    observation_timezone: str | None = None

class DraftFields(StrictModel):
    expected_revision: int = Field(ge=1)
    workout: WorkoutInput

class Confirmation(StrictModel):
    expected_revision: int = Field(ge=1)

class Message(StrictModel):
    message: str = Field(min_length=1, max_length=4000)


class ExerciseSet(StrictModel):
    exercise_id: str = Field(min_length=1,max_length=80)
    repetitions: int | None = Field(default=None,ge=0,le=500)
    seconds: int | None = Field(default=None,ge=0,le=3600)
    weight_kg: float | None = Field(default=None,ge=0,le=500)
    resistance: str = Field(default='',max_length=100)
    effort: int | None = Field(default=None,ge=0,le=10)
    notes: str = Field(default='',max_length=500)

class StrengthInput(StrictModel):
    date: date
    title: str = Field(default='力量训练',min_length=1,max_length=100)
    duration_seconds: int = Field(ge=0,le=172800)
    status: Literal['completed','partial','skipped'] = 'completed'
    exercises: list[ExerciseSet] = Field(default_factory=list,max_length=200)
    effort: int | None = Field(default=None,ge=0,le=10)
    pain_notes: str = Field(default='',max_length=2000)
    notes: str = Field(default='',max_length=2000)
    @model_validator(mode='after')
    def completed_duration(self):
        if self.status!='skipped' and self.duration_seconds==0:raise ValueError('完成或部分完成需填写实际时长')
        if self.status=='skipped' and self.duration_seconds!=0:raise ValueError('跳过训练的实际时长应为零')
        return self
