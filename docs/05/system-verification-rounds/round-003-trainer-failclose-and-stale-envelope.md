# Round 003 - trainer fail-closed ordering + stale error-envelope contract tests

- Date: 2026-06-14
- Path tested: Loop #4 (Persona Teaching) BFF contract surface + error-envelope contract.
  Different path from R001 (config) and R002 (route coverage): this round RUNS the service
  test suite and fixes real red tests.
- Branch: task/verify-r3-trainer-failclose (off dev)

## Findings (both verified by running the suite)

### Finding 1 - fail-closed ordering defect (from R002 escalation, now fixed for this entry)
`GET /api/v1/trainer/sessions` returned **422 (validation) before 401 (auth)** for an
unauthenticated caller, because `persona_id` was a required FastAPI query param validated
before the in-body auth check (`services/control-plane/bff/main.py:12388`). An
unauthenticated caller could probe endpoint existence/shape. Path-param siblings
(`/sessions/{id}`) already returned 401.

Fix: `persona_id` is now `Optional[str] = None`; after `_extract_identity` +
`_require_read_role`, the handler raises a structured 422 (`precondition_failed=persona_id`)
only once the caller is authenticated. Unauthenticated -> 401; authenticated-missing -> 422.

NOTE: the broader pattern (~81 GET endpoints with required query params share this ordering)
remains escalated as a fail-closed-ordering design decision (R002); this round fixes only the
verified persona-teaching entry as a reference pattern + regression test.

### Finding 2 - 8 pre-existing RED contract tests (stale error envelope)
While running `test_trn002_trainer_session_contract.py`, 8 tests were already failing on
clean `origin/dev` with `KeyError: 'detail'`. They asserted
`resp.json()["detail"]["error"][...]`, but the canonical BFF error envelope is top-level
`{"error": {code, i18nKey, message, details: {reason, precondition_failed, suggestion}}, meta}`
(confirmed live and via TestClient). The `detail` wrapper does not exist - a custom exception
handler normalizes all errors to the `{"error": ...}` shape. The tests were stale.

Fix: corrected all 9 `["detail"]["error"]` -> `["error"]` assertions in the file (the 8
pre-existing reds + the 1 new regression test). The `{"error": ...}` shape is correct per
the canonical error contract and live behavior, so the tests were wrong, not the code.

NOTE: the stale `["detail"]["error"]` assertion pattern is SYSTEMIC across 10+ other bff
test files (test_ew04, test_inc001, test_trn003, test_tw02, test_rw01, test_bff_agora_extended,
test_mgmt_syn_006, ...). Escalated for a follow-up sweep round; this round fixes only trn002.

## Test evidence

```
# before (clean dev):  8 failed, 17 passed
# after this round:    27 passed   (incl. 2 new fail-closed regression tests)
```
`PANTHEON_BFF_AUTH_STUB=true python3 -m pytest test_trn002_trainer_session_contract.py -q`

## Files changed
- `services/control-plane/bff/main.py` - trainer list-sessions auth-before-validation
- `services/control-plane/bff/test_trn002_trainer_session_contract.py` - 2 new regression
  tests + 9 stale-envelope assertion corrections

## Loop coverage delta
| Loop | design | API | actually runs |
|------|:--:|:--:|:--:|
| #4 Persona Teaching | y | y | contract suite GREEN (27/27); live cycle still needs token |
