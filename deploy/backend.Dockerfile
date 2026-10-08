FROM python:3.11.17-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PYTHONPATH=/app/backend
WORKDIR /app
COPY backend/requirements.lock /app/backend/requirements.lock
RUN pip install --no-cache-dir -r /app/backend/requirements.lock
COPY backend/app /app/backend/app
COPY backend/migrations /app/backend/migrations
COPY backend/alembic.ini /app/backend/alembic.ini
COPY scripts/backup.py /app/scripts/backup.py
COPY deploy/entrypoint.sh /app/deploy/entrypoint.sh
RUN useradd --uid 10001 --create-home runner && mkdir -p /data/private /backups && chown -R runner:runner /data /backups
USER 10001:10001
ENTRYPOINT ["sh", "/app/deploy/entrypoint.sh"]
CMD ["python", "-m", "uvicorn", "app.run:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "1", "--no-proxy-headers", "--no-access-log"]
