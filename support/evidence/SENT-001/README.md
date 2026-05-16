# SENT-001 Evidence: /bff/v5/sentinel/findings endpoint

Task: SENT-001 — /bff/v5/sentinel/findings endpoint
Owner: Claude2
Reviewer: Codex
Commit: eda173a6

## Scope

SENT-001 adds kind/status/severity query filters to `GET /bff/v5/sentinel/findings`,
enriches the SentinelFinding derived model with the `kind` field, and provides
focused contract tests.

## Implementation Notes

The filter logic and `kind` field were already implemented in a prior run:

- `services/control-plane/bff/main.py` (line ~24361): `_SENTINEL_FINDING_KINDS`,
  `_SENTINEL_FINDING_STATUSES`, `_SENTINEL_FINDING_SEVERITIES` sets; validation
  logic returning 400 for unknown filter values; route passes filters to
  `read_store.list_sentinel_findings(kind=..., status=..., severity=...)`.

- `services/control-plane/bff/read_store.py` (line ~1470):
  `_derive_sentinel_finding` includes `kind` field via `inc.get("kind") or
  cls._infer_sentinel_kind(inc)`; `_infer_sentinel_kind` derives kind from title
  keywords when not present on the incident; `_apply_sentinel_filters` applies
  case-insensitive kind/status/severity filtering.

## New Artifact

`services/control-plane/bff/test_sent001_sentinel_findings_contract.py` — 15 tests

### Test Coverage

| Area | Tests |
|---|---|
| `kind` field in derived model | explicit kind from incident; kind inferred from title |
| `?kind=` filter | returns matching; excludes others; case-insensitive; 400 on invalid |
| `?status=` filter | returns matching; open filter; 400 on invalid |
| `?severity=` filter | returns matching; excludes others; 400 on invalid |
| Combined filters | kind+severity intersection; no-match returns empty list |
| No filters | all non-loop sentinel findings returned |

## Verification

```
pytest services/control-plane/bff/test_sent001_sentinel_findings_contract.py -q
# 15 passed

pytest services/control-plane/bff/test_bff_v5_loop_sentinel_contract.py \
       services/control-plane/bff/test_read_store_loop_sentinel.py -q
# 33 passed (no regression)
```
