# SVC-HEALTH-OBSERVABILITY-UNIFICATION BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-HEALTH-OBSERVABILITY-UNIFICATION-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-HEALTH-OBSERVABILITY-UNIFICATION`
**Parent Owner**: `Gemini`
**Parent Reviewer**: `Codex`
**Parent Status**: `todo`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Gemini`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-28
**Last Refresh**: 2026-04-28T19:05:25Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime/registry/governance implementation, BFF implementation,
frontend code, or compose wiring. The parent owner decides whether and how to
absorb this packet into the main health and observability unification slice.

---

## 1. Scope Snapshot

`SVC-HEALTH-OBSERVABILITY-UNIFICATION` is the production-readiness slice that
should normalize service health/readiness and baseline observability across the
single-VM stack. Its acceptance target in `ai-status.json` is:

- every default and newly materialized service exposes `healthz`, `livez`, and
  `readyz` with a consistent JSON shape;
- legacy `/health` and `/__health__` routes remain compatibility aliases or are
  documented exceptions;
- compose healthchecks and smoke tests use the standardized readiness path;
- baseline observability includes dependency status and a minimal metrics or
  status endpoint where applicable;
- tests verify healthy, degraded, and dependency-failure states.

This sidecar is narrower. It records the current BFF/frontend implications,
query gaps, operator journey rules, and suggested parent implementation order.

---

## 2. Current Implementation Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| BFF service health | `operator-bff` exposes `GET /health` with `{status, service, version, timestamp}`. It does not expose `/healthz`, `/livez`, or `/readyz` today. | `services/control-plane/bff/main.py` |
| Operator health board | `GET /api/v1/operator/health-status` is live and returns operator-domain health groups: `runtime`, `telemetry`, `incident`, `governance`, and `kill_switch`. | `services/control-plane/bff/main.py`, `docs/bff/PKT-011-health-status-board.md` |
| Health board source model | PKT-011 groups are built from BFF read-store dataset/source state and domain projections, not from polling compose service readiness endpoints. | `services/control-plane/bff/main.py`, `services/control-plane/bff/read_store.py` |
| Operator home | `GET /api/v1/operator/home` links to the health board and consumes the same PKT-011 domain-health semantics. | `services/control-plane/bff/main.py`, `.coordination/reviews/PKT-013-operator-home-review.md` |
| Degraded control guidance | `GET /api/v1/operator/degraded-control-guidance` exists for fallback command-path guidance when the BFF read surface is degraded. | `services/control-plane/bff/main.py` |
| Compose healthchecks | Root compose currently mixes `/health`, `/__health__`, `/healthz`, and infrastructure-native health endpoints. | `docker-compose.yml` |
| Smoke readiness | `scripts/smoke_honest_stack.py` waits on the same mixed route set: legacy `__health__` for several services, `/health` for newer services, and `/healthz` for NATS. | `scripts/smoke_honest_stack.py` |
| Newly activated services | `consultation-svc`, `source-ingest`, `search-svc`, and `training-session-svc` currently expose `/health` in their service wrappers. | `services/consultation/main.py`, `services/source_ingestion/main.py`, `services/search/main.py`, `services/training-session/main.py` |
| BFF service clients | BFF has explicit service URL paths for consultation, search, and training-session when env vars are configured. These failures currently map to route-specific degraded/unavailable behavior, not a generic dependency-readiness inventory. | `services/control-plane/bff/read_store.py`, `docker-compose.yml` |
| Pending parent dependencies | Parent task still depends on several service materialization slices that are not all `done` yet, including research orchestrator, research worker gateway, and reconciliation drift. Policy-learning and training-session are already archived as `done`. | `ai-status.json`, `ai-task-archive/tasks/` |

Parent-owner implication: the existing PKT-011 board should be treated as an
operator-facing domain-health surface, not as proof that service readiness is
already unified. The unification work needs an implementation pass below the
BFF and an additive BFF/frontend decision for how service readiness is exposed.

---

## 3. BFF Query Gap Matrix

| Surface / flow | Current behavior | Gap for parent owner |
|---|---|---|
| `GET /health` on BFF | Simple liveness payload; compose healthcheck uses it. | Add or alias standardized `/healthz`, `/livez`, and `/readyz` without breaking `/health`. Decide whether BFF `/readyz` includes dependency checks or only BFF-local readiness. |
| `GET /api/v1/operator/health-status` | Five backend-owned domain groups; no service roster, no per-service readiness, no dependency latency/error fields. | Keep PKT-011 stable or extend it additively. If service readiness is added here, the frontend contract and example payload need an explicit packet update. |
| `GET /api/v1/operator/home` | Health card points to `/operator/health-status` and summarizes PKT-011 domain health. | If parent adds service-readiness status, the home card should remain backend-shaped; do not let the frontend compute service health from scattered calls. |
| BFF service clients | Consultation/search/training failures are handled per route. Source-ingest is not a browser-facing BFF data dependency today. | Add a reusable dependency-status helper if BFF readiness should report downstream dependencies. Include env name, configured/not-configured state, route checked, status, checked time, and error class. |
| Newly activated services | `/health` exists for consultation, source-ingest, search, and training-session. | Add `/healthz`, `/livez`, `/readyz` compatibility and shape tests. Keep `/health` as an alias unless parent intentionally documents an exception. |
| Legacy services | Many services still use `/__health__` or `/health`; some compose checks are infrastructure-specific. | Normalize app-service checks to `/readyz` once implemented. Preserve `/__health__` for compatibility where existing smoke/tests depend on it. |
| Metrics/status observability | Some service health payloads expose counts or paths; there is no uniform minimal metrics/status route. | Decide the minimal common fields and whether `/readyz` carries dependency status while `/metrics` or `/status` carries counters. Avoid implying runtime trading health from static service liveness. |
| Pending services | Parent dependency list includes slices not yet complete. | Report missing/unconfigured pending services truthfully as pending/not-configured or leave them outside the default stack until materialized. Do not synthesize healthy rows for absent services. |

---

## 4. Operator Journey Handoff

### 4.1 Current Safe Journey

1. Frontend opens the Operator Health Status Board and calls only
   `GET /api/v1/operator/health-status`.
2. BFF returns `overall_status`, `safe_mode_state`,
   `secondary_control_path`, five domain health groups, and `meta.surfaces`.
3. The UI renders group labels, summaries, target refs, and secondary control
   guidance exactly as supplied by the BFF.
4. If `overall_status = degraded`, the board remains visible and read-only.
5. If `overall_status = unavailable`, the board renders explicit unavailable
   state and emphasizes the backend-supplied secondary control path.

### 4.2 Service Readiness Journey After Parent Work

The safest frontend-compatible path is additive:

1. BFF remains the only browser-facing health owner.
2. Browser code does not call service `/health`, `/healthz`, `/livez`, or
   `/readyz` endpoints directly.
3. Any service-readiness roster, if exposed to operators, is returned by the BFF
   as backend-owned data with explicit status and source metadata.
4. Empty service rows are authoritative only when the BFF marks the readiness
   surface fresh. Missing, timeout, unconfigured, or dependency failure states
   must render as degraded/unavailable, not as healthy empty state.
5. Existing PKT-011 domain groups remain distinct from raw service liveness.
   Service process readiness must not be shown as proof that runtime, telemetry,
   governance, or safe-mode domain truth is healthy.

---

## 5. Frontend Handoff Materials

No new Lovable/frontend task is created by this sidecar. Existing materials that
remain relevant:

| Screen / flow | Frontend contract material | Notes |
|---|---|---|
| Operator Health Status Board | `docs/bff/PKT-011-health-status-board.md`, `docs/screens/PKT-011-health-status-board.md`, `docs/pantheon-handoffs/PKT-011-health-status-board/FRONTEND_CHANGE_SPEC.md` | Current contract is domain-health only. If parent adds service readiness fields, publish an additive contract update. |
| Operator Home | `docs/bff/PKT-013-operator-home.md`, `.coordination/reviews/PKT-013-operator-home-review.md` | Home should continue to follow BFF-supplied `target_refs`, not local route inference. |
| Degraded control guidance | `GET /api/v1/operator/degraded-control-guidance` | Use this for fallback command guidance; do not turn the health board into a write surface. |

Frontend constraints for the parent slice:

- Use the existing BFF client path only.
- Do not poll compose service health endpoints from browser code.
- Do not derive service health by combining unrelated runtime, telemetry,
  incident, governance, search, consultation, or trainer routes client-side.
- Render `meta.surfaces.*`, service/dependency status, and fallback guidance as
  backend-owned.
- Treat missing required readiness fields as a BFF gap and stop implementation.
- Keep readiness/status screens read-only unless a separate command contract
  explicitly provides CTA authority.

---

## 6. Minimal Parent QA Requests

Current BFF compatibility checks:

```http
GET /health
Host: operator-bff
```

```http
GET /api/v1/operator/health-status
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/operator/home
Authorization: Bearer op-42:operator
```

Current newly activated service health checks:

```http
GET /health
Host: consultation-svc
```

```http
GET /health
Host: source-ingest
```

```http
GET /health
Host: search-svc
```

```http
GET /health
Host: training-session-svc
```

After parent implementation, add equivalent checks for:

```http
GET /healthz
GET /livez
GET /readyz
```

Expected parent verification shape:

```bash
docker compose config --quiet
docker compose --profile smoke run --rm smoke-stack
python3 -m pytest -q services/control-plane/bff/test_pkt011_health_status_board_contract.py
```

Add focused tests for each service family touched by the parent task:

- healthy readiness;
- dependency failure or missing store maps to degraded/unavailable;
- legacy `/health` or `/__health__` alias remains compatible;
- compose healthcheck uses the standardized readiness path once implemented.

---

## 7. Suggested Parent Implementation Sequence

1. Inventory all default compose application services and their current health
   routes before editing.
2. Add a shared health payload helper or local equivalent pattern for app
   services: service name, status, timestamp, version/build if available,
   readiness dependencies, and optional counters.
3. Implement `/healthz`, `/livez`, and `/readyz` aliases across default and
   newly materialized services while preserving existing `/health` and
   `/__health__` compatibility.
4. Update root compose healthchecks and `scripts/smoke_honest_stack.py` to the
   standardized readiness path after aliases exist.
5. Decide the BFF exposure model:
   - preserve PKT-011 unchanged and add a separate service-readiness route, or
   - extend PKT-011 additively and update the frontend packet/example.
6. Add BFF tests for fresh, degraded, and unavailable dependency states if BFF
   exposes the service-readiness roster.
7. Keep absent/pending services truthful. Do not present a service as healthy
   before it is in compose and has a real readiness path.

---

## 8. Reviewer Checklist

| Check | Status | Evidence |
|---|---|---|
| Support artifact only | PASS | Only this `support/sidecars/...` packet is created. |
| Canonical truth untouched | PASS | No L1/L2 policy, runtime, registry, governance, compose, BFF, or frontend implementation files are edited by this sidecar. |
| Parent acceptance mapped | PASS | Packet maps readiness aliases, compose/smoke standardization, dependency status, observability, and tests. |
| BFF query gaps identified | PASS | Packet separates PKT-011 domain health from service readiness inventory. |
| Operator journey included | PASS | Packet keeps BFF as the browser-facing health owner and forbids client-side health synthesis. |
| Frontend handoff included | PASS | Packet references PKT-011/PKT-013 materials and BFF-gap behavior. |

---

## 9. Handoff to Reviewer (`Gemini`)

This sidecar is ready for Gemini review as a support-only BFF/frontend handoff
packet for `SVC-HEALTH-OBSERVABILITY-UNIFICATION`.

Recommended reviewer stance:

1. Approve if the packet accurately reflects current BFF/compose/service health
   reality and preserves the sidecar boundary.
2. Use it as input for the parent implementation plan, especially the split
   between PKT-011 domain health and raw service readiness.
3. Let the parent owner decide whether service readiness becomes a new BFF route
   or an additive PKT-011 extension.
