# Agora hosted acceptance — invalidated

The `PASSED` claim created by PR #4935 is invalid. Its mode was
`simulated-hosted`: the verifier created deployment, readiness, browser,
restart, and rollback results inside the Python process instead of observing
the hosted services.

This evidence must not be used as current deployment acceptance. The
replacement verifier is `scripts/verify_agora_current_hosted_acceptance.py` and
the replacement acceptance identity is
`AGORA-HOSTED-REAL-ACCEPTANCE-20260815`.

See `INVALIDATION.md` for the exact failure analysis and required proof.
