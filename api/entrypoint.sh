#!/bin/sh
set -e

if [ "$#" -gt 0 ]; then
    exec "$@"
fi

# Swarm delivers secrets as files, but the app reads them from the environment.
if [ -n "${CHRONOS_DB_PASSWORD_FILE:-}" ]; then
    CHRONOS_DB_PASSWORD=$(cat "$CHRONOS_DB_PASSWORD_FILE")
    if [ -z "$CHRONOS_DB_PASSWORD" ]; then
        echo "ERROR: database password file is empty: ${CHRONOS_DB_PASSWORD_FILE}" >&2
        exit 1
    fi
    export CHRONOS_DB_PASSWORD
fi
if [ -z "${CHRONOS_DATABASE_URL:-}" ] && [ -n "${CHRONOS_DB_PASSWORD:-}" ]; then
    CHRONOS_DATABASE_URL=$(python -c 'from app.config import settings; print(settings.database_url)')
    export CHRONOS_DATABASE_URL
fi

# Retry database migration until Postgres accepts connections.
attempt=1
until alembic upgrade head; do
    if [ "$attempt" -ge 30 ]; then
        echo "ERROR: database not reachable after ${attempt} attempts" >&2
        exit 1
    fi
    echo "waiting for database (attempt ${attempt})"
    attempt=$((attempt + 1))
    sleep 2
done

exec uvicorn app.main:app --host 0.0.0.0 --port 8000
