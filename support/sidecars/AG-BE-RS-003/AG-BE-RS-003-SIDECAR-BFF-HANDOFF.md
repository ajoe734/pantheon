# AG-BE-RS-003 BFF & Frontend Handoff Packet

> Type: bff_handoff_packet  
> Sidecar: AG-BE-RS-003-SIDECAR-BFF-HANDOFF  
> Parent task: AG-BE-RS-003 (implement `agora-expert-consult` skill)  
> Author: Claude  
> Reviewer: Claude2  
> Date: 2026-06-20  
> Status: ready-for-review  
>
> **This document is a support artifact only.  
> It does not modify canonical truth, BFF main.py, or any L1 policy.**  
> The parent owner decides whether to absorb these recommendations into the main implementation.

---

## 1. Purpose

AG-BE-RS-003 adds the `agora-expert-consult` skill to the Pantheon Agora layer.
The skill dispatches a minimized `ContextBundle` to an OpenClaw
`consult | committee | red_team` session and collects structured `Memo` objects
from central expert personas.

This packet answers three questions for the implementer and the frontend team:

1. **BFF query gaps**: which routes do not exist yet but the skill output will require.
2. **Operator journey**: the end-to-end flow an operator/researcher sees in the UI.
3. **Frontend handoff materials**: contract shape, privacy rules, and UX requirements
   the frontend (Agora Research workbench) must satisfy.

---

## 2. Skill Contract Reference

Source: `docs/04/pantheon_agora_cross_repo_2026-06-20/design-closure/skills/agora/expert-consult/SPEC.md`

### 2.1 Input (ExpertConsultInput)

```ts
type ExpertConsultInput = {
  strategySpecRef: string;        // required — StrategySpec or StrategySpecSeed ref
  question:        string;        // required — the consult question
  relevantSymbols: string[];      // symbols in scope
  evidenceRefs:    string[];      // evidence artifacts to share with experts
  dataCutoff:      string;        // ISO-8601 — prevents future-data leakage
  requiredExpertise: string[];    // expertise tags to route to the right personas
  mode:            "consult" | "committee" | "red_team";
  privateFieldsAllowed: string[]; // only these fields may be in ContextBundle
};
```

### 2.2 Output (ExpertConsultOutput)

```ts
type ExpertConsultOutput = {
  consultGroupId:  string;        // stable id for the whole consult group
  sessionRefs:     string[];      // OpenClaw session ids created
  memos: Array<{
    personaId:    string;
    memoRef:      string;
    conclusion:   string;
    confidence:   number;         // 0.0–1.0
    evidenceRefs: string[];
  }>;
  disagreements:    unknown[];    // unresolved persona conflicts — must be preserved
  missingEvidence:  string[];     // evidence refs that were unavailable
  privacyManifest: {
    rawPromptIncluded:    false;  // invariant — never true
    userIdentityIncluded: false;  // invariant — never true
    fieldsShared:         string[];
  };
};
```

### 2.3 Hard Privacy Rules (non-negotiable)

- Raw private prompt **must not** appear in ContextBundle.
- User identity **must not** appear in ContextBundle.
- Only `strategySpecRef`, `question`, approved `evidenceRefs`, `relevantSymbols`, and `dataCutoff` leave the private servant scope.
- Central expert personas reply with `Memo | Evidence | Critique | RiskNote` only — no writeback to private memory.
- `disagreements` are preserved as-is; the servant must not silently merge them.

---

## 3. BFF Query Gap Analysis

### 3.1 Already Implemented

| Route | Handler | Notes |
|---|---|---|
| `POST /api/v1/consult/requests` | `create_consult_request` | CW-01; creates consult request in read-store |
| `GET /api/v1/consult/requests` | `list_consult_requests` | CW-01; paginated list |
| `GET /api/v1/consult/requests/{id}` | `get_consult_request` | CW-01; detail |
| `POST /api/v1/consult/requests/{id}/cancel` | `cancel_consult_request` | CW-01 |
| `GET /api/v1/consult/memos` | `list_consult_memos` | CW-04; paginated memo list |
| `GET /api/v1/consult/memos/{memo_id}` | `get_consult_memo` | CW-04; memo detail |
| `GET /api/v1/committees` | `list_committees` | CW-03 committee board |
| `GET /api/v1/committees/{id}` | `get_committee` | CW-03 detail |
| `GET /api/v1/workbench/consultation` | `get_consultation_workbench_overview` | workbench tile |
| `POST /bff/agora/committee/{sessionId}/evidence-pack` | evidence pack create | agora committee |
| `GET /bff/agora/research-tasks` | `bff_agora_research_tasks` | research task list stub |

