# Workbench Delivery Backlog

Last updated: 2026-04-24
Status: canonical module-level backlog for remaining product and workbench delivery
Tier: L2 Planning & Execution
Scope: remaining workbench modules, truthful delivery gates, and the order required to close productization gaps
Conflict rule: this file is the canonical remaining workbench backlog, but it does not override L1 architecture or packet-level contracts

## 1. Working Rule

This file tracks the remaining productization backlog after the Wave 1 operator and persona baselines.

A module stays on this backlog until one of these is true:

- its BFF route and packet contract are live and the current frontend loop is `loop-complete`
- its route-live frontend activation packet publication lane is archive-done and no Pantheon-owned implementation gap remains
- it is explicitly deferred by a newer canonical planning decision

Closure-record sync by itself does not keep a module on the remaining backlog.
If a surface is already contract-live, frontend-reviewed, and only needs canonical
closeout bookkeeping, track that work in `ai-status.json` closeout tasks instead
of keeping the module listed here as unfinished product backlog.
The same rule applies when Pantheon has already published the module-local
frontend activation packet and the remaining work is front-owned delivery rather
than a Pantheon route or contract gap.

Use `DELIVERY_CLOSURE_AND_LOOP_STATES.md` for status semantics.

## 2. Already Landed Baselines

These surfaces are not part of the remaining backlog:

- `F-042` Promotion Review
- `PKT-001` Governance Review Queue
- `PKT-002` Incident Home, Incident Detail, Incident Action Drawer
- `PKT-003` Post-Incident Review, Evolution Center, Lineage View baseline
- `PKT-004` Persona drilldowns and management Wave 1
- `PKT-005` Degradation Banner and SSE substrate
- `PKT-006` Approval Queue
- `PKT-007` Deployment Diff
- `PKT-008` Rollback Review
- `PKT-009` Governance Audit Rail
- `PKT-010` Runtime State Board
- `PKT-011` Health Status Board
- `PKT-012` Alerts Rail
- `PKT-013` Operator Home Dashboard
- `PKT-014` Paper / Live Drift

Outside this workbench backlog:

- `Settings` is now Pantheon BFF-backed via `/api/v1/settings*` and should be treated as
  config-domain product refinement, not as a remaining workbench module gap

## 3. Remaining Backlog

The reviewed Operator Console Wave 2 and governance follow-on packet loops are no
longer treated as remaining module backlog in this file. Their residual work is
canonical closeout bookkeeping only and is tracked separately in
`ai-status.json` closeout tasks instead of this remaining module backlog.

### Evolution Workbench

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `EW-04 Inspiration Graph` | route-live in Pantheon, and the current front default branch now mounts the live inspiration graph surface | no new Pantheon BFF implementation gap remains for this slice; the blocked-shell realignment lane is no longer the truthful residual after the front default branch shipped the live route | reopen only if a later GitHub-visible review packet finds a contract-specific regression, replay-clean publication issue, or front follow-up blocker |
| `EW-05 Mutation Review` | route-live in Pantheon, and the current front default branch now mounts the live mutation review surface | no new Pantheon BFF implementation gap remains for this slice; the blocked-shell realignment lane is no longer the truthful residual after the front default branch shipped the live route and command-authority flow | reopen only if a later GitHub-visible review packet finds a contract-specific regression, replay-clean publication issue, or front follow-up blocker |

### Research Workbench

Route-live activation note: `APP-003-ROUTE-LIVE-FRONTEND-002` is archive-done for
`RW-02`, `RW-04`, and `RW-05`. Those modules no longer remain on this Pantheon
backlog purely because front-end activation proceeds downstream from already
published module-local handoff packets.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `RW-01 Research Ticket` | loop-complete — route family live, current frontend loop closed, and hardening follow-up closed for the current wave | ticket list/detail/create/patch truth, degraded behavior, and the archived hardening follow-up are aligned for the current wave; no open Pantheon implementation gap remains for this slice | reopen only if a later ticket-contract revision or runtime regression triggers a new delivery loop |
| `RW-03 Analyze` | loop-complete — route family live, current frontend loop closed, and hardening follow-up closed for the current wave | analysis list/detail truth, backend-owned metric grouping, degraded behavior, and the archived hardening follow-up are aligned for the current wave; no open Pantheon implementation gap remains for this slice | reopen only if a later analysis-contract revision or runtime regression triggers a new delivery loop |

### Knowledge Workbench

