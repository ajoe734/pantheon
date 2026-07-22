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

## Completion

Archive PRs, merge SHAs, accepted deployment manifest, B1/B3 evidence index,
B5 decision, rollback/safe-restore result, and residual risks. Keep real/live
capital disabled; the chain uses governed dev proof authority only.
