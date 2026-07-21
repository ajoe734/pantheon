# APP-003-ROUTELIVE-FOLLOWUP-001 Review

Reviewer: `Codex`
Reviewed at: `2026-04-24T11:37Z`
Disposition: `approved`

## Scope

Re-review the republished RW-05 / KW-03 / KW-05 route-live follow-up bundle
after the owner handoff that pointed Pantheon at front-repo publication commit
`1a1a42eebda033a1fbda4696df5b81271f5eed9b` on `origin/pkt-004-detail-fix`.

Task acceptance remains:

1. Use the route-live activation prompt only for the unresolved RW-05 and KW
   follow-up subset.
2. Do not reopen already loop-complete features in the same packet.
3. Return truthful `ui-done` / `frontend-feedback` or `bff-gap` results for the
   unresolved subset.

## Verification Surface

- Task brief:
  `.orchestrator/task-briefs/app_003_routelive_followup_001.md`
- Reviewer sidecar:
  `support/sidecars/APP-003-ROUTELIVE-FOLLOWUP-001/APP-003-ROUTELIVE-FOLLOWUP-001-SIDECAR-REVIEW.md`
- Front-repo publication commit:
  `1a1a42eebda033a1fbda4696df5b81271f5eed9b`
- Reviewed UI snapshot:
  `6321613cff3c49b11a7619e0f9170217a27a7b17`
- Verification method:
  review performed against the Git tree at
  `origin/pkt-004-detail-fix@1a1a42eebda033a1fbda4696df5b81271f5eed9b`, not the
  sibling repo working tree HEAD.

## Findings

No blocking findings.

The previously reopened publication issues are resolved in the new front-repo
bundle.

## Verification

- `origin/pkt-004-detail-fix` resolves to
  `1a1a42eebda033a1fbda4696df5b81271f5eed9b`.
- The reviewed UI hash
  `6321613cff3c49b11a7619e0f9170217a27a7b17` exists and is an ancestor of the
  publication commit, so the returned `source_commit` is replayable.
- All four checked-in `KW-03` / `KW-05` request files under
  `../front-ai-trading-system/.coordination/requests/` now publish the exact
  full reviewed UI hash
  `6321613cff3c49b11a7619e0f9170217a27a7b17`.
- The mirrored feedback-bundle metadata now matches that same hash in:
  - `../front-ai-trading-system/docs/pantheon-feedback/KW-03-evidence-refs/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/KW-05-strategy-spec/API_GAP_REQUESTS.json`
  - `../front-ai-trading-system/docs/pantheon-feedback/RW-05-artifact-compare/API_GAP_REQUESTS.json`
- Commit `1a1a42e` adds
  `../front-ai-trading-system/.coordination/requests/RW-05-artifact-compare-bff-gap.yaml`
  and deletes the non-example
  `RW-05-artifact-compare-ui-done.yaml` /
  `RW-05-artifact-compare-frontend-feedback.yaml` pair, so RW-05 now returns
  the truthful blocking shape required by the task brief.
- The RW-05 gap packet and mirrored API-gap summary align on the same blocker:
  missing `artifacts[].allowedActions.canCompare` on `GET /api/v1/artifacts`.
- `../front-ai-trading-system/docs/pantheon-feedback/KW-05-strategy-spec/LOVABLE_CHANGE_FEEDBACK.md`
  now limits citation claims to delivered evidence-ref and memory-anchor
  surfaces. The earlier `insight_citations` overclaim is gone.
- The checked-in publication set remains scoped only to the unresolved subset:
  `RW-05-artifact-compare`, `KW-03-evidence-refs`, and
  `KW-05-strategy-spec`.

## Decision

`APP-003-ROUTELIVE-FOLLOWUP-001` is review-clean.

The republished `1a1a42e` route-live bundle is now truthful and replayable for
the current scope:

- `KW-03` and `KW-05` publish real replayable `source_commit` values
- `RW-05` stops at canonical `bff-gap` instead of overclaiming completion
- `KW-05` feedback text no longer claims undelivered citation rendering

The task can move from `review` to `review_approved`. The next step is owner
finalization to `done`.
