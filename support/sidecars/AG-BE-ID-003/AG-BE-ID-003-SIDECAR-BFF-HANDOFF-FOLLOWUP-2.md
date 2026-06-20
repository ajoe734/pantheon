# AG-BE-ID-003 Sidecar BFF and Frontend Handoff Packet — Followup 2

**Sidecar task:** `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2`
**Helper parent:** `AG-BE-ID-003` — Interactive/trainer/research session BFF facade
**Helper kind:** `bff_handoff_packet`
**Parent owner / reviewer:** `Claude` / `Codex`
**Sidecar owner / reviewer:** `Claude` / `Claude2`
**Date:** `2026-06-20`
**Status:** `review`
**Predecessor:** `AG-BE-ID-003-SIDECAR-BFF-HANDOFF` (approved 2026-06-20)

> Scope constraint: support artifact only. This packet does not modify L1
> canonical truth, core contract truth, BFF runtime behavior, registry,
> governance implementation, or frontend implementation.

---

## 1. Purpose

This is a followup assessment of the BFF surface state for parent task
`AG-BE-ID-003`. It supersedes the original packet
(`AG-BE-ID-003-SIDECAR-BFF-HANDOFF.md`) as the current handoff reference,
adding three new findings not covered by that packet:

1. A parallel `/bff/agora/ask/sessions/` surface exists alongside
   `/bff/agora/sessions/` and creates a mode-typed session routing split that
   must be resolved before AG-BE-ID-003 can land cleanly.
2. The default mode emitted by `POST /bff/agora/sessions` when no `sessionType`
   is supplied is `quick_ask` — a non-canonical value not defined in SD §5.3.
3. The `POST /bff/agora/ask/sessions/{sessionId}/close` route satisfies a
   close requirement only for `quick_ask` sessions and does not address the G1
   terminate requirement for the three AG-BE-ID-003 session types.

All six gaps from the original packet (G1–G6) remain unaddressed as of the
current HEAD.

---

## 2. Sources Used

| Source | Why it matters |
|---|---|
| `AG-BE-ID-003-SIDECAR-BFF-HANDOFF.md` | Original gap assessment; predecessor to this packet |
| `services/control-plane/bff/main.py` (lines 20706–20906, 47719–47850) | Current session route implementations |
| `services/control-plane/bff/agora/identity/router.py` | Migration placeholder — still empty |
| `services/control-plane/bff/agora/servant/router.py` | AG-BE-ID-002 servant ensure surface |
| `scripts/dispatch_agora_cross_repo_2026-06-20.py` | Parent task definition and acceptance criteria |
| `docs/04/pantheon_agora_cross_repo_2026-06-20/SD_2026-06-20.md` | SD §5.3 session types, §8.2 audit fields |

---

## 3. Gap Status Re-Assessment

All six gaps identified in the predecessor packet are re-verified as of the
current HEAD.

| Gap | ID | Original status | Current status |
|---|---|---|---|
| Terminate route missing | G1 | Open | **Still open** |
| Session type not validated, no OpenClaw mapping | G2 | Open | **Still open** |
| §8.2 audit fields absent from session write routes | G3 | Open | **Still open** |
| `OPENCLAW_UPSTREAM_DEGRADED` not implemented | G4 | Open | **Still open** |
| SSE stream not session-scoped | G5 | Open | **Still open** |
| Session routes not migrated from `main.py` to package module | G6 | Open | **Still open** |

Verification commands run for this assessment:

```bash
# G1: terminate route check
grep -n "terminate" services/control-plane/bff/main.py | grep "bff/agora/sessions"
# Result: no match — terminate route absent on /bff/agora/sessions/* path

# G4: OPENCLAW_UPSTREAM_DEGRADED in BFF
grep -rn "OPENCLAW_UPSTREAM_DEGRADED" services/control-plane/bff/
# Result: no match in any session handler

# G5: SSE alias still in place
grep -n "bff/sse/agora/sessions" services/control-plane/bff/main.py
# Result: line 42695 — bff_sse_agora_session_alias delegates to stream_ask_events(),
#         sessionId param accepted but not used

# G6: agora package state
ls services/control-plane/bff/agora/
# Result: identity/, servant/, session/ NOT FOUND; no agora/session/ package
```

---

## 4. New Findings

### F1 — Parallel `ask/sessions` surface with mode-typed filtering

Lines 47719–47850 of `main.py` implement a second session surface at
`/bff/agora/ask/sessions/` with three routes:

