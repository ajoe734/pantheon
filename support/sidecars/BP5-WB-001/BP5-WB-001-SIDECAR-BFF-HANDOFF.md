# BP5-WB-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `BP5-WB-001` — Packetize Persona Workbench Wave 1 surfaces
**Parent Owner**: Codex
**Parent Reviewer**: Claude
**Sidecar Task**: `BP5-WB-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: Claude
**Sidecar Reviewer**: Codex
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-16
**Mutates canonical**: no

> This is a support artifact only. It does not modify canonical truth, L1 policy
> documents, or core runtime/registry/governance implementations. It catalogs all
> BFF routes relevant to Persona Workbench Wave 1, identifies query gaps, maps
> operator journeys per module, and provides frontend handoff materials for BP5-WB-001.

---

## 1. Scope

BP5-WB-001 packetizes the Persona Workbench Wave 1 surface family. Per the
`pantheon-console-workbench-backlog.md` inventory, Wave 1 covers four modules:

| Module | Surface scope | Recommended wave |
|---|---|---|
| `PM-01` Persona Management composed screen | `GET /api/v1/operator/persona-management/{persona_id}` | Wave 1 |
| `Module A` — Persona Drilldowns | `PS-01` to `PS-06` | Wave 1 |
| `Module B` — Capital / Binding Drilldowns | `CP-01` to `CP-04` | Wave 1 |
| `Module C` — Deployment / Approval Drilldowns | `DP-01` to `DP-04` (shared with PKT-001) | Wave 1 shared |

Wave 2 surfaces (Tool profile, Consult policy, standalone persona IA shell,
`RT-*`/`TL-*`/`LN-*`/`IN-*`/`EV-*` drilldowns) are out of scope for this packet.

---

## 2. BFF Route Inventory

All routes are implemented in `services/control-plane/bff/main.py`. Read surfaces
require at least `operator`, `approver`, `admin`, or `reviewer` role.

### 2.1 PM-01 — Persona Management Composed View

| ID | Method + Path | Role required | Composes | Status |
|---|---|---|---|---|
| `PM-01` | `GET /api/v1/operator/persona-management/{persona_id}` | read-role | PS-02, CP-03, CP-04, PS-03, PS-05, `allowedActions` | Live |

**Response shape** (top-level):
```
data.persona            — persona summary (lifecycle_state, mandate, strategy_family)
data.bindings[]         — bindings enriched with capital_pool detail
data.sessions[]         — active / idle sessions for this persona
data.teaching_sessions[]— teaching session history
data.allowedActions     — backend-shaped lifecycle action gates
meta.snapshot_at        — UTC timestamp of the composed snapshot
meta.surfaces.*         — per-surface degradation state (ok / degraded / unavailable)
```

### 2.2 Module A — Persona Drilldowns

| ID | Method + Path | Filters / params | Status |
|---|---|---|---|
| `PS-01` | `GET /api/v1/personas` | `lifecycle_state`, `mandate`, `strategy_family` | Live |
| `PS-02` | `GET /api/v1/personas/{persona_id}` | — | Live (returns persona + `bindings[]`) |
| `PS-03` | `GET /api/v1/personas/{persona_id}/sessions` | `status` | Live |
| `PS-04` | `GET /api/v1/sessions/{session_id}` | — | Live (returns session + `capability_snapshot`) |
| `PS-05` | `GET /api/v1/personas/{persona_id}/teaching` | `status` | Live |
| `PS-06` | `GET /api/v1/personas/{persona_id}/capabilities` | — | Live |

### 2.3 Module B — Capital / Binding Drilldowns

| ID | Method + Path | Filters / params | Status |
|---|---|---|---|
| `CP-01` | `GET /api/v1/capital-pools` | `status`, `risk_policy_ref` | Live |
| `CP-02` | `GET /api/v1/capital-pools/{pool_id}` | — | Live (returns pool + `bindings[]`) |
| `CP-03` | `GET /api/v1/bindings` | `capital_pool_id`, `role`, `validity` | Live |
| `CP-04` | `GET /api/v1/bindings/{binding_id}` | — | Live (returns binding + `persona`) |

### 2.4 Module C — Deployment / Approval Drilldowns (shared)

| ID | Method + Path | Filters / params | Status |
|---|---|---|---|
| `DP-01` | `GET /api/v1/deployment-plans` | `stage`, `target_pool_id` | Live |
| `DP-02` | `GET /api/v1/deployment-plans/{plan_id}` | — | Live (returns plan + `approval_decision`) |
| `DP-03` | `GET /api/v1/approval-decisions` | `outcome`, `reviewer`, `time_range` | Live |
| `DP-04` | `GET /api/v1/approval-decisions/{decision_id}` | — | Live |

> Module C is shared with `PKT-001`. Do not fork or duplicate the governance packet
> contract. The Persona Workbench must reference, not re-implement, the approval flow.

---

## 3. BFF Query Gaps

The following gaps are **non-blocking for Wave 1 packetization** because all
required read routes exist. They are recorded here so BP5-WB-001 can annotate
its packet family with accurate backend caveats.

| Gap ID | Surface | Description | Blocking? |
|---|---|---|---|
| `WB-GAP-01` | PM-01 / PS-01 | No Persona Workbench Home composed view. `GET /api/v1/personas` returns a flat list without per-persona enrichment (active session count, latest deployment stage, binding count). Operator must load multiple endpoints to assemble a home-screen summary. | No — Wave 2; use `PS-01` list + per-card drill for now |
| `WB-GAP-02` | PS-03 | Session list has no pagination. `/api/v1/personas/{persona_id}/sessions` returns all sessions in a single response. This could be a large payload for long-lived personas. | No — pagination deferred to Wave 2 |
| `WB-GAP-03` | PS-01 / PS-02 | `get_persona` reads from `read_store._data["personas"]` only (no `CanonicalSnapshotAdapter`). If the governance plane writes personas to a separate store and sets `PANTHEON_GOVERNANCE_DATA_DIR`, those personas will not appear in the BFF persona list until the adapter is extended. | No — Wave 2; current read model covers seed + demo data |
| `WB-GAP-04` | Module C (DP-03) | `time_range` filter on `/api/v1/approval-decisions` is accepted but deferred — no filtering is applied. Operator UI should not depend on time-range filtering for correctness. | No — deferred in v1 by contract |
| `WB-GAP-05` | Module C (DP-02) | `GET /api/v1/deployment-plans/{plan_id}` returns the plan plus its linked `approval_decision` inline. The full Operator Review screen (`PKT-001`) is served by the dedicated `GET /api/v1/operator/deployment-review/{plan_id}` composed view, which includes pool, bindings, runtime binding, rollbacks, `allowedActions`, and `review`. The Persona Workbench should link to the PKT-001 review screen for full operator command authority rather than recreating the composed view. | No — use `PKT-001` link |

---

## 4. Operator Journeys

### 4.1 Persona Management Journey (PM-01)

```
1. Navigate to persona (from persona list or direct link).
2. GET /api/v1/operator/persona-management/{persona_id}?snapshot=preferred
3. Display: persona summary, enriched bindings, sessions, teaching history.
4. Read data.allowedActions to gate lifecycle buttons.
5. If a session is active, drill into GET /api/v1/sessions/{session_id} (PS-04).
6. Operator command (e.g. PauseRuntime) → POST /api/v1/operator/commands
   then poll GET /api/v1/operator/commands/{command_id} for status.