### 3.2 Missing Routes (gaps for AG-BE-RS-003)

The skill returns `consultGroupId` and `memos`.  
There is **no BFF route** to:

| Gap ID | Missing Route | Required For |
|---|---|---|
| RS-GAP-01 | `POST /bff/agora/research/expert-consult` | Dispatch an expert-consult skill call from the frontend research workbench |
| RS-GAP-02 | `GET /bff/agora/research/expert-consult/{consultGroupId}` | Poll consult group status; retrieve memos, disagreements, missingEvidence |
| RS-GAP-03 | `GET /bff/agora/research/expert-consult/{consultGroupId}/memos` | Paginated memo list for a specific consult group |
| RS-GAP-04 | `GET /bff/agora/research/expert-consult/{consultGroupId}/privacy-manifest` | Fetch the privacy manifest for audit/transparency panel |

Additionally:

| Gap ID | Issue | Detail |
|---|---|---|
| RS-GAP-05 | `agora/research/router.py` is an empty stub | `services/control-plane/bff/agora/research/router.py` returns an empty `APIRouter`; the expert-consult routes belong here per the router factory pattern |
| RS-GAP-06 | No `ContextBundle` construction status route | Frontend cannot show "building context…" progress without a status sub-resource; suggest a lightweight `status` field in the RS-GAP-02 response rather than a separate route |

### 3.3 Route Precedence Note

- These routes belong under `/bff/agora/research/...` (Agora-namespaced) rather than under `/api/v1/consult/...` (governance consultation surface).
- The existing `/api/v1/consult/...` routes represent governance-layer session projections backed by the Persona Plane (PERSONA_RUNTIME_MODEL.md §13–14).
- The new `/bff/agora/research/expert-consult/...` routes are the research-workbench-facing projection of an OpenClaw skill invocation result — a different authority chain.
- Both may eventually reference the same underlying `memoRef` artefacts, but they are separate surfaces.

---

## 4. Operator Journey

### 4.1 Happy Path — Research Workbench Expert Consult

```text
Researcher opens Agora Research Workbench
  └─ GET /bff/agora/research-tasks                 # see open research tickets

Researcher selects a research ticket with a StrategySpecRef
  └─ GET /api/v1/research/tickets/{ticket_id}      # load ticket detail

Researcher triggers expert consult (e.g., "Ask experts" button)
  └─ POST /bff/agora/research/expert-consult       # RS-GAP-01 (missing)
       body: {
         strategySpecRef, question, relevantSymbols,
         evidenceRefs, dataCutoff, requiredExpertise,
         mode: "consult" | "committee" | "red_team",
         privateFieldsAllowed
       }
       → returns: { consultGroupId, status: "dispatched", sessionRefs[] }

Frontend polls until all sessions complete
  └─ GET /bff/agora/research/expert-consult/{consultGroupId}   # RS-GAP-02 (missing)
       → returns: {
           status: "pending" | "partial" | "complete" | "degraded",
           memos[], disagreements[], missingEvidence[],
           privacyManifest
         }

Frontend renders memo panel
  └─ GET /bff/agora/research/expert-consult/{consultGroupId}/memos  # RS-GAP-03 (optional, for pagination)

Researcher clicks "View privacy manifest" in audit panel
  └─ GET /bff/agora/research/expert-consult/{consultGroupId}/privacy-manifest  # RS-GAP-04 (optional, may inline in RS-GAP-02)
```

### 4.2 Degraded Path — Expert Unavailable

```text
POST /bff/agora/research/expert-consult returns { status: "dispatched" }

GET /bff/agora/research/expert-consult/{consultGroupId} returns:
  {
    status: "degraded",
    memos: [... available memos ...],
    disagreements: [],
    missingEvidence: ["expertise:macro-regime", "expertise:risk-analyst"],
    privacyManifest: { ... }
  }

Frontend must:
  - NOT suppress the "degraded" status banner
  - Show which expertise was unavailable (from missingEvidence)
  - Allow researcher to proceed with partial memos if they choose
```

### 4.3 Privacy Violation Path — Invariant Check

```text
The backend implementation must enforce:
  privacyManifest.rawPromptIncluded === false  (invariant)
  privacyManifest.userIdentityIncluded === false  (invariant)

If either invariant is violated, the BFF must return 500 / POLICY_SUPPRESSED
and NOT return the ExpertConsultOutput to the frontend.
```

---

## 5. Frontend Handoff Materials

