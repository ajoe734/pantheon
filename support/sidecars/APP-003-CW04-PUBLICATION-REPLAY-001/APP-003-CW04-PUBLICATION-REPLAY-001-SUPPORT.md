# APP-003-CW04-PUBLICATION-REPLAY-001 Support Note

**Task**: `APP-003-CW04-PUBLICATION-REPLAY-001`  
**Owner**: `Codex`  
**Reviewer**: `Codex3`  
**Updated**: `2026-04-23`  
**Scope**: close the remaining `CW-04-redteam-memo` replay-clean front
publication residual now that the sibling front repo has republished a
truthful request pair.

> Support artifact only. This note does not change canonical truth by itself.
> It packages the current replay-close evidence, the narrow reviewer boundary,
> and the reason the parent task is ready for reviewer approval and owner
> finalization.

## Summary

- `CW-04-redteam-memo` already had a completed Pantheon review packet; the
  only historical blocker was front transport truth.
- Revalidated on `2026-04-23`: `origin/pkt-004-detail-fix` and local `HEAD`
  now both resolve to `675f1cc59be537455e776113be9ad8a45fa44208`.
- Both current front request files now pin `source_commit` to the reviewed UI
  transport commit `c94f63082eae1667ed919353d62c85180d7bafba`, and the
  targeted CW-04 paths are clean in the sibling front workspace.
- Pantheon response truth now records `disposition: close`,
  `review_result: replay-clean-and-contract-aligned`, `can_close: true`, and
  `next_action: none`.
- No new Pantheon BFF, schema, or runtime work is required. The parent task is
  ready for narrow reviewer approval and then owner finalization to `done`.

## Current Repo Truth

### Pantheon truth is now closure-ready

- `.coordination/responses/CW-04-redteam-memo-frontend-feedback.yaml` now
  records:
  - `disposition: close`
  - `review_result: replay-clean-and-contract-aligned`
  - `can_close: true`
  - `next_action: none`
- `.coordination/reviews/CW-04-redteam-memo-review.md` now says no blocking
  findings remain and approves the current closeout scope.
- `python3 -m pytest services/control-plane/bff/test_cw04_redteam_memo_contract.py -q`
  passed with `7 passed`.

### Front publication replay is now truthful

Revalidated on `2026-04-23` against `../front-ai-trading-system`:

- current branch: `pkt-004-detail-fix`
- local `HEAD`: `675f1cc59be537455e776113be9ad8a45fa44208`
- remote publish head: `675f1cc59be537455e776113be9ad8a45fa44208`
- reviewed UI transport commit:
  `c94f63082eae1667ed919353d62c85180d7bafba`
- `git show --stat --summary --oneline c94f63082eae1667ed919353d62c85180d7bafba`
  resolves to:
  `c94f630 feat(front): publish CW-04 and PKT-001 follow-up snapshot`
- `git show --stat --summary --oneline 675f1cc59be537455e776113be9ad8a45fa44208`
  resolves to:
  `675f1cc chore(front): republish CW-04 and PKT-001 request pairs`
- `git status --short -- ...` returns no output for the targeted CW-04 UI,
  request-pair, and feedback-bundle paths.

### Published request metadata is replay-clean

Observed from the current sibling workspace request files:

- `../front-ai-trading-system/.coordination/requests/CW-04-redteam-memo-ui-done.yaml`
  now publishes:
  - `source_commit: c94f63082eae1667ed919353d62c85180d7bafba`
  - `source_commit_note: This request pair is republished after commit c94...`
- `../front-ai-trading-system/.coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml`
  now publishes:
  - `source_commit: c94f63082eae1667ed919353d62c85180d7bafba`
  - `source_commit_note: This request pair is republished after commit c94...`
- the targeted diff from
  `c94f63082eae1667ed919353d62c85180d7bafba` to
  `675f1cc59be537455e776113be9ad8a45fa44208` over the CW-04 paths only touches
  the two request files plus
  `docs/pantheon-feedback/CW-04-redteam-memo/API_GAP_REQUESTS.json` and
  `docs/pantheon-feedback/CW-04-redteam-memo/LOVABLE_CHANGE_FEEDBACK.md`

