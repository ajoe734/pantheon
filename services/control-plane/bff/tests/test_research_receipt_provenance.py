from __future__ import annotations

from typing import Any, Dict, Optional

import pytest

from services.control_plane.bff.agora.research.receipt import resolve_run_provenance


def _receipt(**overrides: Any) -> Dict[str, Any]:
    return {
        "receipt_id": "receipt-1", "run_id": "run-1", "executor": "vectorbt",
        "mode": "real", "correlation_id": "corr-1",
        "completed_at": "2026-10-03T00:00:00Z", "spec_version": "1.0",
        **overrides,
    }


def _run(**overrides: Any) -> Dict[str, Any]:
    return {
        "run_id": "run-1", "execution_status": "succeeded", "provenance": "real",
        "metrics": [{"metric": "sharpe", "value": 1.2, "provenance": "real"}],
        **overrides,
    }


def test_receipt_mode_must_match_backend_provenance() -> None:
    provenance, receipt = resolve_run_provenance(
        None, _run(provenance="simulation", receipt=_receipt())
    )
    assert provenance == "unavailable"
    assert receipt is None


@pytest.mark.parametrize("completed_at", [None, "not-a-timestamp"])
def test_receipt_missing_or_invalid_completed_at_fails_closed(completed_at: Optional[str]) -> None:
    provenance, receipt = resolve_run_provenance(
        None, _run(receipt=_receipt(completed_at=completed_at))
    )
    assert provenance == "unavailable"
    assert receipt is None


def test_metric_provenance_must_match_receipt_mode() -> None:
    provenance, receipt = resolve_run_provenance(
        None,
        _run(receipt=_receipt(), metrics=[{"metric": "sharpe", "value": 1.2, "provenance": "simulation"}]),
    )
    assert provenance == "unavailable"
    assert receipt is None


def test_resolve_run_provenance_mismatched_mode_and_bad_time_fails_closed() -> None:
    cases = (
        (_receipt(), _run(provenance="simulation")),
        (_receipt(completed_at="invalid"), _run()),
        (_receipt(completed_at=None), _run()),
    )
    for receipt_data, run in cases:
        provenance, receipt = resolve_run_provenance(None, {**run, "receipt": receipt_data})
        assert (provenance, receipt) == ("unavailable", None)