### 5.1 RS-GAP-01 — Dispatch Expert Consult

**Route**: `POST /bff/agora/research/expert-consult`  
**Auth**: `operator` or `researcher` role (read-role is sufficient; no write-level auth needed for research dispatch)  
**Idempotency**: Require `X-Idempotency-Key` header to prevent duplicate dispatches on retry.

**Request body**:
```json
{
  "strategySpecRef": "strat-spec-abc123",
  "question": "Is the current macro regime suitable for this momentum strategy?",
  "relevantSymbols": ["AAPL", "MSFT"],
  "evidenceRefs": ["artifact-042", "experiment-run-xyz"],
  "dataCutoff": "2026-06-20T00:00:00Z",
  "requiredExpertise": ["macro-regime", "momentum", "risk"],
  "mode": "consult",
  "privateFieldsAllowed": ["strategySpecRef", "question", "relevantSymbols", "evidenceRefs"]
}
```

**Response (202 Accepted)**:
```json
{
  "data": {
    "consultGroupId": "cg-abc123",
    "status": "dispatched",
    "sessionRefs": ["oc-session-001", "oc-session-002"],
    "mode": "consult",
    "dispatchedAt": "2026-06-20T18:00:00Z"
  },
  "meta": {
    "snapshot_at": "2026-06-20T18:00:00Z",
    "idempotency": { "idempotencyKey": "...", "replayed": false }
  }
}
```

**Frontend requirements**:
- Show "Dispatching expert consult…" spinner on 202.
- Begin polling RS-GAP-02 immediately after 202.
- On idempotent replay (`replayed: true`), resume polling without creating a new UI flow.

---

### 5.2 RS-GAP-02 — Consult Group Status & Result

**Route**: `GET /bff/agora/research/expert-consult/{consultGroupId}`

**Response (200 OK)**:
```json
{
  "data": {
    "consultGroupId": "cg-abc123",
    "status": "complete",
    "mode": "consult",
    "strategySpecRef": "strat-spec-abc123",
    "dispatchedAt": "2026-06-20T18:00:00Z",
    "completedAt": "2026-06-20T18:02:30Z",
    "memos": [
      {
        "personaId": "persona-macro-analyst",
        "memoRef": "memo-001",
        "conclusion": "Regime is transitioning; momentum factor is elevated but fragile.",
        "confidence": 0.72,
        "evidenceRefs": ["artifact-042", "telemetry-event-999"]
      },
      {
        "personaId": "persona-risk-officer",
        "memoRef": "memo-002",
        "conclusion": "Volatility cluster warrants reduced sizing.",
        "confidence": 0.85,
        "evidenceRefs": ["artifact-042"]
      }
    ],
    "disagreements": [],
    "missingEvidence": [],
    "privacyManifest": {
      "rawPromptIncluded": false,
      "userIdentityIncluded": false,
      "fieldsShared": ["strategySpecRef", "question", "relevantSymbols", "evidenceRefs"]
    }
  },
  "meta": {
    "snapshot_at": "2026-06-20T18:02:30Z",
    "surfaces": {
      "expert_consult_group": "ok"
    }
  }
}
```

**Status values** the frontend must handle:

| `status` | Meaning | Frontend action |
|---|---|---|
| `dispatched` | OpenClaw sessions created, no memos yet | Show spinner; poll again in 2–5 s |
| `partial` | Some sessions complete, others still running | Show available memos with "X of Y experts responded" label; continue polling |
| `complete` | All sessions closed, memos collected | Stop polling; render full memo panel |
| `degraded` | Some experts unavailable; result is partial-final | Stop polling; show degraded banner with `missingEvidence` list |
| `failed` | Dispatch failed or context build error | Show error with `blocking_reasons` if present; offer retry |

**Polling guidance**:
- Poll interval: 2 s for first 30 s, then back-off to 5 s.
- Stop polling when `status` is `complete | degraded | failed`.
- Maximum poll duration: 120 s; then show timeout message linking to the consult group id for later retrieval.

---

### 5.3 RS-GAP-03 — Paginated Memo List (optional)

**Route**: `GET /bff/agora/research/expert-consult/{consultGroupId}/memos`

Only needed if the consult group is expected to have >10 memos (committee mode with many participants). For `consult` and `red_team` modes, inlining memos in the RS-GAP-02 response is sufficient.

If implemented:
```json
{
  "items": [ /* same memo shape as RS-GAP-02 */ ],
  "page_info": { "next_page_token": null, "total": 5 },
  "meta": { "snapshot_at": "..." }
}
```

---

