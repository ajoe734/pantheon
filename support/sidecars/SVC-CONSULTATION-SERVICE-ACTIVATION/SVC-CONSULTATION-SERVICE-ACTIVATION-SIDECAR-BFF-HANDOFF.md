# SVC-CONSULTATION-SERVICE-ACTIVATION BFF and Frontend Handoff Packet

**Sidecar Task ID**: `SVC-CONSULTATION-SERVICE-ACTIVATION-SIDECAR-BFF-HANDOFF`
**Parent Task**: `SVC-CONSULTATION-SERVICE-ACTIVATION`
**Parent Owner**: `Codex`
**Parent Reviewer**: `Claude`
**Sidecar Owner**: `Codex`
**Sidecar Reviewer**: `Gemini`
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-28
**Last Refresh**: 2026-04-28T14:41:45Z
**Mutates Canonical**: `no`

This is a support artifact only. It does not update canonical truth, L1 policy,
core contracts, runtime/registry/governance implementation, BFF implementation,
frontend code, or compose wiring. The parent owner decides whether and how to
absorb this packet into the main consultation service activation slice.

---

## 1. Scope Snapshot

`SVC-SERVICE-DISPOSITION` closed with consultation classified as
code-present but deployable service activation deferred. The key disposition was:

- `services/consultation/` already has FastAPI routes, a Dockerfile, health
  endpoint, append/replay-backed `ConsultationStore`, audit events, memo
  publication, and gate handoff records.
- Root `docker-compose.yml` does not currently run a `consultation-svc`.
- The BFF normal path does not use a network service client for consultation
  data. It imports `ConsultationStore` and consumes a configured data dir when
  `PANTHEON_BFF_CONSULTATION_DATA_DIR`, `PANTHEON_CONSULTATION_DATA_DIR`, or
  `CONSULTATION_DATA_DIR` is set; otherwise it falls back to file/snapshot
  adapters depending on the route and test configuration.
- `services/control_plane/internal_api.py` also uses consultation data-dir
  access for committee sponsor handoff behavior.

The parent task should therefore not be framed as "create consultation from
scratch." It is "promote the existing consultation implementation into a root
compose service and move BFF/runtime normal paths to an explicit service
boundary, with any shared-store fallback clearly fenced."

---

## 2. Current Implementation Snapshot

| Area | Current fact | Evidence |
|---|---|---|
| Consultation service | FastAPI app exposes `/health` and `/api/consult/...` lifecycle routes. | `services/consultation/main.py` |
| Service storage | `ConsultationStore` persists request, memo, participant, transcript, evidence, handoff, audit, lifecycle, publication, and outbox records under `CONSULTATION_DATA_DIR`. | `services/consultation/store.py` |
| Service container | Dockerfile starts uvicorn on internal port `8080`; `PORT` is set but the CMD currently hardcodes `8080`. | `services/consultation/Dockerfile` |
| Root compose | No `consultation-svc` service, no consultation data volume, and no operator-bff consultation service URL are present. Runtime-manager has `PANTHEON_RUNTIME_CONSULTATION_DATA_DIR=/data/runtime/consultation`. | `docker-compose.yml` |
| BFF workbench routes | `CW-01` through `CW-04` routes are live in BFF: consult requests, transcript, committees, memos, and overview. | `services/control-plane/bff/main.py`, `docs/bff/CW-*.md` |
| BFF service-store adapter | BFF can project consultation records from a `ConsultationStore` data dir, but that is direct store access, not HTTP. | `services/control-plane/bff/read_store.py` |
| Sponsor decision path | `RecordSponsorDecision` is submitted via `POST /api/v1/operator/commands`; BFF worker currently records sponsor state through `read_store.record_sponsor_decision`. | `services/control-plane/bff/main.py`, `services/control-plane/bff/read_store.py` |
| Runtime sponsor handoff | Legacy internal API helper can write consultation handoff state directly through `ConsultationStore`. | `services/control_plane/internal_api.py` |
| Frontend packet state | Consultation Workbench overview loop is closed; CW-01/CW-02/CW-03/CW-04 module contracts are live or route-live and published as frontend materials. | `.coordination/responses/*consult*`, `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` |

---

## 3. Activation Target for Parent Owner

The parent implementation should keep browser-facing authority on the BFF:

- Frontend continues to call `operator-bff`.
- BFF owns all `/api/v1/...` payload shaping, `allowedActions`, degradation
  semantics, and command receipts.
- `consultation-svc` becomes the normal BFF/runtime backend for consultation
  lifecycle records.
- Direct data-dir access remains only as an explicitly accepted fallback,
  migration bridge, or test fixture path.

Suggested service boundary shape for review, not canonical truth:

