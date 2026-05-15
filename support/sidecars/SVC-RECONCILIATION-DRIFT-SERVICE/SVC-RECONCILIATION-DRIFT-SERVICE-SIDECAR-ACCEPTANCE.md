# SVC-RECONCILIATION-DRIFT-SERVICE Acceptance Packet and Dependency Map

**Sidecar Task ID**: `SVC-RECONCILIATION-DRIFT-SERVICE-SIDECAR-ACCEPTANCE`
**Parent Task**: `SVC-RECONCILIATION-DRIFT-SERVICE`
**Parent Owner (current)**: `Codex2` (auto-reassigned 2026-04-28 from Gemini after capacity/429)
**Parent Reviewer**: `Codex`
**Sidecar Owner**: `Claude`
**Sidecar Reviewer**: `Codex2`
**Helper Kind**: `acceptance_packet`
**Date**: 2026-04-28

> This is a support artifact only. It does not modify L1 canonical truth, core
> contracts, the reconciliation/drift implementation, telemetry/lineage runtime
> code, governance flow, or compose wiring. The parent owner decides whether
> and how to absorb this packet into the SVC-RECONCILIATION-DRIFT-SERVICE
> closeout.

---

## 1. Scope Snapshot

`SVC-RECONCILIATION-DRIFT-SERVICE` materializes
`reconciliation-drift-svc` as a deployable HTTP wrapper that produces drift
and reconciliation read models from telemetry/lineage/runtime evidence and
publishes alert handoffs. It is a future-state read/projection service — it
must not own canonical telemetry truth and must not enter the emergency
control chain.

Canonical contract reference:
`Pantheon_API_Service_Contract_設計版.md` §3.16 and §5.13 define the
`reconciliation-drift-svc` positioning, primary internal APIs, and drift
report response shape:

| Aspect | Canonical baseline (per §3.16 / §5.13) |
|---|---|
| Positioning | backtest-paper-live / order-fill-position / drift analysis service |
| Responsibilities | reconciliation runs, drift detection, baseline compare, drift report persistence |
| Primary APIs | `POST /internal/reconciliation/runs`, `GET /internal/reconciliation/runs/{id}`, `GET /internal/drift/reports`, `GET /internal/drift/reports/{id}` |
| Drift report fields | `report_id`, `drift_type`, `scope_ref`, `severity`, `metrics`, `recommended_action` |
| Async events | `drift.report.created` (per §6) |

Current parent task state in `ai-status.json`:

| Field | Value |
|---|---|
| `status` | `todo` |
| `owner` | `Codex2` (auto-reassigned from Gemini 2026-04-28T17:35:49Z) |
| `reviewer` | `Codex` |
| `phase` | Future-State Service Materialization |
| `depends_on` | `SVC-EVIDENCE`, `SVC-COMPOSE` |
| Listed artifacts | `services/reconciliation-drift/`, `services/telemetry/`, `services/lineage-read/`, `docker-compose.yml` |

This packet does not touch any of those parent artifacts — it only references
them for the dependency/acceptance map.

---

## 2. Acceptance Checklist

