# MGMT-PERF-IA-006 BFF Handoff Follow-up 6

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-6` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only parent review submission gate |
| Mutates canonical or runtime | `false` |

This follow-up gives the parent owner a fail-closed submission gate for the
absorption record in follow-up 5. It does not define a route or field, edit
Pantheon BFF or `execute-plans`, approve the parent, or authorize a mutation.

## Review Submission Gate

The parent must not enter review until every implemented journey has one
recorded disposition and its evidence is internally consistent.

| Gate | Required parent record | Fail-closed outcome |
|---|---|---|
| Stable identity | Exact source read and returned identifier; exact destination read and accepted identifier/query | Mark the journey honestly unavailable, or open the separate BFF gap task defined below. |
| Compatible context | Typed route-adapter proof for refresh, copied URL, history, and applicable Inbox return | Fix the frontend adapter; do not infer identity or restore arbitrary context. |
| Fulfillment evidence | Returned source health and period/snapshot metadata for every consumed read | Render the affected section degraded or unavailable; requested URL values are not proof of fulfillment. |
| Analytical scope | Visible distinction among compact entity summary, formal attribution, formal ranking, and Agora execution performance | Relabel or route to the correct center before review. |
| Empty and failure states | Proof that healthy empty differs from missing, stale, fallback, degraded, incompatible, and unlinked data | Never substitute a fixture, heuristic join, fallback authority, or numeric zero. |
| Delivery evidence | Merged `execute-plans` PR/SHA, deployed ancestry, focused tests, strict-live captures, and hosted desktop/mobile proof | Parent delivery is not ready for review. |

## Allowed Dispositions

For each origin in the follow-up 5 absorption table, record exactly one:

1. **absorbed** — a source-returned stable identifier is accepted by the
   destination and the tested navigation preserves only compatible context;
2. **honest unavailable** — authoritative identity or compatible data is
   absent and the UI exposes the reason without inventing data truth; or
3. **separate BFF gap task** — the journey is required by explicit parent
   acceptance, the source/destination identity chain is missing, and honest
   unavailable is insufficient.

A separate BFF gap task must name the exact source response, destination read,
missing stable link or return-context capability, blocked journey,
authorization boundary, snapshot/pagination behavior, focused negative tests,
owner, and reviewer. It must be owned independently of this support sidecar.
This packet deliberately does not select a speculative route name, query
parameter, response field, or universal context token.

## Parent Submission Statement

The parent delivery record should include a short statement in this form:

> All implemented entry points are recorded as absorbed, honest unavailable,
> or separately tasked. Existing source-returned identifiers were [sufficient
> / insufficient] for the delivered journeys. [No new BFF contract was
> required / BFF gap task IDs: ...]. Requested URL context was not treated as
> fulfillment evidence, and no browser-side heuristic join was introduced.

It should then link the completed absorption table, frontend PR and deployment
ancestry, route-adapter tests, strict-live BFF captures, and hosted desktop and
mobile evidence. Human Inbox return targets remain allow-listed, and any
freeze, promote, rebalance, allocation, or other write remains in governed
Human Review/apply-receipt flows.

Parent owner `Antigravity` decides what to absorb and owns implementation or
gap-task routing. Parent reviewer `Claude` evaluates the composed delivery.
Sidecar reviewer `Antigravity` verifies only that this artifact remains
support-only and accurately preserves the reviewed handoff boundary.

## Verification

Re-read the task brief, base BFF/frontend handoff, and follow-ups 2–5.
Confirmed this submission gate preserves their stable-identity, endpoint query
allow-list, scope-separation, honest-unavailable, and stop-and-split rules
without introducing a route or field claim. No canonical document, Pantheon
BFF runtime/schema, registry, governance implementation, or frontend file was
changed. `current-work.md` and the complete `ai-activity-log.jsonl` were not
scanned.