| Boundary | Proposed normal-path target |
|---|---|
| Compose service | `consultation-svc` built from `services/consultation/Dockerfile` |
| Health | `GET /health` |
| Internal service port | Existing Dockerfile port `8080`, unless parent owner updates Dockerfile to honor `PORT` |
| Data dir | `CONSULTATION_DATA_DIR=/data/consultation` on a dedicated compose volume |
| BFF env | New explicit service URL such as `PANTHEON_CONSULTATION_SERVICE_URL=http://consultation-svc:8080` |
| Runtime/internal env | Same service URL if runtime sponsor handoff is routed through HTTP |
| Fallback envs | Existing data-dir envs remain fenced; they should not be the default single-VM normal path after activation |

---

## 4. BFF Query Gap Matrix

| BFF route / flow | Current implementation path | Service API readiness | Activation gap |
|---|---|---|---|
| `GET /api/v1/workbench/consultation` | Static BFF overview builder. | No service dependency needed. | Keep as BFF-owned overview; no direct consultation-svc call required. |
| `POST /api/v1/consult/requests` | BFF writes directly to `ConsultationStore` when data dir is configured; otherwise file-backed fallback. | Service has `POST /api/consult/requests`, but BFF payload shape differs and service status starts as `draft`. | Add BFF HTTP client projection for create, preserving BFF response shape: `status=created`, `request_to_session_status=pending_session`, `allowedActions.canCancel`. |
| `GET /api/v1/consult/requests` | BFF reads `consult_requests` through data-dir/file adapter. | Service has `GET /api/consult/requests` with filters for target and status. | Route BFF list to service URL; preserve BFF pagination, filters, `meta.surfaces.consult_request_list`, and no false-empty degraded behavior. |
| `GET /api/v1/consult/requests/{id}` | BFF detail projection over store/file record. | Service has `GET /api/consult/requests/{id}`. | Route detail to service URL and preserve `session_handoff`, `links`, and BFF surface metadata. |
| `POST /api/v1/consult/requests/{id}/cancel` | BFF mutates request status directly in `ConsultationStore` or file store. | Service has no cancel endpoint today. | Add service cancel route or an accepted service API for cancellation before making HTTP normal path mandatory. |
| `GET /api/v1/consultations/{session_id}/transcript` | BFF resolves root session and reads transcript records from consultation/session datasets. | Service has `GET /api/consult/requests/{request_id}/transcript`, keyed by request id. | BFF HTTP client needs a reliable session-to-request mapping, or service needs a session-keyed transcript endpoint/projection. |
| `GET /api/v1/committees` and `GET /api/v1/committees/{id}` | BFF projects committee board from `SessionPersona.metadata.consultation` records or service-store projections. | Service does not expose committee-board-specific routes. | Either keep BFF projection over service request/session payloads or add service query support for committee refs; do not synthesize committee verdicts client-side. |
| `POST /api/v1/operator/commands` with `RecordSponsorDecision` | BFF validates against `allowedActions.canRecordSponsorDecision`, then writes sponsor state/handoff through `read_store.record_sponsor_decision`. | Service has `POST /api/consult/handoffs`, but no sponsor-decision endpoint that updates committee metadata and creates the gate handoff in one accepted operation. | Add consultation service sponsor-decision/handoff API or explicitly accept a service API sequence; BFF command worker should not use shared data-dir writes as the normal path. |
| `GET /api/v1/consult/memos` | BFF lists memos through store/file adapter. | Store has `list_memos`, but service API lacks `GET /api/consult/memos` collection route. | Add service collection route with status filtering, or add a BFF-supported query endpoint on the service before switching normal path. |
| `GET /api/v1/consult/memos/{memo_id}` | BFF reads and projects memo detail. | Service has `GET /api/consult/memos/{memo_id}`. | Route detail through HTTP and preserve BFF detail envelope, mapping, evidence links, staleness, and governance CTA authority. |
| CS-01 to CS-06 persona consultation reads | BFF follows `CONSULTATION_SURFACE_CONTRACT.md`; several reads remain persona/session-policy oriented. | Consultation service can project sessions from request metadata, but `ConsultPolicy` remains persona-plane truth. | Do not force consult-policy into consultation-svc. Keep persona-plane authority and only use consultation-svc for lifecycle objects it owns. |
| Runtime/internal sponsor handoff | `services/control_plane/internal_api.py` writes handoff state through data-dir `ConsultationStore`. | Service has handoff creation route, but not complete sponsor decision semantics. | Runtime path must either use the same service endpoint as BFF or receive an explicit accepted API boundary before activation is claimed complete. |

---

## 5. Operator Journey Handoff

### 5.1 Normal Consultation Workbench Journey