| Route | Behavior |
|---|---|
| `GET /bff/agora/ask/sessions` | Lists only sessions where `mode == "quick_ask"` |
| `POST /bff/agora/ask/sessions` | Creates a session with `mode` hard-coded to `"quick_ask"` |
| `GET /bff/agora/ask/sessions/{sessionId}` | Returns detail only for `mode == "quick_ask"` sessions; 404 otherwise |
| `POST /bff/agora/ask/sessions/{sessionId}/close` | Closes only `mode == "quick_ask"` sessions |

This surface hardcodes `mode: "quick_ask"` on creation and rejects access to
sessions where `mode` is anything else. It is a separate lane from the main
`/bff/agora/sessions/` surface.

**Implication for G1 and G2:**

- `POST /bff/agora/ask/sessions/{sessionId}/close` does **not** satisfy the G1
  terminate requirement because it only operates on `quick_ask` sessions and
  does not transition `interactive`, `trainer`, or `research_task` sessions.
- The existence of a separate `ask/sessions` surface makes the session type
  routing split ambiguous. The parent owner must declare explicitly whether:
  - `/bff/agora/sessions/` is the canonical multi-type surface (supporting
    `interactive`, `trainer`, `research_task`) with its own terminate endpoint; or
  - the `ask/sessions/` surface is the intended single-type fast path and
    `/bff/agora/sessions/` is retained only for legacy compatibility.
  
  This decision gates G1 and G2 resolution.

### F2 — Default mode `"quick_ask"` is not a valid SD §5.3 session type

`POST /bff/agora/sessions` returns `mode: "quick_ask"` when no `sessionType`
or `mode` is supplied in the payload (line 20754):

```python
"mode": payload.get("mode") or payload.get("sessionType") or "quick_ask",
```

SD §5.3 defines three canonical session types: `interactive`, `trainer`,
`research_task`. The value `"quick_ask"` is not defined there. Creating a
session via the main sessions route without specifying a type produces a record
with a non-canonical type. This:

