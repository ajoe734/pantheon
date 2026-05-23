# BFF-B1-012 Owner Closeout Evidence

Task: BFF-B1-012 - POST /bff/alerts/{id}/acknowledge
Owner: Codex
Reviewer: Codex2
Status at closeout: review_approved

## Reviewed Delivery

- Original implementation PR: https://github.com/ajoe734/pantheon/pull/436
- Original implementation merge commit: `0431aff6edf2774f95f4fb219b1f78b5c10d16bc`
- Closeout/task-brief PR: https://github.com/ajoe734/pantheon/pull/461
- Closeout/task-brief merge commit: `6908ec20ab3df5da985c67430818739352c3dc0b`
- Latest merged task branch head before this evidence note: `9e94ed5e5dad27be3179b789e6223e79d1c795ee`
- Reviewer approval: Codex2 review notes in `ai-status.json` for BFF-B1-012.

## Delivered Scope

- Implemented the dedicated `POST /bff/alerts/{alert_id}/acknowledge` BFF handler.
- Required operator role and header idempotency for acknowledge writes.
- Rejected body-level idempotency keys before command-store side effects.
- Persisted acknowledge commands through the shared command store as
  `CommandType.ALERT_ACKNOWLEDGE` against `ObjectType.RISK_ALERT`.
- Published `alert.acknowledged` SSE events on the `system` channel.
- Recorded acknowledged alerts in `_ACKNOWLEDGED_ALERTS` and suppressed them from
  subsequent `GET /bff/alerts` active alert payloads.
- Preserved replay-safe idempotency and HTTP 409 conflict behavior.
- Returned HTTP 404 `OBJECT_NOT_FOUND` for unknown alert IDs when the alerts
  surface is available.
- Projected `meta.acknowledgement_supported = true` for the alerts surface.

## Closeout Verification

Commands run from `task/BFF-B1-012` on 2026-05-23:

```bash
pytest services/control-plane/bff/tests/test_bff_alerts_acknowledge.py services/control-plane/bff/test_pkt012_alerts_rail_contract.py
pytest services/control-plane/bff/tests/test_bff_alerts_acknowledge.py services/control-plane/bff/test_pkt012_alerts_rail_contract.py
gh pr view 461 --json number,state,mergeCommit,mergeStateStatus,statusCheckRollup,headRefOid,url,title
git merge-base --is-ancestor HEAD origin/dev
```

Results:

- Focused BFF alert acknowledge and alerts rail contract tests passed twice:
  12 passed before the PR branch refresh and 12 passed after composing latest
  `origin/dev`.
- PR #461 merged to `dev` at
  `6908ec20ab3df5da985c67430818739352c3dc0b`.
- Branch CI Gate checks for PR #461 passed: Commit trailers, Runtime mirror
  guard, and Smoke acceptance.
- The merged task branch head `9e94ed5e5dad27be3179b789e6223e79d1c795ee`
  is an ancestor of `origin/dev`.

## Closeout Notes

- No L1 canonical architecture or policy document was changed during owner
  closeout.
- The task brief was refreshed to record the owner reassignment from Claude to
  Codex before PR #461 merged.
- This evidence note keeps the final branch tip on a Codex-authored
  BFF-B1-012 commit with the required task trailers for the canonical `done`
  gate.