1. Operator opens `/consultation`; frontend fetches
   `GET /api/v1/workbench/consultation`.
2. Operator creates a consult request through
   `POST /api/v1/consult/requests`.
3. BFF forwards the lifecycle write to `consultation-svc` in the activated
   normal path, then returns the existing CW-01 response shape.
4. Persona Plane/session materialization remains outside this sidecar. The UI
   only follows `linked_session_id`, `request_to_session_status`, and
   `session_handoff` values returned by BFF.
5. Operator reads transcript, committee, and memo surfaces through BFF routes.
6. If a sponsor decision is required, UI shows the CTA only when
   `allowedActions.canRecordSponsorDecision` is true.
7. UI submits `RecordSponsorDecision` through `POST /api/v1/operator/commands`
   and polls `GET /api/v1/operator/commands/{command_id}`.
8. BFF command worker records the decision through the activated consultation
   service boundary and returns the existing command status/result envelope.

### 5.2 Degraded or Migration Journey

1. If `consultation-svc` is unavailable, BFF should return existing
   degraded/unavailable surface semantics rather than an authoritative empty
   list.
2. If a data-dir fallback remains during migration, BFF responses must expose
   truthful staleness/source metadata and must not claim service-backed
   freshness.
3. Frontend must keep all consultation actions behind BFF-owned
   `allowedActions`.
4. Browser code must not call `consultation-svc` or `/api/consult/...`
   directly as a hidden fallback.

---

## 6. Frontend Handoff Materials

This sidecar does not create a new Lovable task. The existing frontend source
set remains valid, with one implementation clarification: service activation is
a backend routing change behind the BFF and should not require direct browser
calls to `consultation-svc`.

| Screen / flow | Frontend contract material | Notes |
|---|---|---|
| Consultation Workbench Overview | `docs/bff/PKT-consultation-workbench.md`, `docs/examples/PKT-consultation-workbench.json`, `docs/screens/PKT-consultation-workbench.md` | Overview remains read-only and BFF-owned. |
| Packet family / module order | `docs/pantheon-handoffs/CW-008-consultation-workbench/PACKET_FAMILY.md` | Treat module summaries and live routes as backend-owned; do not flatten into false completion. |
| Consult Request | `docs/bff/CW-01-consult-request.md`, `docs/examples/CW-01-consult-request.json`, `docs/screens/CW-01-consult-request.md` | Request create/list/detail/cancel stays on BFF `/api/v1/consult/requests`. |
| Debate Transcript | `docs/bff/CW-02-debate-transcript.md`, `docs/examples/CW-02-debate-transcript.json` | Replay follows `sequence_no`; missing/gap states become BFF gap or degraded UI, not local repair. |
| Committee Board | `docs/bff/CW-03-committee-board.md`, `docs/examples/CW-03-committee-board.json` | Sponsor CTA follows `allowedActions.canRecordSponsorDecision`; decision writes use BFF operator commands. |
| Red-team Memo | `docs/bff/CW-04-redteam-memo.md`, `docs/examples/CW-04-redteam-memo.json`, `docs/screens/CW-04-redteam-memo.md` | Evidence links and governance handoff authority are BFF-owned. |
| Persona consultation reads | `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` | CS-01 to CS-06 remain read surfaces derived from persona/session truth. |

Frontend implementation constraints:

- Use BFF helpers only; do not add browser fetches to `consultation-svc`.
- Emit a `bff-gap` handoff if a required BFF field disappears during service
  activation.
- Render `meta.staleness` and `meta.surfaces.*` exactly; do not show empty
  states as authoritative when a surface is degraded or unavailable.
- Keep request lifecycle, session linkage, committee verdicts, memo mapping,
  and governance/sponsor CTA authority backend-owned.
- Navigate with returned links such as `route_href`, `links.self`, and
  evidence `link` values; do not construct storage or service URLs in the
  browser.

---

## 7. Minimal Smoke Requests for Parent QA

Service health after compose activation:

```http
GET /health
Host: consultation-svc
```

BFF overview:

```http
GET /api/v1/workbench/consultation
Authorization: Bearer op-42:operator
```

BFF create request shape:

```http
POST /api/v1/consult/requests
Authorization: Bearer op-42:operator
Content-Type: application/json

{
  "from_persona_id": "persona-alpha",
  "target_type": "persona",
  "target_ref": "persona-beta",
  "task": "Review deployment risk for persona-beta before next canary window.",
  "context_refs": [
    { "type": "deployment_plan", "id": "plan-F-042" }
  ],
  "priority": "high",
  "consultation_type": "risk_review"
}
```

BFF list/detail/replay checks:

```http
GET /api/v1/consult/requests
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/consult/requests/{request_id}
Authorization: Bearer op-42:operator
```

