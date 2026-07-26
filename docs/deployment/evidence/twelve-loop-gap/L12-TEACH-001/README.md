# L12-TEACH-001 Persona Teaching evidence

Status: ready for independent `Codex` review.

This packet proves the reconciled service boundary delivered by
`L12-TEACH-001`: authenticated service/tenant teaching requests, tenant-bound
session and event contracts, authoritative Postgres state, cross-worker replay
serialization, fail-closed persona mutation, and functional readiness based on
real evaluation/commit outcomes.

It does not claim that this unmerged task branch is already hosted. Runtime
manifest activation belongs to `L12-MANIFEST-001`; the service-boundary and
hosted drills belong to `L12-VERIFY-LEARN-001` and `L12-HOSTED-001`. No seed
fixture or local mock is presented as hosted proof.

## Source identity

- Task: `L12-TEACH-001`
- Owner: `Codex2`
- Reviewer: `Codex`
- Merge target: `dev`
- Authoritative-store anchor:
  `5a942db461633dbd755fdf0578c42ad519c516b2`
- Authority/tenant/health anchor:
  `184b5a54d7816e40841a1c52fd2c970d6c0ade36`

## Acceptance evidence

### Authenticated actor, service, tenant, and MFA

`inbound_authority.py` requires a verified bearer identity, the
`training-service` role, an allowlisted `X-Pantheon-Service` that matches the
verified service claim, and an `X-Tenant-Id` within verified tenant claims.
Commit/discard requires MFA before route execution. Body actor fields cannot
override the verified delegated actor.

The negative matrix proves missing bearer, missing tenant, mismatched service,
cross-tenant read, actor spoof, missing MFA, and malformed MFA rejection.

### Authoritative HA state and restart

Postgres mode stores sessions, controls, previews, preview jobs, replays, and
functional results in `training_session.authority_records`; teaching events
remain in `training_session.teaching_events`. Preview-job and replay mutations
use transaction-scoped Postgres advisory locks.

The real-Postgres test used two independent store instances against isolated
random tables in the existing local dev Postgres. Both workers observed a
terminal committed replay, the persona-target callback ran exactly once, and a
third newly constructed store instance read the same terminal controller
record. The test dropped only its two random tables in `finally`.

### Evaluation failure and functional health

The fail-closed HTTP test forces the authoritative threshold gate to fail,
observes a completed evaluation with a failed governance result, proves the
persona mutation call count remains zero, and verifies `/readyz` returns `503`
with the functional dependency degraded. A later passing evaluation and exact
terminal persona readback restore readiness.

Commit failure is also durable functional state: after authenticated MFA
admission reaches a missing replay, `/readyz` becomes `503` and reports a
failed `persona_commit` result.

### Persona terminal authority

Persona pre-readback, approval, target write, and terminal readback must carry
the same tenant. The tenant is included in the digest-bound proof and committed
target binding, and every authority request carries `X-Tenant-Id`.
`test_persona_target.py` covers exact authoritative metadata, negative
approval, changed preconditions, timeouts, idempotent duplicate commit, and
terminal digest/readback mismatch.

Hosted terminal readback is deliberately `not_claimed` here because the task
branch is not yet merged or activated. The downstream manifest and hosted
verification tasks must cite an exact deployed commit and real owner-service
terminal record before raising maturity to proven-live.

## Validation

```text
PYTHONPATH=services/training-session/tests:services/training-session:. \
  /home/lupin/pantheon/.venv/bin/python -m pytest -q -p no:cacheprovider \
  services/training-session
123 passed, 1 skipped, 1 warning in 45.04s

TRAINING_SESSION_TEST_POSTGRES_DSN=<local-dev-dsn> PYTHONPATH=. \
  /home/lupin/pantheon/.venv/bin/python -m pytest -q -p no:cacheprovider \
  services/training-session/tests/test_postgres_event_store.py::test_real_postgres_two_workers_and_restart_observe_one_terminal_commit
1 passed in 0.94s

PYTHONPATH=. /home/lupin/pantheon/.venv/bin/python -m py_compile \
  services/training-session/inbound_authority.py \
  services/training-session/main.py \
  services/training-session/models.py \
  services/training-session/persona_target.py \
  services/training-session/preview_eval_worker.py \
  services/training-session/store.py
exit 0
```

The single skipped test in the full suite is the same opt-in real-Postgres
test; it passed separately with the local dev DSN.

