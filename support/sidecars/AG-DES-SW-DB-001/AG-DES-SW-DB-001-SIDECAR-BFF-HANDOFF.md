# AG-DES-SW-DB-001 BFF & Frontend Handoff Packet

**Sidecar ID:** AG-DES-SW-DB-001-SIDECAR-BFF-HANDOFF  
**Parent Task:** AG-DES-SW-DB-001 — Workshop tables, lifecycle alignment, exact index migration  
**Sidecar Kind:** bff_handoff_packet  
**Date:** 2026-06-21  
**Author:** Claude (auto-worker)  
**Reviewer:** Claude2  
**Status:** handoff_candidate  

> **Scope notice.** This packet is a support artifact only.
> It does not modify canonical truth (L1 policy, OpenAPI contracts, DB schemas, or service implementations).
> All design decisions cited here originate from:
> - `AG-BE-SW-001_deep_design_closure_2026-06-21.md` (L3 design closure)
> - `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` (L1)
> - `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` (L1)
> - `services/control-plane/openapi/agora_v1_1.openapi.yaml`

---

## 1. What AG-DES-SW-DB-001 delivers

AG-DES-SW-DB-001 creates the exact executable Postgres migration for the Agora Strategy Workshop domain. The BFF and frontend can only be fully wired once this migration is merged into `dev`.

### 1.1 Tables added

| Table | Purpose |
|---|---|
| `strategy_workshop_session` | Workshop aggregate: lifecycle status, strategy pointers, concurrency lock |
| `strategy_workshop_event` | Append-only event log (messages, research dispatches, status transitions) |
| `strategy_workshop_version_link` | Immutable links between a workshop and a StrategySpec Registry version |
| `strategy_completeness_snapshot` | Periodically computed completeness/blocking-items/next-question for a workshop |
| `agora_private_content_object` | Encrypted private-content metadata; plaintext stays in object store |

### 1.2 Key constraints added

- `strategy_workshop_session.status CHECK (status IN ('open','in_review','concluded','archived'))` — the canonical four-value enum enforced at the DB level
- `strategy_workshop_event` constraint: for `event_type='message'`, `private_content_ref`, `redacted_summary`, and `redaction_policy_version` must all be non-null
- `UNIQUE (workshop_id, sequence_no)` on events and version links — guarantees append-only ordering invariant

### 1.3 Indexes added (BFF-relevant)

The migration adds indexes that directly support the BFF read paths:

| Index | BFF route it enables |
|---|---|
| `ix_workshop_user_status_updated (tenant_id, user_id, status, updated_at DESC)` | `GET /bff/agora/workshops?status=...` — paginated user list |
| `ix_workshop_servant_status_updated (servant_persona_id, status, updated_at DESC)` | Servant-scoped workshop lookup (internal) |
| `ix_workshop_strategy_updated (strategy_id, updated_at DESC)` | Strategy drilldown view |
| `ix_workshop_active_registry_ref (active_strategy_spec_registry_id)` | Registry-to-workshop reverse lookup |
| `ux_workshop_openclaw_session (openclaw_session_id)` | Servant session dedup guard |
| `ix_workshop_event_created (workshop_id, created_at, sequence_no)` | `GET /bff/agora/workshops/{id}/events` — ordered replay |
| `ix_workshop_event_trace (trace_id)` | Observability / incident trace lookup |
| `ux_workshop_event_private_ref (private_content_ref)` | One-to-one guard: one event per private object |
| `ux_workshop_registry_version (workshop_id, strategy_spec_registry_id)` | Prevents duplicate version links per workshop |
| `ux_workshop_completeness_version (workshop_id, assessment_version)` | Completeness idempotency |
| `ix_workshop_completeness_latest (workshop_id, created_at DESC)` | `GET /bff/agora/workshops/{id}/completeness` — latest snapshot |
| `ix_private_content_expiry_gc (expires_at)` | GC background job; not a BFF read path |

---

## 2. BFF query gap analysis

### 2.1 Current BFF state

`services/control-plane/bff/agora/strategy_workshop/router.py` currently returns an **empty router** (placeholder, no routes implemented). The 13 workshop routes defined in `agora_v1_1.openapi.yaml` are not yet wired. All workshop query implementation is gated on AG-DES-SW-DB-001 being merged.

### 2.2 Routes not yet implemented (pending AG-DES-SW-DB-001)