| Parent acceptance item | Sidecar verification | Status |
|---|---|---|
| Reconciliation-drift service exposes drift summary, reconciliation status, alert handoff, and health APIs. | Mapped onto canonical `Pantheon_API_Service_Contract_設計版.md` §5.13 routes (`/internal/reconciliation/runs[/{id}]`, `/internal/drift/reports[/{id}]`) plus a `__health__`-style readiness route consistent with sibling services (telemetry, lineage-read, incidents). Alert handoff modeled on `drift.report.created` event (§6) and `recommended_action` field. Implementation work belongs to the parent task. | MAPPED (parent must implement) |
| Dockerfile, env-driven storage, and compose wiring are added. | Compose precedent recorded: `telemetry` (port 8083), `lineage-read` (port 8094), `incidents` (port 8090) are already wired with health probes, env-driven `*_DATA_DIR` volumes, and explicit BFF env vars. New `reconciliation-drift` service should follow the same pattern (recommended port band 8099; placeholder only — final port assignment is the parent owner's call). | PATTERN DOCUMENTED |
| Service consumes telemetry/lineage inputs without owning canonical telemetry truth. | Boundary stated: `reconciliation-drift-svc` reads from `http://telemetry:8083` and `http://lineage-read:8094` only; it must not write canonical telemetry events or canonical lineage records. Drift/reconciliation read models are derived projections persisted in its own volume (proposed `RECONCILIATION_DATA_DIR`). | BOUNDARY STATED |
| Tests cover drift calculation, degraded inputs, alert handoff, and compose config. | Acceptance test surface listed in §6 below; this sidecar does not run them — they remain a parent-task deliverable. | LISTED |
| Support-only sidecar constraint is respected. | This packet only creates `support/sidecars/SVC-RECONCILIATION-DRIFT-SERVICE/SVC-RECONCILIATION-DRIFT-SERVICE-SIDECAR-ACCEPTANCE.md`. No canonical, runtime, contract, or compose files are edited by this sidecar. | PASS |

---

## 3. Proposed API Surface (derived, non-canonical)

The parent owner (`Codex2`) decides the final shape. This table reflects the
canonical contract in §5.13 plus the `health` and `alert handoff` deliverables
called out in the parent acceptance criteria.

| Route | Direction | Notes |
|---|---|---|
| `POST /internal/reconciliation/runs` | command | Accept a reconciliation run request; idempotent via `Idempotency-Key` per §4.1. |
| `GET /internal/reconciliation/runs/{id}` | read | Return reconciliation run status and summary. |
| `GET /internal/drift/reports` | read | List drift reports with filters (`scope_ref`, `severity`, `drift_type`). |
| `GET /internal/drift/reports/{id}` | read | Return a single drift report matching §5.13.2 shape. |
| `GET /__health__` | infra | Liveness probe matching telemetry/lineage-read/incidents convention. |
| `GET /healthz` (optional) | infra | Forward-looking readiness route flagged for `SVC-HEALTH-OBSERVABILITY-UNIFICATION`. |

Alert handoff path (no new control-plane authority):

- emit `drift.report.created` (per §6 event family) when a drift report
  crosses a configured threshold;
- the `incidents-svc` already consumes drift signals as an upstream input
  (per `Pantheon_API_Service_Contract_設計版.md` §3.17 sequence) — this
  service must publish, not call incidents directly with imperative
  authority.

---

## 4. Compose and Discovery Map (proposed wiring; parent owns final wiring)

Existing dependency targets that the parent may consume:

| Upstream input | Compose target | Existing env precedent |
|---|---|---|
| Telemetry events / stats / DLQ | `http://telemetry:8083` | `PANTHEON_TELEMETRY_API_URL` (BFF), `PANTHEON_TELEMETRY_URL` (incidents/postmortems) |
| Lineage projections | `http://lineage-read:8094` | `PANTHEON_LINEAGE_READ_URL` (BFF), `LINEAGE_READ_URL` (smoke-stack) |
| Runtime binding state (read-only) | `http://runtime-manager:8081` | `PANTHEON_RUNTIME_MANAGER_URL`, `PANTHEON_RUNTIME_MANAGER_TOKEN` |
| Event bus | `nats://nats:4222` | `PANTHEON_NATS_URL` |
| Optional shared store | `postgresql://pantheon_app@postgres:5432/pantheon` | `DATABASE_URL` |

Recommended consumer wiring once the service is deployable:

| Consumer/env | Target | Rationale |
|---|---|---|
| `PANTHEON_RECONCILIATION_API_URL` | `http://reconciliation-drift:<port>` | New BFF/operator env var so SVC-SURFACES can swap normal-path drift reads off snapshot/default fallback. |
| `BFF.depends_on` | `reconciliation-drift: service_healthy` | Match treatment of `incidents`, `telemetry`, `lineage-read`. |
| `smoke-stack` | `RECONCILIATION_URL=http://reconciliation-drift:<port>` | Parallel to existing `INCIDENTS_URL`, `LINEAGE_READ_URL`, etc. |

A concrete port number is intentionally not chosen here. Sibling future-state
services already overlap (search-svc 8098). Final port belongs to the parent
owner once compose is edited.

---

## 5. Dependency Map

### Direct prerequisites (already done)

| Dependency | Status | Why it matters |
|---|---|---|
| `SVC-EVIDENCE` | `done` (archived 2026-04-28T11:37:30Z, commit `82a6efd`) | Provides deployable `telemetry-ingest` (port 8083) and `lineage-read` (port 8094) HTTP services that this drift service must read from. Confirms canonical telemetry/lineage stay owned by their respective services, not by reconciliation-drift. |
| `SVC-COMPOSE` | `done` (archived 2026-04-28T17:31:00Z, commit `5a4ece7`) | Locks the single-VM compose stack pattern, smoke profile, healthcheck shape, and dependency wiring this service must follow. Reviewer reproduced `up`/`smoke`/`down` verification. |

### Parallel / adjacent work

| Task | Current role |
|---|---|
| `SVC-SURFACES` (done) | Already removed normal-path snapshot/default fallback for governance-family reads. New BFF route for drift/reconciliation should follow the same explicit-service-URL contract. |
| `SVC-CONSULTATION-SERVICE-ACTIVATION`, `SVC-SOURCE-INGEST-SERVICE`, `SVC-SEARCH-SERVICE`, `SVC-TRAINING-SESSION-SERVICE`, `SVC-RESEARCH-ORCHESTRATOR-SERVICE`, `SVC-RESEARCH-WORKER-GATEWAY`, `SVC-POLICY-LEARNING-BOUNDARY` | Sibling future-state service materializations. None block this service; drift logic must not silently activate any of their production paths. |

### Downstream consumers / integrators

| Downstream task | Dependency on `SVC-RECONCILIATION-DRIFT-SERVICE` |
|---|---|
| `SVC-HEALTH-OBSERVABILITY-UNIFICATION` (todo) | Explicitly lists `SVC-RECONCILIATION-DRIFT-SERVICE` as a prerequisite; will require the new service to expose `healthz`/`livez`/`readyz` with consistent JSON shape and to publish basic dependency status. |
| Future `SVC-INCIDENT-FLOW` work (if materialized) | `incidents-svc` already exists at port 8090; reconciliation-drift should publish `drift.report.created` events for incidents to consume rather than calling incidents directly. |
| Future BFF "drift / reconciliation" surface | Will need an explicit `PANTHEON_RECONCILIATION_API_URL` env var so it does not regress to snapshot/default fallback. |

### Out of scope for this service (must remain delegated)

| Concern | Owner |
|---|---|
| Canonical telemetry event ingest, DLQ, replay | `services/telemetry/` (port 8083) |
| Canonical lineage corpus and projection engine | `services/telemetry/lineage_read/` exposed via `services/lineage-read/` (port 8094) |
| `RuntimeBinding`, kill-switch, safe-mode, rollback dispatch | `services/runtime-manager/` (port 8081) |
| Approval / deployment / capital / evolution writes | governance-family services per `services/control-plane/governance/service_family_contract.md` |
| Incident lifecycle and postmortem authoring | `services/incidents/` (8090), `services/postmortems/` (8091) |

---

## 6. Verification Surface (parent-owned)

Tests the parent task should add or reuse before review. This sidecar does
not run them; running them belongs to the implementation slice.

| Layer | Suggested test target | Why |
|---|---|---|
| Drift calculation | unit tests in `services/reconciliation-drift/` covering baseline-vs-observed metric deltas (e.g. `slippage_delta_bps`, `reject_rate_delta`). | Acceptance item: "drift calculation". |
| Degraded inputs | tests asserting graceful behavior when telemetry-svc / lineage-read / runtime-manager are slow, partially unavailable, or returning empty windows. | Acceptance item: "degraded inputs". Mirrors BFF service-timeout/fallback discipline established in SVC-SURFACES. |
| Alert handoff | tests asserting `drift.report.created` event publish on threshold crossing, with envelope per §6 event contract; assert no direct imperative call into incidents-svc or runtime-manager. | Acceptance item: "alert handoff". |
| Compose config | `docker compose -f docker-compose.yml config` plus `docker compose --profile smoke run --rm smoke-stack` once the new service is wired (parallels SVC-COMPOSE evidence). | Acceptance item: "compose config". |
| Health/readiness | `__health__` smoke parallel to telemetry/lineage-read/incidents; readiness route forward-compatible with `SVC-HEALTH-OBSERVABILITY-UNIFICATION`. | Required before BFF `depends_on: service_healthy`. |

---

## 7. Reviewer Checklist for Codex2

| Check | Expected answer |
|---|---|
| Did this sidecar avoid edits to canonical truth, contracts, runtime code, governance, telemetry/lineage runtime, or compose? | Yes. Only this support packet was created. |
| Are the canonical references (§3.16, §5.13, §6 of `Pantheon_API_Service_Contract_設計版.md`) accurate to the file at the current commit? | The cited sections exist and describe `reconciliation-drift-svc` positioning, the four `/internal/...` routes, the drift report fields, and `drift.report.created` event family. Reviewer should spot-check. |
| Are the listed parent dependencies (`SVC-EVIDENCE`, `SVC-COMPOSE`) actually `done`? | Yes — both archived under `ai-task-archive/tasks/` with terminal_outcome `completed` and delivery commits `82a6efd` and `5a4ece7`. |
| Is the boundary against canonical telemetry truth and the runtime control chain stated clearly enough that the parent owner cannot accidentally promote this service into either? | Yes — §1, §3 alert-handoff note, and §5 "Out of scope" table each repeat the boundary. |
| Does the packet stop short of prescribing implementation details that the parent owner should decide? | Yes — port number, exact data dir env name, and BFF route shape are flagged as parent decisions, not fixed here. |

---

## 8. Handoff

**To**: `Codex2` (sidecar reviewer; current parent owner)
**From**: `Claude` (sidecar owner)
**Requested review outcome**: Approve this sidecar if it is accurate as a
support packet for parent `SVC-RECONCILIATION-DRIFT-SERVICE`.

Recommended parent-owner use:

1. Treat §2 as the acceptance checklist mapping the parent task's acceptance
   items onto canonical contract sections and existing compose precedent.
2. Use §3 / §4 as a prompt for the eventual implementation slice — but final
   API shape, port, env var name, and compose wiring belong to the parent
   task, not this packet.
3. Keep §5 "Out of scope" intact: reconciliation-drift must stay a derived
   read/projection service that consumes telemetry/lineage/runtime evidence
   and publishes events, never a writer of canonical telemetry truth or a
   participant in the emergency control chain.
4. Do not treat this packet as completing `SVC-HEALTH-OBSERVABILITY-UNIFICATION`;
   it only flags the readiness contract that downstream task will require.