7. Check meta.surfaces.* on every response; show degraded-panel per PKT-005
   if any surface is not "ok".
```

### 4.2 Persona Drilldown Journey (Module A)

```
1. Persona list: GET /api/v1/personas?lifecycle_state=active
2. User selects persona → Persona detail: GET /api/v1/personas/{persona_id}
   (also returns bindings[]).
3. Optional drills:
   - Sessions: GET /api/v1/personas/{persona_id}/sessions
   - Session detail: GET /api/v1/sessions/{session_id}
   - Teaching history: GET /api/v1/personas/{persona_id}/teaching
   - Capability snapshot: GET /api/v1/personas/{persona_id}/capabilities
4. To navigate to the full lifecycle management screen, redirect to PM-01.
```

### 4.3 Capital / Binding Drilldown Journey (Module B)

```
Entry path A — from capital pool:
1. Capital pool list: GET /api/v1/capital-pools?status=ready
2. Pool detail: GET /api/v1/capital-pools/{pool_id}
   (returns pool + bindings[]).
3. Binding detail: GET /api/v1/bindings/{binding_id}
   (returns binding + persona).

Entry path B — from persona management view:
1. The persona management composed view (PM-01) already returns enriched bindings[].
   Each binding includes capital_pool inline; no separate CP-01 fetch is needed.
