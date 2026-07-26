# L12-TEACH-001 Persona Teaching evidence

Status: reviewer-requested hardening is ready for independent `Codex`
re-review.

This packet proves the reconciled service boundary delivered by
`L12-TEACH-001`: authenticated service/tenant teaching requests, tenant-bound
session and event contracts, authoritative Postgres state, cross-worker replay
serialization, fail-closed persona mutation, and functional readiness based on
real evaluation/commit outcomes.

PR #4149 delivered the original boundary to `dev`. PR #4166 addresses all
three findings from the reviewer's independent post-merge probes and is merged
to `dev`. It does not claim that the merged follow-up is already hosted.
Runtime manifest activation belongs to `L12-MANIFEST-001`; the
service-boundary and hosted drills belong to `L12-VERIFY-LEARN-001` and
`L12-HOSTED-001`. No seed fixture or local mock is presented as hosted proof.

## Source identity

- Task: `L12-TEACH-001`
- Owner: `Codex2`
- Reviewer: `Codex`
- Merge target: `dev`
- Authoritative-store anchor:
  `5a942db461633dbd755fdf0578c42ad519c516b2`
- Authority/tenant/health anchor:
  `184b5a54d7816e40841a1c52fd2c970d6c0ade36`
- Reviewer-hardening anchor:
  `cc8592176e62907794e3a8943c13d0bd74524bc6`
- Original merged delivery: PR #4149, merge commit
  `6636507f274ffcaf4b6d42b1c3cb8adb09dd49f5`
- Reviewer-hardening merged delivery: PR #4166, source head
  `408db61c5e8884d603555db03f2579ae071a4bb3`, merge commit
  `022bb35f4cd93c82571fcaf2799905a7043efcd2`

## Acceptance evidence

### Authenticated actor, service, tenant, and MFA

`inbound_authority.py` requires a verified bearer identity, the
`training-service` role, an allowlisted `X-Pantheon-Service` that matches the
verified service claim, and an `X-Tenant-Id` within verified tenant claims.
JWTs that omit role claims are no longer promoted to `training-service`; both
missing and wrong-role JWTs fail with `403 AUTH_FORBIDDEN`. Commit/discard
requires MFA asserted by the verified JWT/IdP claim before route execution.
A caller-supplied six-digit `X-MFA-Token` without claim-bound proof fails with
`401 MFA_NOT_VERIFIED`. Body actor fields cannot override the verified
delegated actor.

The negative matrix proves missing bearer, missing tenant, mismatched service,
cross-tenant read, actor spoof, missing role, wrong role, missing MFA,
malformed MFA, and well-formed-but-unverified MFA rejection on both commit and
discard.

### Authoritative HA state and restart

Postgres mode stores sessions, controls, previews, preview jobs, replays, and
functional results in `training_session.authority_records`; teaching events
remain in `training_session.teaching_events`. Preview-job and replay mutations
use transaction-scoped Postgres advisory locks. Session event append now takes
the session-scoped advisory lock and performs event conflict readback plus
session mutation in the same transaction. An identical duplicate returns the
durable prior event; a duplicate `event_id` with a different payload raises a
conflict without replacing the durable row.

The real-Postgres tests used two independent store instances against isolated
random tables in the existing local dev Postgres. The replay workers both
observed a terminal committed replay, the persona-target callback ran exactly
once, and a newly constructed store read the same terminal controller record.
The append workers concurrently produced durable sequence numbers `1` and `2`
without a lost session update. The same test proved identical duplicate
readback and mismatched duplicate rejection while retaining exactly one prior
row. Each test dropped only its random tables in `finally`.

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

Hosted terminal readback is deliberately `not_claimed` for this follow-up.
The reviewer-hardening delivery is merged but has not been proven active on a
hosted runtime. The downstream manifest and hosted verification tasks must cite
an exact deployed commit and real owner-service terminal record before raising
maturity to proven-live.

## Validation

```text
TRAINING_SESSION_TEST_POSTGRES_DSN=<local-dev-dsn> \
PYTHONPATH=services/training-session/tests:services/training-session:. \
  /home/lupin/pantheon/.venv/bin/python -m pytest -q -p no:cacheprovider \
  services/training-session
129 passed, 1 warning in 31.16s

PYTHONPATH=. /home/lupin/pantheon/.venv/bin/python -m py_compile \
  services/training-session/inbound_authority.py \
  services/training-session/main.py \
  services/training-session/store.py \
  services/training-session/tests/test_inbound_authority.py \
  services/training-session/tests/test_postgres_event_store.py
exit 0

git diff --check
exit 0
```

The full suite ran both opt-in real-Postgres tests; no test was skipped.
