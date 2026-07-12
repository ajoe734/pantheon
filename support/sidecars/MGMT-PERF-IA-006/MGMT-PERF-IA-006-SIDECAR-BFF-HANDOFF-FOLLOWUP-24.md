# MGMT-PERF-IA-006 BFF Handoff Follow-up 24

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-24` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet is a dependency-readiness and first-failure worksheet
for the parent owner. It does not define routes, query keys, response fields,
schemas, runtime behavior, frontend code, or canonical truth.

## 1. Current Composition Stop Gate

The parent must not begin cross-entry-point acceptance until every Wave 1
destination is merged and hosted from the recorded frontend SHA.

| Dependency | Task-state observation | Parent disposition |
|---|---|---|
| Performance Center (`MGMT-PERF-IA-003`) | `blocked`; execute-plans PR `#261` is reported green and mergeable but not merged | `dependency-waiting` |
| Rankings Center (`MGMT-PERF-IA-004`) | No task record was returned by the task-scoped `ai-status.json` lookup | `dependency-waiting`; resolve durable task/delivery evidence before composition |
| Governance Decisions (`MGMT-PERF-IA-005`) | `review_approved`; execute-plans PR `#260` is reported green but not merged | `dependency-waiting` |

These observations are routing aids, not delivery proof. The parent must
re-check the merged commit, hosted commit ancestry, strict-live origin, and
authenticated destination behavior when it wakes. A retained query string,
mock response, green unmerged PR, or review approval is not an open gate.

## 2. Wake-up Evidence Card

Complete this card separately for Performance, Rankings, and Governance:

```text
Destination:
Frontend PR and merge SHA:
Hosted frontend SHA and ancestry proof:
Hosted origin and captured at:
BFF SHA and strict-live base URL:
Authenticated actor/role:
Direct-load request/response result:
Healthy empty and unavailable result:
Evidence location:
Gate: open | dependency-waiting
```

If any card remains `dependency-waiting`, stop the affected journey. Do not
create a BFF task merely because the destination has not shipped.

## 3. Journey First-failure Matrix

Once the relevant destination gate is open, capture one row per real source
action rather than grouping actions by destination label.

| Source action | Source-returned stable context | Typed navigation request | Destination-fulfilled context | Return result | First failure | Disposition |
|---|---|---|---|---|---|---|
| Cockpit card or alert | Parent capture | Parent capture | Parent capture | n/a | Parent capture | Parent decision |
| Persona Fleet performance / holdings / ranking | Parent capture | Parent capture | Parent capture | Fleet context | Parent capture | Parent decision |
| Persona detail formal-analysis link | Parent capture | Parent capture | Parent capture | Persona detail | Parent capture | Parent decision |
| Strategy detail attribution / Agora link | Parent capture | Parent capture | Parent capture | Strategy detail | Parent capture | Parent decision |
| Human Inbox review link | Parent capture | Parent capture | Parent capture | Allow-listed origin | Parent capture | Parent decision |
| Capital Pool / Rebalance / Ranking Policy detail | Parent capture | Parent capture | Parent capture | Detail context | Parent capture | Parent decision |
| Agora execution-performance link | Parent capture | Parent capture | Parent capture | Agora strategy context | Parent capture | Parent decision |

Allowed dispositions are:

- `absorbed`: response-authored identity and scope survive direct load,
  refresh, copied URL, back/forward, and applicable return;
- `visibly-unscoped`: destination states that requested scope is unsupported;
- `honest-unavailable`: absent, invalid, stale, unauthorized, or incompatible
  context does not become a fixture, inferred join, sibling record, or zero;
- `split-to-bff`: strict-live evidence isolates a missing response boundary;
  or
- `dependency-waiting`: the required destination gate is closed.

Display names, ranks, row positions, matching values, nearby timestamps, and
browser-retained query parameters do not prove identity or fulfilled scope.

## 4. Query-gap Decision Rule

Classify a BFF gap only when all of the following are true:

1. the destination gate is open and the hosted frontend is using strict-live
   BFF mode;
2. the source response contains a stable identifier or scope that the journey
   legitimately needs;
3. the typed frontend adapter forwards only destination-supported context;
4. the redacted destination response still cannot confirm identity, applied
   scope, source health, snapshot, link, or durable operation receipt; and
5. the frontend cannot remain truthful with `visibly-unscoped` or
   `honest-unavailable` behavior.

When these conditions hold, attach the source route and response, destination
route and request/response, source/request/fulfilled context triplet, first
missing boundary, smallest requested response change, negative cases,
fail-closed interim UI, and named BFF/frontend owners. Do not propose generic
filter expansion, browser-side joins, duplicate analysis pages, fixture
authority, or new mutation semantics.

## 5. Operator Acceptance Run

For desktop and mobile, verify direct load, refresh, copied URL, browser
history, and applicable Human Inbox completion/cancellation return. Preserve
only allow-listed return destinations and supported decision context.

The evidence must also show:

- compact Persona and Strategy summaries remain distinct from formal
  attribution and ranking;
- Agora execution diagnostics remain distinct from Management attribution;
- healthy empty, unavailable, degraded, stale/fallback, invalid identity,
  unauthorized, and non-finite states remain honest and section-local;
- filter or effective-scope changes reset pagination/continuation; and
- review completion is not presented as an applied operation without a
  separate durable operation receipt.

Redact secrets and personal data. Fixture or fallback responses are not
strict-live acceptance evidence.

## 6. Ownership And Handoff

Parent owner `Antigravity` owns gate re-checks, selective absorption into
`execute-plans`, the completed journey matrix, and assignment of any proven
BFF split. Parent reviewer `Claude` reviews the composed implementation and
hosted evidence. Sidecar reviewer `Antigravity` reviews only whether this
packet is accurate, fail-closed, useful, and support-only; sidecar approval
does not approve or complete the parent task.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-24 Antigravity \
  "Support-only dependency gate and first-failure worksheet ready for review."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-24` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, current parent/dependency state, the
  umbrella BFF handoff, and the immediately preceding support packet.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source was
  changed.