2. Use the binding.capital_pool_id to link to the pool detail page.
```

### 4.4 Deployment / Approval Drilldown Journey (Module C — shared)

```
Context: The Persona Workbench enters this journey from a binding or session context
(e.g. "this persona's last deployment" → link to the deployment plan).

1. Deployment plan list: GET /api/v1/deployment-plans?target_pool_id={pool_id}
2. Plan detail: GET /api/v1/deployment-plans/{plan_id}
   (returns plan + approval_decision inline).
3. For full operator review and command authority, redirect to the PKT-001
   Deployment Review screen at GET /api/v1/operator/deployment-review/{plan_id}.
   Do NOT re-implement the approval or rollback command flow in the Persona Workbench.
4. Approval decision history: GET /api/v1/approval-decisions?outcome=approved
5. Approval decision detail: GET /api/v1/approval-decisions/{decision_id}
```

---

## 5. Seed Data for UI Smoke Testing

All IDs are defined in `services/control-plane/bff/read_store.py` →
`_default_read_data()`.

| Object | ID | Notes |
|---|---|---|
| Persona | `persona-alpha` | lifecycle_state: active; mandate: systematic_crypto_trading |
| Persona | `p-risk-analyst` | lifecycle_state: active; mandate: risk_review |
| Capital pool | `pool-main` | status: ready; single_runtime_enforced: true |
| Binding | `binding-042` | persona-alpha → pool-main; role: primary; validity: active |
| Session | `sess-001` | persona-alpha; status: active; tools: signal_read, artifact_load, telemetry_query |
| Session | `sess-002` | persona-alpha; status: idle |
| Teaching session | `teach-001` | persona-alpha; status: completed; topic: drawdown_threshold_tuning |
| Capability snapshot | `cap-001` | persona-alpha |
| Deployment plan | `plan-F-042` | stage: paper; artifact_id: artifact-042 |
| Approval decision | `approval-042` | outcome: approved; risk_level: low |
| Runtime binding | `runtime-042` | deployment_mode: paper; plan: plan-F-042 |

---

## 6. Frontend Handoff Notes

### 6.1 Role Gating

All read surfaces require an `Authorization: Bearer <token>` with at least one of:
`operator`, `approver`, `admin`, or `reviewer` role.

Example token format (stub for local dev): `Bearer op-42:operator`

### 6.2 Degraded-Panel Rules

Every response includes `meta.staleness` or `meta.surfaces.*`. The frontend
**must** respect these fields and show an explicit degraded panel rather than
empty state. See `PKT-005` for the canonical degradation banner contract.

- If `meta.surfaces.{surface}.status == "degraded"`, show a partial-data warning.
- If `meta.surfaces.{surface}.status == "unavailable"`, show an unavailable panel.
- Never derive health state locally; always read from `meta`.

### 6.3 Composed View vs. Individual Routes

| Use case | Recommended approach |
|---|---|
| Persona lifecycle management screen (per-persona) | `GET /api/v1/operator/persona-management/{persona_id}` (PM-01 composed) |
| Persona list / selector / browse | `GET /api/v1/personas` (PS-01) |
| Persona detail card without session/teaching context | `GET /api/v1/personas/{persona_id}` (PS-02) |
| Session detail in-context | `GET /api/v1/sessions/{session_id}` (PS-04) |
| Capital pool detail with binding list | `GET /api/v1/capital-pools/{pool_id}` (CP-02) |
| Deployment + approval full review | `GET /api/v1/operator/deployment-review/{plan_id}` (PKT-001; do not fork) |

### 6.4 allowedActions Semantics

The `data.allowedActions` object in the PM-01 composed view is backend-shaped.
The frontend must not infer action availability from persona fields directly.

```
canActivate       — persona in "draft" state
canEdit           — persona not in "retired" state
canDelete         — persona in "draft" state
canRetire         — persona in "active" state
canPause          — persona active AND no active sessions
canTerminateSession — one or more active sessions exist
canPauseSession   — one or more active sessions exist
canViewTeachingHistory — at least one teaching session exists
```

All destructive actions (pause, retire, terminate session) must be submitted via
`POST /api/v1/operator/commands` and polled at
`GET /api/v1/operator/commands/{command_id}`.

### 6.5 Module C Boundary

Module C (Deployment / Approval Drilldowns) is **shared** with `PKT-001`.
The Persona Workbench packet family must:
- Link to the existing Deployment Review screen (`PKT-001`) for approval command authority.
- Not duplicate the `allowedActions` derivation logic for deployments.
- Carry forward the `PKT-001` degradation and staleness copy exactly.

### 6.6 Recommended Example Requests

**PM-01 Persona Management Composed View:**
```http
GET /api/v1/operator/persona-management/persona-alpha?snapshot=preferred
Authorization: Bearer op-42:operator
```

**PS-01 Persona List:**
```http
GET /api/v1/personas?lifecycle_state=active
Authorization: Bearer op-42:operator
```

**CP-02 Capital Pool Detail:**
```http
GET /api/v1/capital-pools/pool-main
Authorization: Bearer op-42:operator
```

**CP-04 Binding Detail:**
```http
GET /api/v1/bindings/binding-042
Authorization: Bearer op-42:operator
```

**DP-02 Deployment Plan Detail:**
```http
GET /api/v1/deployment-plans/plan-F-042
Authorization: Bearer op-42:operator
```

---

## 7. Wave 2 Dependencies (Out of Scope for Wave 1)

The following items are explicitly out of scope for BP5-WB-001 but should be
noted in the Wave 1 packet family as known Wave 2 work:

| Item | Blocker |
|---|---|
| Persona Workbench Home composed view (multi-persona summary) | Needs net-new BFF route |
| Standalone persona list / detail IA shell | Needs packet-language work; routes exist |
| Tool profile panel | Needs dedicated BFF read route |
| Consult policy panel | `GET /api/v1/personas/{persona_id}/consult-policy` (CS-06) exists but needs packet handoff |
| Session pagination (PS-03) | Deferred in BFF v1 |
| Module D (Runtime), Module E (Telemetry/Lineage), Module F (Incident/Evolution) | Shared drilldown contracts not yet packetized for Persona scope |

---

## 8. Reviewer Checklist (for Codex)

| Check | Expected |
|---|---|
| Support artifact only | File exists only under `support/sidecars/` |
| Canonical truth untouched | No L1 or core runtime files edited |
| PM-01 endpoint correct | `GET /api/v1/operator/persona-management/{persona_id}` in `main.py` |
| All Module A routes verified | PS-01 to PS-06 in `main.py` |
| All Module B routes verified | CP-01 to CP-04 in `main.py` |
| All Module C routes verified | DP-01 to DP-04 in `main.py` |
| Seed data accurate | Confirmed in `read_store._default_read_data()` |
| BFF gaps non-blocking | WB-GAP-01 to WB-GAP-05 annotated as deferred |
| Module C boundary stated | Shared with PKT-001; no fork |
| Wave 2 items listed | Tool profile, Consult policy, session pagination, home composed view |
| Frontend handoff clear | Example requests + allowedActions semantics documented |
