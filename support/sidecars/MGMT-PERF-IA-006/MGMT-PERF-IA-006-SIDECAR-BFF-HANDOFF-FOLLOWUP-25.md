# MGMT-PERF-IA-006 BFF Handoff Follow-up 25

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-25` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet gives the parent owner a composition manifest to
attach to the frontend PR and hosted acceptance evidence. It does not define
routes, query keys, response fields, schemas, runtime behavior, frontend code,
or canonical truth. Earlier packets remain the route and journey inventory.

## 1. Entry Gate

Do not mark a journey ready until its Performance Center, Rankings Center, or
Governance Decisions destination is merged and the hosted frontend SHA is a
descendant of that merge. Record the hosted origin, BFF SHA, authenticated
actor, capture time, and strict-live request/response evidence. A green PR,
review approval, retained query string, mock response, or missing active-task
record does not open the gate.

If a destination is not delivered, use `dependency-waiting`; do not create a
BFF gap for an unshipped frontend dependency.

## 2. Composition Manifest

Copy one block per actual source action into the parent evidence. Do not merge
actions merely because they share a destination label.

```text
Journey id and source action:
Source page / frontend SHA:
Source BFF route and redacted response evidence:
Source-returned stable ids and scope:
Destination page / frontend SHA:
Typed, allow-listed navigation context:
Destination BFF route and redacted request/response evidence:
Response-fulfilled identity and applied scope:
Direct load / refresh / copied URL / back-forward results:
Applicable Human Inbox completion / cancellation return result:
Desktop / mobile evidence paths and capture time:
Healthy-empty / unavailable / invalid-id result:
First loss, if any:
Disposition:
```

Required source actions are Cockpit cards and alerts; Persona Fleet
performance, holdings, ranking, evidence, and review actions; Persona Detail
formal-analysis links; Strategy Detail attribution and Agora links; Human
Inbox review links; Capital Pool, Rebalance, and Ranking Policy details; and
Agora execution-performance links.

Use exactly one disposition:

- `absorbed`: response-authored identity and supported scope survive the full
  navigation and return journey;
- `visibly-unscoped`: the destination explicitly says the requested scope is
  unsupported;
- `honest-unavailable`: absent, invalid, stale, unauthorized, or incompatible
  context remains unavailable without fixture, inferred join, sibling data,
  or false-zero substitution;
- `split-to-bff`: deployed strict-live evidence isolates the first missing
  response boundary; or
- `dependency-waiting`: the destination gate is closed.

Display names, ranks, row order, matching values, nearby timestamps, and URL
retention are not proof of stable identity or fulfilled scope. A visible
filter may claim application only when the destination response confirms it.
Reset pagination or continuation when effective scope changes.

## 3. Truthfulness And Scope Checks

The completed manifest must show that compact Persona and Strategy summaries
remain visually distinct from formal attribution and ranking. Agora execution
diagnostics must remain separate from Management attribution even when a
strategy and period link connects them.

Exercise healthy empty, unavailable, degraded, stale/fallback, invalid
identity, unauthorized, and non-finite metric states on desktop and mobile.
Failures must remain section-local and visible. Fixture or fallback data is
not strict-live evidence. Review completion must not appear to apply an
operation unless a separate durable operation receipt proves that mutation.

## 4. BFF Split Card

Only a `split-to-bff` disposition may create a BFF follow-up. Attach:

```text
Blocked journey and parent acceptance statement:
Frontend SHA / BFF SHA / hosted origin / captured at:
Source route, redacted response, stable ids, and scope:
Destination route, redacted request/response, and fulfilled scope:
First missing stable id, applied scope, health, snapshot, link, or receipt:
Smallest response change requested:
Valid case and expected result:
Invalid / absent / stale / unauthorized / dependency-down cases:
Fail-closed frontend behavior pending delivery:
Named BFF owner / reviewer and frontend owner:
Non-goals:
```

Non-goals include generic filter expansion, convenience aggregates,
browser-side joins, fixture authority, duplicate analysis pages, new mutation
semantics, and canonical-contract changes inferred from this packet.

## 5. Parent Handoff

Parent owner `Antigravity` owns delivery-gate rechecks, selective absorption
into `execute-plans`, completion of the manifest, and assignment of any proven
BFF split. Parent reviewer `Claude` reviews the implementation and hosted
proof. Sidecar reviewer `Antigravity` reviews only whether this packet is
accurate, useful, fail-closed, and support-only; approval does not approve or
complete the parent task.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-25 Antigravity \
  "Support-only composition manifest and bounded BFF split card ready for review."
```

## 6. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-25` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, current parent state, and the three
  immediately preceding support packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source was
  changed.