- is inconsistent with the acceptance criteria ("can create
  interactive/trainer/research session")
- means the session cannot be validated against the dispatch's session-type
  schema or routed to the correct OpenClaw session kind
- will cause the `GET /bff/agora/ask/sessions/{sessionId}` route to return 404
  for any session whose `mode` is not `"quick_ask"`, further coupling the two
  surfaces unexpectedly

**Required:** the G2 fix must change the default or require `session_type` as a
mandatory field with 422 rejection for missing or unrecognized values. Setting
`"quick_ask"` as a silent default must be removed.

### F3 — `agora/identity/router.py` migration comment is stale on scope

The `agora/identity/router.py` migration placeholder lists these routes as
pending migration in addition to those from the original sidecar:

```
POST /bff/agora/ask
GET  /bff/agora/ask/sessions
POST /bff/agora/ask/sessions
GET  /bff/agora/ask/sessions/{sessionId}
POST /bff/agora/ask/sessions/{sessionId}/close
```

These `ask/` routes exist in `main.py` but are owned by the `ask` / SEM
surface layer (ASK-001 annotation in handlers), not by the AG-BE-ID-003 session
facade. The migration note in `identity/router.py` conflates two separate
ownership surfaces. The parent owner should:

- clarify whether `ask/sessions/` is in scope for AG-BE-ID-003 or belongs to a
  separate ASK-001 task;
- update the migration comment in `identity/router.py` to reflect true scope;
- not merge the `ask/sessions/` handlers into the `agora/session/` package
  module unless that ownership is explicitly assigned to AG-BE-ID-003.

---

## 5. Updated Operator Journey

The original sidecar's operator journey remains valid. This table adds
clarifications reflecting the new findings.

| Step | BFF call | Expected behavior | Followup note |
|---|---|---|---|
| 1 | Servant provisioned (AG-BE-ID-002) | `ServantProfile` active | Unchanged |
| 2 | `POST /bff/agora/sessions` `session_type: interactive` | 201; validated type; §8.2 audit fields; OpenClaw mapping | G2/G3 gap; `"quick_ask"` default must be removed first |
| 3 | `POST /bff/agora/sessions` `session_type: trainer` | 201; trainer OpenClaw kind | G2 gap |
| 4 | `POST /bff/agora/sessions` `session_type: research_task` | 201; research OpenClaw kind; compatible with StrategyWorkshop schema | G2 gap; separate from `/bff/agora/research-tasks` research ticket list |
| 5 | `POST .../messages` with §8.2 headers | 201; audit trail stored | G3 gap |
| 6 | `GET /bff/sse/agora/sessions/{sessionId}` | Session-scoped SSE events | G5 gap; currently aliases to shared ask channel |
| 7 | Any session write; OpenClaw unavailable | 503 `OPENCLAW_UPSTREAM_DEGRADED` | G4 gap |
| 8 | `POST /bff/agora/sessions/{sessionId}/terminate` | 200; status `concluded`; SSE terminal event | G1 gap; `ask/sessions/{id}/close` does NOT satisfy this (F1) |

---

## 6. Frontend Handoff Gate Update

The original sidecar's frontend gate table remains valid. This section adds
the new blocking conditions from F1 and F2.

| Gate | Blocked until | Added constraint |
|---|---|---|
| Command-line / session create UI | `POST /bff/agora/sessions` with validated `session_type` | F2: must not silently default to `quick_ask` |
| Session message UI | `POST .../messages` with §8.2 audit fields | Unchanged |
| Session terminate button | `POST .../terminate` exists | F1: `ask/sessions/{id}/close` does not cover this |
| Live session event stream | Session-scoped SSE | G5; unchanged |
| OpenClaw degradation indicator | `OPENCLAW_UPSTREAM_DEGRADED` 503 reachable | G4; unchanged |
| Session type routing clarity | Owner declares `sessions/` vs `ask/sessions/` split | F1: FE must know which route to target for each type |

Recommendation to frontend (`AG-FE-ID-001`): keep the command-line/session
UI in skeleton mode until AG-BE-ID-003 is merged **and** the parent owner
confirms the `sessions/` vs `ask/sessions/` routing split is resolved. Using
`ask/sessions/` for `interactive`/`trainer`/`research_task` session creation
today will produce silent `quick_ask` mode sessions that violate §5.3.

---

## 7. Updated Parent Absorption Gates

Augments the gate table from the original sidecar. New or modified rows are
marked **[new]**.

| Gate | Pass condition | Source |
|---|---|---|
| G1 terminate route | `POST /bff/agora/sessions/{sessionId}/terminate` returns 200 with session status `concluded` and SSE terminal event | Sidecar-1 |
| G2 session type validation | `POST /bff/agora/sessions` validates `session_type` as `interactive`, `trainer`, or `research_task`; rejects missing/unknown values with 422 | Sidecar-1 |
| G2a default removed **[new]** | `"quick_ask"` is not silently assigned as the default session mode; the field is required | F2 |
| G2b surface split declared **[new]** | Parent owner documents whether `sessions/` or `ask/sessions/` is the canonical multi-type surface | F1 |
| G3 §8.2 audit fields | All session write routes include `trace_id`, `request_id`, `actor_id`, `user_id`, `persona_id`, `session_id` in audit trail and response meta | Sidecar-1 |
| G4 OPENCLAW_UPSTREAM_DEGRADED | OpenClaw failure returns 503 with `OPENCLAW_UPSTREAM_DEGRADED` error code in session handlers | Sidecar-1 |
| G5 SSE scope | `GET /bff/sse/agora/sessions/{sessionId}` delivers events scoped to that session | Sidecar-1 |
| G6 module migration | Session facade logic lives in a new `agora/session/router.py`; `main.py` session handlers removed | Sidecar-1 |
| G7 ask/sessions scope clarified **[new]** | `agora/identity/router.py` migration comment corrected to exclude ASK-001-owned routes; `ask/sessions/` ownership documented | F3 |
| G8 schema compliance | `session_type: research_task` response compatible with `strategy_workshop.schema.json` | Sidecar-1 |
| G9 tests | Tests cover 422 rejection, create for all three types, message post with audit fields, terminate, degradation 503, SSE | Sidecar-1 |

---

## 8. Support-Only Boundary Confirmation

- No L1 canonical policy or architecture document has been edited.
- No `main.py`, BFF router, session schema, registry, governance, or
  frontend implementation was changed.
- The intended sidecar artifact is this file:
  `support/sidecars/AG-BE-ID-003/AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md`.

---

## 9. Reviewer Handoff

Reviewer: `Claude2`

Please review this followup packet for:

1. support-only scope compliance (no BFF code or schema changed)
2. accuracy of the three new findings (F1, F2, F3)
3. correctness of the updated gate table (G2a, G2b, G7)
4. whether the `ask/sessions` surface analysis is complete and correctly scoped
5. whether the frontend gate update is actionable for the AG-FE-ID-001 team

Suggested approval command:

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py approve AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 "Followup BFF handoff packet approved; F1/F2/F3 new findings correct; G2a/G2b/G7 gates added; support-only boundary confirmed."
```

Suggested reopen command if changes are required:

```bash
AI_NAME=Claude2 python3 scripts/ai_status.py reopen AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2 "Describe the exact correction needed."
```

*Prepared by Claude for the `AG-BE-ID-003-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` support slice.*
