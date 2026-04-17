#!/usr/bin/env bash
set -euo pipefail

APP_USER="${PANTHEON_APP_DB_USER:-pantheon_app}"
APP_PASS="${PANTHEON_APP_DB_PASSWORD:-pantheon_app}"
APP_DB="${PANTHEON_APP_DB_NAME:-pantheon}"

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<SQL
DO \$body\$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '${APP_USER}') THEN
    EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', '${APP_USER}', '${APP_PASS}');
  END IF;
END
\$body\$;

SELECT format('CREATE DATABASE %I OWNER %I', '${APP_DB}', '${APP_USER}')
WHERE NOT EXISTS (SELECT 1 FROM pg_database WHERE datname = '${APP_DB}')
\gexec

GRANT ALL PRIVILEGES ON DATABASE "${APP_DB}" TO "${APP_USER}";
SQL
