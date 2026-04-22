# CW-03 Committee Board BFF Contract

## Status

**Routes live — partial activation ratified.** The committee board list/detail
routes and the `RecordSponsorDecision` command are implemented in the Pantheon
BFF. `CW-03` may partial-activate now, but transcript-dependent surfaces remain
gated until `CW-02` transcript truth is route-live.

Task: `CW-03-COMMITTEE-001`

## Purpose

Provide one backend-composed committee board projection so operators can review committee state, inspect participant roster and escalation context, and record the final sponsor decision without deriving verdicts from raw participant signals in the client.

## Dependencies

- `CW-01-FOUNDATION-001` for stable consultation request identity
- `CW-02-TRANSCRIPT-001` for transcript-dependent surfaces such as event-level
  reasoning, quote snippets, and full debate replay

## Routes

### List committee boards

- `GET /api/v1/committees`

Supported query params:

- `quorum_state`
- `consensus_state`
- `page_token`
- `page_size`

Each row in `data[]` must contain:

- `committee_id`
- `committee_ref`
- `escalation_reason`
- `quorum_state`
- `consensus_state`
- `linked_request_id`
- `started_at`
- `route_href`

Response metadata must include:

- `page_info.next_page_token`
- `page_info.total`
- `meta.snapshot_at`
- `meta.surfaces.committee_board` — `"ok"` | `"stale"` | `"degraded"` | `"unavailable"`

### Get committee board detail

- `GET /api/v1/committees/{committee_id}`

Required response fields:

- `committee_id`
- `committee_ref`
- `linked_request_id`
- `linked_session_id`
- `started_at`
- `escalation_reason`
- `quorum_state`
- `consensus_state`
- `participant_roster[]`
- `sponsor_assignment`
- `sponsor_decision`
- `sponsor_decided_at`
- `sponsor_decided_by`
- `synthesis_summary`
- `linked_evidence[]`
- `allowedActions.canRecordSponsorDecision`
- `meta.snapshot_at`
- `meta.surfaces.committee_board`

### Record sponsor decision

- `POST /api/v1/operator/commands`

Published payload:

```json
{
  "command_type": "RecordSponsorDecision",
  "committee_id": "committee-regime-risk-20260419-081",
  "sponsor_decision": "approved",
  "rationale_ref": "workspace://committee-rationales/committee-regime-risk-20260419-081/final",
  "note": "Optional operator note"
}
```

`sponsor_decision` must be one of:

- `approved`
- `rejected`
- `conditional`

The command is only valid when `allowedActions.canRecordSponsorDecision` is `true`.

## Committee Detail Objects

### Participant roster row

Each `participant_roster[]` entry must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `participant_id` | string | no | committee participant session identity |
| `persona_id` | string | no | canonical persona identity |
| `persona_label` | string | yes | BFF-resolved display label |
| `role` | string | no | `"committee_participant"` or `"sponsor"` |
| `status` | string | no | backend-owned participant status |
| `outcome_signal` | string | yes | `"approved"` \| `"rejected"` \| `"conditional"` when the participant has emitted one |
| `rationale_ref` | string | yes | linked rationale or dissent reference |

### Synthesis summary

`synthesis_summary` is backend-composed and must contain:

| Field | Type | Nullable | Notes |
|---|---|---|---|
| `outcome` | string | no | canonical committee result; may remain `"pending"` before sponsor decision |
| `rationale_ref` | string | yes | canonical rationale reference |
| `evidence_refs` | string[] | no | evidence identifiers included in the synthesis |
| `dissent_refs` | string[] | no | canonical dissent references |

The client must never synthesize `synthesis_summary` or committee verdicts from `participant_roster[].outcome_signal`.

## Authority Rules

- `allowedActions.canRecordSponsorDecision` is the sole CTA authority signal for the sponsor-decision action.
- The signal must be `false` unless a sponsor assignment is present and the operator holds `operator`, `approver`, or `admin` role.
- The signal must be `false` when:
  - the committee board surface is unavailable
  - `consensus_state != "sponsor_required"`
  - a sponsor decision has already been recorded

## Partial activation boundary

The following surfaces may be live before `CW-02` is route-live:

- committee board summary
- sponsor decision status
- current participants
- verdict summary
- pending actions
- linked memo / review refs
- high-level committee outcome

The following surfaces remain gated on `CW-02` transcript truth:

- transcript timeline panel
- actor-event detail
- quote / evidence-linked debate snippets
- event-level reasoning path
- transcript-driven verdict explanation
- full debate replay

## Non-Goals

- The client must not derive a verdict from participant votes.
- The client must not infer sponsor identity from roster ordering.
- The client must not show a sponsor-decision CTA when `allowedActions` is absent or false.

## Example Payload

- `docs/examples/CW-03-committee-board.json`
