"""Tests for OSS-QUANTLIB-V2-001 production option chain pricing."""
from __future__ import annotations

import json
import math
import sys
from datetime import date
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[3]
SERVICE_DIR = Path(__file__).resolve().parent
for path in (REPO_ROOT, SERVICE_DIR):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

from production_option_chain import (  # noqa: E402
    DEFAULT_DIVIDEND_YIELD,
    DEFAULT_RATE,
    DEFAULT_SPOT,
    ProductionOptionChainError,
    build_pricing_snapshot,
    default_txo_chain_definition,
    default_vol_surface,
    price_chain,
)
from registry_admission_packet import (  # noqa: E402
    REQUIRED_EVIDENCE,
    build_admission_packet,
    emit_admission_packet,
    validate_admission_packet,
)


@pytest.fixture(scope="module")
def txo_chain_definition() -> dict:
    return default_txo_chain_definition()


@pytest.fixture(scope="module")
def pricing_snapshot(txo_chain_definition: dict) -> dict:
    return build_pricing_snapshot(
        DEFAULT_SPOT,
        DEFAULT_DIVIDEND_YIELD,
        DEFAULT_RATE,
        default_vol_surface(),
        txo_chain_definition,
        created_at="2026-05-17T17:00:00Z",
    )


def test_price_chain_handles_txo_strike_expiry_grid(txo_chain_definition: dict) -> None:
    rows = price_chain(
        DEFAULT_SPOT,
        DEFAULT_DIVIDEND_YIELD,
        DEFAULT_RATE,
        default_vol_surface(),
        txo_chain_definition,
    )

    assert len(rows) == 5 * 3 * 2
    assert {row["strike"] for row in rows} == {20400.0, 20700.0, 21000.0, 21300.0, 21600.0}
    assert {row["expiry"] for row in rows} == {"2026-06-17", "2026-07-15", "2026-08-19"}
    assert {row["call_put"] for row in rows} == {"call", "put"}

    required = {"strike", "expiry", "call_put", "price", "delta", "gamma", "vega", "theta"}
    assert all(required <= set(row) for row in rows)
    for row in rows:
        assert row["price"] > 0.0
        assert math.isfinite(row["delta"])
        assert row["gamma"] >= 0.0
        assert row["vega"] > 0.0
        assert math.isfinite(row["theta"])


def test_txo_chain_call_put_parity_with_dividend_yield(txo_chain_definition: dict) -> None:
    rows = price_chain(
        DEFAULT_SPOT,
        DEFAULT_DIVIDEND_YIELD,
        DEFAULT_RATE,
        default_vol_surface(),
        txo_chain_definition,
    )
    by_key = {
        (row["strike"], row["expiry"], row["call_put"]): row
        for row in rows
    }
    as_of = date.fromisoformat(txo_chain_definition["as_of"])

    for strike in txo_chain_definition["strikes"]:
        for expiry_text in txo_chain_definition["expiries"]:
            expiry = date.fromisoformat(expiry_text)
            tenor = (expiry - as_of).days / 365.0
            call = by_key[(float(strike), expiry_text, "call")]
            put = by_key[(float(strike), expiry_text, "put")]
            parity = DEFAULT_SPOT * math.exp(-DEFAULT_DIVIDEND_YIELD * tenor) - float(strike) * math.exp(-DEFAULT_RATE * tenor)
            assert abs((call["price"] - put["price"]) - parity) <= 1e-3


def test_pricing_snapshot_registers_deterministic_checksum(pricing_snapshot: dict, txo_chain_definition: dict) -> None:
    again = build_pricing_snapshot(
        DEFAULT_SPOT,
        DEFAULT_DIVIDEND_YIELD,
        DEFAULT_RATE,
        default_vol_surface(),
        txo_chain_definition,
        created_at="2026-05-17T17:00:00Z",
    )

    assert again["checksum"] == pricing_snapshot["checksum"]
    assert pricing_snapshot["artifact_type"] == "pricing_snapshot"
    assert pricing_snapshot["chain_summary"]["strike_count"] == 5
    assert pricing_snapshot["chain_summary"]["expiry_count"] == 3
    assert pricing_snapshot["registry_entry"]["artifact_type"] == "pricing_snapshot"
    assert pricing_snapshot["registry_entry"]["artifact_state"] == "draft"
    assert pricing_snapshot["registry_entry"]["checksum"] == pricing_snapshot["checksum"]
    assert pricing_snapshot["registry_entry"]["deployment_summary"]["current_stage"] == "none"
    assert pricing_snapshot["pricing_snapshot_ref"]["checksum"] == pricing_snapshot["checksum"]


def test_price_chain_rejects_expired_contract(txo_chain_definition: dict) -> None:
    bad = {**txo_chain_definition, "expiries": ["2026-05-17"]}

    with pytest.raises(ProductionOptionChainError, match="expiry must be after"):
        price_chain(
            DEFAULT_SPOT,
            DEFAULT_DIVIDEND_YIELD,
            DEFAULT_RATE,
            default_vol_surface(),
            bad,
        )


def test_build_admission_packet_conforms_to_promotion_readiness_shape(pricing_snapshot: dict) -> None:
    packet = build_admission_packet(
        pricing_snapshot,
        created_at="2026-05-17T17:10:00Z",
    )
    assert validate_admission_packet(packet) == []
    assert packet["schema_version"] == "PromotionReadinessPacket.v1"
    assert packet["target_type"] == "artifact"
    assert packet["environment"] == "paper"
    assert packet["required_evidence"] == REQUIRED_EVIDENCE
    assert packet["missing_evidence"] == []
    assert packet["can_proceed"] is True
    assert packet["registry_request"]["artifact_type"] == "pricing_snapshot"
    assert packet["registry_request"]["requested_transition"] == "draft_to_candidate"
    assert packet["registry_request"]["registry_write_performed"] is False
    assert packet["candidate_artifact"]["checksum"].startswith("sha256:")
    assert packet["pricing_snapshot_summary"]["chain_summary"]["contract_count"] == 30
    assert packet["safety_assertions"]["no_registry_write"] is True
    assert packet["safety_assertions"]["no_gpu"] is True


def test_emit_admission_packet_writes_json(tmp_path: Path, pricing_snapshot: dict) -> None:
    output = tmp_path / "admission_packet.json"
    packet = emit_admission_packet(
        output,
        pricing_snapshot=pricing_snapshot,
        created_at="2026-05-17T17:10:00Z",
    )

    assert output.exists()
    persisted = json.loads(output.read_text(encoding="utf-8"))
    assert persisted == packet
    assert persisted["pricing_snapshot_ref"]["artifact_type"] == "pricing_snapshot"
