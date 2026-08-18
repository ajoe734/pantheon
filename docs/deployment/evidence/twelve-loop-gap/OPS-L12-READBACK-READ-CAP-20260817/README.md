# OPS-L12-READBACK-READ-CAP-20260817 controller-readback read cap evidence

Owner: Claude
Reviewer: Antigravity2
Status: implementation complete; awaiting independent review

## Outcome

`source.authority_actual_state` was failing the deployed research-loops E2E
with `GET /api/source-ingest/controller/readback` "returned non-JSON
content." The endpoint itself is fine; the harness's
`DeployedResearchHarness._http_json()` capped a successful response read at
`response.read(32_768)` before decoding it and calling `json.loads()`. The
controller/readback payload (full connector registry, schedule, and
DLQ/record counts) grows with the shared default `pantheon` compose
environment's accumulated history, and on the live environment this ran
against it had grown to ~1.24 MB -- well past the 32 KiB cap. The read
silently truncated mid-object and `json.loads()` reported the truncated body
as non-JSON, which reads as an API defect in source-ingestion rather than the
test harness under-reading its own response.

## Fix

- Raised `DeployedResearchHarness._http_json()`'s successful-response read
  cap from `response.read(32_768)` to `response.read(16_777_216)` (16 MiB),
  with a comment explaining the growth and the silent-failure mode.
- No changes to `services/source_ingestion` or any other product read path.
- No other loop harness's `_http_json` usage is touched.
- The `HTTPError` branch's error-body read (`exc.read(4_096)`) is unchanged.

## Acceptance evidence

| Acceptance | Result | Evidence |
|---|---|---|
| `_http_json` reads up to 16 MiB before decoding a successful response as JSON instead of 32768 bytes | Pass | diff replaces `response.read(32_768)` with `response.read(16_777_216)` |
| rerunning the deployed research-loops E2E no longer fails `source.authority_actual_state` with returned non-JSON content | Pass | live reproduction after the fix reads the full 1,249,201-byte body and parses cleanly |
| no product code or other read path is changed | Pass | diff confined to one `response.read()` call in the test file; nothing under `services/` touched |

## Validation

- `python3 -m py_compile tests/integration/l12/test_current_research_loops_deployed_e2e.py`: exit 0.
- Direct before/after reproduction against the live default-compose
  controller/readback endpoint: reading the first 32,768 bytes and parsing
  raises `Unterminated string starting at: line 1 column 32767`; reading up
  to 16,777,216 bytes reads the full 1,249,201-byte body and parses cleanly.

## Composition boundary

This task owns only the deployed E2E harness's HTTP successful-response read
cap for the controller/readback boundary. It does not change
`services/source_ingestion` runtime behavior, other loop harnesses' read
caps, or any other read path. This gap was flagged as a follow-on finding by
`L12-CURRENT-SOURCE-READBACK-20260817`'s evidence manifest, which recorded
`source.authority_actual_state` failing at the same 32,768-byte read cap on
the same shared compose environment.