```http
GET /api/v1/consultations/{session_id}/transcript
Authorization: Bearer op-42:operator
```

Sponsor decision command shape:

```http
POST /api/v1/operator/commands
Authorization: Bearer op-admin:admin
Content-Type: application/json

{
  "command_type": "RecordSponsorDecision",
  "committee_id": "committee-regime-risk-20260419-081",
  "sponsor_decision": "approved",
  "rationale_ref": "workspace://committee-rationales/committee-regime-risk-20260419-081/final",
  "note": "Operator verified committee evidence and sponsor handoff."
}
```

The sample ids are for QA shape validation only. Use fixture ids that exist in
the target test environment.

---

## 8. Verification Evidence

Focused verification run by this sidecar:

```bash
python3 services/consultation/run_smoke.py
```

Result: `2 tests OK`.

```bash
python3 -m pytest \
  services/control-plane/bff/test_consultation_surfaces.py \
  services/control-plane/bff/test_pkt015_consultation_workbench_contract.py \
  services/control-plane/bff/test_cw01_consult_request_contract.py \
  services/control-plane/bff/test_cw02_debate_transcript_contract.py \
  services/control-plane/bff/test_cw03_committee_board_contract.py \
  services/control-plane/bff/test_cw04_redteam_memo_contract.py -q
```

Result: `41 passed, 14 warnings in 4.26s`. The warnings are
`datetime.utcnow()` deprecation warnings from `services/control-plane/bff/read_store.py`.

Static evidence gathered by this sidecar:

| Target | Evidence observed |
|---|---|
| Consultation service smoke coverage | `services/consultation/smoke_test.py` covers health, request lifecycle, participant assignment, evidence attachment, transcript events, memo publish immutability, gate handoff, audit log, restart/replay, and outbox presence. |
| BFF CW-01 contract | `services/control-plane/bff/test_cw01_consult_request_contract.py` covers create/list/detail/cancel and service-store path behavior. |
| BFF CW-02 contract | `services/control-plane/bff/test_cw02_debate_transcript_contract.py` covers transcript envelope, ordering, pagination, actor identity, degradation, auth, and service-store path behavior. |
| BFF CW-03 contract | `services/control-plane/bff/test_cw03_committee_board_contract.py` covers committee list/detail, allowedActions, and service-store path behavior. |
| BFF CW-04 contract | `services/control-plane/bff/test_cw04_redteam_memo_contract.py` covers memo list/detail, mapping, evidence, degradation, governance CTA, and service-store path behavior. |
| BFF overview | `services/control-plane/bff/test_pkt015_consultation_workbench_contract.py` covers the Consultation Workbench overview route. |
| CS-01 to CS-06 | `services/control-plane/bff/test_consultation_surfaces.py` covers persona consultation list/detail/participants/outcome/evidence/policy routes. |

No compose activation or stack-boot proof is claimed by this packet because the
parent task has not implemented the service activation yet. The parent should
rerun these focused tests plus compose config or stack smoke after implementation.

---

## 9. Non-Claims

This packet does not claim:

| Non-claim | Correct owner / follow-up |
|---|---|
| Root compose now includes `consultation-svc`. | Parent task `SVC-CONSULTATION-SERVICE-ACTIVATION`. |
| BFF already uses an HTTP consultation service client in the normal path. | Parent task implementation. |
| Runtime/internal sponsor handoff already uses consultation-svc. | Parent task implementation or explicit API-boundary review. |
| Consultation service has all collection/query endpoints needed by BFF HTTP normal path. | Parent task must add or accept the missing service API boundaries. |
| Persona Plane materializes sessions from consult requests. | Persona/session activation work outside this sidecar. |
| Frontend needs new direct service calls. | Not claimed; frontend should stay BFF-only. |
| Full default-stack boot proof. | `SVC-COMPOSE` / parent verification. |
| Production auth hardening for consultation service endpoints. | Future hardening unless parent task explicitly includes it. |

---

## 10. Reviewer Focus for Gemini

1. Confirm this packet is support-only and does not promote new canonical
   consultation, BFF, compose, or frontend truth.
2. Confirm the activation gaps are correctly framed around explicit service
   boundaries, not hidden shared data-dir normal paths.
3. Confirm the frontend handoff preserves BFF as the only browser-facing route
   family and keeps `allowedActions` backend-owned.
4. Confirm the sponsor-decision path calls out both BFF and runtime/internal
   handoff risks.
5. Confirm the parent owner can use this packet as a checklist without treating
   the suggested env/port names as canonical decisions.

Recommended disposition: approve this sidecar as support material, then let the
parent owner decide which gaps to implement or explicitly defer in the main
activation slice.
