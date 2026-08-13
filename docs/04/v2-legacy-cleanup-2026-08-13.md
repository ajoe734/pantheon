# V2 legacy cleanup and reconciliation — 2026-08-13

Baseline: `origin/dev` at `7f7d28ce83522738727795f58174baea3f299490`.

This is the authoritative disposition record for the final V2 cleanup. It is
not a new scheduler, backlog, migration path, or runtime state source.

## Removed source and artifacts

- Removed all 504 tracked `.coordination/` files. The bus had no production
  consumer and its payloads were historical frontend/Lovable handoffs.
- Removed `validate_twelve_loop_gap_evidence.py` and its dedicated tests. The
  dated checkpoint verifier had no runtime or CI caller.
- Removed the standalone activity-log inventory tool and its dedicated tests.
  The canonical activity reader remains in `.orchestrator/common.py`.
- Removed the byte-pinned 999-line activity overlap exception. The single
  generic 1000-line legacy rotation rule remains; all other overlaps fail
  closed.
- Removed tracked generated status/activity files. Runtime output must not be
  committed as source.

Historical delivery notes and evidence may still name removed paths. They are
records of what existed at their captured commit, not live instructions.

## Canonical task disposition

The 32 stale supervisor/L12 rows were checked against merged V2 source,
archives, current runtime behavior, and later replacement work. None should be
re-materialized.

Completed by current V2 or later merged work (11):

- `OPS-GITHUB-CANONICAL-REVIEW-ENFORCEMENT-001`
- `SUP-DISPATCH-REFACTOR-PROPOSAL-DOC-COMMIT-20260806`
- `OPS-DEV-BRANCH-CONTENT-RESTORE-20260806`
- `OPS-REVIEWER-APPROVAL-BINDING-GUIDANCE-20260807`
- `OPS-ANTIGRAVITY2-DISPATCHER-ENABLE-20260808`
- `SUP-ASSISTANT-DEV-BRIDGE-RETRY-STARVATION-20260808`
- `OPS-SUPERVISOR-ARCHITECTURE-LIVE-ROLLOUT-20260811`
- `SUP-AUTHORITY-V2-LIFECYCLE-REVIEW-20260811`
- `SUP-AUTHORITY-V2-SCHEDULER-ROUTING-20260811`
- `SUP-AUTHORITY-V2-DELIVERY-RECOVERY-20260811`
- `OPS-SUPERVISOR-AUTHORITY-V2-LIVE-CUTOVER-20260811`

Retired because the mechanism or rollout transaction no longer exists (21):

- `SUP-L12-STALE-FAILURE-STREAK-REAPER-20260729`
- `SUP-L12-HELPER-CLAIM-BUSY-PREFERRED-LANE-20260729`
- `SUP-L12-RUNNING-OWNER-RECONCILE-20260729`
- `SUP-L12-HELD-CLOSE-OVERLAP-GUARD-20260731`
- `SUP-L12-CURRENT-GAP-SUPERVISOR-DISPATCH-V2-20260731`
- `SUP-L12-SCOPED-REVIEWER-LEASE-20260731`
- `SUP-L12-POST-DAG-HOSTED-TOPOLOGY-RECONCILE-20260731`
- `SUP-L12-CLOSEOUT-ASSIGNMENT-RECONCILE-20260731`
- `SUP-L12-CURRENT-DAG-WAVE-ADVANCER-20260731`
- `SUP-L12-CURRENT-GAP-INVENTORY-REFRESH-20260731`
- `SUP-L12-FLEET-BOOTSTRAP-ROOT-COHERENCE-GATE-20260801`
- `SUP-L12-CURRENT-GAP-SUPERVISOR-DISPATCH-V3-20260801`
- `SUP-L12-GUARDED-REMEDIATION-CATALOG-CORRECTION-20260803`
- `SUP-PREEMPTION-EXACT-HEAD-REVIEW-20260731`
- `OPS-CODEX-CHATBOX-ROUTING-RULES-REVIEW-20260731-V2`
- `SUP-COMMAND-ROOT-SPLIT-HOTFIX-REVIEW-20260801`
- `SUP-COMMAND-ROOT-SPLIT-HOTFIX-COMPOSED-HEAD-REVIEW-20260801`
- `SUP-AUTOWORKER-QUOTA-ROUTING-LIVE-CANARY-OPERATOR-V9-20260802`
- `SUP-RUNTIME-V10-GOVERNED-ROLLOUT-VERIFY-20260808`
- `SUP-RUNTIME-V10-PROMOTION-GIT-DIR-ENOTDIR-20260808`
- `SUP-RUNTIME-V10-CANDIDATE-GIT-IDENTITY-FOLLOWUP-20260808`

The archive value `terminal_outcome=superseded` is only a board-reset label.
This section provides the semantic distinction between completed and retired.

## Open PR disposition

Forty-five stale PRs were closed after verifying that they were integrated,
superseded by V2, tied to retired L12/control-plane mechanisms, or historical
documentation only. Thirteen were deliberately retained for one final pass.

Closed release/rollout remnants (4): `#4790`, `#4695`, `#4601`, `#4387`.

Closed legacy supervisor/L12/control-plane branches (31): `#4761`, `#4729`,
`#4696`, `#4666`, `#4658`, `#4636`, `#4617`, `#4610`, `#4606`, `#4561`,
`#4544`, `#4542`, `#4528`, `#4522`, `#4450`, `#4447`, `#4442`, `#4426`,
`#4386`, `#4382`, `#4364`, `#4362`, `#4291`, `#3817`, `#3799`, `#3788`,
`#3779`, `#3763`, `#3638`, `#3554`, `#3039`.

Closed in favor of their current product implementation (2): `#4628`,
`#3774`. Closed because their changes were already integrated (5): `#4073`,
`#3736`, `#3572`, `#1554`, `#1539`. Closed historical documentation-only
branches (3): `#1678`, `#1617`, `#1615`.

The following six are directly superseded by this cleanup and may close when
its PR merges: `#4568`, `#4162`, `#3949`, `#3820`, `#1680`, and `#1531`.

The remaining seven do not justify independent execution tasks. Their useful
parts are consolidated in
`docs/05/quality-backlog-after-open-pr-triage-2026-08-13.md`: `#2550`,
`#1722`, `#1635`, `#1552`, `#1551`, `#1548`, and `#1544`.
