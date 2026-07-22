# AG-HOSTED-CLOSE-001 — Replacement-VM Agora hosted acceptance and closeout

Priority: P1
Repository: `ajoe734/pantheon` (evidence and closeout only)
Merge target: `dev`
Owner: Codex
Reviewer: Codex2
Depends on: `AG-COMPAT-002-GATE`, `PAN-SOURCE-FRESH-001`

## Objective

Prove the completed Agora capability set on the replacement dev VM and archive
one reviewer-consumable closeout pinned to the exact accepted FE/BFF pair.

## Required proof

1. Create or update a workshop, versions/selection, research run,
   consultation, conclusion, candidate, Trading Room workspace, and dashboard
   recipe using an authenticated dev operator identity.
2. Capture before-restart snapshots and ETags, restart only the required dev
   service under the deployment runbook, then prove byte/semantic identity and
   selected-version continuity after restart.
3. Prove Performance data is real or explicitly unavailable and that suggestion
   actions return durable receipts/readback.
4. Prove candidate fields never mix live and sample sources.
5. Prove one bounded source refresh and explicit freshness/as-of presentation.
6. Run strict desktop/mobile/a11y/RBAC smoke on execute-plans.
7. Record manifest pair, compatibility hashes, workflow runs, PRs, merge SHAs,
   rollback result, and residual risks.

## Acceptance

- Evidence comes from `pantheon-lupin-dev-20260719` / `35.201.204.12`, not the
  suspended predecessor project or old IP.
- All four Agora durable stores survive restart with tenant/user isolation.
- Six formerly deferred operations return their implemented typed behavior.
- Compatibility manifest and hosted deployment manifest identify the same
  accepted FE/BFF pair.
- Safe read-only frontend profile is restored and watchdog proof passes.
- Independent reviewer approves the closeout packet.

## Exclusions

- No production/live-capital action.
- No evidence copied from the retired VM and relabeled as current.
- No hand-edited stores or route interception.
