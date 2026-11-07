"""Drift test: reviewed fee constants versus a captured copy of the TAIFEX fee page."""

from __future__ import annotations

import re
from decimal import Decimal
from pathlib import Path

import pytest

from services.execution.lean_runtime import taifex_fee_schedule as fees

PAGE = Path(__file__).parent / "fixtures" / "taifex_20261106" / "feeSchedules.txt"
_NUMBER = r"\s*([0-9]+(?:\.[0-9]+)?)\s*\|"


def _page_row(text: str, label: str) -> tuple[Decimal, Decimal, Decimal, Decimal]:
    match = re.search(re.escape(label) + r"[^|]*\|" + _NUMBER * 4, text)
    assert match, f"fee page row not found: {label}"
    return tuple(Decimal(group) for group in match.groups())  # type: ignore[return-value]


@pytest.fixture(scope="module")
def page_text() -> str:
    return PAGE.read_text(encoding="utf-8")


@pytest.mark.parametrize("row", list(fees.FEE_ROWS.values()), ids=list(fees.FEE_ROWS))
def test_fee_constants_match_the_captured_page(page_text: str, row: fees.FeeRow) -> None:
    exchange, clearing, settlement, tax = _page_row(page_text, row.page_label)
    assert (row.exchange_fee, row.clearing_fee, row.settlement_fee) == (exchange, clearing, settlement)
    assert tax == Decimal(str(fees.STOCK_FUTURES_TRANSACTION_TAX_RATE))


def test_tax_rate_matches_the_page_footnote(page_text: str) -> None:
    assert "股價類即十萬分之2" in page_text
    assert fees.STOCK_FUTURES_TRANSACTION_TAX_RATE == 0.00002 == 2 / 100000


def test_option_premium_tax_is_not_confused_with_futures_tax(page_text: str) -> None:
    stock_option = _page_row(page_text, "股票選擇權 (交易標的為股票者)")
    assert stock_option[3] == Decimal("0.001")
    assert fees.STOCK_FUTURES_TRANSACTION_TAX_RATE != float(stock_option[3])


def test_per_side_fee_is_exchange_plus_clearing() -> None:
    assert fees.fee_row_for("stock", 100).per_side_fee == 5.0
    assert fees.fee_row_for("stock", 2000).per_side_fee == 5.0
    assert fees.fee_row_for("etf", 10000).per_side_fee == 12.5
    assert fees.fee_row_for("etf", 1000).per_side_fee == 8.0


def test_unknown_inputs_raise() -> None:
    with pytest.raises(ValueError):
        fees.fee_row_for("etf", 500)
    with pytest.raises(ValueError):
        fees.fee_row_for("option", 100)


def test_module_makes_no_network_call() -> None:
    source = Path(fees.__file__).read_text(encoding="utf-8")
    for forbidden in ("import urllib", "import requests", "import http", "import socket"):
        assert forbidden not in source
