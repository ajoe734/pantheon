# MGMT-PERF-IA-006 BFF Handoff Follow-up 23

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-23` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet is a first-composition-session worksheet for the
parent owner. It does not define routes, query keys, response fields, schemas,
runtime behavior, frontend code, or canonical truth. The parent must populate
it from merged and hosted evidence after its Performance, Rankings, and
Governance destination gates are ready.

## 1. Session Entry Gate

Before editing a source entry point, record the merged frontend SHA, hosted
SHA and origin, BFF SHA, authenticated actor, and capture time for every
destination that journey consumes. A reviewed or mergeable branch, retained
URL parameter, mock response, or absent active-task record is not delivery
evidence.

If a destination is not deployed, mark the journey `dependency-waiting` and
stop. Do not misclassify an unopened dependency as a BFF query gap.

## 2. Entry-point Composition Worksheet

Complete one row per actual source action. Do not group entries merely because
they share a destination label.

| Source action | Returned stable identity | Requested destination context | Response-fulfilled identity and scope | Return target | Result | Evidence |
|---|---|---|---|---|---|---|
| Cockpit card or alert | Parent capture | Parent capture | Parent capture | n/a | Parent decision | Parent capture |
| Persona Fleet performance / holdings / ranking | Parent capture | Parent capture | Parent capture | Fleet context | Parent decision | Parent capture |
| Persona detail formal-analysis link | Parent capture | Parent capture | Parent capture | Persona detail | Parent decision | Parent capture |
| Strategy detail attribution / Agora link | Parent capture | Parent capture | Parent capture | Strategy detail | Parent decision | Parent capture |
| Human Inbox review link | Parent capture | Parent capture | Parent capture | Allow-listed origin | Parent decision | Parent capture |
| Capital Pool / Rebalance / Ranking Policy detail | Parent capture | Parent capture | Parent capture | Detail context | Parent decision | Parent capture |
| Agora execution-performance link | Parent capture | Parent capture | Parent capture | Agora strategy context | Parent decision | Parent capture |

Use one result only:

- `absorbed`: supported stable identity and response-authored scope survive
  direct load, refresh, copied URL, back/forward, and applicable return;
- `visibly-unscoped`: the destination truthfully states that the requested
  scope is unsupported;
- `honest-unavailable`: absent, invalid, stale, unauthorized, or incompatible
  context does not become fixture data, an inferred join, sibling data, or a
  false zero;
- `split-to-bff`: strict-live request/response evidence isolates the first
  missing response boundary and assigns a bounded follow-up; or
- `dependency-waiting`: the destination delivery gate is not open.

Display names, ranks, row positions, matching metric values, nearby timestamps,
and browser-retained query values do not prove identity or fulfilled scope.

## 3. Context Provenance Check

For every non-waiting journey, capture three distinct values:

1. **source-authored context** — stable identifiers and scope returned by the
   source read;
2. **navigation request** — the typed, allow-listed context sent to the
   destination; and
3. **response-fulfilled context** — identity and applied scope confirmed by
   destination data or metadata.

The UI may display a filter as applied only from item 3. Unsupported or invalid
context must be removed or visibly rejected, and a changed effective scope
must reset pagination or continuation. Frontend joins must not manufacture
identity across those boundaries.

## 4. Operator Proof Run

The parent acceptance capture should cover desktop and mobile strict-live
behavior for:

1. entry from each legitimate source, direct load, refresh, copied URL, and
   browser back/forward;
2. Human Inbox completion and cancellation returning only to an allow-listed
   origin with the original supported decision context;
3. compact Persona or Strategy summaries remaining visually distinct from
   formal attribution and ranking;
4. Agora execution diagnostics remaining explicitly separate from Management
   attribution while preserving supported strategy and period context;
5. healthy empty, unavailable, degraded, stale/fallback, invalid identity,
   unauthorized, and non-finite metric states remaining honest and
   section-local; and
6. review completion not being presented as an applied operation without a
   separate durable operation receipt.

Record request/response captures with secrets and personal data redacted. Do
not accept fixture or fallback data as strict-live proof.

## 5. Minimal BFF Escalation

Create a BFF follow-up only for a `split-to-bff` row, using this attachment:

```text
Journey and blocked parent acceptance:
Frontend SHA / BFF SHA / origin / captured at:
Source route and redacted returned stable identifiers:
Destination route and redacted request/response:
Source-authored / requested / response-fulfilled context:
First missing stable id, applied scope, health, snapshot, link, or receipt:
Smallest requested response change:
Valid case and expected result:
Invalid, absent, stale, unauthorized, or dependency-down case:
Fail-closed frontend behavior pending delivery:
Named BFF owner / reviewer and frontend owner:
Non-goals:
```

Non-goals include generic filter expansion, convenience aggregates,
browser-side joins, fixture authority, duplicate analysis pages, new mutation
semantics, and changes to canonical contracts inferred solely from this packet.

## 6. Parent Absorption And Review

Parent owner `Antigravity` owns the completed worksheet, selective absorption
into `execute-plans`, and assignment of any evidence-backed BFF split. Parent
reviewer `Claude` reviews the composed implementation and hosted evidence.
Sidecar reviewer `Antigravity` reviews only whether this packet is accurate,
useful, fail-closed, and support-only; sidecar approval does not approve or
complete `MGMT-PERF-IA-006`.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-23 Antigravity \
  "Support-only first-composition worksheet ready for review and parent absorption."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-23` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, current parent state, and the two
  immediately preceding support packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source was
  changed.

## 8. Closeout Record

- Sidecar reviewer `Antigravity` approved the worksheet in
  `support/reviews/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-23-review-antigravity.md`.
- Approval applies only to this support packet; parent task
  `MGMT-PERF-IA-006` remains owned and accepted independently.
- Finalization verification: `git diff --check origin/dev...HEAD` and explicit
  existence checks for the packet and reviewer artifact.
- Parent absorption target remains `Antigravity`; no canonical, runtime, BFF,
  schema, governance, registry, or frontend implementation was changed during
  closeout.
