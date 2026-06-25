# Round 005 - stale error-CODE drift sweep (BFF contract tests)

- Date: 2026-06-14
- Path: the layer-2 rot discovered in R004 - tests assert removed error-code names.
- Branch: task/verify-r5-error-code-drift (off dev incl. R004). TEST FILES ONLY.

## Verified mapping (harvested from actual-vs-expected failure diffs, not guessed)

pytest `assert '<ACTUAL>' == '<STALE_EXPECTED>'` gives the rename directly. Confirmed every
canonical target IS in `models.py ErrorCode` (26 members) and every stale source is ABSENT,
so the code physically cannot return the old names -> tests are stale, code is correct.

| stale (test expected) | canonical (code returns) |
|---|---|
| INVALID_TOKEN | AUTH_REQUIRED |
| OBJECT_NOT_FOUND | RESOURCE_NOT_FOUND |
| INSUFFICIENT_ROLE | FORBIDDEN |
| INVALID_PARAMS | VALIDATION_FAILED |
| INVALID_REQUEST | VALIDATION_FAILED |
| INVALID_STATE | OPERATION_NOT_ALLOWED |
| DOWNSTREAM_UNAVAILABLE | DEPENDENCY_UNAVAILABLE |
| PRECONDITION_NOT_MET | PRECONDITION_FAILED |
| CONFIRM_TOKEN_REQUIRED | CONFIRMATION_REQUIRED |

## Change
73 quoted-literal replacements across 38 test files.

## Result (before/after on the 38 changed files)
- pre-R5:  77 failed, 234 passed
- post-R5: 36 failed, 275 passed
- +41 passed / -41 failed, total constant -> ZERO pass->fail regressions (proves no
  stale code name was a test INPUT that the sweep would have wrongly altered).

Combined R004+R005 reduced the BFF contract suite from ~77 red to 36 red.

## Layer 3 (escalated to R006+) - remaining 36 failures
Deeper, non-rename failures concentrated in: v5_interventions(7),
bff_b2_list_detail_facade(5), bff_alerts_acknowledge(3), actions_to_commands_adapter(3),
agora_journal_merge_patch(3), me_locale/logout/precondition_errors/command_executor(2 each).
These are structural/behavioral assertion mismatches to triage per-area in later rounds.

## Meta-finding (carried from R004)
The BFF contract suite is not in the CI merge gate, which let three rot layers
(envelope -> error-code -> structural) accumulate silently. Wiring it in is its own round.
