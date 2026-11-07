"""Reviewed TAIFEX exchange fees and futures transaction tax for stock and ETF futures.

Source of record: https://www.taifex.com.tw/cht/4/feeSchedules, section 一
(交易及結算相關費率), read 2026-11-07. The values are constants on purpose: the
openapi ``FuturesAndOptionsFeeSchedule`` row has the STF and STO tax columns
swapped relative to the page and must not be read at run time. A drift test
(``test_taifex_fee_schedule.py``) compares these constants with a captured copy
of the page text under ``fixtures/taifex_20261106/feeSchedules.txt``.

Units are TWD per contract per side. Broker commission is negotiated between
broker and customer, has no official figure, and is not part of this module.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

FEE_SCHEDULE_URL = "https://www.taifex.com.tw/cht/4/feeSchedules"
FEE_SCHEDULE_READ_DATE = "2026-11-07"

# 期貨交易稅: contract amount x rate, per side. The page footnote reads 股價類即十萬分之2.
STOCK_FUTURES_TRANSACTION_TAX_RATE = 0.00002


@dataclass(frozen=True)
class FeeRow:
    """One fee-page row: 交易經手費, 結算手續費, 交割手續費 in TWD per contract."""

    page_label: str
    exchange_fee: Decimal
    clearing_fee: Decimal
    settlement_fee: Decimal

    @property
    def per_side_fee(self) -> float:
        """Fee charged on every fill: 交易經手費 + 結算手續費."""
        return float(self.exchange_fee + self.clearing_fee)


STOCK_UNDERLYING = FeeRow("股票期貨 (交易標的為股票者)", Decimal("3"), Decimal("2"), Decimal("2"))
ETF_10000_UNIT_UNDERLYING = FeeRow(
    "股票期貨(交易標的為10,000受益權單位ETF者)", Decimal("7.5"), Decimal("5"), Decimal("5")
)
ETF_1000_UNIT_UNDERLYING = FeeRow(
    "股票期貨(交易標的為1,000受益權單位ETF者)", Decimal("4.8"), Decimal("3.2"), Decimal("3.2")
)

FEE_ROWS: dict[str, FeeRow] = {
    "stock": STOCK_UNDERLYING,
    "etf_10000_unit": ETF_10000_UNIT_UNDERLYING,
    "etf_1000_unit": ETF_1000_UNIT_UNDERLYING,
}


def fee_row_for(underlying_kind: str, underlying_units_per_contract: int) -> FeeRow:
    """Select the page row for a contract.

    The page keys the ETF rows on the size of the ETF futures' underlying
    (10,000 or 1,000 beneficiary units), which is the contract unit on
    ``stockLists``. A stock underlying uses the stock row whatever the unit; the
    page does not distinguish the small stock contract from the 2,000-share one.
    """
    if underlying_kind == "stock":
        return STOCK_UNDERLYING
    if underlying_kind == "etf":
        if underlying_units_per_contract == 10000:
            return ETF_10000_UNIT_UNDERLYING
        if underlying_units_per_contract == 1000:
            return ETF_1000_UNIT_UNDERLYING
        raise ValueError(f"no fee row for ETF futures with unit {underlying_units_per_contract}")
    raise ValueError(f"unsupported underlying kind: {underlying_kind}")
