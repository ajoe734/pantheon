"""Tests for production StrategySpec distillation from research notes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from services.registry.service import app as registry_app
from services.registry.storage import reset_store
from services.research.strategy_spec.models import validate_strategy_spec_payload
from services.research.strategy_spec.production_distillation import (
    ProductionStrategySpecDistiller,
    ValidationError,
    distill,
)


def test_distills_two_fixture_research_notes_to_valid_strategy_specs(tmp_path, monkeypatch) -> None:
    _write_note(tmp_path, "src-note-tw-momentum-quality-001", _tw_momentum_note())
    _write_note(tmp_path, "src-note-us-vol-reversal-001", _us_reversal_note())
    monkeypatch.setenv("STRATEGY_SPEC_DISTILLATION_SOURCE_DIRS", str(tmp_path))

    tw_spec = distill("src-note-tw-momentum-quality-001")
    us_spec = ProductionStrategySpecDistiller(
        source_dirs=[tmp_path],
        created_at="2026-05-17T18:00:00Z",
    ).distill("src-note-us-vol-reversal-001")

    validate_strategy_spec_payload(tw_spec)
    validate_strategy_spec_payload(us_spec)
    assert tw_spec["market_scope"]["symbols"] == ["universe:twse-tpex-top200"]
    assert tw_spec["market_scope"]["frequency"] == "1d"
    assert tw_spec["metadata"]["risk_caps"] == {
        "max_position_pct": 0.03,
        "max_gross_exposure_pct": 0.35,
        "max_single_sector_pct": 0.25,
    }
    assert tw_spec["metadata"]["task_id"] == "STRAT-V2-001"
    assert tw_spec["metadata"]["registry_write_performed"] is False
    assert tw_spec["provenance"]["source_kind"] == "note"
    assert tw_spec["evidence_refs"][0]["ref_type"] == "evidence_bundle"
    assert tw_spec["evidence_refs"][1]["ref_type"] == "evidence_item"
    assert tw_spec["code_refs"][0] == {
        "repo_ref": "pantheon",
        "path": "services/research/qlib/adapter/qlib_adapter.py",
        "source_id": "src-note-tw-momentum-quality-001",
        "symbol": "QlibAdapter",
        "line_start": 80,
        "line_end": 140,
        "association": "strategy_code_reference",
    }
    assert "src-note-tw-momentum-quality-001" in tw_spec["provenance"]["source_refs"]

    assert us_spec["market_scope"]["symbols"] == ["SPY", "QQQ", "IWM"]
    assert us_spec["market_scope"]["frequency"] == "1d"
    assert us_spec["metadata"]["risk_caps"]["max_position_pct"] == 0.02
    assert us_spec["code_refs"][0]["path"] == "services/research/vectorbt/adapter.py"


def test_registry_payload_from_distillation_is_admissible(tmp_path) -> None:
    _write_note(tmp_path, "src-note-tw-momentum-quality-001", _tw_momentum_note())
    distiller = ProductionStrategySpecDistiller(
        source_dirs=[tmp_path],
        created_at="2026-05-17T18:00:00Z",
    )
    registry_payload = distiller.distill_registry_payload("src-note-tw-momentum-quality-001")
    reset_store()
    try:
        client = TestClient(registry_app)
        response = client.post("/api/registry/strategy-specs", json=registry_payload)

        assert response.status_code == 200, response.text
        entry = response.json()["entry"]
        assert entry["artifact_type"] == "strategy_spec"
        assert entry["artifact_state"] == "draft"
        assert entry["metadata"]["strategy_spec"]["metadata"]["risk_caps"]["max_position_pct"] == 0.03
    finally:
        reset_store()


def test_malformed_research_note_rejects_with_validation_error(tmp_path) -> None:
    _write_note(
        tmp_path,
        "src-note-malformed-001",
        """# Internal Research Note: Missing Risk Caps
Source Record ID: src-note-malformed-001

## Hypothesis
Rank equities by a momentum signal.

## Universe
- Symbols: SPY
- Asset class: equity

## Frequency
daily

## Code Refs
- repo_ref: pantheon; path: services/research/qlib/adapter/qlib_adapter.py
""",
    )

    with pytest.raises(ValidationError, match="risk_caps"):
        ProductionStrategySpecDistiller(source_dirs=[tmp_path]).distill("src-note-malformed-001")


def _write_note(tmp_path, source_id: str, body: str) -> None:
    (tmp_path / f"{source_id}.md").write_text(body, encoding="utf-8")


def _tw_momentum_note() -> str:
    return """# Internal Research Note: TW Momentum Quality Production Seed
Source Record ID: src-note-tw-momentum-quality-001
Date: 2026-05-17
Author: research-ops
Access: research
License: internal_research
Confidence: 0.86

## Hypothesis
TWSE and TPEx equities with positive 60-day momentum, stable profitability,
and moderate volume expansion can rank five-day forward returns better than a
sector-neutral baseline.

## Universe
- Symbols: universe:twse-tpex-top200
- Venues: TWSE, TPEx
- Asset class: equity

## Frequency
daily

## Risk Caps
- Max position pct: 3%
- Max gross exposure pct: 35%
- Max single sector pct: 25%

## Data Requirements
- point-in-time daily OHLCV
- adjusted close
- fundamentals
- sector classifications
- five-day forward return labels

## Feature Hints
- 60-day momentum
- return volatility
- turnover expansion
- gross margin stability

## Label Hints
- five_day_forward_return

## Evaluation
- Metrics: information_coefficient, sharpe_ratio, max_drawdown

## Strategy Seed
- Backend hint: qlib
- Holding period: 5 trading days
- Risk notes: survivorship bias check, corporate-action adjustment check

## Code Refs
- repo_ref: pantheon; path: services/research/qlib/adapter/qlib_adapter.py; symbol: QlibAdapter; line_start: 80; line_end: 140; association: strategy_code_reference
"""


def _us_reversal_note() -> str:
    return """# Internal Research Note: US ETF Volatility Reversal Seed
Source Record ID: src-note-us-vol-reversal-001
Date: 2026-05-17
Author: research-ops
Access: research
License: internal_research
Confidence: 0.81

## Hypothesis
US index ETFs with large overnight downside moves and elevated realized
volatility mean-revert over the next daily session when liquidity remains
above the trailing median.

## Universe
- Symbols: SPY, QQQ, IWM
- Venues: ARCA, NASDAQ
- Asset class: equity

## Frequency
1 day

## Risk Caps
- Max position pct: 2%
- Max gross exposure pct: 20%
- Max daily turnover pct: 15%

## Data Requirements
- point-in-time daily OHLCV
- overnight return labels
- ETF liquidity screens

## Feature Hints
- overnight return
- realized volatility
- liquidity
- reversal

## Label Hints
- next_session_return

## Evaluation
- Metrics: sharpe_ratio, win_rate, max_drawdown

## Strategy Seed
- Backend hint: vectorbt
- Holding period: 1 trading day
- Risk notes: liquidity regime check, borrow and shorting constraint review

## Code Refs
- repo_ref: pantheon; path: services/research/vectorbt/adapter.py; symbol: VectorBTBacktestAdapter; line_start: 40; line_end: 110; association: strategy_code_reference
"""
