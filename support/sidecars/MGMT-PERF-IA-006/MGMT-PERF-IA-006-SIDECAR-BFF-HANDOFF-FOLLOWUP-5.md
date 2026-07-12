# MGMT-PERF-IA-006 BFF Handoff Follow-up 5

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-5` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only absorption record |
| Mutates canonical or runtime | `false` |

This follow-up gives the parent owner one compact record for composing the
reviewed BFF/frontend handoff into the frontend delivery. It does not define a
route or field, edit Pantheon BFF or `execute-plans`, approve the parent, or
authorize a write.

## Parent Absorption Record

Before parent review, fill one row for every implemented entry point. A row is
complete only when the source response supplies the stable identifier used by
the destination and the evidence shows the returned source/snapshot state.

| Origin and action | Source read and returned stable ID | Destination and accepted ID/query | Preserved compatible context | Scope label and unavailable behavior | Test/capture reference |
|---|---|---|---|---|---|
| Cockpit card or alert | Parent records | Parent records | Entity, stage, period as supported | Formal Performance or Rankings; reasoned unavailable | Parent records |
| Persona Fleet or persona detail | Parent records | Parent records | Persona, runtime, period as supported | Compact summary versus formal analysis | Parent records |
| Strategy detail or Agora | Parent records | Parent records | Strategy and period as supported | Management attribution versus Agora execution performance | Parent records |
| Human Inbox decision and return | Parent records | Parent records | Allow-listed origin plus compatible entity/period context | Governance context, not performance truth | Parent records |
| Capital pool or other detail | Parent records | Parent records | Pool/entity and period as supported | Healthy empty distinct from missing/degraded/unlinked | Parent records |

Requested URL values are navigation intent. Returned identifiers, source
health, and period/snapshot metadata are fulfillment evidence. The frontend
adapter may preserve `persona`, `runtime`, `strategy`, `capital_pool`, `stage`,
`period`, and `as_of` when compatible, but each BFF adapter sends only fields
accepted by its selected endpoint.

## Per-Row Decision

Mark each row with exactly one disposition:

- **absorbed**: the source returns a stable identifier accepted by the
  destination, and refresh, copied URL, history, and applicable Inbox return
  preserve compatible context;
- **honest unavailable**: identity or compatible authoritative data is absent,
  and the UI exposes the reason without a fixture, heuristic join, fallback
  authority, or numeric zero;
- **separate BFF gap task**: a required journey lacks a stable source ID
  accepted by its destination and honest unavailable would violate explicit
  parent acceptance.

A separate BFF gap task must name the exact source response, destination read,
missing stable link or return-context capability, blocked journey,
authorization boundary, snapshot/pagination behavior, negative tests, owner,
and reviewer. This sidecar does not select a speculative route, query
parameter, response field, or universal context token.

Browser-side joins by display name, label, rank, actor, timestamp, or matching
text remain rejected. Human Inbox return targets must be allow-listed. Entity
summary, formal attribution, formal ranking, and Agora execution performance
remain distinct analytical scopes.

## Parent Review Gate

The parent delivery record should include:

1. the completed absorption table and an explicit statement of whether any BFF
   gap task was opened;
2. the merged `execute-plans` PR, merge SHA, and hosted deployment ancestry;
3. focused route-adapter and journey tests covering parse/serialize,
   endpoint query allow-lists, refresh, copied links, browser history,
   pagination reset, and allow-listed Inbox return;
4. authenticated strict-live captures for every consumed BFF read, including
   section-local health and snapshot evidence;
5. hosted desktop and mobile proof that identity, period, analytical scope,
   source state, primary action, healthy empty, and reasoned unavailable states
   remain legible.

Parent owner `Antigravity` decides what to absorb and owns any implementation
or gap-task routing. Parent reviewer `Claude` evaluates the composed frontend
delivery. Sidecar reviewer `Antigravity` verifies only that this artifact stays
support-only and preserves the reviewed handoff boundary.

## Verification

Re-read the task brief, parent execution packet, base BFF/frontend handoff, and
follow-ups 2–4. Confirmed this artifact only supplies a parent absorption
record and preserves their stable-identity, query allow-list,
scope-separation, honest-unavailable, and stop-and-split rules. No canonical
document, Pantheon BFF runtime/schema, registry, governance implementation, or
frontend file was changed. `current-work.md` and the complete
`ai-activity-log.jsonl` were not scanned.
