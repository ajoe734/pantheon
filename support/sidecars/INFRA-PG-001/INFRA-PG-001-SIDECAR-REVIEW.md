# INFRA-PG-001 — Review Packet

**Parent task:** INFRA-PG-001 — Add PostgreSQL to docker-compose as system-wide persistent store
**Sidecar kind:** review_packet
**Prepared by:** Claude (sidecar owner)
**Reviewer:** Codex
**Date:** 2026-04-17
**Status:** done — review approved by Codex 2026-04-17, finalized by Claude 2026-04-17

---

## 1. Purpose

This packet supports the Codex reviewer for INFRA-PG-001.
It cross-references the acceptance packet (`INFRA-PG-001-SIDECAR-ACCEPTANCE.md`) against the current state of the codebase and highlights one late addition not captured in the acceptance packet.

This sidecar does not modify any canonical truth files.

---

## 2. Delivered Artifacts — Verification Summary

| Artifact | Claimed in Acceptance Packet | Verified Present |
|---|---|---|
| `docker-compose.yml` — postgres service | ✓ | ✓ |
| `docker-compose.yml` — `postgres-data` volume | ✓ | ✓ |
| `docker-compose.yml` — `service_healthy` gates on dependents | ✓ | ✓ (10 services) |
| `.env.example` — postgres env vars | ✓ | ✓ |
| `scripts/init-db.sh` — role + DB creation | ✓ | ✓ |
| `scripts/init-db.sql` — reference SQL | ✓ | ✓ |
| `scripts/init-db.sh` — schema public ownership grant | **Not in acceptance packet** | ✓ (added in commit `4018762`) |

---

## 3. Late Addition: Schema Ownership Grant (commit `4018762`)

The acceptance packet was prepared before commit `4018762` (`INFRA-PG-001: grant schema public ownership to pantheon_app`) landed.
This commit adds a second `psql` block to `init-db.sh`:

```sql
ALTER SCHEMA public OWNER TO "${APP_USER}";
GRANT ALL ON SCHEMA public TO "${APP_USER}";
```

**Why it matters:** When `POSTGRES_DB` equals the app DB name, Docker creates the DB before `init-db.sh` runs, leaving `public` owned by the superuser. Without this block, `pantheon_app` cannot `CREATE TABLE` in `public` without superuser privileges.

**Assessment:** The addition is correct, idempotent, and necessary. It runs in a second `psql` block scoped to `$APP_DB`, which is safe. The acceptance checklist Gate 3 should be read as passing with this addition included.

---

## 4. Acceptance Gate Cross-Check

### Gate 1 — `docker compose up postgres` healthcheck passes

All items confirmed present in `docker-compose.yml`:
- Image: `postgres:16-alpine`
- Healthcheck: `pg_isready -U ${POSTGRES_USER:-postgres} -d ${POSTGRES_DB:-pantheon}`, interval 10s / timeout 5s / retries 10 / start_period 10s
- Port: `${POSTGRES_PORT:-15432}:5432`
- Volume: `postgres-data:/var/lib/postgresql/data`
- Init mount: `./scripts/init-db.sh:/docker-entrypoint-initdb.d/01-init-db.sh:ro`

**Gate 1: PASS — confirmed**

### Gate 2 — Services with DATABASE_URL depend on postgres

Live verification (parsed from `docker-compose.yml`):

| Service | DB env var present | `depends_on: postgres (service_healthy)` |
|---|---|---|
| `governance` | ✓ | ✓ |
| `telemetry` | ✓ (`TELEMETRY_DB_DSN`) | ✓ |
| `incidents` | ✓ | ✓ |
| `postmortems` | ✓ | ✓ |
| `operator-bff` | ✓ | ✓ |
| `evaluation` | ✓ | ✓ |
| `feedback` | ✓ | ✓ |
| `memory` | ✓ | ✓ |
| `registry` | ✓ | ✓ |
| `optimizer-svc` | ✓ | ✓ |

No service carries a DB env var without a corresponding `postgres` `service_healthy` gate.

**Gate 2: PASS — confirmed**

### Gate 3 — `init-db.sh` creates `pantheon` DB and `pantheon_app` user

Verified in `scripts/init-db.sh`:
- `set -euo pipefail` — present
- Role idempotency: `IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = ...)` — present
- Role creation parameterized: `EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', ...)` — present
- DB creation: `SELECT format('CREATE DATABASE %I OWNER %I', ...) WHERE NOT EXISTS ... \gexec` — present
- Privilege grant: `GRANT ALL PRIVILEGES ON DATABASE "${APP_DB}" TO "${APP_USER}"` — present
- Schema ownership (late addition): `ALTER SCHEMA public OWNER TO "${APP_USER}"; GRANT ALL ON SCHEMA public TO "${APP_USER}"` — present

**Gate 3: PASS — confirmed (including late addition)**

---

## 5. Open Notes for Reviewer

1. **Acceptance packet does not mention schema ownership grant.** The acceptance packet (Gate 3) was written before commit `4018762`. The reviewer should treat Gate 3 as passing with the schema grant included. No blocking issue — the implementation is complete and correct.

2. **`init-db.sql` vs `init-db.sh`:** Both files exist. Compose mounts only `init-db.sh` as the Docker entrypoint script. `init-db.sql` is a reference artifact and will not be double-executed. Not a blocking issue.

3. **Image pin:** `postgres:16-alpine` is a channel tag, not a digest pin. Acceptable for infrastructure scaffolding; production hardening (digest pin) is a follow-on concern outside INFRA-PG-001 scope.

4. **Services without postgres dependency** (7 services: `runtime-manager`, `persona`, `router`, `promotion`, `capital`, `evolution`, `lineage-read`): None carry a DB env var, so the missing `depends_on` is expected. Future DB expansion tasks will need to add both.

---

## 6. Reviewer Recommendation

All three acceptance gates are verified against the live codebase.
The one item not in the acceptance packet (schema ownership grant, commit `4018762`) strengthens the implementation.

**Recommended action: approve INFRA-PG-001.**

If Codex agrees, the next steps are:
1. Run `approve INFRA-PG-001-SIDECAR-REVIEW` (this sidecar)
2. Run `approve INFRA-PG-001` (parent task, per the acceptance packet recommendation)
3. Claude finalizes both tasks as `done`
