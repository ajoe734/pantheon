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


KNOWN_ETF_CONTRACTS = frozenset({"SRF", "NYF", "OAF", "OBF", "OCF", "ODF", "OEF", "OFF", "OGF", "OHF", "OIF", "OJF", "OKF", "OLF", "OMF", "ONF", "OOF", "OPF", "OQF", "ORF"})


def fee_row_for_contract(
    contract: str | None = None,
    *,
    underlying: str | None = None,
    underlying_kind: str | None = None,
    multiplier: int | float = 2000,
) -> FeeRow:
    """Resolve the TAIFEX fee row from contract code, underlying code, or kind."""
    resolved_kind = str(underlying_kind or "").strip().lower()
    clean_contract = str(contract or "").strip().upper()
    clean_underlying = str(underlying or "").strip().upper()
    if resolved_kind not in ("stock", "etf"):
        if clean_contract in KNOWN_ETF_CONTRACTS or clean_underlying.startswith("00"):
            resolved_kind = "etf"
        else:
            resolved_kind = "stock"
    unit = int(round(float(multiplier)))
    if resolved_kind == "etf" and unit not in (1000, 10000):
        unit = 1000
    return fee_row_for(resolved_kind, unit)


def compute_open_or_close_cost(
    contract: str | None,
    price: float,
    contracts: int | float,
    *,
    multiplier: int | float = 2000,
    underlying: str | None = None,
    underlying_kind: str | None = None,
    broker_fee_per_contract_per_side: float = 0.0,
) -> dict[str, float]:
    """Compute per-fill fee + transaction tax for opening or closing a futures position."""
    row = fee_row_for_contract(
        contract,
        underlying=underlying,
        underlying_kind=underlying_kind,
        multiplier=multiplier,
    )
    qty_dec = abs(Decimal(str(contracts)))
    price_dec = Decimal(str(price))
    mult_dec = Decimal(str(multiplier))
    broker_dec = Decimal(str(broker_fee_per_contract_per_side))
    tax_rate_dec = Decimal(str(STOCK_FUTURES_TRANSACTION_TAX_RATE))

    per_contract_fee = row.exchange_fee + row.clearing_fee + broker_dec
    fee_dec = per_contract_fee * qty_dec
    tax_dec = price_dec * mult_dec * qty_dec * tax_rate_dec
    total_dec = fee_dec + tax_dec
    return {
        "exchange_fee": float(row.exchange_fee * qty_dec),
        "clearing_fee": float(row.clearing_fee * qty_dec),
        "broker_fee": float(broker_dec * qty_dec),
        "fee": float(fee_dec),
        "tax": float(tax_dec),
        "total_cost": float(total_dec),
    }


def compute_settlement_cost(
    contract: str | None,
    final_settlement_price: float,
    contracts: int | float,
    *,
    multiplier: int | float = 2000,
    underlying: str | None = None,
    underlying_kind: str | None = None,
    broker_fee_per_contract_per_side: float = 0.0,
) -> dict[str, float]:
    """Compute cash-settlement delivery fee + transaction tax at expiry."""
    row = fee_row_for_contract(
        contract,
        underlying=underlying,
        underlying_kind=underlying_kind,
        multiplier=multiplier,
    )
    qty_dec = abs(Decimal(str(contracts)))
    price_dec = Decimal(str(final_settlement_price))
    mult_dec = Decimal(str(multiplier))
    broker_dec = Decimal(str(broker_fee_per_contract_per_side))
    tax_rate_dec = Decimal(str(STOCK_FUTURES_TRANSACTION_TAX_RATE))

    per_contract_fee = row.settlement_fee + broker_dec
    fee_dec = per_contract_fee * qty_dec
    tax_dec = price_dec * mult_dec * qty_dec * tax_rate_dec
    total_dec = fee_dec + tax_dec
    return {
        "settlement_fee": float(row.settlement_fee * qty_dec),
        "broker_fee": float(broker_dec * qty_dec),
        "fee": float(fee_dec),
        "tax": float(tax_dec),
        "total_cost": float(total_dec),
    }
