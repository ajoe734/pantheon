# MGMT-PERF-IA-006 BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only absorption decision |
| Mutates canonical or runtime | `false` |

This follow-up turns the already reviewed base packet into a narrow decision
handoff for the parent owner. It does not redefine endpoint contracts, edit
Pantheon BFF or `execute-plans`, approve the parent, or authorize mutations.

## Parent Absorption Decision

Absorb the base packet at
`MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF.md` as the integration checklist. Its
route inventory, operator journey, honest-unavailable rules, and frontend
validation guidance remain applicable. Do not duplicate those sections in the
parent implementation record.

| Parent need | Decision | Boundary |
|---|---|---|
| Cockpit, Fleet, entity, Inbox, and Agora navigation | Absorb | Preserve stable backend IDs and compatible period context in canonical frontend URLs. |
| Formal performance and ranking reads | Absorb | Use existing BFF reads; keep attribution, ranking, and Agora execution scopes visibly distinct. |
| Multi-read composition | Absorb | Preserve section-local health, snapshot time, empty, loading, and error state. |
| Browser-side cross-surface record join | Reject | Never join by display name, text, rank, actor, or timestamp. |
| Universal context token or durable return chain | Do not claim | The inspected BFF surface does not prove one contract spanning every entry point and Human Inbox return. |
| New BFF schema or route | Separate task only | Open a Pantheon BFF contract task if frontend acceptance cannot be met with existing stable identifiers. |
| Freeze, promote, rebalance, or allocation mutation | Out of scope | Keep these in governed Human Review/apply-receipt flows. |

## Query Gap Disposition

The parent may use frontend route context such as `persona`, `runtime`,
`strategy`, `capital_pool`, `stage`, `period`, and `as_of`, but an adapter must
send only parameters accepted by the selected endpoint. Requested URL context
is navigation intent; returned BFF identifiers, period/snapshot metadata, and
source diagnostics are fulfillment evidence.

Escalate a separate BFF contract task only when all of the following are true:

1. A required parent journey cannot preserve identity using identifiers already
   returned by its source and accepted by its destination.
2. Rendering unavailable would fail an explicit parent acceptance criterion.
3. The missing link cannot be solved by a typed frontend route adapter without
   inferring identity or data truth.

That task must name the exact source response, destination read, missing stable
identifier or return-context field, and focused contract tests. This sidecar
does not choose the route or field name.

## Operator Journey Checkpoint

The parent owner should demonstrate one continuous read/navigation journey:

1. Enter from Cockpit, Persona Fleet, an entity detail, Human Inbox, or Agora.
2. Preserve supported identity and period context through the canonical center.
3. Render returned source health and snapshot metadata, including degraded or
   unavailable sections.
4. Keep entity summaries separate from formal attribution and ranking; keep
   Agora execution performance separate from portfolio attribution.
5. Round-trip refresh, copied URL, browser history, and Human Inbox return
   without an inferred record join or arbitrary return URL.

Healthy empty is empty. Missing identity, incompatible context, unhealthy
source, stale/fallback evidence, or an unlinked detail is unavailable or
degraded with a visible reason; it is never fixture authority or zero.

## Parent Evidence To Record

- the `execute-plans` PR and merge SHA that implement the contextual links;
- URL adapter tests covering parse, serialize, endpoint allow-lists, history,
  refresh, copied links, and allow-listed Inbox return targets;
- strict-live authenticated captures for each consumed BFF route;
- desktop and mobile hosted evidence showing identity, analytical scope,
  period, source state, and primary navigation action;
- any separately assigned BFF gap task, or an explicit statement that no new
  backend contract was required.

The parent owner `Antigravity` decides what to absorb. The parent reviewer
`Claude` evaluates the composed delivery. This sidecar reviewer verifies only
that this artifact stays support-only and accurately hands off the existing
boundary.

## Verification

Re-read the task brief, the reviewed base handoff, and its Codex2 and Claude
review records. Reconfirmed the named route families are registered in
`services/control-plane/bff/main.py`: trading pulse, Persona Fleet, persona and
capital-pool detail, Portfolio Book, performance attribution, quarterly
ranking/drilldown, and Human Inbox aggregate/detail. No canonical document,
BFF runtime/schema, registry, governance implementation, or frontend file was
changed. `current-work.md` and the complete `ai-activity-log.jsonl` were not
scanned.