Overview packet note: `PKT-knowledge-workbench` now publishes a truthful overview route and handoff bundle. The module backlog below remains open only for modules whose current frontend activation loop has not yet closed.

Route-live activation note: `APP-003-ROUTE-LIVE-FRONTEND-001` and
`APP-003-ROUTE-LIVE-FRONTEND-002` are archive-done for `KW-02`, `KW-03`,
`KW-04`, and `KW-05`. Those modules no longer remain on this Pantheon backlog
purely because front-end activation proceeds downstream from already published
module-local handoff packets.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `KW-01 Institutional Memory` | loop-complete — list/detail routes live, current frontend loop closed, and hardening follow-up closed for the current wave | institutional-memory browse/detail truth, degraded behavior, and the archived hardening follow-up are aligned for the current wave; no open Pantheon implementation gap remains for this slice | reopen only if a later institutional-memory contract revision or runtime regression triggers a new delivery loop |

### Consultation Workbench

Overview packet note: `PKT-consultation-workbench` now publishes a truthful overview route and handoff bundle. The module backlog below must stay honest about cross-repo reality: Pantheon routes are live, and the current front default branch now mounts live request / committee / memo surfaces. Remaining work, where present, is closeout / republish / truth-sync follow-up rather than blocked-shell realignment.

Route-live activation note: `APP-003-ROUTE-LIVE-FRONTEND-001` is archive-done
for `CW-02`. That transcript module is closed for the current wave. `CW-01`,
`CW-03`, and `CW-04` are also now live on the current front default branch, so
they should no longer be read as blocked-shell realignment residuals.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `CW-01 Consult Request` | route-live in Pantheon, and the current front default branch now mounts the live request list/detail flow | no new Pantheon BFF implementation gap remains for this slice; any remaining work should be interpreted as closeout / republish / truth-sync follow-up, not blocked-shell realignment | reopen only if a later GitHub-visible review packet finds a contract-specific regression, replay-clean publication issue, or front follow-up blocker |
| `CW-03 Committee Board` | route-live in Pantheon, and the current front default branch now mounts the live committee board/detail flow | no new Pantheon BFF implementation gap remains for this slice; any remaining work should be interpreted as closeout / republish / truth-sync follow-up, not blocked-shell realignment | reopen only if a later GitHub-visible review packet finds a contract-specific regression, replay-clean publication issue, or front follow-up blocker |
| `CW-04 Red-team Memo` | route-live in Pantheon, and the current front default branch now mounts the live memo list/detail flow | memo lifecycle, session-to-memo mapping, evidence links, and governance-review initiation gating are live in Pantheon; any remaining work should be interpreted as closeout / republish / truth-sync follow-up, not blocked-shell realignment | reopen only if a later GitHub-visible review packet finds a contract-specific regression, replay-clean publication issue, or front follow-up blocker |

### Trainer Workbench

Route-live activation note: `APP-003-ROUTE-LIVE-FRONTEND-002` is archive-done
for `TW-01`, `TW-02`, and `TW-04`. Those modules no longer remain on this
Pantheon backlog purely because front-end activation proceeds downstream from
already published module-local handoff packets.

| Module | Current state | Pantheon-owned gap | Next truthful gate |
|---|---|---|---|
| `TW-03 Before/After Compare` | loop-complete — preview routes live and current frontend loop closed | preview read/refresh routes, warning hierarchy, `preview_unavailable` degraded branch, polling semantics, handoff bundle, and accepted frontend feedback are aligned for the current wave; no open Pantheon implementation gap remains for this slice | reopen only if a later preview-contract revision or runtime regression triggers a new delivery loop |

## 4. Cross-Family Order

Use this order unless a narrower canonical doc says otherwise:

1. finish canonical closeout bookkeeping tasks in `ai-status.json` when a reviewed loop still needs a record-layer closure sync
2. if a later Evolution reopen is required, finish `EW-04` before `EW-05`
3. for Research, Knowledge, Consultation, and Trainer, land identity and lifecycle modules first, then read aggregates, then mutation or replay surfaces

## 5. Relationship To Other Canonical Files

- `ROADMAP.md` sequences the phases.
- `DEVELOPMENT_WORKBREAKDOWN.md` tracks canonical task IDs.
- `DELIVERY_CLOSURE_AND_LOOP_STATES.md` defines when a module is actually closed.
- packet-family files under `docs/pantheon-handoffs/*/PACKET_FAMILY.md` hold the per-surface field-level truth once a module is opened.
