DO
$$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_catalog.pg_roles
        WHERE rolname = 'pantheon_app'
    ) THEN
        CREATE ROLE pantheon_app LOGIN PASSWORD 'pantheon_app';
    END IF;
END
$$;

SELECT 'CREATE DATABASE pantheon OWNER pantheon_app'
WHERE NOT EXISTS (
    SELECT 1
    FROM pg_database
    WHERE datname = 'pantheon'
)
\gexec

GRANT ALL PRIVILEGES ON DATABASE pantheon TO pantheon_app;

\connect pantheon

ALTER SCHEMA public OWNER TO pantheon_app;
GRANT ALL ON SCHEMA public TO pantheon_app;