Working conclusion:

- the replay residual is resolved
- no further Pantheon implementation change is needed for this task
- the correct next move is reviewer approval on the refreshed closeout packet,
  followed by owner finalization

## Acceptance Readout

The task's Pantheon-side acceptance bar is now fully met:

1. the `CW-04` replay-clean follow-up exists as a named supervisor-visible task
2. the task keeps the close condition pinned to one truthful front publish
   chain, and that condition is now met by
   `c94f63082eae1667ed919353d62c85180d7bafba` plus
   `675f1cc59be537455e776113be9ad8a45fa44208`
3. active Pantheon truth no longer treats `CW-04` as an open BFF, contract, or
   transport residual; only workflow closeout remains

What is intentionally not claimed:

- that deployed browser QA has been exercised
- that any new Pantheon delivery work is required

## Reviewer Boundary

When `Codex3` reviews this task, the checks should stay narrow:

- confirm the Pantheon response now says `close`, `can_close: true`, and
  `next_action: none`
- confirm `origin/pkt-004-detail-fix` resolves to
  `675f1cc59be537455e776113be9ad8a45fa44208` and local `HEAD` matches
- confirm both current front request files now publish
  `source_commit: c94f63082eae1667ed919353d62c85180d7bafba`
- confirm `c94f63082eae1667ed919353d62c85180d7bafba` contains the reviewed UI
  files and feedback bundle while the targeted diff to
  `675f1cc59be537455e776113be9ad8a45fa44208` only touches request-pair and
  feedback-metadata files
- confirm Pantheon-side CW-04 contract verification still passes

Do not reopen these already-settled questions as part of this slice:

- whether CW-04 still needs a Pantheon BFF route or schema change
- whether degraded detail is allowed to drop required fields
- whether governance CTA visibility should be inferred client-side
- whether this task should absorb sibling front implementation work

## Immediate Next Step

No additional Pantheon implementation work is required in this repo for the
current task scope. The correct next move is reviewer action:

- move `APP-003-CW04-PUBLICATION-REPLAY-001` to `review` for `Codex3`
- use
  `support/sidecars/APP-003-CW04-PUBLICATION-REPLAY-001/APP-003-CW04-PUBLICATION-REPLAY-001-SIDECAR-REVIEW.md`
  as the reviewer-facing evidence packet
- if approved, have `Codex3` move the task to `review_approved` so the owner
  can finalize it to `done`

## Verification

- `git -C ../front-ai-trading-system rev-parse --abbrev-ref HEAD`
- `git -C ../front-ai-trading-system rev-parse HEAD`
- `git -C ../front-ai-trading-system rev-parse origin/pkt-004-detail-fix`
- `git -C ../front-ai-trading-system show --stat --summary --oneline c94f63082eae1667ed919353d62c85180d7bafba`
- `git -C ../front-ai-trading-system show --stat --summary --oneline 675f1cc59be537455e776113be9ad8a45fa44208`
- `git -C ../front-ai-trading-system diff --name-only c94f63082eae1667ed919353d62c85180d7bafba..675f1cc59be537455e776113be9ad8a45fa44208 -- .coordination/requests/CW-04-redteam-memo-ui-done.yaml .coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml docs/pantheon-feedback/CW-04-redteam-memo`
- `git -C ../front-ai-trading-system status --short -- src/pages/consultation/RedTeamMemoList.tsx src/pages/consultation/RedTeamMemoDetail.tsx .coordination/requests/CW-04-redteam-memo-ui-done.yaml .coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml docs/pantheon-feedback/CW-04-redteam-memo`
- `sed -n '1,220p' ../front-ai-trading-system/.coordination/requests/CW-04-redteam-memo-ui-done.yaml`
- `sed -n '1,240p' ../front-ai-trading-system/.coordination/requests/CW-04-redteam-memo-frontend-feedback.yaml`
- `python3 -m pytest services/control-plane/bff/test_cw04_redteam_memo_contract.py -q`