| # | Method | Path | Table query | Notes |
|---|---|---|---|---|
| 1 | GET | `/bff/agora/workshops` | `strategy_workshop_session` WHERE `tenant_id=T AND user_id=U` | Cursor pagination; status filter (see §2.3) |
| 2 | POST | `/bff/agora/workshops` | INSERT `strategy_workshop_session` + outbox | Requires private-content encrypt + redact (AG-DES-SW-PRIV-001 gate) |
| 3 | GET | `/bff/agora/workshops/{id}` | SELECT + `lock_version` for ETag | Returns ETag header `W/"workshop:{id}:v{lock_version}"` |
| 4 | POST | `/bff/agora/workshops/{id}/messages` | INSERT `strategy_workshop_event` (type=message) | Requires redaction; fails 503 if redaction unavailable |
| 5 | GET | `/bff/agora/workshops/{id}/events` | SELECT `strategy_workshop_event` ORDER BY `sequence_no` | `after_sequence` cursor; omits raw content, returns `redacted_summary` |
| 6 | GET | `/bff/agora/workshops/{id}/completeness` | SELECT latest `strategy_completeness_snapshot` | Returns `state_map_json`, `blocking_items_json`, `next_question_json` |
| 7 | GET | `/bff/agora/workshops/{id}/versions` | SELECT `strategy_workshop_version_link` ORDER BY `created_at` | Registry refs only; no StrategySpec JSON copy |
| 8 | POST | `/bff/agora/workshops/{id}/versions` | INSERT `strategy_workshop_version_link` + update session pointer | Requires `If-Match` + `Idempotency-Key`; calls Strategy Registry API |
| 9 | POST | `/bff/agora/workshops/{id}/versions/{vid}/select` | UPDATE `active_workshop_version_id` on session | Mutates `lock_version`; requires `If-Match` |
| 10 | POST | `/bff/agora/workshops/{id}/research-runs` | INSERT `strategy_workshop_event` (type=research_dispatch) | Requires active strategy version; mutates `lock_version` |
| 11 | POST | `/bff/agora/workshops/{id}/consultations` | INSERT `strategy_workshop_event` (type=consultation_open) | Calls consultation-svc via owner API |
| 12 | POST | `/bff/agora/workshops/{id}/conclude` | UPDATE status=concluded on session | Requires final version; non-reversible; mutates `lock_version` |
| 13 | GET | `/bff/agora/workshops/{id}/stream` | SSE from `strategy_workshop_event` | Not in v1.1 OpenAPI explicitly; assumed via existing SSE infrastructure |

### 2.3 Status filter alignment gap (critical)

