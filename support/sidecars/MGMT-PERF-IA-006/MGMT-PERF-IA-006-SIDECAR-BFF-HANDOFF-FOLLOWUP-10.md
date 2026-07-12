# MGMT-PERF-IA-006 BFF Handoff Follow-up 10

| Field | Value |
|---|---|
| Parent task | `MGMT-PERF-IA-006` |
| Sidecar task | `MGMT-PERF-IA-006-SIDECAR-BFF-HANDOFF-FOLLOWUP-10` |
| Owner / reviewer | `Codex` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Scope | support-only handoff delta and redispatch gate |
| Mutates canonical or runtime | `false` |

This packet records the delta since the approved Follow-up 9 packet. It does
not repeat that packet's route inventory, define a query field, edit Pantheon
BFF or `execute-plans`, approve the parent, or authorize a mutation.

## 1. Delta Verdict

There is no new absorbable BFF or frontend contract delta at preparation time.
Durable task state still reports:

- `MGMT-PERF-IA-004` is archived `done`;
- `MGMT-PERF-IA-003` is `blocked`, waiting for human merge of green
  `execute-plans` PR #261 and subsequent hosted evidence;
- `MGMT-PERF-IA-005` is `blocked`, waiting for human merge of green
  `execute-plans` PR #260 and subsequent hosted evidence; and
- parent `MGMT-PERF-IA-006` remains `todo` and depends on all three Wave 1
  deliveries.

Accordingly, the approved Follow-up 9 dependency gate, query-gap decision
table, operator journey, and frontend evidence bundle remain the current
handoff. An open frontend PR is not destination contract truth, so this packet
does not copy provisional routes, tabs, adapters, or query keys from either
blocked branch.

## 2. Parent Intake Checkpoint

When the parent starts, it should first update the following rows against the
actual merged and deployed revisions. Until then, each row remains pending
rather than inferred.

| Intake item | Required evidence | Current disposition |
|---|---|---|
| Performance Center destination | merged PR/SHA, deployed ancestry, accepted identity/period keys, returned source/snapshot evidence | pending `003` merge and hosted proof |
| Rankings Center destination | archived delivery reference and current deployed ancestry | absorb from completed `004`; reverify at composition time |
| Governance destination and return | merged PR/SHA, Human Inbox destination/return behavior, decision versus apply-receipt semantics | pending `005` merge and hosted proof |
| Origin links | one row for Cockpit, Fleet, entity details, Human Inbox, and Agora naming stable source ID and destination-accepted ID | parent implementation evidence |
| Residual query gaps | exact source response, destination contract, missing stable link, blocked acceptance, owner/reviewer, and negative tests | create separately only when evidence proves a gap |

The allowed dispositions remain `absorbed`, `honest unavailable`, or
`separate BFF task`. Display-name, label, rank, actor, timestamp, or text
matching cannot turn a pending row into an absorbed one.

## 3. Operator Journey Handoff

The parent evidence must still prove, on merged hosted revisions, that an
operator can:

1. enter from Cockpit, Persona Fleet, an entity detail, Human Inbox, or Agora;
2. preserve only destination-supported identity, runtime/stage, period, and
   snapshot context through refresh, copied URL, and browser history;
3. see destination-returned source and snapshot evidence rather than a claim
   based only on requested URL values;
4. distinguish compact entity summary, formal attribution, formal ranking,
   governance decision state, apply receipt, and Agora execution diagnostics;
5. receive an honest empty, unavailable, degraded, stale/fallback, unmatched,
   or incompatible-identity state without fixtures or fabricated zeroes; and
6. complete or cancel Human Inbox review and return through an allow-listed
   canonical origin without guessed identity or an arbitrary return URL.

Freeze, promotion, rebalance, allocation, access, broker, and runtime changes
remain governed writes. Navigation success is not an apply receipt.

## 4. Redispatch Gate

Another `bff_handoff_packet` follow-up for this parent is useful only after at
least one of these facts changes:

- `003` or `005` merges and a concrete destination contract or hosted result
  becomes available for comparison;
- parent implementation identifies a source/destination stable-ID mismatch;
- a reviewed route, adapter, authorization, snapshot, pagination, or safe
  return behavior changes; or
- reviewer requests a specific correction to an existing packet.

If none changes, redispatch should point the parent owner to Follow-up 9 and
this delta record instead of generating another substantively identical
packet. This is a support-lane recommendation, not a supervisor policy or
canonical routing change.

## 5. Composition And Review

Reviewer `Antigravity` should verify the durable dependency posture, absence
of invented contract claims, fail-closed gap disposition, and task isolation.
After sidecar approval, parent owner `Antigravity` decides whether to absorb
the checkpoint; parent reviewer `Claude` reviews the eventual composed
`execute-plans` delivery. Sidecar approval does not approve the parent or
close either blocked dependency.

## 6. Verification

Re-read the task-scoped brief, parent execution packet, base handoff,
Follow-ups 8 and 9, and the Follow-up 9 reviewer approval. Queried durable
task state with `AI_NAME=Codex ./scripts/ai-status.sh show` for `003`, `004`,
`005`, `006`, and this sidecar. Confirmed the dependency posture above and
that no newer contract delta is durable. No canonical document, Pantheon BFF
runtime/schema, registry, governance implementation, supervisor policy, or
frontend file was changed. `current-work.md` and the complete
`ai-activity-log.jsonl` were not scanned.
