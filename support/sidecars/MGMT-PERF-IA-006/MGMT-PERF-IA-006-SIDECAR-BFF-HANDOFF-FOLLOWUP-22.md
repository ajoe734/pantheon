# MGMT-PERF-IA-006 BFF Handoff Follow-up 22

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet is a wake-up and absorption cut-line for the parent.
It does not define routes, query keys, response fields, schemas, runtime or
registry behavior, frontend code, or canonical truth. The base handoff and
earlier follow-ups remain the detailed route and journey inventory.

## 1. Current Wake-up Gate

The parent remains `todo` and depends on `MGMT-PERF-IA-003`,
`MGMT-PERF-IA-004`, and `MGMT-PERF-IA-005`. At preparation time:

- `MGMT-PERF-IA-003` is blocked pending human merge of execute-plans PR
  `#261` and subsequent hosted evidence;
- `MGMT-PERF-IA-005` is `review_approved`, but execute-plans PR `#260` is not
  merged and therefore is not a delivered destination; and
- `MGMT-PERF-IA-004` is absent from the active task list. Absence is not proof
  of merge or deployment; the parent must resolve it from its archived task
  record and delivery evidence before composition.

Do not wake contextual implementation merely because a dependency branch is
mergeable, a review passed, a query value persists in the browser, or a task
left the active list. Wake only when every destination consumed by a journey
has a merged SHA, hosted SHA ancestry, authenticated strict-live behavior, and
response-authored scope evidence.

## 2. Minimal Absorption Ledger

The parent owner should fill one row for each destination before editing
contextual links.

| Destination | Task record / terminal state | PR / merge SHA | Hosted SHA / origin | BFF SHA | Authenticated desktop + mobile proof | Decision |
|---|---|---|---|---|---|---|
| Performance Center (`MGMT-PERF-IA-003`) | Parent records | Parent records | Parent records | Parent records | Parent records | `waiting` / `ready` |
| Rankings Center (`MGMT-PERF-IA-004`) | Archive required | Parent records | Parent records | Parent records | Parent records | `waiting` / `ready` |
| Governance Decisions (`MGMT-PERF-IA-005`) | Parent records | Parent records | Parent records | Parent records | Parent records | `waiting` / `ready` |

A `waiting` row is a dependency condition, not a BFF defect. Do not create a
BFF task until deployed request/response evidence identifies the first missing
stable identity, applied scope, health, snapshot, link, or durable receipt.

## 3. Journey Cut-line

Once the relevant destination rows are `ready`, classify each Cockpit, Persona
Fleet, entity-detail, Human Inbox, capital/rebalance/policy-detail, and Agora
entry point as exactly one of:

- `absorbed`: supported context round-trips and the response proves fulfilled
  identity and scope;
- `visibly-unscoped`: the destination legitimately lacks that scope and says
  so without implying the requested filter was applied;
- `honest-unavailable`: missing or incompatible identity/data stays visibly
  unavailable without fixture, sibling-resource, inferred join, or false-zero
  substitution; or
- `split-to-bff`: deployed evidence isolates one bounded response gap with a
  named BFF owner and a fail-closed negative case.

For every row record the source stable identifier, destination requested
context, response-fulfilled identity/scope, first loss, frontend SHA, BFF SHA,
capture time, and evidence path. Display names, ranks, row order, metric
equality, nearby timestamps, and retained URL values are not identity proof.

## 4. Operator Journey Proof

The parent acceptance run should prove that:

1. direct load, refresh, copied URL, browser back/forward, and applicable
   Human Inbox return preserve only typed, allow-listed context;
2. visible filters reflect response-fulfilled scope, with pagination reset
   when effective scope changes;
3. compact entity summaries remain distinct from formal attribution/ranking,
   and Agora execution performance remains distinct from Management
   attribution;
4. review completion is not presented as an applied mutation without a
   distinct durable operation receipt; and
5. healthy empty, unavailable, degraded, stale/fallback, invalid identity, and
   non-finite metric states remain honest and section-local on desktop and
   mobile strict-live runs.

## 5. BFF Split Attachment

Attach this only when a row is `split-to-bff`:

```text
Journey and blocked parent acceptance:
Frontend SHA / BFF SHA / captured at:
Source route and redacted returned stable identifiers:
Destination route and redacted request/response:
Requested context / response-fulfilled context:
First missing stable id, applied scope, health, snapshot, link, or receipt:
Smallest requested response change:
Valid case and expected result:
Invalid, absent, stale, unauthorized, or dependency-down case:
Fail-closed frontend behavior until delivery:
Named BFF owner / reviewer and frontend owner:
Non-goals:
```

Non-goals include generic filter expansion, convenience aggregates,
browser-side joins, fixture authority, duplicate analysis pages, and new
mutation semantics.

## 6. Ownership And Review

Parent owner `Antigravity` resolves the archived `MGMT-PERF-IA-004` delivery
record, opens each destination gate, and decides what to absorb into
`execute-plans`. Parent reviewer `Claude` reviews the composed implementation
and hosted proof. Sidecar reviewer `Antigravity` reviews only whether this
packet is accurate, useful, fail-closed, and support-only; approval does not
approve or complete the parent task.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-22 Antigravity \
  "Support-only destination wake-up ledger and bounded BFF split cut-line ready for review."
```

## 7. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-22` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, current parent/dependency state, base
  handoff, and immediately preceding cut-line packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source was
  changed.

## 8. Closeout Record

Sidecar reviewer `Antigravity` approved commit `a9ea62fd125cdadcba706d068eaa192e65fa736e`
on `2026-07-12`. The review confirmed that the dependency posture, absorption
ledger, journey classifications, proof criteria, and bounded BFF split template
are useful to the parent while remaining support-only. The approval is recorded
in
`support/reviews/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-22-review-antigravity.md`.

Closeout verification:

- `git diff --check`
- task artifact and reviewer record inspected against the task-scoped brief
- changed paths checked to remain under `support/`; no canonical, runtime,
  registry, governance, schema, or frontend implementation path is included

The parent owner decides whether and how to absorb this packet. Closing this
sidecar does not change the parent task's delivery or dependency status.
