# SVC-SURFACES BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `SVC-SURFACES` - Package BFF and feedback and rewire BFF off snapshots/defaults
**Parent Owner**: Codex
**Parent Reviewer**: Claude
**Parent Status**: `todo`
**Sidecar Task**: `SVC-SURFACES-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: Codex2
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-28
**Last Updated**: 2026-04-28
**Review Status**: approved by Codex review on 2026-04-28

> Support artifact only. This packet does not change canonical truth, L1 policy, core contracts, runtime, registry, governance, or service implementation. It packages current BFF/service-surface facts and handoff guidance for the parent owner to decide what to absorb into `SVC-SURFACES`.

---

## 1. Parent Scope Snapshot

`SVC-SURFACES` is the service-surface convergence slice for the first single-VM baseline. The acceptance target is:

- BFF no longer uses local snapshot/default fallback as the normal integration path.
- BFF and feedback are packaged and runnable in the target stack.
- `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false` is the normal single-VM path.
- Missing backend data surfaces degraded or unavailable states instead of seeded defaults.
- BFF read clients respect the `SVC-GOVERNANCE-API` and `SVC-SERVICE-DISPOSITION` boundaries for governance, runtime, evidence, consultation, and search data.

This sidecar is intentionally narrower: it only records current evidence, query gaps, operator journey implications, and frontend handoff notes.

---

## 2. Current Implementation Snapshot

### 2.1 BFF Packaging

| Area | Current fact | Evidence |
|---|---|---|
| BFF container | `services/control-plane/bff/Dockerfile` builds the BFF and runs `uvicorn main:app` on port `8001`. | `services/control-plane/bff/Dockerfile` |
| BFF compose entry | `operator-bff` exists in root compose, mounts `bff-data`, governance/runtime/incident read volumes, and depends on governance, runtime-manager, evolution, incidents, postmortems, telemetry, Postgres, and NATS. | `docker-compose.yml:258` |
| BFF health | BFF exposes `GET /health` with `{status, service, version, timestamp}`. | `services/control-plane/bff/main.py:5875` |
| Snapshot fallback flag | Runtime construction passes `allow_local_snapshot_fallback` from `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK`; absence defaults to enabled. | `services/control-plane/bff/main.py:119` |

**Parent-owner implication**: packaging is already present, but the normal-path fallback policy still needs explicit single-VM convergence. Root compose does not currently set `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false`, so parent work should treat that as an open implementation/config gap, not as already closed.

### 2.2 Feedback Packaging

| Area | Current fact | Evidence |
|---|---|---|
| Default compose feedback service | Root compose runs `feedback` from `services/feedback/Dockerfile` on port `8085`, with `GET /__health__`. | `docker-compose.yml:384` |
| Legacy/control-plane feedback module | `services/control-plane/feedback/` also exists with a richer trader-feedback ingestion API and `/health`. | `services/control-plane/feedback/main.py` |

**Parent-owner implication**: SVC-SURFACES should decide whether its artifact target is the default compose `services/feedback/` service or the older `services/control-plane/feedback/` module. The status artifact currently names `services/control-plane/feedback/`, while root compose runs `services/feedback/`.

### 2.3 Evidence Surfaces

| Surface | Current BFF route | Current backing behavior | Evidence |
|---|---|---|---|
| Telemetry event list | `GET /api/v1/telemetry` | Adapts telemetry summaries as events; if no service store and no local fallback, list can be empty. | `services/control-plane/bff/main.py:10745`, `services/control-plane/bff/read_store.py:8468` |
| Telemetry summary | `GET /api/v1/telemetry/{runtime_id}/summary` | Reads `telemetry_summaries`; missing summary currently returns 404. | `services/control-plane/bff/main.py:10768` |
| Telemetry performance | `GET /api/v1/telemetry/{artifact_id}/performance` | Reads `telemetry_performance`; missing performance currently returns 404. | `services/control-plane/bff/main.py:10796`, `services/control-plane/bff/read_store.py:8514` |
| Lineage list/detail/graph | `GET /api/v1/lineage`, `/lineage/edges/{edge_id}`, `/lineage/graph` | Reads `lineage_edges` through BFF read_store; missing list surfaces can return empty with meta, detail returns 404. | `services/control-plane/bff/main.py:10626` |
| Lineage service wrapper | `lineage-read` compose service exists on port `8094`. | `docker-compose.yml:551` |

**Parent-owner implication**: `SVC-EVIDENCE` gives deployable telemetry/lineage services, but BFF still appears to rely on configured JSON stores/snapshots for many read projections. Parent implementation should either wire explicit service clients/URLs where in scope, or make no-backend states explicit and test them with fallback disabled.

---

## 3. Query Gap Matrix For SVC-SURFACES

| Domain | Current path | Normal single-VM target for this wave | Gap / action for parent owner |
|---|---|---|---|
| Governance/deployment/capital bindings | BFF has canonical/service-store adapter entries for deployment plans, approval decisions, capital pools, persona bindings, runtime bindings. | Follow `SVC-GOVERNANCE-API` boundaries; no seeded defaults in normal path. | Confirm explicit service-backed source paths or mounted service stores are present, then add fallback-disabled tests for degraded/unavailable behavior. |
| Runtime status / bindings / rollback | BFF reads runtime stores and runtime-manager URL envs exist in compose. | Runtime-manager is the operator runtime truth; no timeout means "no active runtime". | Verify runtime surfaces do not treat missing stores as empty when fallback is disabled. |
| Telemetry | BFF TL-01 adapts telemetry summaries into events; TL-02/TL-03 read service-store datasets. | Telemetry service is deployable; BFF should surface unavailable/degraded rather than default telemetry fixtures. | Add a no-backend test proving missing telemetry data does not look like live zeros or seeded current telemetry. |
| Lineage | BFF routes use `lineage_edges` read-store datasets; `lineage-read` service exists separately in compose. | Lineage read model is a service-backed evidence surface. | Decide whether BFF should call `lineage-read` HTTP or consume its explicit store. Avoid relying on local seed data as the normal path. |
| Consultation | BFF has consultation routes and read_store can read `ConsultationStore` when a local data dir is configured. | Deferred from default compose unless the boundary is implemented explicitly. | Do not add consultation as a hidden default dependency. In normal no-snapshot path, consultation-backed panels should report unavailable/degraded unless a deliberate HTTP/shared-store boundary is accepted by parent. |
| Source ingestion | No HTTP entrypoint, Dockerfile, health endpoint, port, or compose wiring for a service. | Deferred. | Do not create source-ingest calls from BFF in this wave. Any source-ingest UI must be unavailable or explicitly test-only. |
| Search | BFF has `GET /api/v1/research/search`, but repo search service has no deployable HTTP wrapper. | Deferred. | Keep research search normal path degraded/unavailable unless backed by existing explicitly fenced BFF-local test data. Do not add a default compose `search` dependency. |

---

## 4. Negative Boundary From Service Disposition

`SVC-SERVICE-DISPOSITION` resolved the default single-VM baseline boundary for three tempting dependencies:

- `consultation`: code and Dockerfile exist, but root compose does not run it; BFF consultation reads currently use local `ConsultationStore` data-dir behavior rather than an HTTP service client.
- `source_ingestion`: library code exists, but no service wrapper, Dockerfile, health endpoint, port, or compose contract exists.
- `search`: governed search library code exists, but no service wrapper, Dockerfile, health endpoint, port, or compose contract exists.

The parent `SVC-SURFACES` work should preserve this negative boundary. It should not add normal-path dependencies on those services in this wave. Where frontend surfaces remain visible, the BFF should return degraded/unavailable metadata or a clearly fenced test-only path rather than presenting missing backend data as live service data.

---

## 5. Operator Journey Handoff

### 5.1 Safe Operator Journey For This Wave

Frontend and operator flows should prefer BFF-composed views that already carry `meta.surfaces`:

1. Operator home: `GET /api/v1/operator/home`
2. Health status: `GET /api/v1/operator/health-status`
3. Governance queues: `GET /api/v1/operator/governance/review-queue` and `/approval-queue`
4. Incident response: `GET /api/v1/operator/incident-response/{incident_id}`
5. Post-incident review: `GET /api/v1/operator/post-incident-review/{incident_id}`
6. Persona management: `GET /api/v1/operator/persona-management/{persona_id}`
7. Evidence drilldowns: telemetry and lineage routes only when their backing surface status is not unavailable.

### 5.2 Frontend Handling Rules

| Condition | Frontend behavior |
|---|---|
| `meta.surfaces.*.status = ok` | Render normally. |
| `meta.surfaces.*.status = degraded` or `stale` | Keep the panel visible with an explicit degraded/stale message. Do not convert to an empty state. |
| `meta.surfaces.*.status = unavailable` | Show unavailable panel and disable dependent CTAs. |
| HTTP 404 on evidence detail | Treat as missing specific object, not as proof that the backend is healthy. Pair with list/composed surface status where available. |
| Search/consultation/source-ingest unavailable in default stack | Hide production CTAs or show unavailable/deferred state; do not silently use seeded local examples. |

### 5.3 Minimal Frontend Smoke Requests

```http
GET /health
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/operator/health-status
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/operator/home
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/lineage?artifact_id=artifact-042
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/telemetry/runtime-042/summary
Authorization: Bearer op-42:operator
```

For parent verification, run these with `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false` and no seeded `read_surfaces.json` unless the test is explicitly marked as fixture-backed.

---

## 6. Suggested Parent Implementation Sequence

1. Set the intended normal single-VM BFF config explicitly:
   - root compose should include `PANTHEON_BFF_ALLOW_LOCAL_SNAPSHOT_FALLBACK=false`
   - tests should cover this env value directly

2. Audit BFF read-store data sources with fallback disabled:
   - governance/deployment/capital/runtime
   - telemetry and lineage
   - incident/postmortem/evolution
   - consultation/search/research surfaces

3. Convert missing normal-path data into explicit unavailable/degraded semantics:
   - no "no records" empty state on downstream absence
   - no seeded telemetry/search/consultation defaults as normal path
   - CTAs disabled when authority/evidence is unavailable

4. Decide feedback artifact boundary:
   - use default compose `services/feedback/`, or
   - intentionally promote/replace it with `services/control-plane/feedback/`
   - avoid leaving acceptance text pointed at a different service than compose runs

5. Add focused verification:
   - BFF startup and `/health`
   - `operator/health-status` and `operator/home` with fallback disabled
   - telemetry/lineage missing-backend degraded behavior
   - research search unavailable when no search wrapper/index is present
   - consultation unavailable/deferred behavior in default stack

---

## 7. Reviewer Checklist

| Check | Status |
|---|---|
| Support artifact only | PASS |
| Canonical truth untouched | PASS |
| Parent acceptance mapped | PASS |
| BFF query gaps identified | PASS |
| Operator journey handoff included | PASS |
| Frontend degraded/unavailable guidance included | PASS |
| SVC-SERVICE-DISPOSITION negative boundary preserved | PASS |
| Reviewer disposition recorded | PASS |

---

## 8. Handoff Status

Codex review approved. Parent owner can use this packet as a support-only starting point for `SVC-SURFACES`; it should not be treated as canonical design promotion by itself. The sidecar owner should finalize `SVC-SURFACES-SIDECAR-BFF-HANDOFF` to `done`; parent absorption remains a `SVC-SURFACES` owner decision.
