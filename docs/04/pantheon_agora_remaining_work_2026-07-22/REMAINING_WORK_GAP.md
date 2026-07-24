# Pantheon and Agora Remaining Work Gap — 2026-07-22

Status: approved for fleet execution on `dev`

## Purpose

This document converts the 2026-07-22 runtime and repository audit into a
bounded execution backlog. It is intentionally narrower than the historical
task archive: a merged task or UI surface is not reopened unless current code,
hosted evidence, or runtime state proves a remaining product gap.

The canonical execution packet is:

- `docs/bff/execution-tasks/2026-07-22-pantheon-agora-remaining-work/INDEX.md`

## Current accepted dev baseline

- Frontend repository: `ajoe734/execute-plans`, branch `dev`, accepted commit
  `4c71e7934d2455f89a9da536b5c222ed6c60d083`.
- BFF accepted commit:
  `6d1aaddc7abc6a2601de8add908b20c5d2688eda`.
- Hosted deployment state: `accepted`, live BFF mode, strict fallback, real and
  stub writes disabled in the restored read-only profile.
- BFF posture: strict auth, auth stub disabled, dev login enabled, MFA
  required, assistant kernel enabled.
- Current backend `origin/dev` is newer than the hosted BFF. A repository head
  is not deployment evidence; every hosted claim must remain pinned to the
  accepted pair or a later gate-before-switch manifest.

## Findings that require execution

### P0 runtime and acceptance

1. `pantheon-loop-run-projector-scheduler-1` is in an exited/restart loop after
   `ENOSPC`. The current projection points at generation 5036 and has not
   advanced since 2026-07-21 11:58 UTC. Root disk has recovered, but the worker
   has not. `/readyz` does not expose this dependency.
2. `PPL-ALLOC-009` still lacks one correlated governed B1 chain, the same-chain
   authenticated desktop/mobile B3 proof, and the B5 IA decision. Its old
   Human/Ops credential blocker is stale and must be resolved, not repeated.
3. `TJ-E2E-012` has stronger hosted proof, but still lacks an immutable
   scenario-by-scenario ledger for all twelve scenarios and an independent
   Human/Ops verdict.
4. The supervisor dispatch status sync does not propagate `ORCH_RUN_ID`.
   PRs #3936 and #3948 duplicate the same repair and are both stale. One repair
   must be made canonical before the new backlog is allowed to fan out without
   repeating nonterminal worker exits.

### P0/P1 Agora product truth

1. The deployed Strategy Performance page calls `getSimulatedDetails()` for
   compliance, interventions, execution history, warnings, and adjustment
   suggestions. Applying or rejecting a suggestion only changes local UI state
   and shows a toast; it produces no governed receipt.
2. Trading Room candidate mapping combines live title/score with static sample
   reason, concerns, next event, evidence, and details, then marks the card as
   non-sample.
3. Six workshop operations remain intentionally fail-closed with 501. Contract
   honesty was completed by `AG-GAP-005`; implementing the deferred product
   capability is new follow-up work and must use new task IDs.
4. The Agora compatibility manifest remains `pending`, with zero frontend
   runtime/contract/type hashes.
5. The source-ingest service serves persisted records, but current external
   egress is deny-all, no allowed hosts are configured, and no scheduler is
   running. The emergency guard is present only as an uncommitted live repair.
6. Restart-persistence proof exists for the retired VM at `35.201.239.38`.
   The replacement VM at `35.201.204.12` still needs task-scoped reproof.

### P1/P2 delivery control

1. `publish-promote.yml` repeatedly stops on the first old publish-branch
   conflict, while seven promote PRs to `master` remain behind.
2. Twenty-nine overdue Pantheon task PRs and 2,060 no-open-PR task-branch
   candidates need evidence-based triage. This packet authorizes read-only
   classification and closing demonstrably superseded PRs; it does not
   authorize blanket branch deletion.