### 5.4 RS-GAP-04 — Privacy Manifest (optional, recommend inline)

**Recommendation**: Inline `privacyManifest` in the RS-GAP-02 response (already shown above).  
A separate route is only needed if the frontend has a dedicated audit modal that loads the manifest independently.

If implemented as separate route:
```
GET /bff/agora/research/expert-consult/{consultGroupId}/privacy-manifest
```
Response: the `privacyManifest` object from RS-GAP-02 `data`, with the same two invariant fields.

---

### 5.5 Disagreements Panel — UX Requirement

When `disagreements` is non-empty, the frontend **must**:

1. Show a "Disagreements" section — not hide it.
2. Display each disagreement as a readable conflict entry (persona A concluded X; persona B concluded Y).
3. Not attempt to merge or resolve disagreements in the UI — the servant persona decides whether to escalate.
4. Offer a "View raw disagreement" link that surfaces the underlying session refs.

This follows the skill rule: *"disagreement 必須保留，不可由僕人偷偷消除"*.

---

### 5.6 Context Bundle Size Guard — Frontend Hint

The skill spec says *"最小化 ContextBundle"*. The BFF implementation should:

- Validate that `privateFieldsAllowed` contains no fields outside the allowlist (`strategySpecRef`, `question`, `relevantSymbols`, `evidenceRefs`, `dataCutoff`).
- Return `400 VALIDATION_FAILED` with `"code": "CONTEXT_SCOPE_VIOLATION"` if the request attempts to include raw journal, full user profile, or cross-scope fields.
- The frontend should surface this error as: *"Some requested context was excluded to protect privacy."*

---

## 6. Verification Checklist (for AG-BE-RS-003 implementer)

| # | Check | Method |
|---|---|---|
| V-01 | RS-GAP-01 route returns 202 with `consultGroupId` | Unit test with mock OpenClaw adapter |
| V-02 | RS-GAP-02 route polls correctly through `dispatched → partial → complete` lifecycle | State-machine unit test |
| V-03 | `privacyManifest.rawPromptIncluded` is always `false` in any response | Invariant test asserting on every response shape |
| V-04 | `disagreements` is preserved, not filtered | Golden eval 1 from skill SPEC (winner branch consult) |
| V-05 | Expert unavailable → `status: "degraded"` with `missingEvidence` populated | Golden eval 3 from skill SPEC (expert unavailable) |
| V-06 | `privateFieldsAllowed` out-of-scope field → 400 CONTEXT_SCOPE_VIOLATION | Privacy golden eval (SPEC golden eval 2) |
| V-07 | `/bff/agora/research/expert-consult` routes mounted in `agora/research/router.py`, not inline in `main.py` | Code review |
| V-08 | Idempotent replay on RS-GAP-01 returns `replayed: true` without re-dispatching | Idempotency unit test |

---

## 7. Files To Create / Update (AG-BE-RS-003 scope only)

This packet is read-only guidance. The actual implementation work for AG-BE-RS-003 involves:

| File | Action | Notes |
|---|---|---|
| `services/control-plane/bff/agora/research/router.py` | Populate stub | Add `create_research_router` body with RS-GAP-01, RS-GAP-02 routes |
| `services/control-plane/bff/agora/research/__init__.py` | May need models | `ExpertConsultDispatchRequest`, `ExpertConsultGroupResult` Pydantic models |
| `integrations/openclaw/skills/agora/expert-consult/SPEC.md` | Read-only reference | Already exists; do not modify |
| `services/control-plane/bff/test_exp002_bff_research_experiments_contract.py` | May need extension | Add expert-consult contract tests when routes land |

Do **not** modify:
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md`
- `CONSULTATION_SURFACE_CONTRACT.md`
- `PERSONA_RUNTIME_MODEL.md`
- `main.py` (prefer router.py for new routes)

---

## 8. Handoff Summary

| Item | Status |
|---|---|
| BFF gap list | Complete (RS-GAP-01 through RS-GAP-06) |
| Operator journey | Complete (happy path, degraded, privacy violation) |
| Frontend route specs | Complete (RS-GAP-01, RS-GAP-02 fully specced; RS-GAP-03/04 optional) |
| Privacy manifest contract | Complete |
| Disagreements UX requirement | Complete |
| Verification checklist | Complete (V-01 through V-08) |
| Files to create/update | Listed — not created (support artifact only) |

This packet is ready for Claude2 review.  
After approval, the parent owner (AG-BE-RS-003) may absorb these specs into the main implementation or mark this sidecar as superseded if the design diverges.
