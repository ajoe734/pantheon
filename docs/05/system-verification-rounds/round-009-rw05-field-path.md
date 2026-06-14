# Round 009 - rw05 artifact-compare error field path

- Date: 2026-06-14
- Path: the 1 field-drift item from the R008 census (non-security, mechanical).
- Branch: task/verify-r9-bff-authz-tests (off dev). TEST FILE ONLY.

## Finding
`test_rw05_compare_rejects_non_comparable_artifacts` asserted
`payload["non_comparable_artifacts"]` at the response ROOT, but the handler
(main.py ~16444) emits it inside the canonical error envelope at
`error.details.non_comparable_artifacts` via `_pack_d_direct_error_response`. The item
shape `{artifact_id, status, reason}` and the reason text match the code exactly - only the
access path was stale.

## Fix
One-line: `payload["non_comparable_artifacts"]` ->
`payload["error"]["details"]["non_comparable_artifacts"]`. Code correct; test path stale.

## Evidence
`pytest test_rw05_artifact_compare_contract.py` -> 7 passed (was 1 failing).

## Remaining from R008 census (still owner-track)
- 2 deprecated-route tests (governance): retired `/bff/{type}s/{id}/actions/{action}` ->
  code 410 -> new `/bff/actions/{type}/{id}/{action}`; risk/alerts replacement needs
  confirming.
- 2 authz-premise tests: VERIFIED non-bugs in R008 (viewer is a read role; operator read
  surfaces require read role). Owner to correct the test premises (use a no-read principal
  for the negative assertion) - not auto-rewritten (security-relevant).
