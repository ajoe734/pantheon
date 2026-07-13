# PPL-ALLOC-011 Formal Review

Reviewer: Codex  
Owner: Codex2  
Reviewed at: 2026-07-13T17:51:06Z  
Disposition: changes required; return to implementation

## Review conclusion

The hosted happy path and the core task-scoped suite support the delivered
Capital owner mutation, authoritative readback, confirmation-token replay,
two-man admission, and controlled-restart claims. Formal closeout is not yet
approved because the wider BFF contract suite exposes two public-contract
regressions, and the proposal receipt still has a restart crash window inside
the task's stated durability scope.

## Blocking findings

### 1. High: EmergencyContainment advertises a target that admission rejects

`services/control-plane/bff/action_catalog.py:1133` publishes
`EmergencyContainment` with `entity_type="Runtime"`. The task-owned admission
guard in `services/control-plane/bff/main.py:3519` requires the target to be a
`Persona` and rejects every Runtime target before containment-policy validation.
Consequently, a client following `GET /bff/actions` cannot submit the advertised
contract, and the existing containment limit regression at
`services/control-plane/bff/test_bff_mgmt_ops_006_operator_actions_contract.py:116`
fails.

Required change: align the catalog, admission, tests, and known consumers on a
single canonical target contract. If Runtime compatibility must remain, resolve
Runtime to its authoritative Persona before owner mutation without weakening
the target-binding checks.

### 2. High: authoritative proposal receipts can remain submitted after restart

After Capital has durably created an authoritative proposal,
`services/control-plane/bff/main.py:25488` appends a submitted BFF command and
`services/control-plane/bff/main.py:25518` performs a separate rewrite to
executed. A crash between those writes leaves a durable Capital proposal paired
with a permanently submitted BFF receipt. Same-key replay returns that stale
status, and startup recovery only requeues `ApprovedApply` and
`EmergencyContainment`, not `RebalanceProposal`.

Required change: persist the already-complete proposal command and result in one
atomic operation, using the existing `CommandStore.submit_terminal_command`
pattern or an equivalent transaction. Add a fault/restart regression that
interrupts the path after the Capital commit and proves same-key replay returns
the stable terminal proposal identity and a succeeded idempotency record.

### 3. Medium: server-managed evidence types break the action-catalog invariant

`services/control-plane/bff/models.py:98` adds `RebalanceApproval` and
`RebalanceTwoManSign` as `CommandType` values, while the public action catalog
contains neither. Both existing catalog invariants fail. Advertising them as
ordinary actions would conflict with the deliberate server-managed rejection in
the generic command route.

Required change: make the internal-only boundary explicit. Either model these
evidence records outside the public command enum, or add a narrowly documented
server-managed exclusion that is exercised by catalog tests while dedicated
authenticated evidence routes remain the only producer.

## Evidence that passed

- PR #3525 (`e103d29e806c21626dc5a2f7392c663894caa772`) contains the
  substantive Capital owner and restart-readback implementation. PR #3536
  (`0e8c06603eb7ede8fd226837e439282e70fefc80`) contains the final guarded
  admission repair. PR #3540
  (`1eab79a313b03cc4d7f0576ace97ed1cfd9d8b49`) archives the owner evidence.
  All three PRs are merged and their required checks passed.
- Deploy runs `29268814057` and `29270122636` both checked out, deployed, and
  verified target SHA `0e8c06603eb7ede8fd226837e439282e70fefc80`. The second
  run's GitHub event `headSha` is a later dev SHA, but its checkout,
  `TARGET_REF`, remote worktree, and BFF source-SHA verification are all the
  exact task SHA.
- A read-only hosted recheck at the deployed task SHA confirmed rebalance
  `rb-20260713-9e640fe8e883` is applied with approval, receipt, audit, and
  command identities intact; Capital reports authoritative current and target
  weight `0.0101`; token `ct-ppl011-final-0e8c0660` is redeemed; and command
  `cmd-414820143c8240098d5eaceec8e923f9` plus the Persona read expose an
  authoritative frozen containment state. Audit reads contain two distinct
  actors for both final apply and containment signatures.
- The accepted boundary remains one dev writer with a durable host volume and
  `live_capital_side_effects=false`. This review does not infer multi-replica
  serialization, host-volume disaster recovery, broker execution, or real
  capital execution.

## Validation

Passing core suite:

```sh
python3 -m pytest -q \
  services/control-plane/bff/tests/test_bff_rebalance_proposals.py \
  services/control-plane/bff/tests/test_bff_emergency_containment.py \
  services/control-plane/bff/tests/test_bff_b1_007_security_hardening.py \
  services/control-plane/bff/tests/test_bff_b5_001_security_hardening.py \
  services/control-plane/bff/test_bff_two_man_sign_race_contract.py \
  services/control-plane/bff/test_v5_interventions.py \
  services/control-plane/bff/tests/test_bff_b5_humangate_commands.py \
  scripts/test_deploy_nonprod_bff_source_sha_contract.py
```

Result: 133 passed, 33 warnings.

Blocking contract suite:

```sh
python3 -m pytest -q \
  services/control-plane/bff/test_action_catalog.py \
  services/control-plane/bff/test_bff_mgmt_ops_006_operator_actions_contract.py
```

Result: 3 failed, 13 passed, 12 warnings. The failures are both action-catalog
coverage assertions and `test_emergency_containment_limit`.

## Closeout traceability notes

The detailed brief's reference to
`.orchestrator/reviews/PPL-ALLOC-009-TWO-MAN-PROOF-2026-07-13.json` points to an
untracked supervisor-root artifact that demonstrates the pre-fix gap only; it
must not be treated as final containment proof. On the next review handoff,
retain the hosted final-chain evidence above, list base implementation PR #3525
alongside final repair PR #3536, and preserve the exact validation commands.