The `agora_v1_1.openapi.yaml` `GET /bff/agora/workshops?status=` description mentions `active, concluded, archived` — but the canonical status model (enforced by AG-DES-SW-DB-001's CHECK constraint) uses `open, in_review, concluded, archived`.

**Resolution (per §6 of the design closure doc):**

| Frontend passes | BFF translates to |
|---|---|
| `status=open` | `WHERE status='open'` |
| `status=in_review` | `WHERE status='in_review'` |
| `status=concluded` | `WHERE status='concluded'` |
| `status=archived` | `WHERE status='archived'` |
| `status_group=active` | `WHERE status IN ('open','in_review')` |
| `status_group=closed` | `WHERE status IN ('concluded','archived')` |
| `status=active` _(deprecated)_ | treat as `status_group=active` during migration |

Frontend **must not** send `status=active` to new routes; use `status_group=active` instead. The v1.2 OpenAPI contract (AG-XR-OPENAPI-002) will formalize this.

### 2.4 ETag / concurrency contract (required by all mutations)

Every mutation (routes 4, 8, 9, 10, 11, 12) requires:
- `If-Match: W/"workshop:{id}:v{N}"` header from the most recent GET response
- `Idempotency-Key: <uuid>` header for safe retry

A stale `If-Match` returns `409 CONCURRENT_MODIFICATION` with `current_version`, `current_etag`, and `latest_href` in the error body. The frontend must re-fetch the workshop aggregate before retrying.

### 2.5 Private content constraint for frontend

- The frontend **must never** submit `private_content_ref` directly. The BFF generates it server-side.
- `POST /bff/agora/workshops` and `POST /bff/agora/workshops/{id}/messages` accept only `initial_message` (raw text) and `strategy_ref` / `content` respectively.
- The owner-facing event response returns decrypted `content` (direct text). Management projections return only `redacted_summary`. The frontend must render both views based on the caller's role.
- If the BFF returns `503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE`, the message was not persisted; the frontend should surface this as a transient error, not a silent drop.

---

## 3. Operator journey

This section describes the canonical operator flow for the Strategy Workshop feature. The frontend UI must support all states and transitions.

### 3.1 Happy path — free-form idea → concluded workshop

```
Operator                      BFF / DB
──────                        ────────
1. View workshop list          GET /bff/agora/workshops?status_group=active
   (empty state for new users)

2. Create workshop             POST /bff/agora/workshops
   { title, initial_message }     ↳ BFF: redact message → encrypt → INSERT session + event
                                  ↳ Returns: workshop_id, ETag, lock_version=1

3. View workshop               GET /bff/agora/workshops/{id}
   (displays events, completeness,  ETag: W/"workshop:{id}:v1"
    version list — initially empty)

4. Send message                POST /bff/agora/workshops/{id}/messages
   { content: "..." }          If-Match: W/"workshop:{id}:v1"
                               Idempotency-Key: <uuid>
                               ↳ BFF: redact → encrypt → INSERT event (message)
                               ↳ Dispatches servant response workflow
                               ↳ Returns 202; lock_version increments to 2

5. Refresh events              GET /bff/agora/workshops/{id}/events?after_sequence=0
   (poll or SSE)               ↳ Returns events 1..N; redacted_summary for messages
                               ↳ Servant reply appears as new events

6. Check completeness          GET /bff/agora/workshops/{id}/completeness
   (shows blocking items,       ↳ Returns state_map_json, next_question_json
    next question)

7. Create strategy version     POST /bff/agora/workshops/{id}/versions
   { patch: {...} }            If-Match: W/"workshop:{id}:v2"
                               Idempotency-Key: <uuid>
                               ↳ BFF calls Strategy Registry → receives strategy_spec_registry_id
                               ↳ INSERT strategy_workshop_version_link
                               ↳ Returns version_id, strategy_spec_registry_id; lock_version → 3

8. Dispatch research run       POST /bff/agora/workshops/{id}/research-runs
   (optional)                  If-Match: W/"workshop:{id}:v3"
                               Idempotency-Key: <uuid>
                               ↳ INSERT event (research_dispatch); lock_version → 4

9. Conclude workshop           POST /bff/agora/workshops/{id}/conclude
   { final_version_id }        If-Match: W/"workshop:{id}:v4"
                               Idempotency-Key: <uuid>
                               ↳ UPDATE status=concluded, final refs; lock_version → 5
                               ↳ Returns 200; no further mutations accepted
```

### 3.2 Attach to an existing Strategy Registry draft

```
2a. Create workshop from draft  POST /bff/agora/workshops
    { title, initial_message,     ↳ BFF validates strategy_ref → Strategy Registry
      strategy_ref: {                 Checks tenant scope + id match
        strategy_id: "...",           Stores strategy_id + active_strategy_spec_registry_id
        strategy_spec_registry_id:    Does NOT copy StrategySpec JSON
          "..."
      }
    }
```

Mismatch between `strategy_id` and `strategy_spec_registry_id` returns `409 STRATEGY_REFERENCE_MISMATCH`. Missing/unauthorized returns 404/403 without leaking existence.

### 3.3 Workshop status transitions

```
         ┌──────── archived (terminal) ◄────────────────────────┐
         │                                                       │
open ────► in_review ──► concluded ──► (archived)               │
  ▲           │                                                  │
  └───────────┘ (reopen: requires If-Match + audit reason)      │
         │                                                       │
         └───────────────────────────────────────────────────────┘
```

- `open` and `in_review`: messages, versions, research runs, and consultations allowed
- `concluded` and `archived`: all mutations rejected
- `in_review → open` reopen: requires `If-Match` and a non-empty `reason` field
- Frontend must disable input areas and show a banner when status is `concluded` or `archived`

### 3.4 Degraded-mode behavior

Per `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` §5:

| Failure | Frontend behavior |
|---|---|
| Private-content store unavailable | Show 503 "Message temporarily unavailable" — do not suppress |
| Redaction service unavailable | Show 503 "Cannot submit message right now" — do not drop message |
| Consultation-svc unavailable | Show degraded banner on Consultation tab only; other workshop tabs remain |
| BFF total outage | Top-level error state; no stale fallback data to render |

The BFF explicitly marks degradation source and confidence level. The frontend must render what the BFF sends; it must not invent fallback snapshots or default-to-cached-state on error.

---

## 4. Frontend implementation checklist

These items are actionable once AG-DES-SW-DB-001 is merged and the 13 BFF routes are implemented (AG-BE-SW-001).

### 4.1 Workshop list view

- [ ] Fetch `GET /bff/agora/workshops?status_group=active&limit=20` on mount
- [ ] Support `status_group=active` and `status_group=closed` tabs
- [ ] Implement cursor-based pagination (`cursor` query param from previous response)
- [ ] Show empty state for zero workshops
- [ ] Do not use `status=active` filter string — use `status_group=active`

### 4.2 Workshop detail view

- [ ] On enter: `GET /bff/agora/workshops/{id}` — store ETag from response header
- [ ] Render events from `GET /bff/agora/workshops/{id}/events?after_sequence=0`
- [ ] Poll or SSE for new events; use `after_sequence={last_seen}` for incremental load
- [ ] Render `redacted_summary` for management view; `content` for owner view
- [ ] Fetch completeness on enter and after each message: `GET .../completeness`
- [ ] Show blocking items and next question from completeness snapshot
- [ ] Fetch version list: `GET .../versions`
- [ ] Disable all mutation UI when status is `concluded` or `archived`

### 4.3 Message submission

- [ ] Send `POST .../messages` with latest ETag in `If-Match`
- [ ] Generate a UUIDv4 `Idempotency-Key` per submission; reuse on retry
- [ ] Handle `202 Accepted` — message is async; poll events for servant reply
- [ ] Handle `409 CONCURRENT_MODIFICATION` — re-fetch workshop, update ETag, re-enable submit
- [ ] Handle `503 PRIVATE_CONTENT_REDACTION_UNAVAILABLE` — show non-suppressible error toast

### 4.4 Version creation and selection

- [ ] `POST .../versions` to create a draft from the active strategy
- [ ] `POST .../versions/{vid}/select` with current ETag to change active version
- [ ] After version creation/selection, refresh workshop aggregate to get new ETag
- [ ] Versions reference immutable StrategySpec Registry entries; do not duplicate StrategySpec JSON locally

### 4.5 Conclude flow

- [ ] Conclude button only visible when status is `open` or `in_review`
- [ ] Require user to select or confirm `final_version_id` before conclude
- [ ] `POST .../conclude` with current ETag + `Idempotency-Key`
- [ ] On 200: mark workshop read-only, show concluded banner, navigate to list or detail view
- [ ] On `409 WORKSHOP_VERSION_REQUIRED`: prompt user to create or select a version first

### 4.6 Error codes to handle

| Code | Meaning | Frontend action |
|---|---|---|
| `CONCURRENT_MODIFICATION` (409) | Stale ETag | Re-fetch workshop, update ETag, show retry prompt |
| `PRIVATE_CONTENT_REDACTION_UNAVAILABLE` (503) | Redaction service down | Non-suppressible error; message was not saved |
| `PRIVATE_CONTENT_STORE_UNAVAILABLE` (503) | Object store down | Same as above |
| `STRATEGY_REFERENCE_MISMATCH` (409) | strategy_id/registry_id mismatch | Show "Strategy reference mismatch — check draft selection" |
| `STRATEGY_REFERENCE_NOT_FOUND` (404) | Strategy draft not found | Show "Strategy draft not found or access denied" |
| `WORKSHOP_ALREADY_CONCLUDED` (409) | Workshop already in concluded state | Reload workshop, show concluded state |
| `WORKSHOP_ARCHIVED` (409) | Workshop is archived (terminal) | Reload, show archived state |
| `WORKSHOP_VERSION_REQUIRED` (409) | Conclude without a version link | Prompt user to create/select a version |

---

## 5. Blocked-on dependencies

| Dependency | Status | What unblocks |
|---|---|---|
| **AG-DES-SW-DB-001** ← this packet's parent | Design phase | Exact migration + indexes (this task) |
| **AG-DES-SW-PRIV-001** | Parallel design | Private-content store interface and encryption contract |
| **AG-DES-SW-REF-001** | Parallel design | StrategySpec Registry reference and version mapping |
| **AG-XR-OPENAPI-002** | Parallel design | Agora v1.2 OpenAPI bundle (status_group param, error codes) |
| **AG-BE-SW-001** | Blocked on all 4 above | BFF route implementation wiring all 13 workshop endpoints |

The BFF handoff (routes §2.2) cannot be implemented until all four design tasks are merged. The frontend can build static mocks and skeletons against `agora_v1_1.openapi.yaml` now, but must update status filter params when v1.2 lands.

---

## 6. Non-scope of this sidecar

The following are **not** changed or extended by this sidecar:

- `DATABASE_OWNERSHIP_AND_SHARED_CLUSTER_POLICY.md` — DB write ownership map is unchanged; the new workshop tables follow the existing `control-plane/governance` ownership model with `promotion-svc` / `registry-core-svc` as analogs
- `agora_v1.openapi.yaml` / `agora_v1_1.openapi.yaml` — frozen audit artifacts; v1.2 is a separate task
- `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` — no BFF HA topology changes
- Any service implementation files in `services/`
- `ai-status.json` task assignments for the main sprint tasks

---

## 7. Handoff notes for Claude2 review

This packet is ready for a **narrow docs/process review**, not a full runtime acceptance review.

Review checklist:
- [ ] Section §2.2 route/table mapping is accurate against `agora_v1_1.openapi.yaml`
- [ ] Section §2.3 status filter alignment correctly captures the DB constraint from the design closure doc
- [ ] Section §3 operator journey is consistent with the write sequence in §3.9 of the design closure doc
- [ ] Section §4 frontend checklist does not contradict existing `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` degraded-mode policy
- [ ] Section §5 dependency list is complete
- [ ] No canonical L1/L2 document was modified by this sidecar

If the reviewer finds factual errors against the design closure doc or L1 policy, reopen with specific correction requests. No implementation changes are expected from this review.
