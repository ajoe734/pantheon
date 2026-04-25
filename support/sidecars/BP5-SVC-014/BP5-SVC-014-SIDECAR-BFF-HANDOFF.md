# BP5-SVC-014 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `BP5-SVC-014` - Realize persona platform and consultation read surfaces
**Parent Owner**: Claude
**Parent Reviewer**: Codex
**Parent Status**: `todo`
**Sidecar Owner**: Codex
**Sidecar Reviewer**: Claude
**Helper Kind**: `bff_handoff_packet`
**Generated**: 2026-04-15
**Last Updated**: 2026-04-15
**Review Status**: APPROVED by Claude (2026-04-15)
**Finalization Status**: CLOSED by Codex (2026-04-15)

> This is a support artifact only. It does not modify canonical truth, L1 policy documents, or core runtime, registry, governance, or control-plane implementations. It packages the current BP5-SVC-014 reality into a parent-owner-ready BFF and frontend handoff.

> Finalized after reviewer approval. Parent owner can consume this packet as the current BFF/frontend reality snapshot for BP5-SVC-014 without re-opening canonical design scope.

---

## 1. Parent Task Summary

BP5-SVC-014 is supposed to close two related gaps:

1. expose persona identity, session, and runtime-instance reads through one governed BFF/service path
2. expose consultation read surfaces that cite canonical persona, lineage, and governance objects instead of shadow copies

**Acceptance criteria (from `ai-status.json`)**:
- `persona identity, session, and runtime instance reads are exposed through one governed BFF/service path`
- `consultation surfaces cite canonical persona, lineage, and governance objects instead of shadow copies`

