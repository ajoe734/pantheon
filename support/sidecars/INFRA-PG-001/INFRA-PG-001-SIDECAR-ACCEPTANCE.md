# INFRA-PG-001 — Acceptance Packet

**Parent task:** INFRA-PG-001 — Add PostgreSQL to docker-compose as system-wide persistent store  
**Sidecar kind:** acceptance_packet  
**Prepared by:** Claude (sidecar owner)  
**Reviewer:** Codex  
**Date:** 2026-04-17  
**Status:** done

---

## 1. Scope

This packet verifies the three acceptance gates from INFRA-PG-001 without modifying canonical truth files. It captures a snapshot of the delivered artifacts, a per-gate checklist, a dependency map across services, and open notes for the reviewer.

---

## 2. Delivered Artifacts

| File | Purpose |
|---|---|
| `docker-compose.yml` | `postgres` service, `postgres-data` volume, `service_healthy` gates on all dependent services |
| `.env.example` | All postgres-related env vars with safe defaults |
| `scripts/init-db.sh` | Docker entrypoint script; creates `pantheon_app` role and `pantheon` DB idempotently |
| `scripts/init-db.sql` | Equivalent SQL form (also present for reference; runtime uses `init-db.sh`) |

---

## 3. Acceptance Checklist

### Gate 1 — `docker compose up postgres` healthcheck passes

| Check | Finding | Pass? |
|---|---|---|
| Image pinned | `postgres:16-alpine` — channel-pinned, no digest | ✓ |
| Healthcheck defined | `pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-pantheon}` | ✓ |
| Healthcheck timing | interval 10s / timeout 5s / retries 10 / start_period 10s | ✓ |
| Port binding | `${POSTGRES_PORT:-15432}:5432` — non-default host port avoids system-postgres conflicts | ✓ |
| Volume declared | `postgres-data:/var/lib/postgresql/data` in named-volume section | ✓ |
| Init script mounted | `./scripts/init-db.sh:/docker-entrypoint-initdb.d/01-init-db.sh:ro` | ✓ |

**Gate 1: PASS**

---

### Gate 2 — All services with `DATABASE_URL` can connect to postgres

Services that carry `DATABASE_URL` and explicitly depend on `postgres` (`service_healthy`):

| Service | `DATABASE_URL` | `depends_on: postgres` |
|---|---|---|
| `governance` | ✓ | ✓ |
| `incidents` | ✓ | ✓ |
| `postmortems` | ✓ | ✓ |
| `operator-bff` | ✓ | ✓ |
| `evaluation` | ✓ | ✓ |
| `feedback` | ✓ | ✓ |
| `memory` | ✓ | ✓ |
| `registry` | ✓ | ✓ |
| `optimizer-svc` | ✓ | ✓ |

`telemetry` uses `TELEMETRY_DB_DSN` (same DSN value) instead of `DATABASE_URL`, and also depends on `postgres` (`service_healthy`). Covered.

Services without `DATABASE_URL` **and** without a `postgres` depends_on (expected — they do not use the DB directly at this stage):

| Service | Note |
|---|---|
| `runtime-manager` | Uses NATS + Redis only; no DB dependency declared |
| `persona` | No DB dependency declared |
| `router` | No DB dependency declared |
| `promotion` | No DB dependency declared |
| `capital` | Has own `capital-data` volume (object store); no direct PG usage |
| `evolution` | No DB dependency declared |
| `lineage-read` | No DB dependency declared |

These omissions are not gaps in INFRA-PG-001; they reflect services that do not yet write to PG. If future tasks add PG usage to these services they will need both `DATABASE_URL` and `depends_on: postgres`.

Default DATABASE_URL in `.env.example` and in compose service definitions:
```
postgresql://pantheon_app:pantheon_app@postgres:5432/pantheon
```
This matches the app role + DB created by `init-db.sh`.

**Gate 2: PASS** (for all services that carry a DB env var)

---

### Gate 3 — `init-db.sh` creates `pantheon` DB and `pantheon_app` user successfully

| Check | Finding | Pass? |
|---|---|---|
| Script is idempotent | Role creation wrapped in `IF NOT EXISTS`; DB creation uses `WHERE NOT EXISTS \gexec` | ✓ |
| App role login | `EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', ...)` — parameterized, safe | ✓ |
| DB ownership | `CREATE DATABASE ... OWNER pantheon_app` | ✓ |
| Privilege grant | `GRANT ALL PRIVILEGES ON DATABASE "pantheon" TO "pantheon_app"` | ✓ |
| Error-safe execution | `set -euo pipefail` at top of shell script | ✓ |
| Variables parameterized | Uses `PANTHEON_APP_DB_USER/PASSWORD/NAME` env vars with safe defaults | ✓ |
| `init-db.sql` consistency | Equivalent SQL also present; slight style difference (uses `\gexec` format vs `EXECUTE format(...)`) — functional parity confirmed | ✓ |

**Gate 3: PASS**

---

## 4. Dependency Map

```
postgres (postgres:16-alpine)
│
├─ depends_on (service_healthy) ─────────────────────────────────────────────
│   governance     → DATABASE_URL → postgresql://pantheon_app:…@postgres/pantheon
│   telemetry      → TELEMETRY_DB_DSN → same DSN
│   incidents      → DATABASE_URL → same DSN
│   postmortems    → DATABASE_URL → same DSN
│   operator-bff   → DATABASE_URL → same DSN
│   evaluation     → DATABASE_URL → same DSN
│   feedback       → DATABASE_URL → same DSN
│   memory         → DATABASE_URL → same DSN
│   registry       → DATABASE_URL → same DSN
│   optimizer-svc  → DATABASE_URL → same DSN
│
├─ volume: postgres-data (named, persisted across restarts)
│
└─ init: scripts/init-db.sh (mounted at /docker-entrypoint-initdb.d/01-init-db.sh)
         creates: role pantheon_app + database pantheon
```

---

## 5. Notes for Reviewer

1. **`init-db.sql` vs `init-db.sh`**: Both files exist. The compose mount uses `init-db.sh` (bash wrapper). `init-db.sql` is a reference copy. Reviewer should confirm this is intentional and the `.sql` file won't be double-executed.

2. **Image pin**: `postgres:16-alpine` is a channel tag, not a digest pin. If the project policy (see `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md`) requires digest pinning for production, this should be raised as a follow-on task rather than a blocker here (INFRA-PG-001 is infrastructure scaffolding, not production hardening).

3. **Services without postgres dependency**: Seven app services (listed in Gate 2) do not currently connect to PG. This is not a defect in INFRA-PG-001 but the reviewer should note it for future DB expansion tasks.

4. **`POSTGRES_DB` vs app DB**: The postgres container authenticates via `POSTGRES_USER/POSTGRES_DB` (superuser context) while the application connects via `pantheon_app` to the `pantheon` DB. `init-db.sh` bridges these correctly.

5. **`.env.example` scope**: The comment notes that DEPLOY-005 should expand this into a fuller single-VM environment contract. This packet does not block on DEPLOY-005.

---

## 6. Recommendation

All three acceptance gates pass. No blocking defects found.

The two non-blocking items (init-db.sql redundancy, image pin policy) should be logged as follow-on notes if the reviewer deems them worth tracking, but neither blocks INFRA-PG-001 from being marked `done`.

Recommend: **approve INFRA-PG-001**.
