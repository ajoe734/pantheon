# Review: BFF-B1-007-SEC-FIX — Validate confirm/approval/two-man tokens + remove bearer-in-audit + scope idempotency by caller

Reviewer: Claude
Task commit: ce76b8e0ffb88033b4e977801aaac186c8f25dcb
PR: #589 (merged into dev at 74e6b0cb)
Review date: 2026-05-25

## Verdict: Approved

## Scope Verified

Six files changed in the task commit (910 insertions, 67 deletions):
- `docs/04/pantheon_bff_api_gap_2026-05-23/BFF_API_GAP_final_integration_spec.md` — §13 addendum
- `services/control-plane/bff/command_queue.py` — operator-scoped idempotency lookup
- `services/control-plane/bff/main.py` — evidence validation + bearer token runtime isolation
- `services/control-plane/bff/test_governance_command_submission.py` — extended coverage
- `services/control-plane/bff/tests/test_bff_b1_007_security_hardening.py` — new focused security test module
- `services/control-plane/bff/tests/test_command_replay_conflict.py` — operator-scoped replay assertions

All changes are contained within the declared artifact set. No unrelated files touched.

## Acceptance Criteria

| # | Criterion | Status |
|---|---|---|
| 1 | Confirm-token validated against backing record (existence, status==created, command+target binding, caller binding) before command-store write | Pass — `_validate_final_command_evidence` lines 2558–2617 |
| 2 | Approval decision validated (existence, not consumed, command+target binding) | Pass — lines 2619–2669 |
| 3 | Two-man signature validated (existence, ≥2 distinct operators via set(), command+target binding) | Pass — lines 2671–2722 |
| 4 | Raw bearer token NOT persisted in audit/command records | Pass — `_command_runtime_auth_context` stores only `bearer_token_present: bool`; raw token lives only in `_COMMAND_AUTH_CONTEXT[command_id]` (line 1080) and is `.pop()`-ed on use (line 34101) |
| 5 | Idempotency replay scoped by operator_id (different operator with same key → new command_id) | Pass — `command_queue.py:92–98` filters by operator_id; main.py cache key uses `f"{operator_id}\x00{idempotency_key}"` |
| 6 | Runtime auth propagation preserved for downstream calls | Pass — auth context dict keeps `auth_token` + `mfa_token` for in-process use; not logged or serialized |
| 7 | Existing governance tests unbroken | Pass — 33 tests pass including pre-existing replay and governance suites |

## Security Review

Dedicated security sub-agent reviewed all implementation surfaces:

- **Evidence validation**: All three evidence types validated at submit time against live store state — no TOCTOU window.
- **Bearer token isolation**: Token value never appears in error responses, logs, or persisted records. Only `bool` flags stored in audit context.
- **Idempotency scope**: Null-byte separator `\x00` between `operator_id` and `idempotency_key` prevents collision attacks.
- **Two-man distinctness**: Signer list stored as `set()` before `len < 2` check — duplicate operator cannot satisfy the two-man gate.
- **No vulnerabilities found** (confidence threshold >80% applied; no DoS, rate-limit, or theoretical findings included).

## Test Run

```
python3 -m py_compile services/control-plane/bff/main.py \
  services/control-plane/bff/command_queue.py \
  services/control-plane/bff/test_governance_command_submission.py \
  services/control-plane/bff/tests/test_command_replay_conflict.py \
  services/control-plane/bff/tests/test_bff_b1_007_security_hardening.py
# → py_compile OK

python3 -m pytest \
  services/control-plane/bff/tests/test_bff_b1_007_security_hardening.py \
  services/control-plane/bff/test_governance_command_submission.py \
  services/control-plane/bff/tests/test_command_replay_conflict.py -q
# → 33 passed in 8.52s
```

## Notes

- Commit trailers are well-formed: `LLM-Agent: Codex`, `Task-ID: BFF-B1-007-SEC-FIX`, `Reviewer: Claude`, `Verified` summary included.
- PR #589 is already merged into dev; no outstanding CI issues.
- The `_COMMAND_AUTH_CONTEXT` in-process dict pattern is consistent with the runtime propagation model — auth tokens are never serialized to disk.
- `_record_bound_to_command_and_target` uses case-insensitive normalization via `_binding_token()` — correct, prevents case-variant bypass.
