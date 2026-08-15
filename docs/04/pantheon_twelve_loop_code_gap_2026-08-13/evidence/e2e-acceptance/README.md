# Retired acceptance path

This directory is not Pantheon product closure or liveness evidence.

The reports previously stored here were retired by
`L12-CURRENT-LEGACY-RETIRE-20260814`. They combined self-declared identities,
GET-only inventory checks, and mutually contradictory pass/fail output. Git
history preserves them for incident archaeology, but they must not be restored
as an acceptance gate or wrapped by a compatibility verifier.

Current cross-loop acceptance is owned by the deployed HTTP suite and its
independently reviewed evidence:

- `tests/integration/l12/test_current_cross_loop_deployed_e2e.py`
- `docs/deployment/evidence/twelve-loop-current/cross-loop/evidence.json`
- `docs/deployment/evidence/twelve-loop-current/cross-loop/run-report.json`
- `docs/deployment/evidence/twelve-loop-current/cross-loop/review.md`

The adjacent `evidence.json` records only this retirement task and explicitly
has no product-closure authority.
