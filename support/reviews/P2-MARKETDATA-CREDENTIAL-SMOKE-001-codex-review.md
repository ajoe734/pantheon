# Review: P2-MARKETDATA-CREDENTIAL-SMOKE-001

Reviewer: Codex
Owner: Codex2
Date: 2026-05-01
Disposition: approved

## Scope Reviewed

- `scripts/run_marketdata_credential_smoke.py`
- `scripts/test_run_marketdata_credential_smoke.py`
- `support/evidence/P2-MARKETDATA-CREDENTIAL-SMOKE-001/repo-local-uncredentialed/`
- `docs/deployment/ep5-canary-ready/operator-approval-checklist.md`
- `DATA_SOURCE_SCOPE_MATRIX.md` scoped change
- Related read-only adapter boundaries in `services/execution/ibkr_adapter.py`, `services/execution/kraken_adapter.py`, and `services/execution/shioaji_adapter.py`

## Findings

No blocking findings remain.

The earlier blocking finding is resolved. Every provider packet now carries
explicit non-secret `rate_limit` / quota evidence and `session_provenance`.
HTTP success/error/no-network paths preserve observed allowlisted headers or a
clear unavailable/not-observed reason. IBKR and Shioaji quote-readback paths
record source env, file name, SHA-256 checksum, loaded/captured timestamps, and
credential/session evidence status without raw secret material.

## Verified Passing Checks

```bash
python3 -m py_compile scripts/run_marketdata_credential_smoke.py scripts/test_run_marketdata_credential_smoke.py

python3 -m unittest scripts.test_run_marketdata_credential_smoke scripts.test_run_broker_sandbox_order_smoke
# Ran 14 tests: OK

python3 scripts/run_marketdata_credential_smoke.py --output-dir /tmp/pantheon-review-marketdata-smoke-rerun
# {"status": "pass", "output_dir": "/tmp/pantheon-review-marketdata-smoke-rerun"}

git diff --check -- DATA_SOURCE_SCOPE_MATRIX.md docs/deployment/ep5-canary-ready/operator-approval-checklist.md docs/deployment/external-data-integration-materialization-audit.md scripts/run_marketdata_credential_smoke.py scripts/test_run_marketdata_credential_smoke.py support/evidence/P2-MARKETDATA-CREDENTIAL-SMOKE-001 support/reviews/P2-MARKETDATA-CREDENTIAL-SMOKE-001-codex-review.md

for f in support/evidence/P2-MARKETDATA-CREDENTIAL-SMOKE-001/repo-local-uncredentialed/*.json support/evidence/P2-MARKETDATA-CREDENTIAL-SMOKE-001/repo-local-quote-readback/*.json; do [ "$(basename "$f")" = summary.json ] && continue; jq -e 'has("rate_limit") and has("session_provenance") and (.credential.raw_secret_material_present_in_artifact == false) and (.session_provenance.raw_secret_material_present_in_artifact == false) and (.order_side_effects_allowed == false) and (.capital_side_effects_allowed == false)' "$f" >/dev/null || { echo "missing required evidence: $f"; exit 1; }; done; echo "marketdata evidence packets passed field checks"
# marketdata evidence packets passed field checks
```

## Non-Blocking Notes

- The 9-provider scope is present in the runner and repo-local evidence.
- IBKR, Shioaji, and Kraken stay on read-intent / readback paths in this runner; I did not find an order payload construction or order submission path in the reviewed smoke code.
- Raw credential values are not written for normal credential envs; the Polygon redaction test covers the API-key query string path.
- Repo-local uncredentialed evidence records all nine governed providers; the separate quote-readback evidence records `read_ok` for IBKR and Shioaji using non-secret readback fixture files.