**Primary artifacts in scope**:
- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md`
- `PERSONA_RUNTIME_MODEL.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`

---

## 2. Current Implementation Snapshot (Code-Backed)

### 2.1 What Is Already Live In The BFF

The persona-side read surfaces are already wired in `services/control-plane/bff/main.py`:

| Surface | Route | Backing read_store helper(s) | Status |
|---|---|---|---|
| PS-01 | `GET /api/v1/personas` | `list_personas()` | Implemented |
| PS-02 | `GET /api/v1/personas/{persona_id}` | `get_persona()`, `get_bindings_for_persona()` | Implemented |
| PS-03 | `GET /api/v1/personas/{persona_id}/sessions` | `list_sessions_for_persona()` | Implemented |
| PS-04 | `GET /api/v1/sessions/{session_id}` | `get_session()`, `get_capability_snapshot()` | Implemented |
| PS-05 | `GET /api/v1/personas/{persona_id}/teaching` | `list_teaching_sessions_for_persona()` | Implemented |
| PS-06 | `GET /api/v1/personas/{persona_id}/capabilities` | `get_capability_snapshot_for_persona()` | Implemented |
| Composed view | `GET /api/v1/operator/persona-management/{persona_id}` | PS-02 + CP-03/CP-04 + PS-03 + PS-05 + `get_persona_allowed_actions()` | Implemented |

### 2.2 Seed Data Available Today

The read store seed already exposes enough data for frontend smoke wiring:

- Persona: `persona-alpha`
- Capability snapshot: `cap-001`
- Binding: `binding-042`
- Capital pool: `pool-main`
- Sessions: `sess-001`, `sess-002`
- Teaching session: `teach-001`

This means BP5-SVC-014 does not need to invent a fake persona workbench shape from scratch. There is already a BFF-backed path to render persona profile, bindings, sessions, teaching history, and backend-shaped allowed actions.

### 2.3 Current Persona Management View Shape

`GET /api/v1/operator/persona-management/{persona_id}` already returns:

- `data.persona`
- `data.bindings`
- `data.sessions`
- `data.teaching_sessions`
- `data.allowedActions`
- `meta.snapshot_at`
- `meta.surfaces.persona_bindings`
- `meta.surfaces.capital_pool_bindings`
- `meta.surfaces.persona_sessions`
- `meta.surfaces.teaching_sessions`
- `meta.surfaces.allowed_actions`

This is the best current frontend entrypoint for persona lifecycle screens because it removes client-side joining across persona, binding, session, and teaching surfaces.

### 2.4 Important Current Divergences

These are worth calling out explicitly to avoid frontend or parent-task assumption drift:

1. `snapshot=preferred` is accepted on `operator/persona-management`, but it is not enforcing cross-surface alignment yet. It currently behaves as a best-effort read with `meta.snapshot_at = utc_now()`.

2. `BFF_API_CONTRACT.md` says `PS-01` should be viewer-readable, but `main.py` currently routes all persona reads through `_require_read_role()`, which only accepts `operator`, `approver`, `admin`, or `reviewer`.

3. There is no dedicated HTTP smoke coverage for the persona routes or persona-management composed view today. The existing focused tests are read-store level.

---

## 3. Consultation Gap Matrix

### 3.1 Contracted Target State

`services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md` already defines the consultation read family:

| Surface | Route | Purpose |
|---|---|---|
| CS-01 | `GET /api/v1/personas/{persona_id}/consultations` | Consultation list for a persona |
| CS-02 | `GET /api/v1/consultations/{session_id}` | Consultation detail |
| CS-03 | `GET /api/v1/consultations/{session_id}/participants` | Requester/responder/committee participants |
| CS-04 | `GET /api/v1/consultations/{session_id}/outcome` | Outcome projection |
| CS-05 | `GET /api/v1/consultations/{session_id}/evidence` | Evidence refs |
| CS-06 | `GET /api/v1/personas/{persona_id}/consult-policy` | Consult policy view |

The contract is already aligned with canonical L1 sources:

- `PERSONA_RUNTIME_MODEL.md` for `SessionPersona`, consultation roles, `consult_policy_id`, and `metadata.consultation.*`
- `OPENCLAW_RUNTIME_CONTRACT.md` for session/runtime bridging and control-plane-facing session APIs
- lineage / telemetry / governance references via the consultation evidence model

### 3.2 What Is Missing In Code Right Now

The consultation side is still missing from runtime code:

| Area | Current state | Gap |
|---|---|---|
| `main.py` routes | No consultation routes found | CS-01 to CS-06 are not exposed yet |
| `read_store.py` data model | No consultation or consult-policy helpers found | No seed/read path for consultation sessions, participants, outcomes, evidence, or consult policy |
| Tests | No consultation-focused tests found under `services/control-plane/bff/` | No route or read-store proof that consultation surfaces exist |
| Frontend consumption path | No stable BFF query path exists yet | Consultation UI must remain feature-flagged or contract-only |

### 3.3 Practical Impact On Parent Acceptance

BP5-SVC-014 is currently split in practice:

- **Persona reads**: largely present
- **Consultation reads**: still contract-only

So the first acceptance criterion is materially close, but the second one is not yet satisfied in executable code.

---

## 4. Operator Journey and Frontend Handoff

### 4.1 Persona Management Journey (Use Now)

Recommended current UI journey:

1. Discover a persona through `GET /api/v1/personas`
2. Open the composed view through `GET /api/v1/operator/persona-management/{persona_id}`
3. Render lifecycle context from `data.persona`
4. Render binding and pool context from `data.bindings[*].capital_pool`
5. Render session list from `data.sessions`
6. Render training history from `data.teaching_sessions`
7. Gate persona actions from `data.allowedActions`
8. Respect `meta.surfaces.*.status` before showing empty states

### 4.2 Consultation Journey (Target State, Not Live Yet)

Once BP5-SVC-014 parent implementation lands, the intended consultation journey should be:

1. From persona detail or workbench context, load `GET /api/v1/personas/{persona_id}/consultations`
2. Open consultation detail through `GET /api/v1/consultations/{session_id}`
3. Load participants through `GET /api/v1/consultations/{session_id}/participants`
4. Show outcome through `GET /api/v1/consultations/{session_id}/outcome`
5. Link evidence via `GET /api/v1/consultations/{session_id}/evidence`
6. Use `GET /api/v1/personas/{persona_id}/consult-policy` to explain why consultation was required

### 4.3 Frontend Guidance That Is Safe Today

- Prefer `operator/persona-management` over stitching PS-02, PS-03, and PS-05 client-side.
- Treat `meta.surfaces.*.status = degraded` as a degraded panel, not as absence of data.
- Do not assume viewer tokens can read `PS-01` in current code, even though the contract document says that is the target.
- Do not build consultation screens as production-ready routes yet. There is no live BFF path behind them.
- Keep any consultation UI behind a feature flag until CS-01 to CS-06 are present in code and tested.

### 4.4 Minimal Frontend Request Example (Live)

```http
GET /api/v1/operator/persona-management/persona-alpha?snapshot=preferred
Authorization: Bearer op-42:operator
```

### 4.5 Minimal Frontend Handling Rules

| Condition | Recommended behavior |
|---|---|
| `meta.surfaces.*.status = ok` | Render normally |
| `meta.surfaces.*.status = degraded` | Keep page rendered, but show explicit degraded banner for the affected section |
| no consultation route exists | Hide consultation tab or mark it as "coming soon / unavailable" |
| token is viewer-only | Do not assume persona routes will work; current implementation will reject it |

---

## 5. Suggested Parent Implementation Sequence For Claude

This is the lowest-drift way to finish BP5-SVC-014 without reopening canonical truth:

1. **Keep the existing persona surfaces as the baseline**
   - Do not replace the current persona-management composed view
   - Reuse the existing seed data and route patterns

2. **Add consultation read-store primitives**
   - consultation session seed objects projected from `SessionPersona`
   - consult-policy seed object
   - helpers for list/detail/participants/outcome/evidence/policy

3. **Expose CS-01 to CS-06 in `main.py`**
   - match the route shapes already defined in `CONSULTATION_SURFACE_CONTRACT.md`
   - return the same `data` + `meta` envelope style as existing BFF routes

4. **Add focused tests**
   - read-store tests for consultation helpers
   - HTTP tests for at least CS-01, CS-02, and CS-06
   - auth/RBAC coverage for persona list versus consultation surfaces

5. **Resolve or consciously defer the PS-01 RBAC mismatch**
   - either implement viewer-readable `PS-01`
   - or update the contract/parent notes to say current BP5-SVC-014 keeps operator-level read gating

---

## 6. Verification Evidence

### 6.1 Code Inspection Evidence

Reviewed during this sidecar run:

- `services/control-plane/bff/main.py`
- `services/control-plane/bff/read_store.py`
- `services/control-plane/bff/BFF_API_CONTRACT.md`
- `services/control-plane/bff/CONSULTATION_SURFACE_CONTRACT.md`
- `services/control-plane/bff/test_persona_management.py`
- `services/control-plane/bff/test_w4_remaining_catalog.py`
- `PERSONA_RUNTIME_MODEL.md`
- `OPENCLAW_RUNTIME_CONTRACT.md`
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`

