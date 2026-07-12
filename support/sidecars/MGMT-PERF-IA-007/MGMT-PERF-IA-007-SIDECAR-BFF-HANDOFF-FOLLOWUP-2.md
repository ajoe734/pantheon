# MGMT-PERF-IA-007 BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-007` |
| Parent owner / reviewer | `Claude` / `Antigravity` |
| Sidecar task | `MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Sidecar owner / reviewer | `Codex2` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Generated | `2026-07-12` |
| Mutates canonical | `false` |

This support-only packet gives the parent owner a decision-ready cleanup gate.
It does not change canonical truth, Pantheon BFF routes or schemas,
`execute-plans`, route registries, or governance behavior, and it does not
approve or finalize the parent task.

## 1. Parent Decision

The parent may prepare inventory and non-destructive regression coverage now,
but must not remove legacy aliases, exported pages, secondary navigation, or
compatibility behavior yet.

At inspection time:

- `MGMT-PERF-IA-003` is blocked pending merge of `execute-plans` PR `#261` and
  hosted evidence;
- `MGMT-PERF-IA-005` is `review_approved`, but PR `#260` remains unmerged;
- `MGMT-PERF-IA-006` remains `todo`;
- the parent itself remains `todo` and depends on `003` through `006`.

Therefore planned destination shapes are not sufficient deletion evidence.
The parent should absorb cleanup only after the merged centers and contextual
links establish the actual route, query, return-path, and data-state behavior.

| Work slice | Current disposition |
|---|---|
| Route/export/link inventory | Prepare now. Include static, lazy, dynamic, test, command-palette, breadcrumb, mobile, and contextual consumers. |
| Manifest-derived crawl and redirect assertions | Prepare without deleting compatibility entries. Keep expected aliases sourced from the merged manifest baseline. |
| `ManagementOperationsNav` removal | Defer until every affected route proves equivalent sidebar, tab, breadcrumb, mobile, and return navigation. |
| Dead-page or alias removal | Defer until dependencies merge and zero consumers plus an explicit compatibility disposition are proven. |
| Hosted regression and redirect-expiry decision | Defer until the composed frontend revision is deployed against strict-live BFF. |

## 2. BFF And Query Preservation Gate

Cleanup should not create a new BFF endpoint or infer a translation in the
browser. For every legacy-to-canonical transition, compare the incoming and
final contexts and preserve every field supported by the merged destination:

- persona, runtime, strategy, capital pool or sleeve, artifact, broker, and
  deployment-stage identity;
- period, as-of time, ranking dimension, snapshot, and source context;
- immutable ranking-evidence, recommendation, review, decision, command, and
  receipt identifiers when present.

The final read must retain backend-owned source health: confidence, freshness,
coverage, missing bindings, observed time, and formal/partial/fallback/
degraded/unavailable distinctions. A redirect or removed page must never turn
missing data into a fixture, synthetic default, `0`, or apparent healthy empty.

If the merged BFF/read model does not define a compatible mapping, retain the
compatibility route and record a bounded BFF/frontend gap. Do not join by label,
timestamp, actor, persona, quarter, or matching display text.

## 3. Operator Journeys

### Legacy investigation to canonical center

1. An operator opens a legacy performance or ranking URL with identity and
   time filters.
2. A single bounded redirect lands in Performance Center or Rankings Center.
3. Applicable query context, fragment, source state, and back/forward behavior
   survive.
4. Empty, stale, fallback, degraded, unavailable, and transport failure remain
   distinct.

### Ranking evidence to governed decision

1. A legacy ranking entry lands on the canonical rolling or quarterly view
   with snapshot context intact.
2. The operator follows immutable evidence to Governance Decisions or Human
   Review.
3. Recommendation, submission, decision, accepted/applying, and completed
   apply receipt remain separate states.
4. No ranking row or redirect directly mutates capital, access, promotion,
   freeze, rebalance, broker, or runtime state.

### Contextual drill-down and return

1. Cockpit, Fleet, entity detail, Inbox, or Agora opens a canonical center with
   the relevant entity and investigation window.
