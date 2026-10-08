#!/bin/sh
set -eu
umask 077
cd /app/backend
python -m alembic upgrade head
cd /app
exec "$@"
