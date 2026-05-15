# DEPTH-CAP002 Sidecar Review Packet

## Date

2026-04-18

## Scope

This is a support-only `review_packet` for `DEPTH-CAP002-SIDECAR-REVIEW`.
It does not modify L1 canonical truth, core runtime or governance behavior, or
the parent task's acceptance contract. Its purpose is to give `Codex` a compact
review handoff for the already-approved `DEPTH-CAP002` parent work.

## Parent Task Snapshot

`DEPTH-CAP002` is currently active in `ai-status.json` with:

- owner: `Claude`
- reviewer: `Codex`
- status: `review_approved`
- review file: `.coordination/reviews/DEPTH-CAP002-review.md`
- next: parent acceptance is satisfied and ready for owner finalization to `done`

The parent review record already says:

- weighted fusion is implemented
- sponsor selection chooses the highest effective-weight proposal
- committee referral path exists
- `ConflictResolutionLog` is recorded on synthesis runs
- unit tests and smoke verification passed on the reviewed snapshot

This sidecar packet does not reopen that decision. It packages the evidence and
highlights the only support-level nuance worth carrying forward.

## Current Evidence Summary

### Review and runtime surface agree

The current `services/optimizer-svc/main.py` is not a placeholder anymore. It
now:

- exposes `POST /api/optimizer/synthesize`
- instantiates `PortfolioSynthesizer`
- returns either an artifact outcome or a committee referral
- records `ConflictResolutionLog` IDs in the response path
- stores artifacts/referrals for `GET /api/optimizer/policies/{policy_id}`
- exposes `GET /api/optimizer/logs/{log_id}`

That matches the parent review claim that the HTTP surface now returns the
artifact or committee-referral path and supports log lookup.

### Supporting module evidence is still present

The synthesis module remains aligned with the parent acceptance targets:

- `services/optimizer-svc/portfolio_synthesis/models.py`
  defines `AllocationPolicyArtifact`, `CommitteeReferral`, and
  `ConflictResolutionLog`
- `services/optimizer-svc/portfolio_synthesis/synthesizer.py`
  implements weighted fusion, sponsor selection, escalation, and log emission
- `services/optimizer-svc/test_portfolio_synthesis.py`
  covers the required decision paths
- `services/optimizer-svc/smoke_test_portfolio_synthesis.py`
  exercises the artifact, referral, and all-vetoed cases

## Fresh Verification In This Sidecar Run

Executed again on the current snapshot:

```bash
python3 -m unittest discover -s services/optimizer-svc -p 'test_*.py'
python3 services/optimizer-svc/smoke_test_portfolio_synthesis.py
```

Observed results:

- `unittest`: `7` tests passed
- smoke test: `3/3` groups passed

These results match `.coordination/reviews/DEPTH-CAP002-review.md` and do not
show drift between the reviewed snapshot and the live repo state.

## Important Sidecar Nuance

The older support artifact
`support/sidecars/DEPTH-CAP002/DEPTH-CAP002-SIDECAR-ACCEPTANCE.md` contains a
now-stale warning that `services/optimizer-svc/main.py` was still a shallow
placeholder. That warning no longer matches the current repo state or the
parent review record.

Safest reading:

- treat the older acceptance packet as historical context only
- use `.coordination/reviews/DEPTH-CAP002-review.md` plus this sidecar packet
  for current reviewer handoff
- do not reopen the parent task on the basis of the stale placeholder note

## Reviewer Guidance For Codex

The support-only conclusion is straightforward:

- the parent review approval is internally consistent with the live repo
- the previously ambiguous `main.py` gap appears closed
- no extra support artifact is needed beyond handing this packet back to the
  assigned reviewer / parent owner chain

## Files Reviewed

- `ai-status.json`
- `.coordination/reviews/DEPTH-CAP002-review.md`
- `support/sidecars/DEPTH-CAP002/DEPTH-CAP002-SIDECAR-ACCEPTANCE.md`
- `services/optimizer-svc/main.py`
- `services/optimizer-svc/portfolio_synthesis/models.py`
- `services/optimizer-svc/portfolio_synthesis/synthesizer.py`
- `services/optimizer-svc/test_portfolio_synthesis.py`
- `services/optimizer-svc/smoke_test_portfolio_synthesis.py`
- `docs/02-architecture/consensus/sessions/phase6-2026-04-16-oss-ecosystem-closure/planning-session.json`

## Sidecar Outcome

Support review packet created for `Codex`. No canonical files or runtime
implementation files were modified.
