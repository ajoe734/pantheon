# PPL-ALLOC-009 acceptance addendum — 2026-07-22

This addendum replaces the stale Human/Ops credential blocker in the 2026-07-15
recheck. It does not replace the canonical `PPL-ALLOC-009` task ID.

## Cleared prerequisite

The five dedicated dev-login clients and control passphrase material were
provisioned, strict BFF deployment succeeded, and hosted write/read-only restore
proof completed on 2026-07-21. B2 is cleared. Do not ask Human/Ops to provision
the same values again and do not record their raw values.

## Remaining acceptance

### B1 — one correlated governed allocation chain

Using one safe, canonical Persona identity, archive linked IDs and authoritative
responses for:

```text
Persona create/paper_running
  -> canonical quarterly paper ranking
  -> promotion review and human decision
  -> real ranking eligibility
  -> target weights
  -> rebalance proposal
  -> distinct approval and apply
  -> authoritative Capital readback
```

Every transition must be governance-produced. A stub fixture, direct store edit,
or uncorrelated rows do not pass.

#### B1 amendment — paper-only governed simulation (Human/Ops, 2026-07-24)

Human/Ops (bjoe734@gmail.com) has decided, on 2026-07-24, to accept B1 as a
**paper-only governed simulation**. The `real ranking eligibility` transition
and everything downstream of it (`target weights -> rebalance proposal ->
distinct approval and apply -> authoritative Capital readback`) are satisfied
using governed paper / dev-proof authority only, with real and live capital
kept disabled throughout. No authorized dev canary packet, broker/capital
evidence, or four-authority MFA ceremony is required for this task.

This amendment relaxes only the capital authority of the promotion step. It does
NOT relax correlation or provenance: every transition must still be
governance-produced with linked IDs and authoritative responses, the promotion
step must run through the real governance promotion-review path (a human
decision is still recorded), and stub fixtures, direct store edits, or
uncorrelated rows still do not pass. The `authoritative Capital readback` is the
Capital service's read-only view of the applied paper allocation; it must be a
genuine governed readback, not a fabricated row.

Rationale: consistent with the Completion section's standing rule to keep
real/live capital disabled and use governed dev proof authority only. A true
paper->canary capital promotion remains a separate, later governed decision and
is explicitly out of scope for PPL-ALLOC-009.

### B3 — authenticated desktop and mobile proof of B1

Run the same B1 chain through the accepted execute-plans/BFF pair on desktop
and 393px mobile. Record operator identity class, route/request/response IDs,
console/network results, accessibility result, and exact Persona/ranking/
proposal/receipt identities. The 2026-07-21 PINT proof is useful prerequisite
evidence but does not replace this exact allocation chain.

### B5 — IA reviewer decision

The reviewer must explicitly accept that the canonical Rankings, Governance,
and Performance centers supersede the original primary-workbench contract, or
reopen a bounded UI task. Silence is not acceptance.

## Additional dependencies

- `OPS-DISPATCH-LEASE-SYNC-001`
- `PAN-LIFECYCLE-RECOVERY-001`

## Human/Ops decision — 2026-07-24

- Decision: **B1 accepted as paper-only governed simulation** (see the B1
  amendment above); real/live capital stays disabled.
- Human/Ops identity: bjoe734@gmail.com (Human/Ops)
- UTC timestamp: 2026-07-24T00:00:00Z (interactive session decision)
- Scope authorized: governed paper / dev-proof authority for the full B1 chain
  including the promotion and Capital-readback steps.
- NOT authorized: any real/live capital, broker capital movement, dev canary
  packet, or production/default rollout. Those remain a separate later decision.
- Still required to complete the task (owner + reviewer work, not Human/Ops):
  B1 executed as a correlated governed paper-only chain, B3 desktop/393px-mobile
  proof of that exact chain, and the B5 IA reviewer decision.

This decision resolves the Human/Ops fork recorded in the task `next` note; it
does not by itself satisfy B1/B3/B5, which still require governed execution and
reviewer acceptance.

## Completion

Archive PRs, merge SHAs, accepted deployment manifest, B1/B3 evidence index,
B5 decision, rollback/safe-restore result, and residual risks. Keep real/live
capital disabled; the chain uses governed dev proof authority only.
