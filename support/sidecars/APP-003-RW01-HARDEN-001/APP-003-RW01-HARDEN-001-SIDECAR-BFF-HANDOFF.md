# APP-003-RW01-HARDEN-001 BFF and Frontend Handoff Packet (Sidecar)

**Parent Task**: `APP-003-RW01-HARDEN-001` - Harden RW-01 production reads away from local snapshot fallback
**Parent Owner**: `Codex`
**Parent Reviewer**: `Codex2`
**Parent Status**: `review`
**Sidecar Task**: `APP-003-RW01-HARDEN-001-SIDECAR-BFF-HANDOFF`
**Sidecar Owner**: `Codex2`
**Sidecar Reviewer**: `Codex`
**Helper Kind**: `bff_handoff_packet`
**Generated**: `2026-04-22`
**Mutates canonical**: `no`

> This is a support artifact only. It does not change L1 truth, canonical BFF
> contracts, runtime behavior, registry/governance implementations, or mainline
> workbench code. It records the original RW-01 hardening gap, the current
> repo state after the parent patch, and the frontend/BFF handoff boundaries
> that should stay stable while the parent task finishes review.

## 1. Executive Summary

`APP-003-RW01-HARDEN-001` was opened because `RW-01` research-ticket detail
reads used to allow a production-path fallback to local seeded snapshot data.
That violated the 2026-04-22 execution packet and the L1 BFF resilience rule
that normal BFF paths must not pretend backend readiness by silently serving
local seed/snapshot data as authoritative truth.

Current repo truth is tighter than the original gap report:

- `GET /api/v1/research/tickets` remains a service-backed list surface and
  reports `meta.surfaces.ticket_list` truthfully.
- `GET /api/v1/research/tickets/{ticket_id}` now disables both snapshot and
  local fallback on the public read route and returns `404` if no
  service-backed record is available.
- Targeted regression tests now lock in the intended behavior: service-backed
  data wins, seeded-only detail does not render as live truth, and list reads
  still expose degraded/unavailable truth explicitly.

For frontend and BFF handoff purposes, the important point is simple:

- do not widen the UI contract
- do not add client-side fallback logic
- keep backend-owned truth backend-owned
- treat missing service-backed detail as a backend outcome, not as a reason to
  rehydrate normal detail from local seeded data

## 2. Source References

| Source | Why it matters |
|---|---|
| `ai-status.json` | Durable owner/reviewer/lifecycle truth for the parent task and this sidecar |
| `.orchestrator/task-briefs/app_003_rw01_harden_001_sidecar_bff_handoff.md` | Task-scoped execution brief and artifact target |
| `docs/reviews/2026-04-22-full-blueprint-gap-execution-packet.md` | Explicitly identifies `RW-01` as a truth-hardening gap caused by production-path local snapshot fallback |
| `BFF_HA_AND_CONTROL_PLANE_RESILIENCE.md` | L1 rule that degraded mode must be explicit and normal BFF paths must not pretend backend readiness through local seed/snapshot fallback |
| `services/control-plane/bff/main.py` | Defines the live RW-01 list/detail API shape and `meta.surfaces` envelopes consumed by UI |
| `services/control-plane/bff/read_store.py` | Still exposes optional fallback behavior at the helper layer, which matters for scoping what the parent task hardened versus what remains internal/helper-only |
| `services/control-plane/bff/test_rw01_research_ticket_contract.py` | Encodes the regression expectations that now define the public read-path boundary |

## 3. RW-01 Query Gap And Current Repo State

### 3.1 Contract surface already exposed to UI

The frontend-facing route family for RW-01 is already stable enough to hand off:

- `POST /api/v1/research/tickets`
- `GET /api/v1/research/tickets`
- `GET /api/v1/research/tickets/{ticket_id}`
- `PATCH /api/v1/research/tickets/{ticket_id}`

List responses return:

- `data[]`
- `page_info.next_page_token`
- `page_info.total`
- `meta.surfaces.ticket_list`

Detail responses return:

- ticket fields such as `ticket_id`, `title`, `description`, `status`,
  `priority`, `owner`
- `lifecycle_history[]`
- `linked_experiments[]`
- `linked_artifacts[]`
- `allowedActions`
- `links.self`
- `links.workbench_detail`
- `meta.surfaces.ticket_detail`

### 3.2 Historical gap that triggered the parent task

The hardening gap recorded in the execution packet was this:

- ask the service store for a single `research_tickets` record
- if that record is unavailable, read from `_local_fallback("research_tickets")`
- return that local seeded record as normal ticket detail if present

That behavior let the UI receive what looked like an ordinary detail payload
even when the service-backed truth path was unavailable. This is the production
read-path behavior the parent task was created to remove.

### 3.3 Current repo truth after hardening

The public RW-01 read surface now behaves as follows:

