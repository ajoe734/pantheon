# Review: AG-FE-ID-001-SIDECAR-BFF-HANDOFF-FOLLOWUP-36

| Field | Value |
|---|---|
| Reviewer | Claude |
| Reviewed at | 2026-06-22 |
| Packet commit | `3f0ce8a9421213b35011da8e5fa9979b9621d645` |
| Pantheon dev base | `35d53db2af6dba878fc2322557d055099cb7edf6` |
| Decision | **Approved** |

## Scope Discipline Check

The packet declares `mutates_canonical: false` and opens with an explicit constraint
listing what it does not touch: L1 canonical truth, OpenAPI/source-of-truth contract
semantics, BFF runtime code, route registries, governance policy, database migrations,
OpenClaw adapter code, compatibility manifest source, and execute-plans source files.
No violation was observed.

## Factual Accuracy Assessment

### Identity/servant routes unchanged since followup-35

The packet records the git diff command and result:

```
git diff --name-status 8809835963a8..origin/dev -- \
  services/control-plane/bff/agora/router.py \
  services/control-plane/bff/agora/servant \
  services/control-plane/bff/agora/identity \
  services/control-plane/bff/main.py \
  docs/contracts/agora
```

Result: no output. The followup-35 BFF query ledger entries for `/me`,
`/capabilities`, and the full `/bff/agora/servant/*` suite carry forward without
modification. **Accepted.**

### Trading-room routes landed on Pantheon dev through AG-BE-TR-001

PR `#2191` merged AG-BE-TR-001 into Pantheon dev. The Agora BFF diff shows:
`trading_room/router.py`, `trading_room/store.py`, and `trading_room/test_trading_room.py`.
The packet records 24 trading-room tests passing. **Accepted.**

### Trading-room/candidate-pool/research/workshop remain outside AG-FE-ID-001 Phase 1

Section 5 of the packet keeps these surfaces in separate ledger rows, each marked
"Separate Phase N context." The operator journey in Section 8 ends at servant ensure
and does not extend into trading-room or candidate-pool controls. The parent absorption
checklist (Section 9) includes explicit trading-room separation and candidate-pool
separation checks. **Accepted.**

### Execute-plans PR `#66` remains the parent merge/deployment blocker

`gh pr view 66` returns `OPEN` / `UNSTABLE`; `gh pr checks 66` shows `integration-gate
fail` in run `27923882836`, job `82622466995`. The packet correctly preserves the gate
ownership table (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2) and does not bury
the aggregate failure in AG-FE-ID-001 closeout. **Accepted.**

## Additional Observations

1. The five-file diff comparison (`AgoraApp.tsx`, `identity.ts`, `identity.test.ts`,
   `servant.ts`, `servant.test.ts`) against execute-plans `dev ee835e2e` is accurate
   and confirms no `types.ts` reintroduction at head `d1ae3149`.

2. The compatibility manifest gate is correctly flagged as fail-closed with three
   expected blocking reasons.

3. PR `#63` legacy compatibility follow-through risk is correctly preserved as
   unresolved, not silently dropped.

4. The 39 BFF identity/servant/session tests plus 3 candidate-pool tests all pass,
   providing appropriate backend regression evidence.

## Carry-Forward Rules For Parent

The parent (`AG-FE-ID-001`, owner Claude) should not absorb this sidecar into
completion until:

- execute-plans PR `#66` merges into execute-plans `dev`
- the compatibility manifest records a non-placeholder frontend runtime commit and
  `compatibility_status: compatible`
- the aggregate gate reruns cleanly or a formal exception is recorded

Gate ownership (Gate 1: Gemini, Gate 2/5/7: Codex, Gate 6: Codex2) remains active
until each gate owner closes or explicitly dispositions their items.

## Decision

Approved. The packet is scope-disciplined, factually accurate, and provides the
correct handoff baseline for the parent task to resume once the execute-plans
aggregate gate clears.
