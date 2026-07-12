# MGMT-PERF-IA-006 BFF Handoff Follow-up 21

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Date | `2026-07-12` |
| Mutates canonical truth | `false` |

This support-only packet is a fillable composition decision record for the
parent owner. It does not define routes, query keys, response fields, schemas,
runtime behavior, frontend code, registry/governance behavior, or canonical
truth. It must be completed from merged and deployed evidence; provisional
branch behavior and URL intent are not contract evidence.

## 1. Destination Readiness Gate

Do not evaluate a contextual journey until its formal destination is merged
and deployed. Record one row for each dependency actually consumed.

| Dependency | Merged PR / SHA | Hosted SHA and origin | BFF SHA | Strict-live capture | Gate |
|---|---|---|---|---|---|
| `MGMT-PERF-IA-003` Performance Center | Parent records | Parent records | Parent records | Parent records | `open` / `waiting-for-destination` |
| `MGMT-PERF-IA-004` Rankings Center | Parent records | Parent records | Parent records | Parent records | `open` / `waiting-for-destination` |
| `MGMT-PERF-IA-005` Governance Decisions | Parent records | Parent records | Parent records | Parent records | `open` / `waiting-for-destination` |

An unopened gate is a dependency state, not a BFF gap. The parent must not
infer accepted filters or response behavior from task prose, mock data, or an
unmerged branch.

## 2. Composition Decision Record

Create one row per implemented entry point. Preserve requested context and
response-fulfilled context separately.

| Journey | Source route / returned stable ID | Destination route / requested context | Response-fulfilled ID, scope, health, time | First loss | Decision | Evidence / owner |
|---|---|---|---|---|---|---|
| Cockpit -> Performance or Rankings | Parent records | Parent records | Parent records | Parent records | Parent records | Parent records |
| Persona Fleet -> Performance or holdings | Parent records | Parent records | Parent records | Parent records | Parent records | Parent records |
| Persona detail -> Performance or Rankings | Parent records | Parent records | Parent records | Parent records | Parent records | Parent records |
| Strategy detail -> Performance or Agora | Parent records | Parent records | Parent records | Parent records | Parent records | Parent records |
| Human Inbox -> decision -> return | Parent records | Parent records | Parent records | Parent records | Parent records | Parent records |
| Capital Pool / Rebalance / Ranking Policy detail | Parent records | Parent records | Parent records | Parent records | Parent records | Parent records |
| Agora -> Management Performance -> return | Parent records | Parent records | Parent records | Parent records | Parent records | Parent records |

Use exactly one decision per row:

- `absorbed`: stable identity and supported scope round-trip through direct
  load, refresh, copied URL, browser history, and applicable Inbox return;
- `visibly-unscoped`: the destination is legitimately unscoped and labels
  that limitation without implying a requested filter was fulfilled;
- `honest-unavailable`: identity, data, source health, or compatible scope is
  absent, and the UI does not substitute a fixture, inferred join, sibling
  resource, stale fallback, or false zero; or
- `split-to-bff`: deployed evidence isolates one smallest missing response
  boundary and assigns it to a named Pantheon BFF owner.

Display names, labels, ranks, row positions, metric equality, nearby
timestamps, and browser-retained query values are not stable identity or
fulfilled-scope evidence.

## 3. Smallest BFF Gap Attachment

Attach this block only to a `split-to-bff` row. One attachment covers one
first-loss boundary.

```text
Journey and blocked parent acceptance:
Frontend SHA / BFF SHA / captured at:
Source route and redacted returned identifiers:
Destination route and redacted request/response:
Requested context:
Response-fulfilled context:
First missing stable id, link, applied scope, health, snapshot, or receipt:
Smallest requested response change:
Valid case and expected result:
Invalid, absent, stale, unauthorized, or dependency-down case:
Fail-closed frontend behavior until delivery:
Named BFF owner / reviewer and frontend owner:
Non-goals:
```

The non-goals must exclude generic filter expansion, a convenience aggregate
endpoint, browser-side identity joins, fixture authority, duplicate analysis
pages, and new mutation semantics. Independent reads keep section-local
loading, health, freshness, empty, and error state unless an operator action
demonstrably requires atomic semantics.

## 4. Journey Proof Checklist

For every absorbed or visibly-unscoped row, record evidence that:

1. the canonical destination receives only its typed allow-listed context and
   resets pagination or continuation when effective scope changes;
2. visible filters reflect response-fulfilled scope rather than URL intent;
3. compact entity summaries remain distinct from formal attribution/ranking,
   while Agora execution diagnostics remain distinct from Management
   attribution;
4. Human Inbox completion and cancellation return only to an allow-listed
   origin, and review completion is not presented as applied without a
   distinct durable operation receipt;
5. healthy empty, unavailable, degraded, stale/fallback, invalid identity, and
   non-finite metric states remain honest and section-local; and
6. strict-live desktop and mobile runs cover direct load, refresh, copied URL,
   back/forward, and applicable cross-surface return without fixture or
   fallback authority.

## 5. Parent Absorption And Review

Parent owner `Antigravity` owns completion of this record, selective
absorption into `execute-plans`, and assignment of any bounded BFF gaps.
Parent reviewer `Claude` reviews the eventual composed implementation and
hosted evidence. Sidecar reviewer `Antigravity` verifies only that this packet
is accurate, useful, fail-closed, and support-only; approval of this packet
does not approve or complete the parent task.

Suggested transition:

```bash
AI_NAME=Codex2 ./scripts/ai-status.sh handoff \
  MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-21 Antigravity \
  "Support-only composition decision record ready for review and parent absorption."
```

## 6. Preparation Evidence

- Prepared on
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-21` from `dev`.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  protocols, parent execution packet, base handoff, and the immediately
  preceding handoff cut-line and intake-gate packets.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed only this support artifact. No canonical truth, Pantheon BFF/runtime
  code, schema, registry/governance implementation, or frontend source was
  changed.
