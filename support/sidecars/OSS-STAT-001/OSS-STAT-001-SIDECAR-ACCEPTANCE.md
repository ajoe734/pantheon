# OSS-STAT-001 Acceptance Packet: statsmodels Cointegration Adapter

**Sidecar kind:** `acceptance_packet`
**Sidecar task:** `OSS-STAT-001-SIDECAR-ACCEPTANCE`
**Helper parent:** `OSS-STAT-001`
**Parent owner:** `Claude`
**Parent reviewer:** `Codex`
**Parent status:** `todo` (preempted)
**Prepared by:** `Gemini`
**Reviewer:** `Claude`
**Date:** `2026-05-17`
**Status:** `in_progress`

> Scope constraint: support artifact only. This packet summarizes the acceptance
> checklist and dependency map for the `statsmodels` cointegration adapter
> skeleton without changing L1 canonical truth, runtime/governance policy,
> or the active implementation task record.

## Executive Summary

The task `OSS-STAT-001` aims to implement a `statsmodels` adapter for stat-arb style
cointegration (Engle-Granger) testing. The initial implementation failed review
due to a package/file shadowing conflict (`adapter.py` vs `adapter/` directory).

This sidecar packet documents the resolved state where `cointegration_test` is
correctly placed within the `services/research/statsmodels/adapter/` package.
This layout preserves the existing `statsmodels` infrastructure while
introducing the new cointegration capabilities and the `signal_snapshot`
research artifact.

## Acceptance Checklist

This checklist is derived from the criteria in `ai-status.json` and the
`OSS-STAT-001` task brief.

| Criterion | Result | Note |
|---|---|---|
| `adapter` exposes `cointegration_test(prices_a, prices_b)` | [x] | Implemented in `services/research/statsmodels/adapter/cointegration.py` and exported via `__init__.py`. |
| `cointegration_test` returns `p_value`, `spread`, `half_life` | [x] | Returns a dict with these three keys. `half_life` handles non-stationary cases (returns `inf`). |
| `smoke_test.py` runs on deterministic synthetic series | [x] | Generates cointegrated series with fixed seed (42). |
| `smoke_test.py` asserts `p_value < 0.05` | [x] | Verified in `test_cointegration_smoke`. |
| `smoke_test.py` produces `signal_snapshot` artifact | [x] | Generates a dict with `artifact_type: signal_snapshot`. |
| `signal_snapshot` contains deterministic `checksum` | [x] | SHA-256 of sorted-key JSON (rounded values). |
| `requirements.txt` pins `statsmodels` version | [x] | `statsmodels==0.14.2` |
| `Dockerfile` is present | [x] | Uses `python:3.11-slim` and runs the smoke test. |
| No other `services/research/` subdirectories modified | [x] | Changes are self-contained in `services/research/statsmodels/`. |

## Dependency Map

### External Dependencies
- `statsmodels==0.14.2`: Core econometrics framework.
- `numpy>=1.26.4`: Numeric processing and array handling.
- `pandas>=2.2.2`: Time-series data handling.

### Internal Relationships
- `services/research/statsmodels/adapter/`: Package hosting the governed statsmodels logic.
  - `statsmodels_adapter.py`: Existing governed input/output logic.
  - `cointegration.py`: New Engle-Granger implementation.
- `services/research/statsmodels/smoke_test.py`: Entry point for verification and artifact emission.

## Verification Snapshot

### Smoke Test Logic
The `smoke_test.py` performs the following:
1. Generates 120 observations of two cointegrated series using `numpy.random.default_rng(42)`.
2. Invokes `cointegration_test(prices_a, prices_b)`.
3. Asserts `p_value < 0.05` (typically ~0.0001 for this seed).
4. Rounds `p_value` to 8 decimal places and `half_life` to 4 decimal places.
5. Emits a `signal_snapshot` with a SHA-256 checksum of the sorted JSON payload.

### Resolved Shadowing Issue
The previous review failure reported that `adapter.py` shadowed the `adapter/`
directory. The current state resolves this:
- New location: `services/research/statsmodels/adapter/cointegration.py`
- Export: `from .cointegration import cointegration_test` in `adapter/__init__.py`
- This allows `from adapter import cointegration_test` to work correctly when
  the service directory is in `sys.path`.

## Disposition
This sidecar packet is ready for review by `Claude` to support the eventual
finalization of `OSS-STAT-001`.
