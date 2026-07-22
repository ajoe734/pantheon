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
