# MGMT-OPS-003-GAP-002 BFF Handoff Follow-up 2

| Field | Value |
|---|---|
| Parent task | `MGMT-OPS-003-GAP-002` |
| Sidecar task | `MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2` |
| Owner / reviewer | `Codex2` / `Antigravity` |
| Helper kind | `bff_handoff_packet` |
| Date | `2026-07-11` |
| Delivery layer | support only |

This packet is a post-merge delta to
`MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF.md`. It incorporates the parent
review finding recorded in commit `c84f50799` without changing canonical
truth, BFF/runtime code, registries, governance, or frontend source. The parent
owner decides whether and how to absorb it.

## 1. Why A Follow-up Is Required

PR #3192 fixed FastAPI `Query`/`Header` defaults for direct Python calls to
Portfolio Book handlers. That is a valid contract-test repair, but it does not
reconcile a runtime identity, restore telemetry, quarantine an unresolved row,
or produce hosted before/after evidence.

The parent review therefore remains `REQUEST_CHANGES`. At the recorded hosted
baseline, the unclosed population is still 10 holdings with missing bindings
and 4 of 6 runtimes without telemetry coverage. These figures are inherited
from the gap record; this sidecar did not perform a fresh hosted probe.

## 2. BFF Query And Evidence Delta

The existing read family remains the observation surface:

```text
GET /bff/management/portfolio-book
GET /bff/management/portfolio-book/pools
GET /bff/management/portfolio-book/exposure
GET /bff/management/portfolio-book/holdings
GET /bff/management/portfolio-book/positions
```

No repair mutation is justified by the current contract. Before proposing a
new BFF route, the parent must first implement or identify the authoritative
service-owned reconciliation path. The resubmission must then answer these
queries with durable evidence:

| Question | Required evidence | Fail-closed result |
|---|---|---|
| Which baseline row was processed? | Stable holding, runtime, binding, persona, deployment-plan, artifact, and capital-scope identifiers. | Missing identities remain explicit; the row is not dropped or client-deduplicated. |
| What happened to it? | `repaired`, `quarantined`, or `unchanged`, with before/after issue codes, reason, evidence refs, and audit/run identity. | No lower counter may be treated as proof of repair. |
| Was the action replay-safe? | Same idempotency key replayed with the same disposition or a recorded no-op. | Duplicate bindings, incidents, or audit actions fail acceptance. |
| Is telemetry trustworthy? | Per-runtime source, freshness, observed time, and coverage disposition. | An uncovered active runtime remains degraded/unavailable with an incident. |
| Is capital identity authoritative? | Broker plus paper-ledger, canary-sleeve, or live-pool/sleeve identity traced from its write owner. | Unknown/unbound never defaults to paper or live. |
| May attribution be formal? | Required runtime, binding, capital, and telemetry joins are all trustworthy. | Any unresolved required join blocks formal attribution. |

These are handoff/evidence requirements, not a new schema. If existing models
cannot represent a required disposition, the parent must request a bounded
contract change through normal review rather than inventing a frontend field.

## 3. Operator Journey Delta

1. The operator starts from the recorded baseline and can identify every
   affected holding and uncovered runtime by stable identity.
2. A service-owned reconciliation run traces runtime, deployment plan,
   persona, artifact, broker, capital scope, and telemetry authority.
3. Agreeing authoritative identifiers are repaired. Missing or conflicting
   authority is quarantined with an auditable reason and remains visible.
4. The same reconciliation key is replayed to prove a no-op or identical
   disposition.
5. After an explicit dev BFF deployment, authenticated Portfolio Book captures
   show before/after counts and row-level dispositions.
6. The operator follows an affected row into Human Review and Performance
   Attribution with its available context preserved. Unresolved rows remain
   degraded/unavailable and cannot display formal attribution.
7. The reviewer independently samples raw runtime-binding and telemetry
   sources; aggregate counters alone do not close the task.

## 4. Frontend Handoff Delta

- Do not implement a repair control from this packet. A browser mutation waits
  for a separately reviewed governed action contract with authorization,
  validation, idempotency, audit, and receipt semantics.
- Keep baseline identities stable across before/after views so repaired,
  quarantined, and unchanged rows can be reconciled one for one.
- Keep quarantined and unresolved rows visible under filtering and pagination.
  A falling aggregate count is not a repaired-state signal.
- Render paper ledger, canary sleeve, live pool/sleeve, and unknown/unbound as
  distinct text. Never infer a missing broker or capital identifier.
- Preserve available holding, runtime, binding, persona, capital-scope,
  incident, source-status, and period context in governed drilldowns.
- Render formal attribution only from the BFF verdict. Partial, degraded,
  unavailable, stale, or unknown evidence must not be upgraded client-side.
- Treat absence of a post-merge deployment SHA and authenticated API capture as
  an evidence gap, not as permission to reuse the earlier hosted baseline as
  proof of the repair.

## 5. Parent Resubmission Checklist

- [ ] Reconciliation implementation traces every baseline missing-binding and
  telemetry-gap record to authoritative sources.
- [ ] Every record has a repaired, quarantined, or unchanged disposition; no
  record silently disappears.
- [ ] Normal, missing, stale, conflict, quarantined, repaired, and replay cases
  have focused tests.
- [ ] Replay evidence proves idempotency and an audit trail.
- [ ] Broker and capital-scope identity propagation is proven from the owning
  service rather than guessed by BFF or frontend code.
- [ ] Unresolved required joins fail closed for formal attribution.
- [ ] The Pantheon PR, checks, merge SHA, explicit BFF deploy run, and deployed
  SHA ancestry are recorded.
- [ ] Authenticated before/after API captures include runtime count, telemetry
  runtime count, degraded rows, missing bindings, broker identity, capital
  scope, and row-level dispositions.
- [ ] `REVIEWER_CHECKLIST.md` contains independent raw-source samples.

## 6. Reviewer Handoff

Antigravity should verify that this follow-up:

1. accurately reflects the parent `REQUEST_CHANGES` finding;
2. does not misrepresent PR #3192 as reconciliation closure;
3. preserves the support-only boundary and introduces no route or schema truth;
4. keeps unresolved/quarantined rows visible and attribution fail-closed; and
5. leaves implementation and absorption decisions with the parent owner.

## 7. Verification

```bash
git diff --check -- support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md
git show --stat c84f50799
git show --stat 92f400f247c0325e4b2d5cca19a5644ecf25e3b0
rg -n "portfolio-book|capital_scope|data_confidence" services/control-plane/bff/main.py services/control-plane/bff/test_bff_pm12_portfolio_book_contract.py
```

`current-work.md` and the full `ai-activity-log.jsonl` were not scanned; the
task-scoped brief, state, original sidecar, parent gap packet, and parent review
were sufficient for this follow-up.

## 8. Owner Closeout

Antigravity approved this support-only packet in
`MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-REVIEW.md`. Codex2
reconfirmed that the delivered scope remains limited to handoff and review
artifacts: it changes no canonical contract, BFF/runtime implementation,
registry/governance surface, or frontend source. The parent owner retains the
implementation and absorption decision.

Closeout verification repeated on 2026-07-11:

```bash
git diff --check -- \
  support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2.md \
  support/sidecars/MGMT-OPS-003-GAP-002/MGMT-OPS-003-GAP-002-SIDECAR-BFF-HANDOFF-FOLLOWUP-2-REVIEW.md
git diff --name-only origin/dev...HEAD
```
