# Round 006 - residual two-line error-envelope unwrap (BFF contract tests)

- Date: 2026-06-14
- Path: the two-line envelope form R004's single-line regex missed.
- Branch: task/verify-r6-residual-envelope (off dev incl. R004/R005). TEST FILES ONLY.

## Finding
R004 fixed `resp.json()["detail"]["error"]` (single line). A second form remained:
```
detail = response.json()["detail"]      # stale: unwraps a non-existent "detail" key
assert detail["error"]["code"] == ...   # then accesses error under it
```
The canonical envelope has no `detail` wrapper (root is `{"error": {...}, "meta": ..., 
"foundation_error": ...}`), so `response.json()["detail"]` KeyErrors. Correct fix: drop the
`["detail"]` subscript so `detail` binds the root dict; all later `detail["error"]`,
`detail["foundation_error"]`, `detail["meta"]` accesses then resolve.

## Change
Precise replacement `.json()["detail"]` -> `.json()` (26 occurrences, 10 files). This matches
only response-root unwraps; legitimate nested `[...]["detail"]` fields (e.g.
`error_summary...["detail"]`, `analysis_links[0]["detail"]`, `body.get("detail")`
back-compat checks) are NOT touched - verified preserved.

## Result (before/after on the 10 files)
- pre-R6:  29 failed, 102 passed
- post-R6:  6 failed, 125 passed
- +23 passed / -23 failed, total constant -> zero pass->fail regressions.

## Remaining 6 (escalated, NOT auto-masked) - v5 confirm-token binding
All 6 remaining are `test_v5_interventions.py` confirm-token cases. They hardcode an
`X-Confirm-Token` header string without minting a bound token, so the code correctly returns
428 `CONFIRM_TOKEN_INVALID` ("not valid for this command"; requires binding to
command+target+operator). This is the code's stricter fail-closed confirm-token binding
working AS DESIGNED - a fixture drift, not a live bug. Fixing needs the tests to mint a
properly-bound confirm token via the issuance flow (a harness change), tracked for a later
round. Deliberately not masked by editing the expected status.

## Cumulative (R003-R006) BFF contract-suite repair
Stale-test rot fixed in three verified layers (envelope single-line, error-code constants,
envelope two-line) - all provably regression-free, production code untouched. Remaining
failures are now genuine harness/behavioral work, not mechanical rot.
