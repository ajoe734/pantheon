# MGMT-PERF-IA-006 BFF And Frontend Handoff Follow-up 7

| Field | Value |
|---|---|
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` |
| Parent task | `MGMT-PERF-IA-006` — Contextual integration |
| Helper kind | `bff_handoff_packet` |
| Parent owner / reviewer | `Antigravity` / `Claude` |
| Sidecar owner / reviewer | `Codex` / `Antigravity` |
| Date | `2026-07-12` |
| Status | Ready for sidecar review; parent absorption pending |
| Mutates canonical truth | `false` |

This is a support-only packet. It does not change L1 truth, BFF runtime or
schemas, route registries, governance behavior, or `execute-plans` source. It
does not approve, implement, or close the parent task.

## 1. Dependency And Delivery Posture

The parent is `todo` and depends on `MGMT-PERF-IA-003`, `004`, and `005`.
At inspection time, `004` records merged frontend delivery, while `003` and
`005` have not both completed their required merge-and-hosted-evidence gates.
Contextual integration may be prepared against the reviewed shapes, but it
must not claim completion until all three dependencies are merged and the
deployed revision is proven.

The parent owns composition in `ajoe734/execute-plans`. This sidecar does not
copy frontend source into Pantheon and does not turn planned page shapes into
BFF contract truth.

## 2. BFF Query Gap Ledger

No new BFF endpoint is established by this packet. The parent should first use
the read models already consumed by the merged centers and treat the following
as integration checks or explicit gaps.

| Context / gap | Required handoff behavior | Fail-closed disposition |
|---|---|---|
| Entity identity | Preserve only identifiers supported by the destination read model, such as persona, runtime, strategy, capital-pool, sleeve, broker, and deployment-stage identity. | If the source identity cannot be mapped unambiguously, render an unavailable/missing-binding state and retain a return link; do not join by label or display name. |
| Investigation time | Preserve the applicable period, as-of time, ranking cadence, and snapshot reference across deep links and tab changes. | Do not silently reset to “now” or substitute a different ranking snapshot. |
| Source state | Carry the destination's supported confidence, freshness, coverage, missing-binding, and observed-time semantics rather than inferring authority in the link layer. | Empty, partial, fallback, stale, degraded, unavailable, and transport failure remain distinct. |
| Cockpit / Fleet summaries | Summary cards may link to formal Performance or Rankings views but are not formal attribution or ranking evidence themselves. | Label fallback summaries and never promote summary zeroes or aggregates into formal results. |
| Ranking to governance | Preserve an immutable snapshot/evidence reference when navigating from Rankings to a recommendation or Human Review. | A ranking row cannot directly apply capital, access, promotion, freeze, rebalance, broker, or runtime changes. |
| Human Inbox return | Preserve the originating center, tab, entity, period, and decision/recommendation identity where those fields are supported. | If the origin cannot be reconstructed, return to a safe canonical center with an explicit lost-context notice rather than a guessed entity. |
| Capital Pool / Rebalance / Ranking Policy detail | Use a real detail contract only when the merged BFF/read model provides one. | An absent or empty contract renders honest unavailable/empty state; fixtures and synthetic authority are forbidden. |
| Agora boundary | Strategy and period may be linked into Management Performance, while Agora remains execution diagnostics in Trading Room. | Do not duplicate Agora execution metrics into the management hierarchy or relabel them as formal management attribution. |

Any missing field or join discovered during implementation belongs in a
bounded parent/backend follow-up. The frontend must not invent query keys,
wire fields, response values, or client-side joins to close it.

## 3. Operator Journeys

### A. Cockpit or Fleet to formal analysis

1. The operator starts from a Cockpit alert/card or Persona Fleet row.
2. The link opens the appropriate canonical Performance or Rankings tab with
   supported entity, runtime/stage, period, and source context intact.
3. The destination distinguishes formal evidence from summary fallback and
   shows missing bindings or degraded sources honestly.
4. Back navigation restores the originating list/filter state.

### B. Entity detail to canonical center

1. Persona or Strategy Detail shows a compact contextual summary.
2. A clearly labelled link opens formal Performance attribution/exposure or
   the applicable Rankings snapshot.
3. The detail summary is not visually or semantically presented as the formal
   analysis owner.
4. Capital Pool, Rebalance, or Ranking Policy detail with no live contract
   renders unavailable instead of fixture-backed detail.

### C. Ranking evidence through Human Review

1. The operator opens a reproducible ranking snapshot and follows its
   recommendation/review link.
2. Governance Decisions or Human Inbox receives the supported snapshot,
   recommendation, entity, and period context.
3. Recommendation, review decision, accepted/applying state, and completed
   apply receipt remain separate.
4. Returning from Human Review restores the originating ranking or governance
   context without directly executing a mutation.

### D. Agora execution diagnostics

1. Agora Strategy Performance remains in Trading Room and is labelled as
   execution-focused evidence.
2. A context link may open Management Performance with supported strategy and
   period filters.
3. The management page identifies missing formal attribution or bindings
   rather than substituting Agora metrics.
4. Return navigation restores the Agora investigation context.

## 4. Frontend Absorption Notes

The `execute-plans` parent owner should:

1. derive contextual links from the shared canonical route/menu manifest;
2. use one typed context serializer/parser for supported identity, period,
   snapshot, origin, and tab fields instead of page-local query assembly;
3. keep shared filters stable through tab changes, refresh, deep links, and
   browser back/forward;
4. distinguish compact summaries from formal Performance/Rankings evidence in
   headings, source badges, and unavailable states;
5. route recommendation/review actions to governed surfaces and require apply
   receipts before showing completed mutation state;
6. keep Agora out of the management navigation hierarchy while providing a
   labelled cross-domain link;
7. log unmapped context as a bounded gap rather than silently discarding or
   guessing it; and
8. avoid adding BFF calls merely to support navigation when the destination
   center already owns the required read.

## 5. Evidence Required Before Parent Completion

- `MGMT-PERF-IA-003`, `004`, and `005` have merged delivery records and the
  contextual work composes with those exact revisions.
- Unit tests cover context serialization/parsing, unsupported identifiers,
  tab persistence, and safe return behavior.
- Route tests cover Cockpit, Fleet, Persona/Strategy detail, Human Inbox, and
  Agora entry points with persona/runtime/strategy/pool/stage/period/snapshot
  combinations applicable to each source.
- Regression tests distinguish formal, partial, fallback, stale,
  healthy-empty, degraded, unavailable, and transport-failure states.
- Capital Pool, Rebalance, and Ranking Policy empty contracts render honest
  unavailable states with no fixtures or fabricated zeroes.
- Desktop and mobile keyboard/focus, back/forward, and responsive behavior
  preserve the same information hierarchy.
- Hosted smoke runs against the deployed `execute-plans` revision in strict
  live BFF mode and records frontend PR, merge SHA, deployed SHA, evidence
  links, and residual gaps.

Until these gates pass, this packet is preparation evidence only.

## 6. Reviewer And Parent Handoff

Reviewer `Antigravity` should verify that:

- dependency claims match the durable task records;
- no endpoint, schema field, or client-side join is invented;
- summaries, formal evidence, governance decisions, and apply receipts remain
  distinct;
- Agora remains execution-focused and separate; and
- only task-scoped support artifacts are committed.

After sidecar approval, parent owner `Antigravity` decides whether to absorb
these notes into `MGMT-PERF-IA-006`. Sidecar approval is not parent approval or
delivery evidence.

## 7. Preparation Evidence

- Confirmed task branch
  `task/MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-7` and Pantheon origin.
- Read the task-scoped brief, collaboration guide, worker-anchor and closeout
  rules, parent record, dependency records, and execution packets.
- Inspected current Pantheon BFF/service route sources only to avoid asserting
  an unverified dedicated contextual endpoint.
- Did not scan `current-work.md` or the complete `ai-activity-log.jsonl`.
- Changed no canonical, runtime, schema, route-registry, governance, or
  frontend implementation file.