3. GitHub currently reports 20 open Dependabot alerts on the default branch:
   seven critical, three high, nine medium, and one low. Six npm alerts point at
   a removed historical `execute-plans/package-lock.json`; reachable MLflow,
   Ray/RLlib, and Torch manifests still require fresh remediation/isolation
   evidence rather than reuse of stale June PR claims.

## Explicit exclusions

- No production or live-capital activation. EP5 remains a Human/Ops gate and
  is not dispatched by this packet.
- No direct push to `dev` or `master`, no force push, and no blanket deletion.
- No fake hosted evidence, no hand-edited read store, and no hidden sample data.
- No frontend code in the Pantheon repository. Frontend tasks target
  `ajoe734/execute-plans` and merge to `dev`.
- No duplicate implementation PR for the dispatch lease defect: converge on
  one of #3936/#3948 or supersede both with a single rebased replacement.

## Completion definition

Each execution task must use a clean task worktree, run focused validation,
commit with required trailers, push, open a PR to the declared `dev` target,
wait for checks, merge, and record merge/deploy evidence. Hosted tasks are not
done until the accepted manifest and live readback prove the exact deployed
SHAs. Runtime rescue may be performed first only when it is the smallest safe
repair and the same change is delivered through the repository flow.

## Closeout addendum — 2026-07-24 (Agora hosted exact pair)

The hosted Agora exact-pair blocker recorded above (P0/P1 Agora product truth
finding 6, replacement-VM reproof; and the `AG-HOSTED-CLOSE-001` block on
`target_type=strategy_workshop` `422` / `APPROVAL_TARGET_TYPE_MISMATCH` `409` /
`STRATEGY_SPEC_STRATEGY_ID_MISMATCH`) is **resolved and closed**:

- Canonical `strategy_workshop` target type and distinct Registry/strategy
  identity were repaired by `AG-GOV-WORKSHOP-CONTRACT-001` (PRs #4036, #4037).
- The compatibility gate was regenerated and the exact accepted FE/BFF pair was
  deployed with strict auth by `AG-GOV-WORKSHOP-COMPAT-DEPLOY-001`, which ran
  the full hosted seed → approve → research → conclude → governed BFF restart →
  readback repair probe on the replacement VM `35.201.204.12`.
- Managed-dev lifecycle freshness was raised to 300 s and kept strict exact-SHA
  `operator-bff` ready across the switch and restart
  (`OPS-DEV-LIFECYCLE-FRESHNESS-001`, PR #4043).
- `AG-HOSTED-CLOSE-002` archived the reviewer-consumable final closeout and an
  as-of `2026-07-24T05:58Z` independent read-only re-probe confirming the pair
  is still served, strict, read-only with safe write defaults, and `/readyz`
  healthy.
- The authoritative lifecycle archived `AG-HOSTED-CLOSE-002` as completed at
  `2026-07-24T06:23:50Z` and records `AG-HOSTED-CLOSE-001` as its superseded
  predecessor. The successor evidence merged through PR #4050 (`874103d1a`);
  independent review state merged through PR #4051 (`cd4f42c4f`).

Accepted hosted pair after this closeout (supersedes the pre-fix baseline in
§ Current accepted dev baseline for the Agora Governance/Workshop surface):

- Frontend `e4399e3ec68f882ace35d0349e6597cdd101525f`.
- BFF `f71c1f8ba889ba64956006ef0f9159840be6d065`.
- Pair ID `ec91a4aaaee16719f6db6a3d7b6edba048c08e676d789bfb9301df92913c3de2`;
  compatibility manifest SHA-256
  `d61e11cf2cead97d4a66ab153a2081ef4d633671ee4f962d271a7b3feeb86867`;
  contract family `agora.v1.13`.

Closeout evidence:

- `docs/deployment/evidence/agora/ag-hosted-close-002.md`
- `docs/deployment/evidence/agora/ag-hosted-close-002/` (as-of re-probe capture)
- `docs/deployment/evidence/agora/ag-gov-workshop-compat-deploy-001.md`
- `docs/deployment/evidence/agora/ag-gov-workshop-contract-001/README.md`
