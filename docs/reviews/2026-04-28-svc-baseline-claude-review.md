# SVC-BASELINE Review — Claude (auto-reassigned from Gemini)

- Date: 2026-04-28
- Task: `SVC-BASELINE` — Lock the single-VM service baseline contract
- Owner: Codex2
- Reviewer: Claude (auto-reassigned after Gemini 429 capacity failure)
- Verdict: **APPROVED — return to owner for finalization**

## Artifacts reviewed

- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/starter-draft.md`
  - Section: `SVC-BASELINE locked contract (2026-04-28)`
- `docs/02-architecture/consensus/sessions/phase4-2026-04-15-service-layer-completion/phase2-phase6-gap-inventory.md`
  - Section 8: `SVC-BASELINE Closure Note`
- Cross-referenced against `docker-compose.yml` and on-disk service Dockerfiles.

## Acceptance criteria check

1. **Port / env / volume contract is explicit across the target stack** — pass.
   - Port table covers every service in the default profile plus the optional `openclaw` and `smoke` profile services. Container ports, host ports, health paths, and env-var overrides match `docker-compose.yml` line-for-line for postgres (5432→15432), minio (9000/9001→19000/19001), nats (4222/8222→14222/18222), signal-store (6379, not host-published), runtime-manager (8081→18081, `/__health__`), governance (8082→18082, `/health`), telemetry (8083→18083, `/__health__`), evaluation (8084→18084, `/__health__`), feedback (8085→18085, `/__health__`), memory (8086→18086, `/__health__`), registry (8087→18087, `/__health__`), optimizer-svc (8088→18088, `/__health__`), promotion (8089→18089, `/__health__`), incidents (8090→18090, `/__health__`), postmortems (8091→18091, `/__health__`), capital (8092→18092, `/health`), evolution (8093→18093, `/health`), lineage-read (8094→18094, `/__health__`), operator-bff (8001→18001, `/health`), persona (8002→18002, `/health`), router (8001→18003, `/health`), openclaw-gateway (18789, `/healthz`, profile `openclaw`), and smoke-stack (profile `smoke`).
   - All 13 named volumes in the locked contract (`postgres-data`, `minio-data`, `nats-data`, `openclaw-data`, `runtime-data`, `governance-data`, `telemetry-data`, `incident-data`, `bff-data`, `promotion-data`, `capital-data`, `evolution-data`, `lineage-data`) appear in the compose `volumes:` block. BFF mounts `governance-data`, `runtime-data`, and `incident-data` read-only and owns only `bff-data`, matching the contract.
   - Env-name families are enumerated and consistent with compose: shared infra (`DATABASE_URL`, `PANTHEON_NATS_URL`, `PANTHEON_S3_ENDPOINT`, `PANTHEON_ARTIFACT_BUCKET`, `PANTHEON_RUNTIME_MANAGER_URL`, `PANTHEON_BFF_URL`, `PANTHEON_REGISTRY_URL`, `PANTHEON_TELEMETRY_URL`, `PANTHEON_INTERNAL_API_URL`); data-dir family (`BFF_DATA_DIR`, `PANTHEON_GOVERNANCE_DATA_DIR`, `GOVERNANCE_DATA_DIR`, `PANTHEON_RUNTIME_DATA_DIR`, `PANTHEON_RUNTIME_BINDING_STORE_PATH`, `TELEMETRY_STORAGE_DIR`, `INCIDENTS_DATA_DIR`, `POSTMORTEMS_DATA_DIR`, `PROMOTION_DATA_DIR`, `CAPITAL_DATA_DIR`, `EVOLUTION_DATA_DIR`, `LINEAGE_DATA_DIR`); secret defaults (`MINIO_ROOT_USER`, `MINIO_ROOT_PASSWORD`, `OPENCLAW_GATEWAY_TOKEN`, `PANTHEON_S3_ACCESS_KEY`, `PANTHEON_S3_SECRET_KEY`).

2. **Compose profile boundaries and Dockerfile conventions are documented for implementation** — pass.
   - Profiles are explicit: `default` is the single-VM control/evidence/surface stack plus local infra; `openclaw` gates only the gateway smoke; `smoke` gates the verification runner; `web`/`cron`/research/learning are explicitly outside default. Compose enforces this (`profiles: ["openclaw"]` on `openclaw-gateway`, `profiles: ["smoke"]` on `smoke-stack`).
   - Dockerfile conventions cover build context (repo-root vs service-local), base image (`python:3.11-slim`), env hygiene (`PYTHONDONTWRITEBYTECODE=1`, `PYTHONUNBUFFERED=1`), requirements scoping, port matching, FastAPI vs Flask health-path rule, and the runtime-manager kill-switch preservation rule.
   - Every service named in the locked contract has a corresponding Dockerfile on disk (verified `find services -maxdepth 4 -name Dockerfile`): runtime-manager, governance, telemetry, evaluation, feedback, memory, registry, optimizer-svc, promotion, incidents, postmortems, capital, evolution, lineage-read, operator-bff, persona, router.
   - Explicit deferrals (single-replica BFF, scope limited to deployability and smoke wiring) are recorded in the contract so downstream `SVC-*` slices inherit a clear non-goal list.

## Cross-document consistency

- `phase2-phase6-gap-inventory.md` §8 correctly demotes the older Dockerfile-missing / unlocked-baseline narrative to a 2026-04-15 planning snapshot and points downstream tasks to the starter-draft locked contract as the active source of truth. This avoids future readers mistaking the gap inventory for the implementation contract.

## Mechanical check

- `docker compose config --quiet` exits 0 against the current `docker-compose.yml`, confirming the locked contract parses as valid compose syntax.

## Verdict

Approved. Returning to Codex2 for finalization to `done`. Downstream `SVC-RUNTIME-CONTROL`, `SVC-GOVERNANCE-API`, `SVC-EVIDENCE`, `SVC-SURFACES`, and `SVC-COMPOSE` may now build against this locked contract.
