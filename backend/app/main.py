import hashlib
import io
import secrets
import warnings
from collections import defaultdict, deque
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4
from fastapi import FastAPI, Depends, Request, Response, HTTPException, UploadFile, File
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse, FileResponse
from PIL import Image, ImageOps, UnidentifiedImageError
from sqlalchemy import select, func, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DBSession
from .config import Settings
from .database import create_database
from .models import User, Session, Workout, Checkin, ImportDraft, Recognition
from .schemas import Credentials, Profile, Availability, WorkoutInput, CheckinInput, CheckinRecord, DraftFields, Confirmation
from .security import hash_password, verify_password, token_hash

COOKIE = 'runner_session'

def create_app(settings: Settings | None = None):
    cfg = settings or Settings()
    engine, session_factory = create_database(cfg.database_url)
    cfg.private_storage_dir.mkdir(parents=True, exist_ok=True)
    app = FastAPI(title='Runner API', version='0.1.0',docs_url=None if cfg.app_env=='production' else '/docs',redoc_url=None if cfg.app_env=='production' else '/redoc',openapi_url=None if cfg.app_env=='production' else '/openapi.json')
    if cfg.app_env=='production':
        from urllib.parse import urlsplit
        from starlette.middleware.trustedhost import TrustedHostMiddleware
        app.add_middleware(TrustedHostMiddleware,allowed_hosts=[urlsplit(cfg.app_public_url).hostname,'127.0.0.1'],www_redirect=False)
    app.state.settings = cfg
    app.state.engine = engine
    login_attempts = defaultdict(deque)

    def db():
        with session_factory() as session:
            yield session

    @app.middleware('http')
    async def request_context(request: Request, call_next):
        request.state.request_id = str(uuid4())
        # Browser writes must originate from this application (Vite proxy preserves Origin).
        origin = request.headers.get('origin')
        allowed = {cfg.app_public_url.rstrip('/')}
        if cfg.app_env != 'production':
            allowed |= {'http://127.0.0.1:5173', 'http://localhost:5173'}
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and origin and origin.rstrip('/') not in allowed:
            return JSONResponse(status_code=403, content={'code':'origin_rejected','message':'请求来源不被允许','field_errors':[], 'request_id':request.state.request_id})
        response = await call_next(request)
        response.headers['X-Request-ID'] = request.state.request_id
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Cache-Control'] = 'no-store'
        return response

    @app.exception_handler(HTTPException)
    async def http_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={'code':f'http_{exc.status_code}', 'message':str(exc.detail),'field_errors':[], 'request_id':request.state.request_id})

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        errors = [{'field':'.'.join(map(str, e['loc'][1:])), 'message':e['msg']} for e in exc.errors()]
        return JSONResponse(status_code=422, content={'code':'validation_error','message':'请检查填写内容', 'field_errors':errors,'request_id':request.state.request_id})

    def current(request: Request, session: DBSession = Depends(db)):
        token = request.cookies.get(COOKIE)
        record = session.get(Session, token_hash(token)) if token else None
        if not record:
            raise HTTPException(401, '请先登录')
        expiry = record.expires_at.replace(tzinfo=timezone.utc)
        if expiry <= datetime.now(timezone.utc):
            session.delete(record)
            session.commit()
            raise HTTPException(401, '登录已过期，请重新登录')
        if request.method not in ('GET', 'HEAD', 'OPTIONS') and not secrets.compare_digest(request.headers.get('x-csrf-token', ''), record.csrf_token):
            raise HTTPException(403, '会话校验失败，请刷新页面')
        user = session.get(User, record.user_id)
        if not user:
            raise HTTPException(401, '账户不存在')
        return user

    def issue_session(user: User, session: DBSession, response: Response):
        raw = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(24)
        session.add(Session(token_hash=token_hash(raw), user_id=user.id, csrf_token=csrf, expires_at=datetime.now(timezone.utc)+timedelta(days=7)))
        session.commit()
        response.set_cookie(COOKIE, raw, max_age=7*86400, httponly=True, samesite='strict', secure=cfg.app_env=='production', path='/')
        return {'id':user.id, 'username':user.username, 'csrf_token':csrf}

    def throttle(request):
        key = request.client.host if request.client else 'unknown'
        times = login_attempts[key]
        now = datetime.now(timezone.utc).timestamp()
        while times and times[0] < now-600:
            times.popleft()
        if len(times) >= 10:
            raise HTTPException(429, '尝试次数过多，请十分钟后重试')
        times.append(now)

    @app.get('/api/v1/health')
    def health():
        return {'status':'ok', 'stage':'M3-local', 'ai_provider':cfg.ai_provider, 'coach_available':True}

    @app.get('/api/v1/auth/status')
    def status(session: DBSession = Depends(db)):
        return {'initialized':bool(session.scalar(select(func.count()).select_from(User))), 'setup_allowed':cfg.app_env!='production'}

    @app.post('/api/v1/auth/setup', status_code=201)
    def setup(data: Credentials, request: Request, response: Response, session: DBSession = Depends(db)):
        if cfg.app_env == 'production':
            raise HTTPException(403, '生产环境请使用本机初始化命令')
        throttle(request)
        # A fixed primary key makes simultaneous bootstrap requests mutually exclusive.
        if session.scalar(select(func.count()).select_from(User)):
            raise HTTPException(409, '账户已初始化，请登录')
        user = User(id='owner', username=data.username, password_hash=hash_password(data.password), profile=Profile().model_dump(mode='json'))
        session.add(user)
        try:
            session.commit()
        except IntegrityError:
            session.rollback()
            raise HTTPException(409, '账户已初始化，请登录')
        return issue_session(user, session, response)

    @app.post('/api/v1/auth/login')
    def login(data: Credentials, request: Request, response: Response, session: DBSession = Depends(db)):
        throttle(request)
        user = session.scalar(select(User).where(User.username==data.username))
        if not user or not verify_password(data.password, user.password_hash):
            raise HTTPException(401, '用户名或密码不正确')
        return issue_session(user, session, response)

    @app.get('/api/v1/auth/me')
    def me(request: Request, user: User = Depends(current), session: DBSession = Depends(db)):
        record = session.get(Session, token_hash(request.cookies[COOKIE]))
        return {'id':user.id, 'username':user.username, 'csrf_token':record.csrf_token}

    @app.post('/api/v1/auth/logout')
    def logout(request: Request, response: Response, user: User = Depends(current), session: DBSession = Depends(db)):
        record = session.get(Session, token_hash(request.cookies[COOKIE]))
        session.delete(record)
        session.commit()
        response.delete_cookie(COOKIE, path='/')
        return {'ok':True}

    @app.get('/api/v1/profile')
    def profile(user: User = Depends(current)):
        return Profile.model_validate(user.profile).model_dump(mode='json')

    @app.patch('/api/v1/profile')
    def update_profile(data: Profile, user: User = Depends(current), session: DBSession = Depends(db)):
        user.profile = data.model_dump(mode='json')
        session.add(user)
        bump_facts(user,session)
        session.commit()
        return user.profile

    @app.get('/api/v1/availability')
    def availability(user: User = Depends(current)):
        return {'days':user.availability}

    @app.put('/api/v1/availability')
    def update_availability(data: Availability, user: User = Depends(current), session: DBSession = Depends(db)):
        user.availability = data.model_dump(mode='json')['days']
        session.add(user)
        bump_facts(user,session)
        session.commit()
        return {'days':user.availability}

    def bump_facts(user,session):
        from .models import PlanOutbox,NotificationJob
        session.execute(update(NotificationJob).where(NotificationJob.user_id==user.id,NotificationJob.state=='pending').values(state='cancelled',reason='事实已变化'))
        session.execute(update(PlanOutbox).where(PlanOutbox.user_id==user.id,PlanOutbox.state=='pending').values(state='cancelled'))
        session.execute(update(User).where(User.id==user.id).values(facts_revision=User.facts_revision+1))

    def workout_json(row):
        return {'id':row.id, **row.payload, 'revision':row.revision, 'created_at':row.created_at.isoformat(), 'pace_seconds_per_km':round(row.payload['duration_seconds']/(row.payload['distance_m']/1000)), 'pace_origin':'derived'}

    @app.get('/api/v1/workouts')
    def workouts(user: User = Depends(current), session: DBSession = Depends(db)):
        rows = session.scalars(select(Workout).where(Workout.user_id==user.id).order_by(Workout.created_at.desc())).all()
        return sorted([workout_json(r) for r in rows], key=lambda r:(r['date'], r.get('started_time') or '', r['created_at']), reverse=True)

    @app.post('/api/v1/workouts', status_code=201)
    def add_workout(data: WorkoutInput, user: User = Depends(current), session: DBSession = Depends(db)):
        row = Workout(user_id=user.id, payload=data.model_dump(mode='json'))
        session.add(row)
        bump_facts(user,session)
        session.commit()
        return workout_json(row)

    @app.get('/api/v1/checkins')
    def checkins(user: User = Depends(current), session: DBSession = Depends(db)):
        rows = session.scalars(select(Checkin).where(Checkin.user_id==user.id).order_by(Checkin.created_at.desc())).all()
        from .feedback import checkin_key
        return sorted([{'id':r.id, **CheckinRecord.model_validate(r.payload).model_dump(mode='json'), 'created_at':r.created_at.replace(tzinfo=timezone.utc).isoformat()} for r in rows],key=checkin_key,reverse=True)

    @app.post('/api/v1/checkins', status_code=201)
    def add_checkin(data: CheckinInput, user: User = Depends(current), session: DBSession = Depends(db)):
        if data.related_training_type=='strength':raise HTTPException(422,'当前身体反馈只关联跑步训练')
        from .feedback import validate_feedback
        validate_feedback(data,user,session)
        row = Checkin(user_id=user.id, payload={**data.model_dump(mode='json'),'observation_timezone':user.profile.get('timezone','Asia/Shanghai')})
        session.add(row)
        bump_facts(user,session)
        session.commit()
        return {'id':row.id, **row.payload}

    def import_json(row):
        from .recognition import summarize
        with session_factory() as detail_session:
            job = detail_session.get(Recognition,row.id)
            recognition = summarize(job)
            from .metric_reviews import history
            metric_reviews = history(detail_session,row,job)
            similar = [w.id for w in detail_session.scalars(select(Workout).where(Workout.user_id==row.user_id)) if w.id!=row.workout_id and row.fields.get('date')==w.payload.get('date') and abs(row.fields.get('distance_m',0)-w.payload.get('distance_m',0))<=20 and abs(row.fields.get('duration_seconds',0)-w.payload.get('duration_seconds',0))<=5]
        return {'id':row.id, 'original_name':row.original_name, 'width':row.width, 'height':row.height, 'status':row.status, 'fields':row.fields, 'revision':row.revision, 'workout_id':row.workout_id, 'image_url':f'/api/v1/imports/{row.id}/image', 'is_mock':cfg.ai_provider=='mock', 'recognition_performed':bool(recognition and recognition['done']), 'recognition':recognition, 'metric_reviews':metric_reviews, 'possible_duplicate_ids':similar}

    def own_import(id, user, session):
        row = session.get(ImportDraft, id)
        if not row or row.user_id != user.id:
            raise HTTPException(404, '找不到该截图草稿')
        return row

    @app.get('/api/v1/imports')
    def imports(user: User = Depends(current), session: DBSession = Depends(db)):
        rows = session.scalars(select(ImportDraft).where(ImportDraft.user_id==user.id).order_by(ImportDraft.created_at.desc())).all()
        return [import_json(r) for r in rows]

    @app.post('/api/v1/imports', status_code=201)
    async def upload(file: UploadFile = File(...), user: User = Depends(current), session: DBSession = Depends(db)):
        content = await file.read(cfg.import_max_file_mb*1024*1024+1)
        await file.close()
        if len(content) > cfg.import_max_file_mb*1024*1024:
            raise HTTPException(413, '图片过大，请选择不超过上传上限的原图')
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('error', Image.DecompressionBombWarning)
                image = Image.open(io.BytesIO(content))
                width, height = image.size
                fmt = image.format
                if width*height > cfg.import_max_pixels:
                    raise HTTPException(413, '图片像素超过安全处理上限')
                image.verify()
        except HTTPException:
            raise
        except (UnidentifiedImageError, OSError, SyntaxError, Image.DecompressionBombError, Image.DecompressionBombWarning):
            raise HTTPException(422, '文件不是可读取的图片')
        if fmt not in ('JPEG','PNG','WEBP'):
            raise HTTPException(422, '当前支持 JPEG、PNG 和 WebP')
        with Image.open(io.BytesIO(content)) as orientation_image:
            width,height = ImageOps.exif_transpose(orientation_image).size
        digest = hashlib.sha256(content).hexdigest()
        existing = session.scalar(select(ImportDraft).where(ImportDraft.user_id==user.id, ImportDraft.file_hash==digest))
        if existing:
            return {**import_json(existing), 'duplicate':True}
        suffix = {'JPEG':'.jpg', 'PNG':'.png', 'WEBP':'.webp'}[fmt]
        storage_name = str(uuid4())+suffix
        path = cfg.private_storage_dir/storage_name
        path.write_bytes(content)
        row = ImportDraft(user_id=user.id, file_hash=digest, original_name=Path(file.filename or 'image').name[:200], storage_name=storage_name, mime={'JPEG':'image/jpeg','PNG':'image/png','WEBP':'image/webp'}[fmt], width=width, height=height, fields={})
        session.add(row)
        try:
            session.commit()
        except Exception:
            session.rollback()
            path.unlink(missing_ok=True)
            raise
        return {**import_json(row), 'duplicate':False}

    @app.get('/api/v1/imports/{id}')
    def get_import(id: str, user: User = Depends(current), session: DBSession = Depends(db)):
        return import_json(own_import(id,user,session))

    @app.get('/api/v1/imports/{id}/image')
    def get_image(id: str, user: User = Depends(current), session: DBSession = Depends(db)):
        row = own_import(id,user,session)
        path = cfg.private_storage_dir/row.storage_name
        if not path.exists():
            raise HTTPException(404, '原图文件已不存在')
        return FileResponse(path, media_type=row.mime)

    @app.patch('/api/v1/imports/{id}/fields')
    def update_fields(id: str, data: DraftFields, user: User = Depends(current), session: DBSession = Depends(db)):
        row = own_import(id,user,session)
        if row.status == 'confirmed':
            raise HTTPException(409, '记录已经确认')
        from sqlalchemy import update
        result = session.execute(update(ImportDraft).where(ImportDraft.id==id, ImportDraft.user_id==user.id, ImportDraft.revision==data.expected_revision, ImportDraft.status!='confirmed').values(fields=data.workout.model_dump(mode='json'), revision=ImportDraft.revision+1))
        if not result.rowcount:
            session.rollback()
            raise HTTPException(409, '草稿已变化，请刷新后重试')
        session.commit()
        session.refresh(row)
        return import_json(row)

    @app.post('/api/v1/imports/{id}/confirm')
    def confirm(id: str, data: Confirmation, user: User = Depends(current), session: DBSession = Depends(db)):
        row = own_import(id,user,session)
        if row.workout_id:
            return workout_json(session.get(Workout,row.workout_id))
        if row.revision != data.expected_revision:
            raise HTTPException(409, '草稿已变化，请刷新后重试')
        if not row.fields:
            raise HTTPException(422, '请先填写并保存训练数据')
        values = WorkoutInput.model_validate(row.fields).model_dump(mode='json')
        from sqlalchemy import update
        workout = Workout(id=str(uuid4()), user_id=user.id, payload=values)
        session.add(workout)
        session.flush()
        from .metric_reviews import confirmed_metrics
        metrics=confirmed_metrics(session,row)
        if metrics:workout.payload={**values,'confirmed_metrics':metrics}
        result = session.execute(update(ImportDraft).where(ImportDraft.id==id, ImportDraft.user_id==user.id, ImportDraft.revision==data.expected_revision, ImportDraft.workout_id.is_(None)).values(status='confirmed',workout_id=workout.id,revision=ImportDraft.revision+1))
        if not result.rowcount:
            session.rollback()
            raise HTTPException(409, '草稿已确认或变化，请刷新查看')
        bump_facts(user,session)
        session.commit()
        return workout_json(workout)

    from .metric_reviews import install_routes as install_metric_reviews
    install_metric_reviews(app,current,db,own_import,import_json)

    from .assessment import install_routes as install_assessment
    install_assessment(app,current,db)

    from .chat import install_routes as install_chat
    install_chat(app,cfg,current,db)

    from .recognition import install_routes
    install_routes(app,cfg,current,db,own_import)
    from .planning import install_routes as install_planning
    install_planning(app,cfg,current,db,bump_facts)
    from .cycle_planning import install_routes as install_cycle
    install_cycle(app,cfg,current,db,bump_facts)
    from .weather import install_routes as install_weather
    install_weather(app,cfg,current,db)
    from .notifications import install_routes as install_notifications
    install_notifications(app,cfg,current,db)
    from .data_management import install_routes as install_data_management
    install_data_management(app,cfg,current,db)
    from .data_deletion import install_routes as install_data_deletion
    install_data_deletion(app,cfg,current,db)
    return app