- the public `GET /api/v1/research/tickets/{ticket_id}` route now calls
  `read_store.get_research_ticket(..., include_snapshot_fallback=False,
  include_local_fallback=False)`
- if no service-backed detail exists, the route returns `404` instead of
  fabricating seeded detail as live truth
- the list route continues to expose explicit surface state through
  `meta.surfaces.ticket_list`
- the helper layer in `read_store.py` still supports optional fallback for
  callers that explicitly opt into it, so this hardening should be understood
  as a public read-surface boundary, not a blanket claim that all helper-level
  fallback code disappeared everywhere

The targeted tests now document that boundary directly:

- service-backed detail returns the service record and `ticket_detail: fresh`
- a seeded-only ticket detail request returns `404`
- service-backed reads override seeded snapshot content
- list reads can still surface `degraded` or `unavailable` without pretending
  local seeded data is normal live truth

This sidecar therefore does not propose a new contract. It documents the
original gap, the now-landed public read-path hardening, and the frontend
behavior that should remain unchanged while the parent task finishes review.

## 4. Frontend Handoff Guidance

### 4.1 UI should keep rendering the existing backend-owned contract

Frontend/workbench consumers should continue to treat these fields as
authoritative and render them verbatim:

- `allowedActions`
- `lifecycle_history`
- `linked_experiments`
- `linked_artifacts`
- `meta.surfaces.ticket_list`
- `meta.surfaces.ticket_detail`

The UI should not:

- invent a "healthy" detail state when service truth is missing
- synthesize fallback ticket data from packet prose, local seeds, or stale mock data
- infer write authority from status text instead of `allowedActions`
- treat a `404` detail response as permission to backfill seeded/local ticket
  content on the client

### 4.2 Operator journey after hardening

Expected operator flow once the parent task lands:

1. Operator opens `/research/tickets`.
2. BFF returns service-backed list truth plus `meta.surfaces.ticket_list`.
3. Operator opens a ticket detail view.
4. If a service-backed detail record exists, BFF returns that detail plus
   `meta.surfaces.ticket_detail`.
5. If only seeded/local detail exists, BFF returns `404` instead of silently
   rendering seeded ticket detail as if it were live.
6. The UI should follow its normal not-found/error handling path rather than
   inventing client-owned fallback detail.

### 4.3 Frontend assumptions that remain safe

These assumptions appear safe to keep:

- workbench detail links remain `/research/tickets/{ticket_id}`
- list and detail allowed actions remain backend-shaped
- lifecycle rendering remains read-only and backend-owned
- no new frontend query parameters are needed for the hardening slice alone
- the hardening slice does not introduce new detail fields or alternate
  ticket-detail payload variants

## 5. Reviewer / Parent-Owner Checklist

For the sidecar reviewer:

- confirm the packet stays support-only and does not redefine canonical BFF truth
- confirm the packet identifies the real gap as public read-path local fallback,
  not a missing frontend feature
- confirm the packet is synced to current `ai-status.json` ownership/lifecycle
  truth and current repo behavior

For the parent owner:

- use this packet as a scope guard for `APP-003-RW01-HARDEN-001`
- keep the review conversation centered on the public detail read boundary
  rather than reopening UI scope
- keep regression coverage centered on service-backed precedence, seeded-only
  detail rejection, and truthful list surface signaling
- if the final implementation changes field shape or workbench navigation
  semantics, that should trigger a separate contract/handoff update rather than
  being hidden inside this hardening task

For the parent reviewer:

- verify the public detail route disables snapshot/local fallback
- verify the seeded-only detail case fails closed instead of rendering local
  seeded truth as live detail
- verify the packet does not over-claim removal of every helper-layer fallback
  path outside the public read surface

## 6. Suggested Acceptance Framing For The Parent Task

When the parent task closes review, the reviewer should be able to say all of
the following are true:

- RW-01 public production reads prefer service-backed truth for both list and
  detail
- the public detail route no longer depends on local seeded snapshot fallback
- seeded-only detail no longer masquerades as live truth
- list degraded/unavailable conditions remain surfaced explicitly to the UI
- the frontend contract remains stable and backend-owned

## 7. Sidecar Scope Check

| Check | Result |
|---|---|
| Support artifact only | PASS |
| No L1/L2/L3 truth edited | PASS |
| No runtime/BFF implementation changed | PASS |
| Packet is useful to reviewer and parent owner | PASS |
| Handoff stays within BFF/frontend support scope | PASS |

## 8. Handoff Note

Recommended review disposition for this sidecar:

- approve if the packet is sufficiently precise for reviewer use and does not
  overstep into canonical redesign or claim behavior the current repo does not have

Recommended parent-task interpretation:

- the original unresolved work was public detail read-path hardening in the BFF
- the current repo already reflects that public hardening boundary and the
  parent review should focus on correctness, regression coverage, and contract
  stability
- the frontend should continue to consume the existing contract without adding
  client-owned truth or fallback logic
