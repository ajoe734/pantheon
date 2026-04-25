# CW-04 Red-team Memo BFF Contract

## Status

**Route-live.** The `2026-04-22` follow-up architecture response closes the
remaining memo lifecycle, mapping, and governance-gate questions, the current
BFF implements the `GET /api/v1/consult/memos` route family against that
ratified contract, and the module-local frontend handoff bundle is now
published under `docs/pantheon-handoffs/CW-04-redteam-memo/`. Remaining work
is activating the UI against the live memo surface.

Task: `CW-04-REDTEAM-MEMO-001`

## Purpose

Provide one backend-composed red-team memo surface so operators can list
published findings, inspect memo recommendations and evidence, and initiate
downstream governance review without deriving memo state, mapping, or handoff
authority from client-side synthesis.

## Dependencies

- `CW-01-FOUNDATION-001` for stable `ConsultRequest` identity
- `CW-02-TRANSCRIPT-001` for transcript identity and transcript-version
  anchoring

## Routes

### List red-team memos

- `GET /api/v1/consult/memos`

Supported query params:

- `status` — `draft | published`
- `page_token`
- `page_size`

Required response fields:

- `items[]`
  - `object_ref`
  - `memo_id`
  - `memo_type = "red_team"`
  - `status`
  - `linked_request_id`
  - `recommendation_count`
  - `published_at`
  - `created_at`
  - `route_href`
- `page_info.next_page_token`
- `page_info.page_size`
- `page_info.total` — optional
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.redteam_memo.state` — `ok | degraded | unavailable`

### Get red-team memo detail

- `GET /api/v1/consult/memos/{memo_id}`

Required response fields:

- `object_ref`
  - `type = "ConsultMemo"`
  - `id = memo_id`
- `memo_id`
- `memo_type = "red_team"`
- `status`
- `lifecycle_state`
- `author_ref`
- `linked_request_id`
- `linked_session_id`
- `session_to_memo_mapping`
- `summary`
- `recommendations[]` — plain string list in v1
- `evidence_refs[]`
- `published_at`
- `created_at`
- `supersedes_memo_id` — optional
- `superseded_by_memo_id` — optional
- `allowedActions.canInitiateGovernanceReview`
- `meta.snapshot_at`
- `meta.staleness`
- `meta.surfaces.redteam_memo.state`

## ConsultMemo lifecycle

The v1 lifecycle is:

```text
draft -> published
```

- `draft`: memo is being authored; governance-review initiation must be false
- `published`: memo is finalized and may be handed to governance if all gating
  checks pass

Do not introduce `superseded` or `archived` as primary v1 lifecycle states.

If a published memo must be revised, preserve the published memo and create a
new version / superseding memo.

Optional relationship metadata:

- `supersedes_memo_id`
- `superseded_by_memo_id`

## Recommendations

`recommendations[]` remains a plain string list in v1.

Per-recommendation severity, workflow status, or approval status are out of
scope unless a later explicit contract decision adds them.

## Session-to-memo mapping

The relationship between a red-team session and a memo must be explicit in the
BFF response.

`session_to_memo_mapping` object:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `mapping_id` | string | no | canonical mapping identity |
| `source_session_id` | string | no | red-team session identity |
| `transcript_id` | string | no | transcript identity used by the memo |
| `transcript_version` | string | yes | transcript version snapshot when applicable |
| `memo_id` | string | no | memo identity |
| `memo_type` | string | no | `red_team` |
| `created_by.actor_type` | string | no | creator actor type |
| `created_by.actor_id` | string | no | creator actor identity |
| `evidence_refs[]` | string[] | no | evidence refs that anchor the mapping |
| `mapping_status` | string | no | mapping lifecycle / validity state |
| `created_at` | ISO 8601 string | no | mapping creation time |

Clients must not derive this mapping from raw session or transcript objects.

## Evidence link object

Each `evidence_refs[]` entry must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `id` | string | no | canonical evidence identifier |
| `evidence_type` | string | no | evidence category |
| `artifact_ref` | string | yes | linked artifact identity when applicable |
| `description` | string | yes | short display description |
| `link` | string | no | BFF-resolved canonical navigation path |

## Governance initiation gate

`allowedActions.canInitiateGovernanceReview` is the sole authority signal for
the downstream review CTA.

It may be `true` only when all conditions hold:

1. memo lifecycle = `published`
2. memo target has a valid `strategy_id`, `artifact_id`, or
   `deployment_plan_id`
3. actor has reviewer / governance authority
4. no active governance review already exists for the same target + memo
5. memo is not suppressed or withdrawn
6. evidence surface is not `unavailable`
7. governance service accepts the memo target type

Frontend must never derive this signal from `status` alone.

## Degradation semantics

| `meta.surfaces.redteam_memo.state` | Behavior |
|---|---|
| `ok` | render memo content normally |
| `degraded` | render last-known memo with degraded banner; governance CTA must be false |
| `unavailable` | hide memo content and render canonical unavailable banner; governance CTA must be false |

Freshness belongs in `meta.staleness`, not as a primary surface state.

For memo detail, `degraded` still returns the full published detail envelope,
including mapping and metadata fields; only
`allowedActions.canInitiateGovernanceReview` is forced false. The
`unavailable` branch is the only state that suppresses memo content fields such
as `summary`, `recommendations[]`, and `evidence_refs[]`.

## Non-goals

- The client must not derive memo lifecycle from transcript state.
- The client must not derive governance-review authority from publication state
  alone.
- The client must not add per-recommendation severity or workflow columns in
  v1.
- The client must not derive the session-to-memo relationship from raw session
  or transcript payloads.

## Example Payload

- `docs/examples/CW-04-redteam-memo.json`
- `docs/pantheon-handoffs/CW-04-redteam-memo/FRONTEND_CHANGE_SPEC.md`
