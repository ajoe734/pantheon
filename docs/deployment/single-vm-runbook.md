# Single-VM Deployment Runbook

Brings up the full Pantheon control-plane stack on a single VM.

## Prerequisites

| Requirement | Version |
|---|---|
| Docker | ≥ 24 |
| Docker Compose plugin | ≥ 2.20 |
| Python | ≥ 3.11 (host, for `db_migrate.sh`) |
| asyncpg | ≥ 0.29 (`pip install asyncpg`) |
| psql client | any recent version (for bootstrap user/db provisioning) |

All application services run inside Docker containers. Python and psql are only needed on the host to run the migration helper during bootstrap.

## Files

| File | Purpose |
|---|---|
| `.env.example` | Template for all environment variables |
| `docker-compose.control.yml` | VM-1 control-plane service definitions |
| `env/prod-control.env.example` | Alternate env template using isolated port range |
| `scripts/bootstrap.sh` | One-shot bring-up: start → migrate → health-check |
| `scripts/db_migrate.sh` | Idempotent DB schema migrations |

## Quick Start

```bash
# 1. Clone and prepare
git clone git@github.com:ajoe734/pantheon.git
cd pantheon

# 2. Create .env from the template
cp .env.example .env
# Edit .env — at minimum set real passwords and API keys

# 3. Bootstrap the stack
bash scripts/bootstrap.sh
```

`bootstrap.sh` performs four steps automatically:
1. Start infra services (postgres, minio, nats) and wait for healthy
2. Create MinIO bucket via minio-init
3. Run idempotent DB migrations (`scripts/db_migrate.sh`)
4. Start all application services and verify each is healthy

## Environment Variable Reference

All variables are documented in `.env.example`. Key groups:

### Infrastructure

| Variable | Default | Notes |
|---|---|---|
| `POSTGRES_USER` | `postgres` | Superuser for initial provisioning |
| `POSTGRES_PASSWORD` | `postgres` | **Change in production** |
| `POSTGRES_PORT` | `15432` | Published host port |
| `PANTHEON_APP_DB_USER` | `pantheon_app` | Application DB user |
| `PANTHEON_APP_DB_PASSWORD` | `pantheon_app` | **Change in production** |
| `DATABASE_URL` | `postgresql://pantheon_app:…@postgres:5432/pantheon` | Used by services inside Docker |
| `TELEMETRY_DB_DSN` | same as `DATABASE_URL` | Used by telemetry ingest path |
| `MINIO_ROOT_PASSWORD` | `pantheonminio` | **Change in production** |
| `PANTHEON_NATS_URL` | `nats://nats:4222` | Internal NATS address |

### Application Services

| Variable | Default | Notes |
|---|---|---|
| `BFF_READ_SURFACE_STATE` | `degraded` | Set to `live` when execution plane is available |
| `PANTHEON_RUNTIME_MANAGER_URL` | _(empty)_ | URL of VM-2 runtime-manager |
| `LLM_BACKEND` | `anthropic` | LLM provider for persona service |
| `ANTHROPIC_API_KEY` | _(empty)_ | Required when `LLM_BACKEND=anthropic` |

## Advanced Usage

### Run with an alternate env file

```bash
bash scripts/bootstrap.sh --env-file env/prod-control.env.example
```

### Skip DB migration (already applied)

```bash
bash scripts/bootstrap.sh --skip-migration
```

### Run migrations independently

```bash
# From host (requires asyncpg + psql installed)
TELEMETRY_DB_DSN=postgresql://pantheon_app:pantheon_app@localhost:15432/pantheon \
  bash scripts/db_migrate.sh

# Or directly inside a container
docker compose -f docker-compose.control.yml exec postgres \
  psql -U pantheon_app -d pantheon
```

### Start only infra services

```bash
docker compose -f docker-compose.control.yml up -d postgres minio nats
```

### Restart a single service

```bash
docker compose -f docker-compose.control.yml restart telemetry
```

### Tail logs

```bash
docker compose -f docker-compose.control.yml logs -f operator-bff
```

### Shut down and remove volumes

```bash
docker compose -f docker-compose.control.yml down -v
```

## Health Checks

All services expose a health endpoint. `bootstrap.sh` polls them automatically.
You can also check manually:

```bash
# BFF
curl -s http://localhost:8001/health | python3 -m json.tool

# Services using /__health__
for port in 8083 8084 8085 8086 8087 8088 8089 8090 8091 8094; do
  printf "port %-5s: " "$port"
  curl -s "http://localhost:$port/__health__" 2>/dev/null || echo "no response"
done

# capital (8092) and evolution (8093) use /health
for port in 8092 8093; do
  printf "port %-5s: " "$port"
  curl -s "http://localhost:$port/health" 2>/dev/null || echo "no response"
done
```

## DB Migrations

`scripts/db_migrate.sh` applies all schema migrations idempotently (safe to re-run).

Current migrations:

| # | Table | DDL |
|---|---|---|
| 1 | `telemetry_events` | Primary telemetry ingest table (asyncpg write path) |
| 2 | `idx_telemetry_events_created_at` | Time-range index |
| 3 | `idx_telemetry_events_event_type` | Type-filter index |

## Service Port Map

Default single-VM port assignments:

| Service | Container port | Host port |
|---|---|---|
| operator-bff | 8001 | 8001 |
| persona | 8002 | 8002 |
| telemetry | 8083 | 8083 |
| evaluation | 8084 | 8084 |
| feedback | 8085 | 8085 |
| memory | 8086 | 8086 |
| registry | 8087 | 8087 |
| optimizer-svc | 8088 | 8088 |
| promotion | 8089 | 8089 |
| incidents | 8090 | 8090 |
| postmortems | 8091 | 8091 |
| capital | 8092 | 8092 |
| evolution | 8093 | 8093 |
| lineage-read | 8094 | 8094 |
| postgres | 5432 | 15432 |
| minio API | 9000 | 19000 |
| minio console | 9001 | 19001 |
| nats | 4222 | 14222 |
| nats monitor | 8222 | 18222 |

## Troubleshooting

### Service fails to start

```bash
docker compose -f docker-compose.control.yml logs <service-name>
```

Common causes:
- Missing env var — check `.env` covers all variables in `.env.example`
- Port conflict — set `*_PORT` variables in `.env` to use different host ports
- Postgres not ready — bootstrap waits, but if you see connection errors, run `bash scripts/bootstrap.sh` again

### Migration fails

- Ensure `asyncpg` is installed: `pip install asyncpg`
- Ensure `TELEMETRY_DB_DSN` or `DATABASE_URL` is set and points to the published host port
- Ensure postgres container is healthy before running migrations

### MinIO bucket already exists

The minio-init step is safe to re-run; it uses `--ignore-existing` internally.

## Next Steps

After the single-VM stack is healthy:

- **DEPLOY-006**: Run the end-to-end smoke test (`scripts/smoke_test_single_vm.sh`)
- **DEPLOY-007/DEPLOY-008**: Split control-plane and execution-plane onto separate VMs
- **DEPLOY-009**: Dual-VM acceptance test including kill-switch and rollback flows
