# Round 004 - BFF contract-test rot: error-envelope sweep + error-code drift discovery

- Date: 2026-06-14
- Path tested: run the BFF contract test suite at scale (33 files carrying the stale
  error-envelope pattern) - a different path from R001-R003.
- Branch: task/verify-r4-envelope-sweep (off dev). TEST FILES ONLY; no production code touched.

## What was run

`PANTHEON_BFF_AUTH_STUB=true pytest <33 files with ["detail"]["error"]>` under
services/control-plane/bff.

Baseline (clean dev): **70 failed, 249 passed**. The BFF contract suite is substantially
red on dev today, and these tests are NOT in the merge gate (dev ships with them red).

## Layer 1 (FIXED this round) - stale error-envelope access path

81 assertions across 33 files used `resp.json()["detail"]["error"]`, but the canonical BFF
error envelope is top-level `{"error": {code, i18nKey, message, details}, meta}` (a global
exception handler normalizes away FastAPI's `detail` wrapper; confirmed live + TestClient).
A passing test could never have used `["detail"]["error"]` (it would KeyError), so replacing
`["detail"]["error"]` -> `["error"]` is provably regression-free.

Result after fix: **63 failed, 256 passed** (+7 fully green, -7 failed, total constant ->
zero pass->fail regressions, proven by monotonic counts + the impossibility argument).

## Layer 2 (DISCOVERED, escalated to R005) - stale error-CODE constants

Fixing layer 1 de-masked the real dominant rot: ~56 tests assert error-code names the code
has since renamed to the canonical catalog (`services/control-plane/bff/models.py ErrorCode`).
Verified the old names are NOT in the enum (code is correct; tests are stale):

| stale code asserted by tests | canonical code returned by live code |
|---|---|
| INVALID_TOKEN | AUTH_REQUIRED |
| INSUFFICIENT_ROLE | FORBIDDEN |
| OBJECT_NOT_FOUND | RESOURCE_NOT_FOUND |
| (more to be mapped in R005) | |

Sample evidence (post-envelope-fix tracebacks):
- `test_human_inbox_requires_authentication`: assert code == INVALID_TOKEN, got AUTH_REQUIRED
- `test_remediate_v5_intervention_insufficient_role_returns_403`: assert INSUFFICIENT_ROLE, got FORBIDDEN
- `test_rw04_detail_404_for_missing_experiment`: assert OBJECT_NOT_FOUND, got RESOURCE_NOT_FOUND

## Remaining-failure concentration (R005 backlog)
v5_interventions(14), assistant_security(5), rw04(5), bff_session_auth_me(5),
bff_governance_runtime_risk_audit(3), + ~20 files with 1-2 each.

## Decision
Ship the provably-safe envelope sweep (layer 1). Escalate error-code drift (layer 2) to R005
with the verified mapping approach. Also a meta-finding: these contract tests are not wired
into CI, so both rot layers accumulated silently - a gate gap worth its own round.

## Loop coverage note
This round is BFF-contract-suite health, cross-cutting loops #1-#15 (the suite exercises
trainer/research/experiment/intervention/governance/incident surfaces). It does not change
the design/API/actually-runs matrix; it restores test-layer trustworthiness.
