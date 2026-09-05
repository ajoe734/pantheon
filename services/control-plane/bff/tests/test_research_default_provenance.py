"""Receipt claims cannot turn locally generated research into real results."""
from __future__ import annotations

import pytest

from services.control_plane.bff.agora.research.dispatcher import (
    DefaultAllowlistedAdapter,
)


@pytest.mark.parametrize("receipt_claim", [None, "invented-owner-receipt"])
@pytest.mark.parametrize("has_receipt", [False, True])
@pytest.mark.parametrize("default_provenance", ["simulation", "real"])
def test_default_adapter_keeps_synthetic_outputs_simulation(
    receipt_claim: str | None, has_receipt: bool, default_provenance: str,
) -> None:
    adapter = DefaultAllowlistedAdapter("prototype_backtest", "vectorbt", default_provenance)
    result = adapter.execute(
        stage={
            "stage_id": "provenance-boundary",
            "routing": {"backend_mode": "real"},
            "real_backend_receipt_id": receipt_claim,
        },
        plan={"strategy_id": "provenance-strategy"},
        context={"has_real_receipt": has_receipt},
        downstream_key="provenance-boundary-test",
    )
    assert result.provenance == "simulation"
    assert result.warnings == ["default_adapter_did_not_execute_real_backend"]
    assert all(metric["provenance"] == "simulation" for metric in result.metrics)
    assert all(ref["provenance"] == "simulation" for ref in result.evidence_refs)


def test_real_default_cannot_bypass_provenance_with_unknown_mode() -> None:
    result = DefaultAllowlistedAdapter("prototype_backtest", "vectorbt", "real").execute(
        stage={"routing": {"backend_mode": "unknown"}},
        plan={}, context={}, downstream_key="unknown-mode",
    )
    assert result.provenance == "simulation"
