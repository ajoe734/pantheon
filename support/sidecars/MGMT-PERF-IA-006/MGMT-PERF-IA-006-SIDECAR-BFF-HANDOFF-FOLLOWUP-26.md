# MGMT-PERF-IA-006 BFF Handoff Follow-up 26

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet gives the parent owner a bounded absorption ledger for
the BFF query gaps, operator journeys, and frontend handoff evidence needed by
contextual integration. It does not define routes, query keys, response fields,
schemas, runtime behavior, frontend code, or canonical truth.

## 1. Absorption Gate

Evaluate each source action only after its Performance Center, Rankings Center,
or Governance Decisions destination is merged and the hosted frontend commit is
a descendant of that merge. Record the hosted origin, frontend and BFF SHAs,
authenticated actor, strict-live configuration, and capture time.

Until that gate is open, record `dependency-waiting`. A green or approved PR,
retained URL parameter, mock response, fixture, or absent task-state record is
not proof of delivery and must not create a BFF gap.

## 2. Parent Absorption Ledger

Complete one row for every actual source action. Persona Fleet performance,
holdings, ranking, evidence, and review are separate actions.

| Journey / source action | Destination gate evidence | Source-authored identity and scope | Typed navigation request | Destination-fulfilled identity and scope | Return behavior | Decision / evidence |
|---|---|---|---|---|---|---|
| Cockpit card or alert | Parent capture | Parent capture | Parent capture | Parent capture | n/a | Parent decision |
| Persona Fleet action | Parent capture | Parent capture | Parent capture | Parent capture | Fleet context | Parent decision |
| Persona Detail formal-analysis link | Parent capture | Parent capture | Parent capture | Parent capture | Persona context | Parent decision |
| Strategy Detail attribution or Agora link | Parent capture | Parent capture | Parent capture | Parent capture | Strategy context | Parent decision |
| Human Inbox review link | Parent capture | Parent capture | Parent capture | Parent capture | Allow-listed origin | Parent decision |
| Capital Pool, Rebalance, or Ranking Policy detail | Parent capture | Parent capture | Parent capture | Parent capture | Detail context | Parent decision |
| Agora execution-performance link | Parent capture | Parent capture | Parent capture | Parent capture | Agora context | Parent decision |

Use exactly one decision:

- `absorb`: response-authored identity and supported scope survive direct load,
  refresh, copied URL, browser history, and any applicable return journey;
- `visibly-unscoped`: the destination explicitly rejects or omits unsupported
  scope;
- `honest-unavailable`: absent, invalid, stale, unauthorized, incompatible, or
  dependency-down data remains unavailable without fixture, inferred join,
  sibling data, or false-zero substitution;
- `split-to-bff`: deployed strict-live evidence isolates the first missing
  response boundary and the frontend cannot truthfully complete the journey;
  or
- `dependency-waiting`: the destination gate is closed.

Display labels, rank, row position, matching metric values, nearby timestamps,
and URL retention do not prove stable identity or applied scope. Reset
pagination or continuation whenever the effective scope changes.

## 3. Minimal Query-gap Card

Create a BFF follow-up only for `split-to-bff`. Attach this card without
inventing a contract:

```text
Blocked journey and parent acceptance statement:
Hosted origin / frontend SHA / BFF SHA / captured at:
Authenticated role and strict-live proof:
Source route and redacted response:
Source-authored stable ids and scope:
Destination route and redacted request/response:
Requested versus response-fulfilled context:
First missing identity, scope, health, snapshot, link, or receipt boundary:
Smallest response change requested:
Valid case and expected result:
Invalid / absent / stale / unauthorized / dependency-down cases:
Fail-closed frontend behavior pending delivery:
BFF owner / reviewer and frontend owner:
Non-goals:
```

Non-goals include generic filter expansion, convenience aggregates,
browser-side joins, fixture authority, duplicate analysis pages, new mutation
semantics, or canonical-contract changes inferred from this packet.

## 4. Operator Journey Proof

Capture desktop and mobile evidence for direct load, refresh, copied URL,
back/forward, and applicable Human Inbox completion and cancellation. Return
only to an allow-listed origin and preserve only response-supported decision
context.

The evidence must also prove that compact Persona and Strategy summaries stay
visibly distinct from formal attribution and ranking; Agora execution
diagnostics stay separate from Management attribution; and healthy empty,
unavailable, degraded, stale/fallback, invalid identity, unauthorized, and
non-finite states remain honest and section-local. Review completion must not
look like an applied operation without a separate durable operation receipt.

Redact secrets and personal data. Fixture and fallback responses are not
strict-live acceptance evidence.

## 5. Ownership And Handoff

Parent owner `Antigravity` owns destination-gate rechecks, selective absorption
into `execute-plans`, ledger completion, and assignment of evidence-backed BFF
splits. Parent reviewer `Claude` reviews the composed implementation and hosted
proof. Sidecar reviewer `Antigravity` reviews only whether this packet is
accurate, useful, fail-closed, and support-only; approval does not approve or
complete the parent task.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-26 Antigravity \
  "Support-only absorption ledger and bounded BFF query-gap card ready for review."
```

## 6. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-26` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, current task and parent state, and the
  three immediately preceding support packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source changed.

## 7. Review And Finalization Evidence

- Implementation commit `a8600b6e36b35daab5472ed71815194974716430`
  merged to `dev` through PR #3396.
- Reviewer `Antigravity` approved the packet as accurate, fail-closed, useful,
  and support-only in
  `docs/reviews/2026-07-12-mgmt-perf-ia-006-sidecar-bff-handoff-followup-26-antigravity-review.md`.
- Finalization rechecked the task-scoped brief, approval record, packet scope,
  branch ancestry, and whitespace integrity with `git diff --check`.
- The parent owner remains responsible for selective absorption and for any
  later canonical, BFF/runtime, or frontend implementation decision.
