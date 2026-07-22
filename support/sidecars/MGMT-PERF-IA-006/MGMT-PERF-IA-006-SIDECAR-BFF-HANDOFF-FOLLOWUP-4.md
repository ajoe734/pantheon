# MGMT-PERF-IA-006 BFF Handoff Follow-up 4

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-4` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only implementation handoff |
| Mutates canonical or runtime | `false` |

This follow-up converts the reviewed handoff and readiness gate into a compact
implementation and escalation checklist for the parent owner. It does not
define a new route or field, edit Pantheon BFF or `execute-plans`, approve the
parent task, or authorize a mutation.

## Parent Implementation Handoff

For each legitimate origin—Cockpit, Persona Fleet, entity detail, Human Inbox,
or Agora—the parent should record the source route, returned stable identifier,
destination center, compatible period context, and destination read. A typed
frontend route adapter may preserve navigation values such as `persona`,
`runtime`, `strategy`, `capital_pool`, `stage`, `period`, and `as_of`, but each
BFF adapter must send only parameters accepted by its endpoint.

| Implementation checkpoint | Pass condition | Failure disposition |
|---|---|---|
| Identity | The source returns a stable identifier accepted by the destination. | Render unavailable unless the stop-and-split gate below requires a BFF task. |
| Context | Refresh, copied URL, history, and return preserve only compatible identity and period context. | Fix the typed route adapter; never infer a join from display text. |
| Analytical scope | Entity summary, formal attribution, formal ranking, and Agora execution performance remain visibly distinct. | Relabel or redirect to the canonical center; do not duplicate an analysis surface. |
| Source truth | Each composed read exposes its own health, snapshot, loading, empty, and error state. | Treat the affected section as degraded or unavailable, not page-wide success. |
| Human Inbox return | The return target is allow-listed and restores compatible originating context on completion and cancellation. | Reject arbitrary return URLs and do not invent a universal return token. |
| Empty detail | Healthy authoritative empty is distinguishable from missing, stale, fallback, incompatible, or unlinked data. | Render an honest reasoned unavailable/degraded state; never use fixture authority or zero. |

## BFF Gap Escalation Packet

Open a separately owned Pantheon BFF contract task only when a required parent
journey lacks a stable source identifier accepted by its destination **and** an
honest unavailable state would violate explicit parent acceptance. The new task
must contain all of the following evidence:

1. exact source route and the relevant response identifier or missing link;
2. exact destination route and identifier/query contract it currently accepts;
3. blocked origin-to-destination journey and why unavailable is insufficient;
4. required authorization, schema, snapshot/pagination, and negative tests;
5. an explicit owner and reviewer separate from this support sidecar.

Do not choose a speculative route name, query parameter, response field, or
universal context token in the parent frontend task. Browser-side joins by
display name, label, rank, actor, timestamp, or matching text remain rejected.

## Parent Review Evidence

Before the parent enters review, its delivery record should provide:

- the merged `execute-plans` PR, merge SHA, and hosted deployment ancestry;
- a journey matrix naming source identifier, destination read, preserved
  context, and resulting scope label for every legitimate entry point;
- focused URL parse/serialize, endpoint allow-list, refresh, copied-link,
  browser-history, pagination-reset, and Inbox-return tests;
- authenticated strict-live captures for every consumed BFF route, including
  returned health and snapshot evidence;
- hosted desktop and mobile proof for healthy, empty, unavailable, degraded,
  stale/fallback, and incompatible identity states where applicable;
- either the separately assigned BFF gap task or an explicit statement that
  existing stable identifiers were sufficient.

Parent owner `Antigravity` decides whether to absorb this checklist together
with the base packet and follow-ups 2–3. Parent reviewer `Claude` evaluates the
composed frontend delivery. Sidecar review covers only the accuracy and
support-only boundary of this packet.

## Verification

Re-read the task brief, parent execution packet, reviewed base handoff, and
follow-ups 2–3. Confirmed this artifact preserves their query-gap,
operator-journey, scope-separation, honest-unavailable, and separately owned
BFF escalation boundaries without introducing a new endpoint or field claim.
No canonical document, Pantheon BFF runtime/schema, registry, governance
implementation, or frontend file was changed. `current-work.md` and the full
`ai-activity-log.jsonl` were not scanned.