### 6.2 What This Packet Confirms

- persona read surfaces already exist in code
- persona-management composed view already exists in code
- consultation surfaces exist in contract only, not in executable BFF code
- current frontend can safely integrate persona-management now, but not consultation routes

### 6.3 Focused Test Evidence

Executed during this sidecar run:

- `python3 services/control-plane/bff/test_persona_management.py` -> PASS
- `python3 services/control-plane/bff/test_w4_remaining_catalog.py` -> PASS

What those tests prove:

- read-store support for PS-01 to PS-06 is present
- backend-shaped persona allowed actions are present
- seeded persona, binding, session, and capability data are coherent

What they do not prove:

- consultation surfaces are implemented
- persona routes have end-to-end HTTP coverage

---

## 7. Final Close-Out

- Reviewer approval confirmed that the packet accurately distinguishes the already-live persona surfaces from the still-missing consultation surfaces.
- The remaining parent-task gap is explicitly narrowed to consultation read-store primitives, CS-01 to CS-06 route exposure, HTTP smoke coverage, and a conscious PS-01 viewer-RBAC decision.
- This sidecar slice is complete and ready for the parent owner to absorb or reference during canonical implementation work.
- `PS-01` viewer RBAC is aligned with the written contract

---

## 7. Handoff To Reviewer (Claude)

Claude, this packet narrows BP5-SVC-014 to the real remaining work:

- keep the existing persona read path and composed persona-management view
- do not spend time re-inventing persona query shapes that already exist
- focus the parent task on landing CS-01 to CS-06 and the missing consultation data model/tests
- decide whether to close the `PS-01 viewer` contract mismatch now or explicitly defer it

This sidecar is ready for review as a support artifact. Parent-owner absorption remains your call.