2. Missing joins remain explicit and route to diagnostics or governed triage.
3. Human Review and detail pages restore the originating context on return.
4. Agora stays an execution-diagnostics surface and is not duplicated into the
   management hierarchy.

## 4. Frontend Handoff

The `execute-plans` owner should:

- derive desktop, mobile, command-palette, breadcrumb, and crawl inventories
  from one route/menu manifest;
- give each legacy route exactly one evidence-backed outcome: canonical,
  query-preserving compatibility redirect, explicit unavailable detail, or
  deliberate removal;
- resolve `RankingDashboardPage`, Capital Pool Detail, Rebalance Detail, and
  Ranking Formula Detail individually rather than treating “unrouted export”
  as deletion proof;
- preserve shared filters when changing tabs and prevent redirect loops;
- keep Rankings comparative, Governance Decisions governed, and Agora
  execution-focused;
- record telemetry owner, observation window, expiry criterion, and rollback
  for every compatibility redirect selected for later removal.

## 5. Required Evidence Before Absorption

- Dependencies `003` through `006` have merged delivery evidence.
- Static and runtime route inventories agree, including dynamic imports and
  contextual links.
- Canonical and compatibility URLs pass crawl, bounded redirect, broken-link,
  query/fragment preservation, and back/forward tests.
- Formal, partial, fallback, stale, healthy-empty, degraded, unavailable, and
  transport-failure states have regression assertions.
- Keyboard/focus behavior and the same hierarchy pass on desktop and mobile
  without secondary-navigation overlap.
- Hosted smoke uses the deployed frontend revision, strict-live BFF, and proves
  filter persistence plus governed return navigation.
- The delivery record includes frontend PR and merge SHA, deployed revision,
  evidence links, redirect telemetry/expiry owner, and residual gaps.

Until all gates pass, compatibility retention is the fail-safe disposition.
This packet is advisory; it is not implementation, deployment, review, or
acceptance evidence for the parent.

## 6. Reviewer Handoff

Technical reviewer `Claude` should verify that:

- the dependency-state claims match `ai-status.json`;
- the packet permits preparation but not premature destructive cleanup;
- query preservation and source-health rules do not invent wire fields or
  endpoints;
- governed-action and Agora boundaries remain intact; and
- only this support artifact is intentionally committed.

Parent owner `Claude` decides whether and when to absorb the packet. Parent
reviewer and formal sidecar reviewer `Antigravity` evaluates the composed
`execute-plans` delivery and owns the formal lifecycle gate; sidecar approval
does not substitute for the parent review.

## 7. Verification Notes

Source inspection only. Re-read the task-scoped brief, the original approved
handoff packet, the parent execution packet, and current task records for the
parent and dependencies. Confirmed that this follow-up changes no canonical,
BFF/runtime/schema, route-registry, governance, or frontend implementation.
`current-work.md` and the complete `ai-activity-log.jsonl` were not scanned.

## Review And Closeout Record

Claude performed the technical review of this packet against the live
`MGMT-PERF-IA-003`, `-005`,
`-006`, and `-007` task records in `ai-status.json`, the cited `execute-plans`
PR #261 and PR #260 states, and the prior approved
`MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF` packet, and approved it: every
dependency-state claim matches current status exactly, the BFF/query
preservation vocabulary matches existing repo terminology rather than
inventing new wire fields, and the packet permits only preparation, not
premature destructive cleanup. Full verification is in
`support/reviews/MGMT-PERF-IA-007-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-review-claude.md`.
This approval covers only this support artifact, not the parent task's own
implementation or dependency-merge requirements.

The task-scoped brief records lifecycle status `review_approved` with
`Antigravity` as the formal reviewer. Closeout therefore preserves Claude's
technical review as advisory evidence while using `Antigravity` for the
durable task metadata and commit trailer. The packet and technical review were
merged to `dev` by PR #3344 at merge commit
`131a7c8cf20fde4e3d7e1d0d2b15388a797fbd08`.
