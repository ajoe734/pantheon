# MGMT-PERF-IA-006 BFF Handoff Follow-up 12

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-12` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only dependency checkpoint and composition guardrail |
| Mutates canonical or runtime | `false` |

This packet records the durable state after Follow-up 11. It does not define
routes or query fields, edit Pantheon BFF or `execute-plans`, approve the
parent, or authorize a mutation.

## 1. Dependency Checkpoint

There is no new BFF or frontend contract delta for the parent to absorb:

- `MGMT-PERF-IA-003` remains `blocked`, waiting for human merge of green
  `execute-plans` PR #261 and subsequent hosted evidence;
- `MGMT-PERF-IA-004` remains archived `done` and is the only completed Wave 1
  dependency;
- `MGMT-PERF-IA-005` remains `blocked`, waiting for human merge of green
  `execute-plans` PR #260 and subsequent hosted evidence; and
- parent `MGMT-PERF-IA-006` remains `todo` and depends on all three Wave 1
  deliveries.

Follow-up 9 remains the substantive query-gap, operator-journey, and evidence
handoff. Follow-ups 10 and 11 remain its no-delta checkpoints. Open frontend
PRs are not merged or deployed destination-contract truth, so this packet does
not infer their routes, accepted query keys, adapter behavior, or hosted
behavior.

## 2. Composition Guardrail

The parent may compose contextual links only against merged destination
revisions and must record the following evidence.

| Origin | Required composition proof |
|---|---|
| Cockpit | Stable source identity maps to a destination-accepted identity; requested period/runtime context is fulfilled by returned evidence. |
| Persona Fleet | Performance, holdings, ranking, evidence, and review links preserve only accepted identity and period keys. |
| Entity details | Compact summary is visibly distinct from formal attribution/ranking, with honest unavailable behavior when no stable link exists. |
| Human Inbox | Complete/cancel returns to an allow-listed canonical origin and keeps governance decision state separate from apply receipt. |
| Agora | Execution diagnostics remain in Trading Room; management attribution is linked, not duplicated, with strategy/period context preserved only where accepted. |

Display names, labels, ranks, actors, timestamps, and text matching are not
stable identity bridges. URL parameters prove a request, not destination
fulfillment. Empty, unmatched, incompatible, stale, degraded, or unavailable
responses must remain honest and must not be converted into fixture authority
or fabricated zeroes.

## 3. BFF Gap Disposition

No concrete BFF gap is established by the current durable evidence. If parent
implementation later proves one, create a separately owned BFF task that
names:

- the exact source response and destination contract;
- the missing stable identity/context link and blocked acceptance criterion;
- authorization, snapshot, pagination, and safe-return semantics;
- negative tests and the fail-closed unavailable behavior; and
- an owner and reviewer distinct from this support lane.

Until then, the valid row dispositions are `absorbed`, `honest unavailable`,
or `separate BFF task`. Freeze, promotion, rebalance, allocation, capital
access, broker, and runtime changes remain governed writes; successful
navigation is not an apply receipt.

## 4. Next Useful Wake-up

Another handoff follow-up is useful only after a dependency merges and deploys,
a concrete stable-ID mismatch is demonstrated, a reviewed contract changes, or
the reviewer requests a specific correction. If none occurs, the supervisor
should point the parent owner to Follow-up 9 and the latest checkpoint rather
than generating another substantively identical packet. This is task-scoped
handoff advice, not a supervisor-policy change.

## 5. Review And Composition

Reviewer `Antigravity` should verify the dependency posture, absence of
invented contract claims, fail-closed gap disposition, and isolation to this
support artifact. After approval, `Antigravity` as parent owner decides what
to absorb into `MGMT-PERF-IA-006`; parent reviewer `Claude` reviews the eventual
composed `execute-plans` delivery. Sidecar approval neither approves the parent
nor closes the blocked Wave 1 dependencies.

## 6. Verification

Re-read the task-scoped brief, parent execution packet, Follow-ups 10 and 11,
and queried durable task records with `AI_NAME=Codex ./scripts/ai-status.sh
show` for `003`, `004`, `005`, `006`, and this sidecar. Confirmed that no newer
contract delta is durable. No canonical document, Pantheon BFF runtime/schema,
registry, governance implementation, supervisor policy, or frontend file was
changed. `current-work.md` and the complete `ai-activity-log.jsonl` were not
scanned.
